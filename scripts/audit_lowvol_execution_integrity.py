"""Read-only execution-gap forensics for the frozen low-vol cross-sectional study.

No new selection attempt, no Stage 3 trial, no holdout access. Reproduces
the exact frozen Warden one-bar position delay, then distinguishes missing
observations from the validation-window terminal accounting boundary.
"""

from __future__ import annotations

import argparse
import bisect
import json
from collections import Counter
from pathlib import Path

import numpy as np
import polars as pl
import yaml

from warden.contracts import load_contract
from warden.provenance import resolve_dataset
from warden.runner import _run_strategy_isolated


def classify_execution_gaps(
    observed_open: np.ndarray,
    delayed_positions: np.ndarray,
    timestamps: list,
    assets: list[str],
    *,
    start: int,
    stop: int,
) -> dict:
    """Exact sparse-panel boundary for a 1h open-to-open execution contract."""
    if observed_open.shape != delayed_positions.shape:
        raise ValueError("Position and open-price arrays must align")
    if len(timestamps) != observed_open.shape[0] or len(assets) != observed_open.shape[1]:
        raise ValueError("Panel dimensions mismatch")
    if not 0 <= start < stop <= len(timestamps):
        raise ValueError("Invalid exploratory validation slice")
    seen = np.isfinite(observed_open) & (observed_open > 0)
    active = np.isfinite(delayed_positions) & (np.abs(delayed_positions) > 1e-12)
    counts: Counter[str] = Counter()
    exposures: Counter[str] = Counter()
    events = []
    for t in range(start, stop):
        indices = np.flatnonzero(active[t])
        if not len(indices):
            continue
        terminal = t + 1 >= stop
        consecutive_global = t + 1 < len(timestamps) and (timestamps[t+1]-timestamps[t]).total_seconds() == 3600
        for asset_idx in indices:
            if not seen[t, asset_idx]:
                reason = "missing_current_open"
            elif terminal:
                reason = "terminal_no_next_exploratory_open"
            elif not consecutive_global:
                reason = "nonconsecutive_global_hour"
            elif not seen[t+1, asset_idx]:
                reason = "missing_next_open"
            else:
                continue
            symbol = assets[asset_idx]
            counts[reason] += 1
            exposures[reason] += abs(float(delayed_positions[t, asset_idx]))
            events.append({
                "time_utc": timestamps[t].isoformat(),
                "symbol": symbol,
                "reason": reason,
                "position_weight": float(delayed_positions[t, asset_idx]),
                "current_observed": bool(seen[t, asset_idx]),
                "next_hour_observed": bool(
                    t+1 < len(timestamps) and seen[t+1, asset_idx]
                ),
            })
    return {
        "counts": dict(sorted(counts.items())),
        "gross_exposure_by_reason": dict(sorted(exposures.items())),
        "total": len(events),
        "events": events,
    }


def investigate(spec_path: Path) -> dict:
    cfg = yaml.safe_load(spec_path.read_text())
    contract = load_contract(Path("contracts") / (cfg["contract_id"].lower()+".yaml"))
    if contract.exploratory_data_path == contract.holdout_data_path:
        raise ValueError("Refusing holdout re-use")
    dataset = resolve_dataset(contract, Path("."))
    if not dataset.eligible_market:
        raise ValueError("Source not market verified")
    panel = pl.read_parquet(dataset.path).sort("timestamp", "asset")
    timestamps = sorted(panel["timestamp"].unique().to_list())
    assets = sorted(panel["asset"].unique().to_list())
    start = bisect.bisect_left(timestamps, contract.val_start)
    stop = bisect.bisect_left(timestamps, contract.val_end)
    raw = _run_strategy_isolated(Path(cfg["strategy_module"]), panel, contract, cfg["parameters"])
    opens = (
        panel.pivot(index="timestamp", on="asset", values="open")
        .sort("timestamp").select(assets).to_numpy()
    )
    positions = np.asarray(raw["positions"], dtype=float)
    if positions.shape != opens.shape:
        raise ValueError("Strategy/source panel shape disagreement")
    delay = contract.execution_delay_bars
    delayed = np.zeros_like(positions)
    delayed[delay:] = positions[:-delay]
    gap = classify_execution_gaps(opens, delayed, timestamps, assets, start=start, stop=stop)
    # This is a separate independent diagnostic for historical panel holes:
    # feature calculations do not treat a missing one-hour candle as a return.
    interval_hours = np.array([
        (timestamps[t+1]-timestamps[t]).total_seconds()/3600
        for t in range(len(timestamps)-1)
    ])
    return {
        "contract_id": contract.contract_id,
        "dataset_sha256": dataset.sha256,
        "validation_begin": str(contract.val_start),
        "validation_end_exclusive": str(contract.val_end),
        "n_hourly_observations": stop-start,
        "n_assets": len(assets),
        "nonconsecutive_global_hours": int(np.sum(interval_hours != 1)),
        "n_negative_or_zero_observed_opens": int(np.sum(np.isfinite(opens)&(opens <= 0))),
        "executable_gap_audit": gap,
        "holdout_read": False,
        "source_mutated": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=Path("experiments/active/arxiv_crypto_lowvol_24h_20261102.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Evidence is write-once; do not replace a prior analysis")
    result = investigate(args.spec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print(json.dumps({
        k: v for k, v in result.items() if k != "executable_gap_audit"
    } | {"executable_gap_audit": {
        k: v for k,v in result["executable_gap_audit"].items() if k != "events"
    }}, indent=2))


if __name__ == "__main__":
    main()
