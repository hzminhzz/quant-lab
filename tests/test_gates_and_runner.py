"""Tests for Ticket TKT-07: Family-specific gates, runner, CLI, and worktree integrity."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from warden.cli import check_worktree, generate_manifest
from warden.contracts import load_contract
from warden.gates import (
    compute_hac_tstat,
    evaluate_stage1_cross_sectional_ml,
    evaluate_stage1_rules_ts,
)
from warden.registry import ResearchRegistry
from warden.runner import (
    ExperimentRunner,
    LinearBaselineRequiredError,
    simulate_positions,
)


def test_hac_tstat_and_family_gates() -> None:
    # 1. HAC t-stat calculation
    rng = np.random.default_rng(42)
    sig = np.full(50, 0.05) + rng.normal(0, 0.01, 50)
    t_val = compute_hac_tstat(sig)
    assert t_val > 5.0

    # 2. cross_sectional_ml gate
    passed_ml = evaluate_stage1_cross_sectional_ml(sig, min_mean_ic=0.015, min_hac_tstat=2.0)
    assert passed_ml.passed is True
    assert passed_ml.strategy_family == "cross_sectional_ml"

    failed_ml = evaluate_stage1_cross_sectional_ml(rng.normal(0, 0.01, 50), min_mean_ic=0.02)
    assert failed_ml.passed is False

    # 3. rules_ts gate: uses conditional edge and profit factor, NOT cross-sectional IC
    net_ret = np.array([0.01, -0.005, 0.015, 0.02, -0.004] * 20)
    gross_ret = net_ret + 0.0006
    passed_rules = evaluate_stage1_rules_ts(
        net_returns=net_ret, gross_returns=gross_ret, one_way_cost=0.0006, min_profit_factor=1.15
    )
    assert passed_rules.passed is True
    assert passed_rules.strategy_family == "rules_ts"
    assert "profit_factor" in passed_rules.metrics


def test_simulate_positions_delay_and_signed_funding() -> None:
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    n_bars, n_assets = 10, 2
    positions = np.ones((n_bars, n_assets))
    returns = np.full((n_bars, n_assets), 0.01)
    funding = np.full((n_bars, n_assets), 0.001)  # 10 bps positive funding

    # Baseline simulation: position must be shifted by contract.execution_delay_bars (1)
    sim = simulate_positions(positions, returns, funding, contract, slice(0, n_bars))
    # Bar 0 should have position 0.0 due to delay
    assert np.allclose(sim["positions"][0], 0.0)
    # Long positions (+pos) pay positive funding -> net < gross
    assert sim["net_returns"][5] < sim["gross_returns"][5]

    # Short positions (-pos) receive positive funding -> funding cashflow is positive
    sim_short = simulate_positions(-positions, returns, funding, contract, slice(0, n_bars))
    # Gross is negative because returns are positive and pos is short
    # Net return should receive funding cashflow (+pos * fund subtracted means -(-pos)*fund = +gain)
    assert sim_short["net_returns"][5] > sim_short["gross_returns"][5]


def test_linear_baseline_required_gate(tmp_path: Path) -> None:
    reg = ResearchRegistry(tmp_path / "reg.db")
    runner = ExperimentRunner(registry=reg, repo_root=Path("."))

    # Contract & Hypothesis registration
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    reg.register_contract(contract)

    from warden.contracts import HypothesisSpec
    hyp = HypothesisSpec(
        hypothesis_id="HYP-ML-TEST",
        contract_id=contract.contract_id,
        title="Cross-sectional funding reversal test",
        strategy_family="cross_sectional_ml",
        economic_mechanism="Testing linear-first gate before tree models",
        counterparty_or_friction="Testing friction requiring linear baseline first",
        expected_holding_bars=4,
        falsification_criteria="Reject if no linear baseline",
    )
    reg.register_hypothesis(hyp)

    # Attempting to run a LightGBM spec before any Ridge baseline
    spec_yaml = tmp_path / "lgbm_spec.yaml"
    spec_yaml.write_text(f"""
contract_id: {contract.contract_id}
hypothesis_id: {hyp.hypothesis_id}
strategy_name: CS_LGBM_Model
strategy_family: cross_sectional_ml
model_type: lightgbm
strategy_module: strategies/candidates/cs_lgbm_1h.py
parameters:
  num_leaves: 15
""")

    with pytest.raises(LinearBaselineRequiredError, match="Linear baseline required"):
        runner.run_spec(spec_yaml)


def test_worktree_tampering_aborts_with_exit_code_2(tmp_path: Path) -> None:
    # 1. Clean worktree check passes
    assert check_worktree(Path(".")) is True

    # 2. Tampering test in isolated copy
    import shutil
    copy_dir = tmp_path / "tampered_repo"
    shutil.copytree(Path("."), copy_dir, ignore=shutil.ignore_patterns(".venv", ".git", "data", "cache"))
    generate_manifest(copy_dir)

    # Initial check on fresh copy passes
    assert check_worktree(copy_dir) is True

    # Tamper with a contract file
    contract_file = copy_dir / "contracts" / "crypto_perps_1h_v1.yaml"
    contract_file.write_text(contract_file.read_text() + "\n# TAMPERED\n")

    # check_worktree must fail closed
    assert check_worktree(copy_dir) is False

    # CLI check-worktree should exit with code 2
    cmd = [sys.executable, "-m", "warden.cli", "check-worktree"]
    proc = subprocess.run(cmd, cwd=copy_dir, capture_output=True, text=True, check=False)
    assert proc.returncode == 2


def test_loc_budget_script_passes() -> None:
    proc = subprocess.run([sys.executable, "scripts/check_loc_budget.py"], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"LOC budget script failed:\n{proc.stdout}\n{proc.stderr}"
    assert "PASS: All LOC budgets satisfied." in proc.stdout
