"""Read-only funding-event stress of the already-rejected low-vol Stage 3 preflight.

Warden's pinned 1h crypto simulator charges funding at UTC hours 0/8/16.
Binance's *observed* event archive includes 1/2/4/8-hour intervals. This
separate diagnostic replays observed events for the same pre-existing target
positions, and stresses unknown events without inventing a zero funding rate.

Does not change Warden evaluation, source datasets, or selection attempts.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl
import yaml

from scripts.build_pit_top100_proposal import ROOT, funding_paths
from scripts.evaluate_lowvol_terminal_close import terminal_flat_targets
from warden.contracts import load_contract
from warden.provenance import resolve_dataset
from warden.runner import (
    _aligned_matrix,
    _funding_settlement_mask,
    _run_strategy_isolated,
    calculate_metrics_from_returns,
    execution_returns,
    simulate_positions,
)

DEFAULT_SPEC = Path("experiments/active/arxiv_crypto_lowvol_24h_20261102.yaml")


def event_rate_matrix(
    events: pl.DataFrame, timestamps: list, assets: list[str],
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Aggregate actual funding-event timestamps into UTC hourly buckets."""
    rate = np.zeros((len(timestamps), len(assets)), dtype=float)
    count = np.zeros_like(rate, dtype=np.int32)
    time_to_index = {t: i for i,t in enumerate(timestamps)}
    asset_to_index = {sym: i for i,sym in enumerate(assets)}
    intervals: Counter[str] = Counter()
    off_grid = 0
    duplicated = 0
    for item in events.iter_rows(named=True):
        original = item["timestamp"]
        hour = original.replace(minute=0, second=0, microsecond=0)
        i = time_to_index.get(hour)
        j = asset_to_index.get(item["asset"])
        if i is None or j is None:
            off_grid += 1
            continue
        val = float(item["funding_rate"])
        if not np.isfinite(val):
            off_grid += 1
            continue
        if count[i,j]:
            duplicated += 1
        rate[i,j] += val
        count[i,j] += 1
        intervals[str(item["funding_interval_hours"])] += 1
    return rate, count, {
        "event_intervals_hours": dict(sorted(intervals.items())),
        "observed_funding_event_hours": int(np.sum(count > 0)),
        "duplicate_same_asset_hour": duplicated,
        "off_grid_events": off_grid,
    }


