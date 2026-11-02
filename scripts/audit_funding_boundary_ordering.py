"""Read-only funding/fill ordering sensitivity for frozen low-vol positions.

Binance funding events can timestamp exactly at an hourly bar open while
Warden assumes a NEXT_BAR open fill at the same time. With hourly prices
alone, settlement-before-fill versus fill-before-settlement cannot always
be determined. This module computes both outcomes and an explicit
bounded ambiguity interval. It never estimates a missing event as zero.

It is a source diagnostic, NOT native Warden screening, Stage 3, or a
replacement for the protected eight-hour funding evaluator. All price
and funding inputs end strictly before the sealed 2025-07-01 boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from bisect import bisect_left
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import yaml

from scripts.audit_lowvol_event_funding import get_validation_events
from scripts.evaluate_lowvol_terminal_close import terminal_flat_targets
from warden.contracts import load_contract
from warden.runner import (
    _aligned_matrix,
    _funding_settlement_mask,
    _run_strategy_isolated,
    calculate_metrics_from_returns,
    execution_returns,
    simulate_positions,
)

SPEC = Path("experiments/active/arxiv_crypto_lowvol_24h_20261102.yaml")
CONTRACT = Path("contracts/crypto_perps_top100_1h_v1.yaml")
OUTPUT = Path("docs/evidence/arxiv_stage1_20261102/funding_boundary_ordering_20261102.json")
BOUNDARY_UNCERTAINTY_MS = 1000


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def reconcile_event_positions(
    events: pl.DataFrame,
    timestamps: list[datetime],
    assets: list[str],
    positions: np.ndarray,
    *,
    boundary_uncertainty_ms: int = BOUNDARY_UNCERTAINTY_MS,
) -> dict:
    """Compute funding costs on either side of a hypothetical open fill.

    positions[t] is Warden's filled exposure *after* the hourly open
    at timestamps[t], carried until the following open. For events at
    the open or within the specified uncertainty interval, compare to
    exposure immediately before that open (positions[t-1]). Previous
    position before the first validation open is deliberately zero,
    matching Warden's initial zero portfolio boundary.

    Best/worst per event are a bounding stress, not a realizable trading
    policy. Missing funding events remain UNKNOWN and are not imputed.
    """
    if type(boundary_uncertainty_ms) is not int or boundary_uncertainty_ms < 0:
        raise ValueError("Uncertainty window must be a nonnegative integer in milliseconds")
    if positions.shape != (len(timestamps), len(assets)):
        raise ValueError("Positions/assets/timestamps shape mismatch")
    if not np.isfinite(positions).all():
        raise ValueError("Unknown positions cannot be treated as flat")
    if len(set(assets)) != len(assets) or len(set(timestamps)) != len(timestamps):
        raise ValueError("Duplicate asset/timestamp")
    if any(
        (timestamps[i] - timestamps[i - 1]) != timedelta(hours=1)
        for i in range(1, len(timestamps))
    ):
        raise ValueError("Funding hourly clock is not contiguous")
    if any(t.tzinfo is None or t.utcoffset() != timedelta(0) for t in timestamps):
        raise ValueError("Only UTC-aware hourly clocks supported")
    if any(t.minute or t.second or t.microsecond for t in timestamps):
        raise ValueError("Hourly opens must align to exact UTC hour")
    required = {"timestamp", "asset", "funding_rate", "funding_interval_hours"}
    if required - set(events.columns):
        raise ValueError(f"Missing observed funding fields: {sorted(required - set(events.columns))}")

    t_index = {t: i for i, t in enumerate(timestamps)}
    a_index = {a: i for i, a in enumerate(assets)}
    previous = np.zeros_like(positions)
    previous[1:] = positions[:-1]
    n = len(timestamps)
    funding_post = np.zeros(n, dtype=np.float64)
    funding_pre = np.zeros(n, dtype=np.float64)
    funding_best = np.zeros(n, dtype=np.float64)
    funding_worst = np.zeros(n, dtype=np.float64)
    observed_count = np.zeros((n, len(assets)), dtype=np.int32)
    status = {
        "events_in_window": 0,
        "ambiguous_near_open": 0,
        "events_later_than_open_window": 0,
        "events_before_window": 0,
        "events_after_window": 0,
        "unmapped_symbol": 0,
        "invalid_event": 0,
        "duplicate_asset_hour": 0,
        "ambiguous_events_with_changed_position": 0,
        "nonzero_ms_offset_in_boundary_window": 0,
    }
    first = timestamps[0]
    stop = timestamps[-1] + timedelta(hours=1)
    for row in events.iter_rows(named=True):
        timestamp = row["timestamp"]
        if timestamp is None or timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            status["invalid_event"] += 1
            continue
        if timestamp < first:
            status["events_before_window"] += 1
            continue
        if timestamp >= stop:
            status["events_after_window"] += 1
            continue
        asset_idx = a_index.get(row["asset"])
        if asset_idx is None:
            status["unmapped_symbol"] += 1
            continue
        rate = row["funding_rate"]
        interval = row["funding_interval_hours"]
        if rate is None or not np.isfinite(rate) or interval not in (1, 2, 4, 8):
            status["invalid_event"] += 1
            continue
        hour = timestamp.replace(minute=0, second=0, microsecond=0)
        idx = t_index.get(hour)
        if idx is None:
            status["invalid_event"] += 1
            continue
        if observed_count[idx, asset_idx]:
            status["duplicate_asset_hour"] += 1
        observed_count[idx, asset_idx] += 1
        status["events_in_window"] += 1
        offset_ms = (timestamp - hour).total_seconds() * 1000
        post_cost = float(positions[idx, asset_idx]) * float(rate)
        if offset_ms <= boundary_uncertainty_ms:
            status["ambiguous_near_open"] += 1
            if offset_ms > 0:
                status["nonzero_ms_offset_in_boundary_window"] += 1
            pre_cost = float(previous[idx, asset_idx]) * float(rate)
            if abs(pre_cost - post_cost) > 1e-15:
                status["ambiguous_events_with_changed_position"] += 1
        else:
            status["events_later_than_open_window"] += 1
            pre_cost = post_cost
        funding_pre[idx] += pre_cost
        funding_post[idx] += post_cost
        funding_best[idx] += min(pre_cost, post_cost)
        funding_worst[idx] += max(pre_cost, post_cost)

    return {
        "observed_event_count": observed_count,
        "funding_cost_pre_open": funding_pre,
        "funding_cost_post_open": funding_post,
        "funding_cost_best_bound": funding_best,
        "funding_cost_worst_bound": funding_worst,
        "event_statistics": status,
    }


def evaluate(panel_path: Path) -> dict:
    cfg = yaml.safe_load(SPEC.read_text())
    contract = load_contract(CONTRACT)
    if cfg["contract_id"] != contract.contract_id or contract.val_end != datetime.fromisoformat(
        "2025-07-01T00:00:00+00:00"
    ):
        raise ValueError("Unexpected contract/validation boundary")
    if sha256(panel_path) != contract.data_snapshot_sha256:
        raise ValueError("Original frozen Binance panel source digest mismatch")
    panel = pl.read_parquet(panel_path).sort("timestamp", "asset")
    if panel["timestamp"].max() >= contract.val_end:
        raise ValueError("Exploratory panel contains protected time")
    stamps = sorted(panel["timestamp"].unique().to_list())
    assets = sorted(panel["asset"].unique().to_list())
    start = bisect_left(stamps, contract.val_start)
    stop = bisect_left(stamps, contract.val_end)
    if start >= stop or stop != len(stamps):
        raise ValueError("Validation window cannot be mapped without holdout")
    source = _run_strategy_isolated(
        Path(cfg["strategy_module"]), panel, contract, cfg["parameters"]
    )
    raw_pos = np.asarray(source["positions"], dtype=np.float64)
    observed = execution_returns(panel, contract, assets)
    funding = _aligned_matrix(panel, "funding_rate", assets)
    fixed_mask = _funding_settlement_mask(stamps, contract)
    events, source_receipt = get_validation_events(
        assets, contract.val_start, contract.val_end
    )
    finding = {}
    for mode, cost_multiplier, delay_extra in (
        ("base_cost", 1.0, 0),
        ("double_cost", 2.0, 0),
        ("delay_plus_one", 1.0, 1),
    ):
        target = terminal_flat_targets(
            raw_pos, val_start=start, val_end=stop,
            execution_delay=contract.execution_delay_bars + delay_extra,
        )
        sim = simulate_positions(
            target, observed["long"], funding, contract, slice(start, stop),
            cost_multiplier=cost_multiplier, delay_offset=delay_extra,
            funding_mask=fixed_mask, quote_returns=observed,
        )
        filled = np.asarray(sim["positions"])
        adjusted = reconcile_event_positions(
            events, stamps[start:stop], assets, filled
        )
        fee_cost = sim["turnover"] * contract.total_one_way_cost_decimal() * cost_multiplier
        gross = sim["gross_returns"]
        variants = {}
        for name, key in (
            ("pre_open_if_ambiguous", "funding_cost_pre_open"),
            ("post_open_if_ambiguous", "funding_cost_post_open"),
            ("optimistic_bound", "funding_cost_best_bound"),
            ("adverse_bound", "funding_cost_worst_bound"),
        ):
            net = gross - fee_cost - adjusted[key]
            metrics = calculate_metrics_from_returns(
                gross, net, sim["turnover"], contract.bars_per_year
            )
            variants[name] = {
                "net_sharpe": metrics["net_sharpe"],
                "net_profit_factor": metrics["profit_factor"],
                "max_drawdown_pct": metrics["max_drawdown_pct"],
                "funding_cash_pct_of_initial_equity": float(100 * adjusted[key].sum()),
                "simple_total_return_pct": float(100 * (np.prod(1 + net) - 1)),
            }
        finding[mode] = {
            "existing_fixed_8h_net_sharpe": calculate_metrics_from_returns(
                gross, sim["net_returns"], sim["turnover"], contract.bars_per_year
            )["net_sharpe"],
            "event_ordering": variants,
            "event_statistics": adjusted["event_statistics"],
            "terminal_gross_weight": float(np.abs(filled[-1]).sum()),
        }
    return {
        "schema": "binance-event-time-settlement-ordering-v1",
        "study": cfg["hypothesis_id"],
        "data_sha256": contract.data_snapshot_sha256,
        "data_provenance": "original SHA-pinned 100-asset panel (hindsight selected)",
        "event_sources": source_receipt,
        "ordering_window_ms": BOUNDARY_UNCERTAINTY_MS,
        "cases": finding,
        "scientific_status": "SENSITIVITY_ONLY__UNQUALIFIED",
        "missing_events_are_unknown": True,
        "stage1_or_stage3_attempt_created": False,
        "protected_holdout_accessed": False,
        "not_a_venue_fills_simulation": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Research artifact is immutable: {args.output}")
    report = evaluate(args.panel_path)
    payload = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
        tmp = Path(fh.name)
    try:
        os.link(tmp, args.output)
    finally:
        tmp.unlink(missing_ok=True)
    print(json.dumps({
        "path": str(args.output),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "cases": {
            k: {"fixed_8h": v["existing_fixed_8h_net_sharpe"],
                "sharpe_ordering": {name: x["net_sharpe"]
                                    for name, x in v["event_ordering"].items()},
                "events": v["event_statistics"]}
            for k, v in report["cases"].items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
