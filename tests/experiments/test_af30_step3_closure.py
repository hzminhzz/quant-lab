"""Tests for the Step-3 ledger generator.

Hermetic: the generator is exercised against a temporary SQLite file built here, so a
clean checkout with no `registry/registry.db` still runs them. The drift check against
the committed ledger needs the live registry and the ledger file, so it is opt-in and
skips when either is absent.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path("scripts/build_af30_step3_closure.py")
LIVE_DB = Path("registry/registry.db")
LEDGER = Path(".workspace/af30_campaign/step3_closure.md")

SCHEMA = """
CREATE TABLE hypotheses (hypothesis_id TEXT, strategy_module TEXT);
CREATE TABLE trials (hypothesis_id TEXT, run_id TEXT, net_sharpe REAL,
    stress_2x_cost_sharpe REAL, delay_plus1_sharpe REAL, annualized_turnover REAL,
    breakeven_cost_bps REAL, max_drawdown_pct REAL, param_neighbor_stability REAL,
    deflated_sharpe_prob REAL, verdict TEXT, created_at REAL, stress_1p5_cost_sharpe REAL);
CREATE TABLE selection_attempts (hypothesis_id TEXT, kind TEXT, outcome TEXT,
    diagnostics_json TEXT);
CREATE TABLE audit_evidence (run_id TEXT, verdict TEXT);
"""


def _load():
    spec = importlib.util.spec_from_file_location("build_af30_step3_closure", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture(tmp_path: Path) -> Path:
    db = tmp_path / "registry.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    for i in range(94, 112):
        con.execute("INSERT INTO hypotheses VALUES (?,?)",
                    (f"HYP-2026-{i:03d}", f"strategies/candidates/s{i:02d}.py"))
    con.execute("INSERT INTO hypotheses VALUES ('HYP-2026-112','strategies/candidates/s02.py')")
    # Stage-3 rows: one REVISE of its own, three CANDIDATE reached via the indices coverage.
    con.execute("INSERT INTO trials VALUES ('HYP-2026-095','RUN-X-1',1.3,1.3,1.3,0.4,100,8.0,1.0,0.4,'REVISE',1,NULL)")
    for hid in (112, 113, 114):
        con.execute("INSERT INTO trials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"HYP-2026-{hid}", f"RUN-Y-{hid}", 1.2, 1.2, 1.2, 1.0, 200, 10.0, 0.95, 0.97,
                     "CANDIDATE_FOR_QUALIFICATION", 1, 1.19))
    for hid in (112, 113, 114):
        con.execute("INSERT INTO audit_evidence VALUES (?, 'APPROVED')", (f"RUN-Y-{hid}",))
    con.execute("INSERT INTO selection_attempts VALUES ('HYP-2026-100','standalone_screen','REJECT',?)",
                (json.dumps({"profit_factor": 1.03, "bootstrap_p_value": 0.31}),))
    con.execute("INSERT INTO selection_attempts VALUES ('HYP-2026-101','standalone_screen','PASS',?)",
                (json.dumps({"profit_factor": 1.20, "bootstrap_p_value": 0.01}),))
    con.commit()
    con.close()
    return db


def test_block_covers_every_strategy_and_counts_the_dispositions(tmp_path):
    mod = _load()
    block = mod.build(_fixture(tmp_path))
    for hyp in mod.STRATEGIES:
        assert hyp in block, f"{hyp} missing"
    assert ("3 CANDIDATE, 1 REVISE with a Stage-3 row, 1 falsified at Stage 1, "
            "1 Stage-1 PASS without a Stage-3 row, 12 unscreened") in block
    # the 1.5x column exists now; unmeasured rows must render NULL, never a number
    assert "| NULL |" in block and "1.5x" in block


def test_absent_and_passing_screens_are_not_called_falsified(tmp_path):
    """Only a recorded REJECT counts as a Stage-1 falsification."""
    block = _load().build(_fixture(tmp_path))
    falsified = [ln for ln in block.splitlines() if "Falsified at Stage 1" in ln]
    assert len(falsified) == 1 and "`HYP-2026-100`" in falsified[0]
    assert "Stage-1 PASS — no Stage-3 row" in block
    assert "UNSCREENED" in block


def test_rehomed_rows_are_attributed_to_their_indices_run(tmp_path):
    mod = _load()
    block = mod.build(_fixture(tmp_path))
    for src, dst in mod.INDICES_COVERAGE.items():
        assert f"`{src}`" in block and f"(via `{dst}`)" in block


LIVE = os.environ.get("AF30_LIVE_CHECKS") == "1"


@pytest.mark.skipif(not LIVE, reason="set AF30_LIVE_CHECKS=1 to run live-registry checks")
def test_committed_ledger_matches_the_live_registry():
    """Opt-in: the registry is append-only, so any legitimate new trial changes this ledger."""
    assert _load().main(["--check"]) == 0
