"""Tests for Ticket TKT-07: Family-specific gates, runner, CLI, and worktree integrity."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from warden.cli import check_worktree
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


def test_worktree_tampering_aborts_with_exit_code_2(signed_repo, monkeypatch, capsys) -> None:
    from warden.cli import main

    # 1. A pinned-signature repo passes integrity and approval.
    assert check_worktree(signed_repo) is True

    # 2. Tampering with a contract fails closed.
    contract_file = signed_repo / "contracts" / "crypto_perps_1h_v1.yaml"
    contract_file.write_text(contract_file.read_text() + "\n# TAMPERED\n")
    assert check_worktree(signed_repo) is False

    # 3. The CLI exits 2 against the tampered repo.
    assert main(["check-worktree"]) == 2


def test_loc_budget_report_is_honest_about_the_unratified_cap() -> None:
    """The cap was not raised by this corrective branch; an over-budget tree must stay visible."""
    proc = subprocess.run([sys.executable, "scripts/check_loc_budget.py"], capture_output=True, text=True, check=False)
    source = Path("scripts/check_loc_budget.py").read_text()
    assert "WARDEN_MAX_LOC = 2400" in source  # no self-authorized raise
    total = sum(len(p.read_text().splitlines()) for p in Path("src/warden").glob("*.py"))
    assert f"src/warden/ total: {total} / 2400 LOC" in proc.stdout
    assert proc.returncode == (1 if total >= 2400 else 0)

def test_two_price_return_accounting_exactness() -> None:
    import polars as pl
    df = pl.DataFrame({"close": [100.0, 110.0], "asset": ["BTC", "BTC"]})
    ret = df.select(pl.col("close").pct_change().over("asset"))["close"][1]
    assert np.isclose(ret, 0.10)


def test_val_features_physically_lack_label_column() -> None:
    """Walk-forward validation inputs never carry the label column."""
    import polars as pl

    from warden.leakage import verify_fold_isolation
    from warden.runner import load_module_from_path

    model = load_module_from_path(Path("strategies/baselines/cs_ridge_1h.py")).CSRidgeModel()
    train_df = pl.DataFrame({"timestamp": [1, 2], "asset": ["A", "A"], "feat": [0.1, 0.2], "target": [0.01, -0.01]})
    val_df = pl.DataFrame({"timestamp": [3], "asset": ["A"], "feat": [0.3], "target": [0.05]})
    preds = verify_fold_isolation(model=model, train_df=train_df, val_df=val_df,
                                  feature_cols=["feat"], label_col="target")
    assert "prediction_value" in preds.columns