def get_validation_events(symbols: list[str], start: datetime, end: datetime) -> tuple[pl.DataFrame, dict]:
    all_paths = funding_paths(ROOT, set(symbols))
    selected = []
    files_sha = []
    for path in all_paths:
        parts = path.parts
        year = int(next(x for x in parts if x.startswith("year=")).split("=")[1])
        month = int(next(x for x in parts if x.startswith("month=")).split("=")[1])
        if (year,month) < (start.year,start.month) or (year,month) > (end.year,end.month):
            continue
        if year == 2025 and month >= 7:
            raise ValueError("Refusing source path at/after protected holdout")
        selected.append(path)
        files_sha.append({"path":str(path.relative_to(ROOT)),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if not selected:
        raise ValueError("No observed historical funding source files")
    frame=(
        pl.scan_parquet([str(p) for p in selected],hive_partitioning=False)
        .select(
            pl.col("timestamp").cast(pl.Datetime("us","UTC")),
            pl.col("symbol").alias("asset"),
            pl.col("funding_rate"),
            pl.col("funding_interval_hours"),
        )
        .filter((pl.col("timestamp") >= start) & (pl.col("timestamp") < end))
        .sort("timestamp","asset")
        .collect(engine="streaming")
    )
    return frame, {
        "source_file_count": len(selected),
        "source_file_sha256": hashlib.sha256(
            json.dumps(files_sha,sort_keys=True,separators=(",",":")).encode()
        ).hexdigest(),
        "source_events": frame.height,
        "source_assets": int(frame["asset"].n_unique()),
    }


def audit(spec_path: Path = DEFAULT_SPEC) -> dict:
    config = yaml.safe_load(spec_path.read_text())
    contract = load_contract(Path("contracts") / (config["contract_id"].lower()+".yaml"))
    ds = resolve_dataset(contract, Path("."))
    if not ds.eligible_market:
        raise ValueError("Original observed source no longer matches contract")
    panel = pl.read_parquet(ds.path).sort("timestamp","asset")
    stamps = sorted(panel["timestamp"].unique().to_list())
    assets = sorted(panel["asset"].unique().to_list())
    left = bisect.bisect_left(stamps, contract.val_start)
    right = bisect.bisect_left(stamps, contract.val_end)
    if right != len(stamps):
        raise ValueError("Unexpected exploratory end, do not access holdout")
    raw = _run_strategy_isolated(Path(config["strategy_module"]), panel, contract, config["parameters"])
    original_targets = np.asarray(raw["positions"], dtype=float)
    opens = execution_returns(panel, contract, assets)
    funding = _aligned_matrix(panel, "funding_rate", assets)
    funding_mask = _funding_settlement_mask(stamps, contract)
    events, evidence = get_validation_events(
        assets, contract.val_start, contract.val_end
    )
    actual_rate, event_count, event_report = event_rate_matrix(events, stamps, assets)

    outputs = {}
    for mode, multiplier, extra_delay in (
        ("base",1.0,0),
        ("double_cost",2.0,0),
        ("delay_plus_one",1.0,1),
    ):
        targets = terminal_flat_targets(
            original_targets,val_start=left,val_end=right,
            execution_delay=contract.execution_delay_bars+extra_delay,
        )
        sim = simulate_positions(
            targets, opens["long"],funding,contract,slice(left,right),
            cost_multiplier=multiplier,delay_offset=extra_delay,
            funding_mask=funding_mask,quote_returns=opens,
        )
        pos = sim["positions"]
        rate = actual_rate[left:right]
        counts = event_count[left:right]
        known_funding = np.sum(pos * rate,axis=1)
        known_only_net = (
            sim["gross_returns"]
            - sim["turnover"] * contract.total_one_way_cost_decimal() * multiplier
            - known_funding
        )

        # Every 4 hours without a sourced event, assume a 1bp adverse funding
        # payment per unit gross exposure. Conservatively overcounts missing
        # 8h settlement schedules; a *scenario*, not assumed historical truth.
        hours = np.array([t.hour for t in stamps[left:right]],dtype=int)
        potential = (hours % 4 == 0)[:,None] & (counts == 0)
        unsupported_abs_exposure = float(np.sum(np.abs(pos) * potential))
        punitive = np.sum(np.abs(pos) * potential * 0.0001,axis=1)
        adverse_net = known_only_net - punitive

        metrics_known = calculate_metrics_from_returns(
            sim["gross_returns"], known_only_net,
            sim["turnover"], contract.bars_per_year,
        )
        metrics_adverse = calculate_metrics_from_returns(
            sim["gross_returns"], adverse_net,
            sim["turnover"], contract.bars_per_year,
        )
        outputs[mode]={
            "original_8h_mask_net_sharpe":calculate_metrics_from_returns(
                sim["gross_returns"],sim["net_returns"],sim["turnover"],contract.bars_per_year,
            )["net_sharpe"],
            "observed_events_only_net_sharpe":metrics_known["net_sharpe"],
            "missing_4h_event_adverse_1bp_net_sharpe":metrics_adverse["net_sharpe"],
            "observed_events_only_net_return_pct":float((np.prod(1+known_only_net)-1)*100),
            "unknown_funding_abs_exposure_event_units":unsupported_abs_exposure,
            "observed_settlement_funding_cash_pct_of_equity":float(np.sum(known_funding)*100),
            "unknown_4h_event_adverse_cash_pct_of_equity":float(np.sum(punitive)*100),
            "terminal_position_abs":float(np.abs(pos[-1]).sum()),
        }
    return {
        "hypothesis":config["hypothesis_id"],
        "contract":contract.contract_id,
        "data_sha256":ds.sha256,
        "funding_source":evidence,
        "event_classification":event_report,
        "diagnostics":outputs,
        "limitations":[
            "No funding source events does not imply zero cost",
            "The 1bp 4-hour missing-event penalty is a scenario, not actual historical funding",
            "Event timestamps near simultaneous opens may cross market fill order",
            "Fixed 100-coin panel is hindsight selected; source lacks verified delisting fills",
        ],
        "status":"READ_ONLY_NOT_STAGE3",
        "protected_data_accessed":False,
    }


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Economic source audit is write-once")
    result=audit()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result,indent=2,sort_keys=True))


if __name__=="__main__":
    main()
