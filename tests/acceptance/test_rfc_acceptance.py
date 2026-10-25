"""Section 9 Objective Acceptance Criteria & Verification Suite (RFC-2026-Q4-001)."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.cli import check_worktree, generate_manifest
from warden.contracts import EvaluationContract, HypothesisSpec, load_contract
from warden.gates import evaluate_stage0_feasibility
from warden.leakage import (
    LookaheadLeakageError,
    compute_and_cache_features,
    purged_walk_forward_splits,
    verify_prefix_invariance,
)
from warden.qualify import HoldoutAlreadyUnsealedError, qualify_candidate
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner, simulate_positions
from warden.synthetic import generate_canonical_market_panel

# ============================================================================
# Test 1: Bit-for-Bit Reproducibility
# ============================================================================


def test_1_bit_for_bit_reproducibility(tmp_path: Path) -> None:
    """Two isolated subprocess runs on the same contract produce bit-for-bit identical returns."""
    script = """
import numpy as np
import polars as pl
from pathlib import Path
from warden.contracts import load_contract
from warden.synthetic import generate_canonical_market_panel
from strategies.baselines.cs_ridge_1h import generate_positions

contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
panel = generate_canonical_market_panel()
out = generate_positions(panel, contract, ridge_alpha=10.0)
np.save(Path("pos.npy"), out["positions"])
"""
    run_dir1 = tmp_path / "run1"
    run_dir2 = tmp_path / "run2"
    run_dir1.mkdir()
    run_dir2.mkdir()

    p1 = subprocess.run([sys.executable, "-c", script], cwd=Path("."), capture_output=True, text=True, check=False)
    assert p1.returncode == 0, p1.stderr
    pos1 = np.load("pos.npy")
    Path("pos.npy").unlink()

    p2 = subprocess.run([sys.executable, "-c", script], cwd=Path("."), capture_output=True, text=True, check=False)
    assert p2.returncode == 0, p2.stderr
    pos2 = np.load("pos.npy")
    Path("pos.npy").unlink()

    # Assert exact bitwise identity (max |diff| == 0.0)
    assert np.array_equal(pos1, pos2)
    assert np.max(np.abs(pos1 - pos2)) == 0.0


# ============================================================================
# Test 2: Causal Prefix-Invariance
# ============================================================================


def test_2_causal_prefix_invariance() -> None:
    """Prefix-invariance verifier achieves 100% detection of planted lookahead bugs."""
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    panel = generate_canonical_market_panel().filter(pl.col("timestamp") < contract.val_end)

    class PlantedLookaheadShift:
        def compute(self, bars_df: pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
            return bars_df.with_columns(leak=pl.col("close").shift(-1).over("asset"))

    class PlantedGlobalZscore:
        def compute(self, bars_df: pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
            return bars_df.with_columns(leak=(pl.col("close") - bars_df["close"].mean()) / bars_df["close"].std())

    with pytest.raises(LookaheadLeakageError, match="leak"):
        verify_prefix_invariance(PlantedLookaheadShift(), panel, contract)

    with pytest.raises(LookaheadLeakageError, match="leak"):
        verify_prefix_invariance(PlantedGlobalZscore(), panel, contract)


# ============================================================================
# Test 3: Purged & Embargoed Fold Disjointness
# ============================================================================


def test_3_purged_and_embargoed_fold_disjointness() -> None:
    """For every fold, max(train_idx) + label_horizon + embargo <= min(test_idx)."""
    h = 4
    e = 2
    splits = purged_walk_forward_splits(
        n_timestamps=1200,
        n_folds=4,
        min_train_timestamps=300,
        label_horizon=h,
        embargo=e,
    )
    assert len(splits) == 4
    for train_idx, test_idx in splits:
        assert len(train_idx) > 0 and len(test_idx) > 0
        assert train_idx[-1] + h + e <= test_idx[0], (
            f"Purge boundary violated: train_max={train_idx[-1]}, test_min={test_idx[0]}, h={h}, e={e}"
        )


# ============================================================================
# Test 4: Holdout & Governance Isolation
# ============================================================================


def test_4_holdout_and_governance_isolation(tmp_path: Path) -> None:
    """Contract tampering, trial deletion, and lineage re-qualification all fail closed."""
    # A. Contract tampering check
    tamper_dir = tmp_path / "tamper_repo"
    shutil.copytree(Path("."), tamper_dir, ignore=shutil.ignore_patterns(".venv", ".git", "data", "cache"))
    generate_manifest(tamper_dir)
    assert check_worktree(tamper_dir) is True

    # Tamper with contract
    cfile = tamper_dir / "contracts" / "crypto_perps_1h_v1.yaml"
    cfile.write_text(cfile.read_text() + "\n# TAMPER\n")
    assert check_worktree(tamper_dir) is False

    # B. One-shot holdout qualification gate
    db_file = tmp_path / "isolation_reg.db"
    reg = ResearchRegistry(db_file)
    # C. One-shot holdout qualification gate
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    reg.register_contract(contract)
    hyp = HypothesisSpec("HYP-QUAL-TEST", contract.contract_id, "T", "rules_ts", "Mechanism at least 20 chars", "Friction at least 20 chars", 8, "Crit")
    reg.register_hypothesis(hyp)

    from warden.contracts import ExperimentResult
    res = ExperimentResult("RUN-QUAL-001", hyp.hypothesis_id, contract.contract_id, "Strat", "rules_ts", "cfg", "STAGE_3_STRESS", "CANDIDATE_FOR_QUALIFICATION", 2.0, 1.5, 0.95, 0.5, 1, 1.0, None, None, 0.5, 1.3, 3.0, 50.0, 15.0, 1.0, 1.0, "notes")
    reg.record_trial(res, {}, np.linspace(0.01, -0.005, 100))

    with reg._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="Append-only violation"):
        conn.execute("DELETE FROM trials")
    runner = ExperimentRunner(registry=reg, repo_root=Path("."))
    holdout_fixture = tmp_path / "holdout.parquet"
    panel = generate_canonical_market_panel()
    panel.write_parquet(holdout_fixture)

    # First qualification passes
    q1 = qualify_candidate("RUN-QUAL-001", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout_fixture)
    assert "verdict" in q1

    # Second qualification on the same lineage must raise HoldoutAlreadyUnsealedError
    with pytest.raises(HoldoutAlreadyUnsealedError, match="already unsealed"):
        qualify_candidate("RUN-QUAL-001", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout_fixture)


# ============================================================================
# Test 5: Backtest Accounting & Cost Floor Fidelity
# ============================================================================


def test_5_backtest_accounting_and_cost_floor_fidelity() -> None:
    """Analytical vs simulated fill price and fees match to 1e-8 precision."""
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    n_bars = 5
    # Buy 1 unit at bar 1 (delayed to bar 2), price moves 100 -> 110 (+10%), sell at bar 3
    positions = np.array([[0.0], [1.0], [1.0], [0.0], [0.0]])
    returns = np.array([[0.0], [0.0], [0.10], [0.0], [0.0]])  # Bar 2 price return is +10%

    sim = simulate_positions(positions, returns, funding_panel=None, contract=contract, val_slice=slice(0, n_bars))
    gross_returns = sim["gross_returns"]
    net_returns = sim["net_returns"]
    turnover = sim["turnover"]

    # Bar 2 (entry established due to 1-bar execution delay):
    # gross return = 1.0 * 0.10 = 0.10
    # turnover = |1 - 0| = 1.0; cost = 1.0 * 0.0006 = 0.0006
    # expected net = 0.10 - 0.0006 = 0.0994
    assert np.isclose(gross_returns[2], 0.10, atol=1e-8)
    assert np.isclose(net_returns[2], 0.10 - 0.0006, atol=1e-8)

    # Bar 4 (exit to 0.0 established at bar 4 due to 1-bar execution delay):
    # turnover = |0 - 1| = 1.0; cost = 0.0006; gross = 0.0
    # expected net = -0.0006
    assert np.isclose(turnover[4], 1.0, atol=1e-8)
    assert np.isclose(net_returns[4], -0.0006, atol=1e-8)

# ============================================================================
# Test 6: Research Ergonomics & Throughput Funnel Timing
# ============================================================================


def test_6_research_ergonomics_and_throughput_funnel_timing(tmp_path: Path) -> None:
    """Stage 0 < 100ms, Stage 1 cached screen < 2.0s, full vertical slice < 60s."""
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    hyp = HypothesisSpec("HYP-TIME-TEST", contract.contract_id, "Timing", "rules_ts", "Mechanism explaining 20 chars", "Friction explaining 20 chars", 8, "Crit")

    # 1. Stage 0 Feasibility Timing (< 100 ms)
    t0 = time.perf_counter()
    evaluate_stage0_feasibility(contract, hyp, holding_volatility=0.024, trials_used=1)
    stage0_time_ms = (time.perf_counter() - t0) * 1000.0
    assert stage0_time_ms < 100.0, f"Stage 0 took {stage0_time_ms:.1f}ms (must be < 100ms)"

    # 2. Stage 1 Cached Feature Screen (< 2.0 s)
    panel = generate_canonical_market_panel().filter(pl.col("timestamp") < contract.val_end)
    cache_dir = tmp_path / "cache"

    from warden.runner import load_module_from_path
    vol_mod = load_module_from_path(Path("strategies/candidates/vol_breakout_1h.py"))
    pipe = vol_mod.VolBreakoutPipeline()
    # Populate cache
    compute_and_cache_features(pipe, panel, contract, cache_dir=cache_dir)

    # Measure cached retrieval
    t1 = time.perf_counter()
    feat = compute_and_cache_features(pipe, panel, contract, cache_dir=cache_dir)
    stage1_cached_time = time.perf_counter() - t1
    assert len(feat) == len(panel)
    assert stage1_cached_time < 2.0, f"Stage 1 cached screen took {stage1_cached_time:.3f}s (must be < 2.0s)"

    # 3. Full Vertical Slice Simulation (< 60 s)
    t2 = time.perf_counter()
    timing_reg = ResearchRegistry(tmp_path / "timing_reg.db")
    timing_reg.register_contract(contract)
    timing_reg.register_hypothesis(hyp)
    timing_spec = tmp_path / "hyp_time.yaml"
    timing_spec.write_text(f"""
contract_id: {contract.contract_id}
hypothesis_id: {hyp.hypothesis_id}
strategy_name: VolBreakoutTiming
strategy_family: rules_ts
model_type: rules
strategy_module: strategies/candidates/vol_breakout_1h.py
parameters:
  lookback_bars: 12
  vol_window: 24
  z_threshold: 2.0
  rebalance_every: 4
""")
    runner = ExperimentRunner(registry=timing_reg, repo_root=Path("."))
    res = runner.run_spec(timing_spec, override_run_id="RUN-TIME-TEST")
    total_time = time.perf_counter() - t2
    assert res.run_id == "RUN-TIME-TEST"
    assert total_time < 60.0, f"Full vertical slice took {total_time:.1f}s (must be < 60s)"
