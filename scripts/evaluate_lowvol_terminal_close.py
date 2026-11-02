"""Terminal flat accounting for an exploratory NEXT_BAR cross-sectional replay.

The last next-bar open is at val_end-exclusive minus one hour. A research
portfolio can submit a zero target at the previous close and settle at that
final observed open, without opening a forbidden protected-period price.

This is a validation-end accounting *sensitivity*, not a tradable alpha
signal, not an alternate Warden gate, and not a new selection attempt.
"""

from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path

import numpy as np
import polars as pl
import yaml

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


def terminal_flat_targets(
    targets: np.ndarray,
    *,
    val_start: int,
    val_end: int,
    execution_delay: int,
) -> np.ndarray:
    """Copy source signals, allow flatten at final observable next-bar open."""
    if targets.ndim != 2 or not 0 <= val_start < val_end <= targets.shape[0]:
        raise ValueError("Invalid target panel or validation bounds")
    if not 1 <= execution_delay < (val_end - val_start):
        raise ValueError("Cannot flatten with delayed decision outside validation")
    last_execution = val_end - 1
    flat_decision = last_execution - execution_delay
    out = targets.copy()
    out[flat_decision:val_end] = 0.0
    return out


def calculate(spec_path: Path) -> dict:
    cfg = yaml.safe_load(spec_path.read_text())
    contract = load_contract(Path("contracts") / (cfg["contract_id"].lower() + ".yaml"))
    ds = resolve_dataset(contract, Path("."))
    if not ds.eligible_market:
        raise ValueError("Missing exact pinned market source")
    panel = pl.read_parquet(ds.path).sort("timestamp", "asset")
    stamps = sorted(panel["timestamp"].unique().to_list())
    assets = sorted(panel["asset"].unique().to_list())
    begin = bisect.bisect_left(stamps, contract.val_start)
    end = bisect.bisect_left(stamps, contract.val_end)
    if not (end > begin and end == len(stamps)):
        raise ValueError("Unexpected dataset boundary; refusal avoids reading holdout")
    raw = _run_strategy_isolated(Path(cfg["strategy_module"]), panel, contract, cfg["parameters"])
    original = np.asarray(raw["positions"], dtype=float)
    prices = execution_returns(panel, contract, assets)
    funding = _aligned_matrix(panel, "funding_rate", assets)
    settlement = _funding_settlement_mask(stamps, contract)

    scenarios = {}
    for scenario, multiplier, delay_extra in (
        ("base", 1.0, 0),
        ("double_cost", 2.0, 0),
        ("triple_cost", 3.0, 0),
        ("delay_plus_one", 1.0, 1),
    ):
        delay = contract.execution_delay_bars + delay_extra
        flat_targets = terminal_flat_targets(
            original, val_start=begin, val_end=end, execution_delay=delay
        )
        run = {}
        for mode, positions in (("original", original), ("terminal_flat", flat_targets)):
            sim = simulate_positions(
                positions, prices["long"], funding, contract, slice(begin, end),
                cost_multiplier=multiplier, delay_offset=delay_extra,
                funding_mask=settlement, quote_returns=prices,
            )
            metrics = calculate_metrics_from_returns(
                sim["gross_returns"], sim["net_returns"],
                sim["turnover"], contract.bars_per_year,
            )
            pos = sim["positions"]
            run[mode] = {
                "metrics": metrics,
                "end_exposure": float(np.sum(np.abs(pos[-1]))),
                "terminal_bar_net_bps": float(sim["net_returns"][-1] * 1e4),
                "terminal_bar_turnover": float(sim["turnover"][-1]),
                "net_simple_total_pct": float((np.prod(1.0 + sim["net_returns"]) - 1.0) * 100.0),
                "last_hour": stamps[end-1].isoformat(),
            }
            if mode == "original":
                prior_net = sim["net_returns"].copy()
            else:
                # NumPy vectorized reductions may differ by ~1e-19 when
                # terminal targets change the array's reduction order.
                if not np.allclose(
                    prior_net[:-1], sim["net_returns"][:-1],
                    atol=1e-14, rtol=1e-12, equal_nan=False,
                ):
                    bad = np.flatnonzero(~np.isclose(
                        prior_net[:-1], sim["net_returns"][:-1],
                        atol=1e-14, rtol=1e-12,
                    ))
                    raise ValueError(f"Unexpected preterminal change in {scenario}: {bad[:10].tolist()}")
                if run[mode]["end_exposure"] != 0:
                    raise ValueError("Failed to close positions at final valid open")
        scenarios[scenario] = {
            **run,
            "delta_net_sharpe": (
                run["terminal_flat"]["metrics"]["net_sharpe"]
                - run["original"]["metrics"]["net_sharpe"]
            ),
        }

    return {
        "source_spec": str(spec_path),
        "contract_id": contract.contract_id,
        "dataset_sha256": ds.sha256,
        "validation_end_exclusive": contract.val_end.isoformat(),
        "method": "Force zero target at t_end - delay - 1; execute at last observed open; pay full exit turnover cost",
        "scenarios": scenarios,
        "status": "EXPLORATORY_ACCOUNTING_ONLY",
        "protected_period_accessed": False,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--spec", type=Path, default=Path("experiments/active/arxiv_crypto_lowvol_24h_20261102.yaml"))
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError("Do not overwrite prior read-only evidence")
    result = calculate(args.spec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print(json.dumps({
        "contract": result["contract_id"],
        "data_sha256": result["dataset_sha256"],
        "scenarios": {
            k: {
                "original_net_sr": v["original"]["metrics"]["net_sharpe"],
                "flat_net_sr": v["terminal_flat"]["metrics"]["net_sharpe"],
                "original_gross_end": v["original"]["end_exposure"],
                "flat_gross_end": v["terminal_flat"]["end_exposure"],
                "flat_terminal_turnover": v["terminal_flat"]["terminal_bar_turnover"],
            } for k,v in result["scenarios"].items()
        }
    }, indent=2))


if __name__ == "__main__":
    main()
