"""Tests for the FX Stage-3 ledger generator.

Hermetic: the generator runs against a temporary SQLite file and an injected blocker string,
so a clean checkout with no registry and no FX panels still exercises the disposition logic.
The drift check against the committed ledger needs the live registry, so it is opt-in.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path("scripts/build_fx_stage3_closure.py")
LEDGER = Path(".workspace/fx_stage3/stage3_closure.md")

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
    spec = importlib.util.spec_from_file_location("build_fx_stage3_closure", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture(tmp_path: Path, *, trial_for: str | None = None, reject_for: str | None = None,
             af30_rejects: bool = False, fx_trials: bool = False) -> Path:
    mod = _load()
    db = tmp_path / "registry.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    for i, hyp in enumerate(mod.STRATEGIES, start=1):
        con.execute("INSERT INTO hypotheses VALUES (?,?)",
                    (hyp, f"strategies/candidates/carver_daily/s{i:02d}_x.py"))
    for src, pf, p in ((95, 1.258, 0.0), (96, 1.252, 0.0), (97, 1.213, 0.01),
                       (98, 1.213, 0.01), (99, 1.154, 0.045)):
        if f"HYP-2026-{src:03d}" not in set(mod.STAGE1_SOURCE.values()):
            continue
        con.execute("INSERT INTO selection_attempts VALUES (?, 'standalone_screen', 'PASS', ?)",
                    (f"HYP-2026-{src:03d}", json.dumps({"profit_factor": pf, "bootstrap_p_value": p})))
    for hyp in mod.AF30_SET:
        con.execute("INSERT OR IGNORE INTO hypotheses VALUES (?,?)",
                    (hyp, f"strategies/candidates/carver_daily/af30_{hyp[-3:]}.py"))
    if af30_rejects:
        for hyp in mod.AF30_SET:
            con.execute("INSERT INTO selection_attempts VALUES (?, 'standalone_screen', 'REJECT', ?)",
                        (hyp, json.dumps({"profit_factor": 0.93, "bootstrap_p_value": 0.76})))
    if fx_trials:
        for hyp in mod.STRATEGIES:
            con.execute("INSERT INTO trials VALUES (?,?,1.2,1.1,1.15,4.0,500,15.0,0.95,0.97,"
                        "'REVISE',1,1.18)", (hyp, f"RUN-{hyp}-001"))
    if trial_for:
        con.execute("INSERT INTO trials VALUES (?,?,1.2,1.1,1.15,4.0,500,15.0,0.95,0.97,"
                    "'REVISE',1,1.18)", (trial_for, f"RUN-{trial_for}-001"))
    if reject_for:
        con.execute("INSERT INTO selection_attempts VALUES (?, 'standalone_screen', 'REJECT', ?)",
                    (reject_for, json.dumps({"profit_factor": 0.93, "bootstrap_p_value": 0.76})))
    con.commit()
    con.close()
    return db


def test_every_strategy_is_listed_with_its_stage1_source(tmp_path):
    mod = _load()
    block = mod.build(_fixture(tmp_path), why="ExecutionDataError: no quotes")
    for hyp in mod.STRATEGIES:
        assert hyp in block, f"{hyp} missing from the ledger"
    # Stage-1 evidence lives on the source hypothesis and must be attributed, not copied silently
    first_src = mod.STAGE1_SOURCE[mod.STRATEGIES[0]]
    assert f"(via `{first_src}`)" in block and "PF 1.258" in block
    assert "5 blocked before any trial" in block


def test_a_real_trial_supersedes_the_blocker(tmp_path):
    """A recorded Stage-3 row must win over the derived blocker, never be hidden by it."""
    mod = _load()
    hyp = mod.STRATEGIES[2]
    block = mod.build(_fixture(tmp_path, trial_for=hyp), why="ExecutionDataError: no quotes")
    row = next(ln for ln in block.splitlines() if f"`{hyp}`" in ln)
    assert f"REVISE (`RUN-{hyp}-001`)" in row
    assert "BLOCKED_EXECUTION" not in row
    assert "1 REVISE with a Stage-3 row, 4 blocked" in block


def test_no_blocker_and_no_screen_is_unscreened_not_blocked(tmp_path):
    """An executable contract with no evidence is unscreened; conflating it with a blocker would
    hide a mechanism that was simply never run."""
    mod = _load()
    block = mod.build(_fixture(tmp_path), why="")
    assert "0 blocked before any trial, 0 falsified at Stage 1, 5 unscreened" in block
    assert "UNSCREENED" in block


def test_a_stage1_reject_is_terminal_and_labelled(tmp_path):
    mod = _load()
    hyp = mod.STRATEGIES[-1]
    block = mod.build(_fixture(tmp_path, reject_for=hyp), why="")
    row = next(ln for ln in block.splitlines() if f"`{hyp}`" in ln)
    assert "Falsified at Stage 1" in row and "PF 0.930" in row


LIVE = os.environ.get("FX_LIVE_CHECKS") == "1"


@pytest.mark.skipif(not LIVE, reason="set FX_LIVE_CHECKS=1 to run live-registry checks")
def test_committed_ledger_matches_the_live_registry():
    """Opt-in: the registry is append-only, so any legitimate new trial changes this ledger."""
    assert _load().main(["--check", "--ledger", str(LEDGER)]) == 0


def test_coverage_accounts_for_all_eighteen_af30_hypotheses(tmp_path):
    """The FX set is five of eighteen; a reader must see where the other thirteen went."""
    mod = _load()
    block = mod.build(_fixture(tmp_path, af30_rejects=True, fx_trials=True), why="")
    assert len(mod.AF30_SET) == 18
    assert "18 total" in block
    assert "5 measured on FX" in block
    assert "12 Stage-1 REJECT carried as terminal" in block
    assert "1 excluded on the mechanism" in block
    breakout = next(ln for ln in block.splitlines() if "`HYP-2026-094`" in ln)
    assert "breakout" in breakout and "HYP-2026-109" in breakout
    # an FX-measured hypothesis must point at its own run, not just the source screen
    for fx_hyp, src in mod.STAGE1_SOURCE.items():
        assert f"`{fx_hyp}`" in block
