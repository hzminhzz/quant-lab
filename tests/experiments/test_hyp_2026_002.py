"""Tests for Ticket TKT-09: Vertical Slice 2 (HYP-2026-002 Cross-Sectional ML)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.runner import ExperimentRunner, LinearBaselineRequiredError, load_module_from_path

ridge_mod = load_module_from_path(Path("strategies/baselines/cs_ridge_1h.py"))
CSRidgeModel = ridge_mod.CSRidgeModel


def test_hyp_2026_002_linear_baseline_required_gate(tmp_path: Path) -> None:
    # In an empty temporary registry with no prior Ridge baseline, running LightGBM must raise
    from warden.contracts import HypothesisSpec, load_contract
    from warden.registry import ResearchRegistry

    reg = ResearchRegistry(tmp_path / "reg.db")
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    reg.register_contract(contract)

    hyp = HypothesisSpec(
        hypothesis_id="HYP-ML-TEMP",
        contract_id=contract.contract_id,
        title="Testing Linear-First Gate",
        strategy_family="cross_sectional_ml",
        economic_mechanism="Testing linear-first gate before tree models",
        counterparty_or_friction="Testing friction requiring linear baseline first",
        expected_holding_bars=8,
        falsification_criteria="Reject if no linear baseline",
    )
    reg.register_hypothesis(hyp)

    spec_yaml = tmp_path / "hyp_lgbm.yaml"
    spec_yaml.write_text(f"""
contract_id: {contract.contract_id}
hypothesis_id: {hyp.hypothesis_id}
strategy_name: CrossSectionalLGBM_1H
strategy_family: cross_sectional_ml
model_type: lightgbm
strategy_module: strategies/candidates/cs_lgbm_1h.py
run_id: RUN-ML-999
parameters:
  num_leaves: 15
""")

    runner = ExperimentRunner(registry=reg, repo_root=Path("."))
    with pytest.raises(LinearBaselineRequiredError, match="Linear baseline required"):
        runner.run_spec(spec_yaml)


def test_hyp_2026_002_both_trials_recorded_with_ic_metrics() -> None:
    db_path = Path("registry/registry.db")
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT run_id, hypothesis_id, strategy_family, stage_reached,
               mean_ic, ic_hac_tstat, gross_sharpe, net_sharpe,
               deflated_sharpe_prob, effective_trials_keff, total_trials_n, verdict
        FROM trials
        WHERE hypothesis_id = 'HYP-2026-002'
        ORDER BY created_at ASC
        """
    ).fetchall()

    assert len(rows) == 2, f"Expected 2 trials for HYP-2026-002, got {len(rows)}"

    # RUN-ML-001 (Ridge baseline)
    r1 = rows[0]
    assert r1[0] == "RUN-ML-001"
    assert r1[2] == "cross_sectional_ml"
    assert r1[3] == "STAGE_3_STRESS"
    assert r1[4] > 0.02, f"Expected mean_ic >= 0.02, got {r1[4]}"
    assert r1[5] > 2.0, f"Expected ic_hac_tstat >= 2.0, got {r1[5]}"
    assert r1[9] == pytest.approx(2.58, abs=0.10)  # K_eff ~ 2.58
    assert r1[10] == 4  # N = 4 (3 prior rules + 1 ML)
    assert r1[11] == "CANDIDATE_FOR_QUALIFICATION"

    # RUN-ML-002 (LightGBM)
    r2 = rows[1]
    assert r2[0] == "RUN-ML-002"
    assert r2[3] == "STAGE_1_SIGNAL"
    assert r2[4] is not None
    assert r2[5] is not None
    assert r2[9] == pytest.approx(2.58, abs=0.10)  # Preserves contract K_eff penalty
    assert r2[10] == 5  # Contract-wide N = 5
    assert r2[11] == "REJECTED_EARLY"


def test_val_features_physically_lack_label_column() -> None:
    # Prove that WalkForwardModelProtocol receives val_features_df without label_col
    train_df = pl.DataFrame({
        "timestamp": [1, 2], "asset": ["A", "A"], "feat": [0.1, 0.2], "target": [0.01, -0.01]
    })
    val_df_with_label = pl.DataFrame({
        "timestamp": [3], "asset": ["A"], "feat": [0.3], "target": [0.05]
    })

    # When stripped by warden's verify_fold_isolation:
    from warden.leakage import verify_fold_isolation
    model = CSRidgeModel()
    preds = verify_fold_isolation(
        model=model,
        train_df=train_df,
        val_df=val_df_with_label,
        feature_cols=["feat"],
        label_col="target",
    )
    assert "prediction_value" in preds.columns

def test_early_rejection_does_not_contaminate_return_matrix() -> None:
    from warden.registry import ResearchRegistry
    reg = ResearchRegistry(Path("registry/registry.db"))
    # load_prior_trial_returns should return exactly 4 valid non-zero streams, skipping RUN-ML-002 Stage 1 early reject
    streams = reg.load_prior_trial_returns("CRYPTO_PERPS_1H_V1", 1400)
    assert len(streams) == 4, f"Expected 4 valid simulated streams, got {len(streams)}"
    for s in streams:
        assert len(s) == 1400
        assert np.std(s) > 1e-6
