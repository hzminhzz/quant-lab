"""Tests for Ticket TKT-08: Vertical Slice 1 (HYP-2026-001 Vol Breakout)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from warden.registry import HypothesisBudgetExhaustedError
from warden.runner import ExperimentRunner


def test_hyp_2026_001_trials_recorded_in_registry() -> None:
    db_path = Path("registry/registry.db")
    assert db_path.is_file(), "registry.db must exist"

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT run_id, hypothesis_id, strategy_family, mean_ic, profit_factor,
               hit_rate, breakeven_cost_bps, stress_2x_cost_sharpe, effective_trials_keff, total_trials_n
        FROM trials
        WHERE hypothesis_id = 'HYP-2026-001'
        ORDER BY created_at ASC
        """
    ).fetchall()

    assert len(rows) == 3, f"Expected exactly 3 trials for HYP-2026-001, got {len(rows)}"
    run_ids = [r[0] for r in rows]
    assert run_ids == ["RUN-RULES-001", "RUN-RULES-002", "RUN-RULES-003"]

    keff_progression = []
    for r in rows:
        assert r[2] == "rules_ts"
        assert r[3] is None, f"rules_ts should not have mean_ic, got {r[3]}"
        assert r[4] is not None and r[4] > 0.0, "profit_factor must be populated"
        assert r[5] is not None and r[5] > 0.0, "hit_rate must be populated"
        assert r[6] is not None, "breakeven_cost_bps must be populated"
        assert r[7] is not None, "stress_2x_cost_sharpe must be populated"
        keff_progression.append(r[8])

    # K_eff progression: 1.0 -> ~1.39 -> ~1.65..1.68
    assert keff_progression[0] == pytest.approx(1.0, abs=1e-3)
    assert keff_progression[1] > 1.30
    assert keff_progression[2] > keff_progression[1]


def test_hyp_2026_001_fourth_attempt_budget_exhausted() -> None:
    runner = ExperimentRunner(repo_root=Path("."))
    spec_yaml = Path("experiments/active/hyp_2026_001.yaml")

    # Attempting to run a 4th trial on HYP-2026-001 must raise HypothesisBudgetExhaustedError
    with pytest.raises(HypothesisBudgetExhaustedError, match="HYP-2026-001"):
        runner.run_spec(spec_yaml, override_run_id="RUN-RULES-004")


def test_hyp_2026_001_return_parquet_artifacts_exist() -> None:
    returns_dir = Path("registry/returns/CRYPTO_PERPS_1H_V1")
    for idx in range(1, 4):
        p = returns_dir / f"RUN-RULES-{idx:03d}.parquet"
        assert p.is_file(), f"Return artifact {p} must exist"
