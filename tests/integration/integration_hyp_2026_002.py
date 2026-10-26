"""Tests for Ticket TKT-09: Vertical Slice 2 (HYP-2026-002 Cross-Sectional ML)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from warden.runner import load_module_from_path

ridge_mod = load_module_from_path(Path("strategies/baselines/cs_ridge_1h.py"))
CSRidgeModel = ridge_mod.CSRidgeModel


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


def test_early_rejection_does_not_contaminate_return_matrix() -> None:
    from warden.registry import ResearchRegistry
    reg = ResearchRegistry(Path("registry/registry.db"))
    # load_prior_trial_returns should return exactly 4 valid non-zero streams, skipping RUN-ML-002 Stage 1 early reject
    streams = reg.load_prior_trial_returns("CRYPTO_PERPS_1H_V1", 1400)
    assert len(streams) == 4, f"Expected 4 valid simulated streams, got {len(streams)}"
    for s in streams:
        assert len(s) == 1400
        assert np.std(s) > 1e-6
