"""Read-only executable economics of the recorded low-vol Stage 1 PASS.

This is *diagnostic* and does not create a Warden trial, change historical
selection debt, or touch a protected holdout. Uses Warden execution, funding,
delay and cost functions, never close-to-close proxy PnL.
"""

from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path

import numpy as np
import polars as pl
import yaml

from warden.cli import check_worktree
from warden.provenance import resolve_dataset
from warden.registry import ResearchRegistry
from warden.runner import (
    ExperimentRunner,
    _aligned_matrix,
    _funding_settlement_mask,
    _run_strategy_isolated,
    calculate_metrics_from_returns,
    execution_returns,
    simulate_positions,
)

SPEC = Path("experiments/active/arxiv_crypto_lowvol_xs_20261102.yaml")
OUTPUT = Path(".workspace/stage1_optimization/lowvol_economics.json")
REGISTRY = Path(".workspace/stage1_optimization/registry.db")


def evaluate(spec: Path = SPEC, output: Path = OUTPUT) -> dict:
    if not check_worktree():
        raise ValueError("Warden protected worktree integrity failed")
    cfg = yaml.safe_load(spec.read_text())
    registry = ResearchRegistry(REGISTRY)
    runner = ExperimentRunner(registry=registry, repo_root=Path("."))
    with registry._connect() as conn:
        row = conn.execute(
            "SELECT attempt_id,outcome FROM selection_attempts WHERE hypothesis_id=? "
            "AND kind='standalone_screen' ORDER BY created_at DESC LIMIT 1",
            (cfg["hypothesis_id"],),
        ).fetchone()
    if row is None or row[1] != "PASS":
        raise ValueError("No recorded original Warden Stage 1 PASS")
    contract = runner.resolve_contract(cfg["contract_id"])
    dataset = resolve_dataset(contract, Path("."))
    panel = pl.read_parquet(dataset.path).sort("timestamp", "asset")
    ts = sorted(panel["timestamp"].unique().to_list())
    assets = sorted(panel["asset"].unique().to_list())
    val_slice = slice(bisect.bisect_left(ts, contract.val_start), bisect.bisect_left(ts, contract.val_end))
    raw = _run_strategy_isolated(Path(cfg["strategy_module"]), panel, contract, cfg["parameters"])
    raw_positions = np.asarray(raw["positions"])
    returns = execution_returns(panel, contract, assets)
    funding = _aligned_matrix(panel, "funding_rate", assets)
    settlement = _funding_settlement_mask(ts, contract)
    modes = {}
    for name, multiplier, offset in (
        ("base", 1.0, 0),
        ("double_cost", 2.0, 0),
        ("triple_cost", 3.0, 0),
        ("delay_plus_one", 1.0, 1),
    ):
        sim = simulate_positions(
            raw_positions, returns["long"], funding, contract, val_slice,
            cost_multiplier=multiplier, delay_offset=offset,
            funding_mask=settlement, quote_returns=returns,
        )
        metrics = calculate_metrics_from_returns(
            sim["gross_returns"], sim["net_returns"], sim["turnover"],
            contract.bars_per_year,
        )
        modes[name] = {
            **metrics,
            "mean_net_return_bps_per_hour": float(np.mean(sim["net_returns"]) * 1e4),
            "total_turnover_units": float(np.sum(sim["turnover"])),
            "positions_active_pct": float(np.mean(np.any(abs(sim["positions"]) > 1e-6, axis=1)) * 100),
            "net_total_simple_return_pct": float((np.prod(1 + sim["net_returns"]) - 1) * 100),
        }

    shifted_positions = np.zeros_like(raw_positions)
    delay = contract.execution_delay_bars
    shifted_positions[delay:] = raw_positions[:-delay]
    open_arr = _aligned_matrix(panel, "open", assets)
    observed = np.isfinite(open_arr) & (open_arr > 0)
    tradable_next = np.zeros_like(observed)
    tradable_next[:-1] = observed[:-1] & observed[1:]
    invalid = (abs(shifted_positions[val_slice]) > 1e-12) & ~tradable_next[val_slice]
    invalid_events = int(invalid.sum())
    invalid_gross = np.where(invalid, shifted_positions[val_slice] * returns["long"][val_slice], 0.0)
    invalid_gross_total = float(np.nansum(invalid_gross))
    invalid_gross_abs_total = float(np.nansum(np.abs(invalid_gross)))
    valid_bars = int(np.count_nonzero(np.any(abs(shifted_positions[val_slice]) > 1e-6, axis=1)))

    # Exact independent market-neutral split on the same Warden price/funding basis.
    leg_stats = {}
    for name, leg in (
        ("long_leg", np.maximum(raw_positions, 0)),
        ("short_leg", np.minimum(raw_positions, 0)),
    ):
        s = simulate_positions(
            leg, returns["long"], funding, contract, val_slice,
            funding_mask=settlement, quote_returns=returns,
        )
        leg_stats[name] = calculate_metrics_from_returns(
            s["gross_returns"], s["net_returns"], s["turnover"], contract.bars_per_year,
        )

    result = {
        "hypothesis": cfg["hypothesis_id"],
        "stage1_attempt": row[0],
        "data_sha256": dataset.sha256,
        "basis": "Warden NEXT_BAR open-to-open, signed funding, costs on turnover",
        "mode_economics": modes,
        "legs": leg_stats,
        "untradable_active_asset_hours": invalid_events,
        "gapped_asset_gross_pnl_sum_pct_of_initial_equity": invalid_gross_total * 100.0,
        "gapped_asset_gross_abs_pnl_sum_pct_of_initial_equity": invalid_gross_abs_total * 100.0,
        "active_validation_hours": valid_bars,
        "no_stage3_trial_or_protected_holdout": True,
        "limitations": [
            "Frozen survivor-only universe, not independently reconstructed PIT listings",
            "Source funding observations may contain explicitly zero-filled gaps",
            "Open-to-open simulation is an exploratory proxy, not observed exchange fills",
            "This diagnostic does not justify qualification or orders",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError("Write-once economic audit already exists")
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=SPEC)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = evaluate(spec=args.experiment, output=args.output)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
