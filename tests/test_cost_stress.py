"""1.5x cost stress: numerical behaviour, schema nullability, constructor compatibility.

These are inert until the `stress_1p5_cost_sharpe` field exists (the patch in
`.workspace/plans/step3_1p5_cost_stress/cost_stress.patch`); they skip rather than fail so
the suite stays green on a tree without it, and they exercise the change the moment it lands.
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields
from pathlib import Path

import numpy as np
import pytest

from warden.contracts import ExperimentResult, load_contract
from warden.registry import _ensure_columns
from warden.runner import simulate_positions

HAS_FIELD = "stress_1p5_cost_sharpe" in {f.name for f in fields(ExperimentResult)}
pytestmark = pytest.mark.skipif(not HAS_FIELD, reason="cost-stress patch not applied")


def test_mean_net_return_falls_as_the_cost_multiplier_rises():
    """Higher cost reduces mean return. Assert on means, not Sharpe: Sharpe is not monotone."""
    contract = load_contract(Path("contracts/indices_1d_v1.yaml"))
    rng = np.random.default_rng(0)
    n_bars = 60
    returns = rng.normal(0.0005, 0.01, size=(n_bars, 1))
    positions = np.zeros((n_bars, 1))
    positions[::5] = 0.5  # deliberate churn so turnover is non-zero
    span = slice(0, n_bars)

    means = [simulate_positions(positions, returns, None, contract, span,
                                cost_multiplier=m)["net_returns"].mean()
             for m in (1.0, 1.5, 2.0)]
    assert means[0] > means[1] > means[2], means


def test_new_column_is_nullable_so_unmeasured_is_not_zero():
    """A REAL column added to an existing table backfills NULL, never 0.0."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE trials (run_id TEXT)")
    con.execute("INSERT INTO trials VALUES ('OLD')")
    _ensure_columns(con, "trials", ExperimentResult)
    con.commit()

    types = {row[1]: row[2] for row in con.execute("PRAGMA table_info(trials)")}
    assert types["stress_1p5_cost_sharpe"] == "REAL"
    value = con.execute("SELECT stress_1p5_cost_sharpe FROM trials WHERE run_id='OLD'").fetchone()[0]
    assert value is None, "an unmeasured row must read NULL, not 0.0"


def test_experiment_result_keeps_the_old_positional_signature():
    result = ExperimentResult("R", "H", "C", "S", "rules_ts", "cfg", "STAGE_3_STRESS", "REVISE",
                              1.0, 1.0, 0.5, 0.1, 1, 1.0, None, None, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0,
                              1.0, "notes", 1.0, 0.0, 0, "", "", "", "")
    assert result.stress_1p5_cost_sharpe is None
