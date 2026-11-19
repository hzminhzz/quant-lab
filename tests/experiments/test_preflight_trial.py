"""The preflight snapshot must see WAL content, or it measures the wrong history."""

from __future__ import annotations

import json
import runpy
import sqlite3
from pathlib import Path

from warden.registry import ResearchRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = runpy.run_path(str(REPO_ROOT / "scripts" / "preflight_trial.py"))
snapshot = PREFLIGHT["snapshot"]
check_alignment = PREFLIGHT["check_alignment"]
prior_stream_lengths = PREFLIGHT["prior_stream_lengths"]


def _seed(db: Path, attempts: int = 2) -> sqlite3.Connection:
    """Seed a WAL registry and return the still-open writer, which keeps the WAL alive."""
    ResearchRegistry(db)  # schema, and switches the file to WAL
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO trials (run_id,hypothesis_id,contract_id,returns_json,trial_number_in_hyp) "
        "VALUES ('RUN-A','H','MULTI_ASSET_1D_V1',?,1)", (json.dumps([0.01] * 806),))
    for i in range(attempts):
        con.execute(
            "INSERT INTO selection_attempts (attempt_id,attempt_base,hypothesis_id,contract_id,kind,"
            "stage,outcome,returns_observed,returns_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"ATT-{i}", f"base-{i}", "H", "MULTI_ASSET_1D_V1", "param_grid", "STAGE_3_STRESS",
             "GRID", 1, json.dumps([0.01] * 806), float(i)))
    con.commit()
    return con


def test_snapshot_captures_rows_that_live_only_in_the_wal(tmp_path):
    live = tmp_path / "live.db"
    writer = _seed(live)
    wal = Path(str(live) + "-wal")
    assert wal.exists() and wal.stat().st_size > 0, "fixture must leave uncheckpointed WAL content"

    snap = snapshot(live, tmp_path / "snap.db")
    for table in ("trials", "selection_attempts"):
        with sqlite3.connect(f"file:{live}?mode=ro", uri=True) as a, \
                sqlite3.connect(f"file:{snap}?mode=ro", uri=True) as b:
            assert a.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == \
                b.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    assert prior_stream_lengths(snap, "MULTI_ASSET_1D_V1") == [806]
    writer.close()


def test_snapshot_does_not_touch_the_source(tmp_path):
    live = tmp_path / "live.db"
    writer = _seed(live)
    before = (live.stat().st_size, live.stat().st_mtime_ns)
    snapshot(live, tmp_path / "snap.db")
    assert (live.stat().st_size, live.stat().st_mtime_ns) == before
    writer.close()


def test_alignment_refuses_a_length_mismatch():
    assert check_alignment(806, [806]) == []
    assert check_alignment(806, []) == []
    problems = check_alignment(805, [806])
    assert problems and "SelectionHistoryError" in problems[0]


def test_alignment_refuses_an_empty_candidate():
    assert check_alignment(0, [806])
