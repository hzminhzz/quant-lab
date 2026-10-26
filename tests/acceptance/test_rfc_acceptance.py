"""Section 9 Objective Acceptance Criteria & Verification Suite (RFC-2026-Q4-001)."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.cli import check_worktree
from warden.contracts import EvaluationContract, HypothesisSpec, load_contract
from warden.gates import evaluate_stage0_feasibility
from warden.leakage import (
    LookaheadLeakageError,
    compute_and_cache_features,
    purged_walk_forward_splits,
    verify_prefix_invariance,
)
from warden.qualify import HoldoutAlreadyUnsealedError, qualify_candidate
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


def test_4_holdout_and_governance_isolation(signed_repo, qualify_repo) -> None:
    """Contract tampering, trial deletion, and lineage re-qualification all fail closed."""
    # A. A signed worktree detects contract tampering.
    assert check_worktree(signed_repo) is True
    cfile = signed_repo / "contracts" / "crypto_perps_1h_v1.yaml"
    cfile.write_text(cfile.read_text() + "\n# TAMPER\n")
    assert check_worktree(signed_repo) is False

    # B. Trial rows are append-only.
    fixture = qualify_repo()
    with (pytest.raises(sqlite3.IntegrityError, match="Append-only violation"),
          fixture.registry._connect() as conn):
        conn.execute("DELETE FROM trials")

    # C. One-shot holdout qualification per lineage.
    q1 = qualify_candidate("RUN-Q1", "YES_UNSEAL_HOLDOUT", fixture.runner, reviewer="human-operator")
    assert "verdict" in q1
    with pytest.raises(HoldoutAlreadyUnsealedError, match="already unsealed"):
        qualify_candidate("RUN-Q1", "YES_UNSEAL_HOLDOUT", fixture.runner, reviewer="human-operator")


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


def test_6_research_ergonomics_and_throughput_funnel_timing(tiny_repo, tmp_path) -> None:
    """Stage 0 < 100ms, cached feature screen < 2.0s, full vertical slice < 60s (isolated fixtures)."""
    fixture = tiny_repo()
    contract = fixture.contract
    hyp = HypothesisSpec("HYP-TIME-TEST", contract.contract_id, "Timing", "rules_ts",
                         "Mechanism explaining 20 chars", "Friction explaining 20 chars", 8, "Crit")

    t0 = time.perf_counter()
    evaluate_stage0_feasibility(contract, hyp, holding_volatility=0.024, trials_used=1)
    assert (time.perf_counter() - t0) * 1000.0 < 100.0

    panel = fixture.panel
    cache_dir = tmp_path / "cache"
    from warden.runner import load_module_from_path

    vol_mod = load_module_from_path(Path("strategies/candidates/vol_breakout_1h.py"))
    pipe = vol_mod.VolBreakoutPipeline()
    compute_and_cache_features(pipe, panel, contract, cache_dir=cache_dir)
    t1 = time.perf_counter()
    feat = compute_and_cache_features(pipe, panel, contract, cache_dir=cache_dir)
    assert len(feat) == len(panel)
    assert time.perf_counter() - t1 < 2.0

    t2 = time.perf_counter()
    res = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo).run_spec(
        fixture.spec, override_run_id="RUN-TIME-TEST")
    assert res.run_id == "RUN-TIME-TEST"
    assert time.perf_counter() - t2 < 60.0
