"""Fixture-only exclusive pre-read lineage claims, including legacy migration."""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import pytest

from warden.registry import ResearchRegistry


def _claim(db_path: str, lineage: str, run: str, result: mp.Queue) -> None:
    try:
        ResearchRegistry(db_path).reserve_holdout_lineage(lineage, run)
    except PermissionError:
        result.put("DENIED")
    else:
        result.put("ACCEPTED")


def test_exclusive_claim_before_read(tmp_path: Path) -> None:
    db = tmp_path / "fixture.db"
    reg = ResearchRegistry(db)
    q: mp.Queue = mp.Queue()
    callers = [mp.Process(target=_claim, args=(str(db), "LIN-A", f"RUN-{i}", q)) for i in range(2)]
    for caller in callers:
        caller.start()
    outcomes = [q.get(timeout=12) for _ in callers]
    for caller in callers:
        caller.join(timeout=12)
        assert caller.exitcode == 0
    assert sorted(outcomes) == ["ACCEPTED", "DENIED"]
    with reg._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM holdout_claims").fetchone()[0] == 1
        assert conn.execute("SELECT state FROM holdout_claims WHERE lineage_id='LIN-A'").fetchone()[0] == "CLAIMED"
    with pytest.raises(PermissionError):
        reg.reserve_holdout_lineage("LIN-A", "RUN-RETRY")


def test_read_start_and_uncertain_claim_never_release(tmp_path: Path) -> None:
    reg = ResearchRegistry(tmp_path / "fixture.db")
    reg.reserve_holdout_lineage("LIN-A", "RUN-A")
    reg.mark_holdout_read_started("LIN-A", "RUN-A")
    with reg._connect() as conn:
        assert conn.execute("SELECT state FROM holdout_claims WHERE lineage_id='LIN-A'").fetchone()[0] == "READ_STARTED"
    with pytest.raises(PermissionError):
        reg.reserve_holdout_lineage("LIN-A", "RUN-A")
    with pytest.raises(PermissionError):
        reg.mark_holdout_read_started("LIN-A", "RUN-B")


def test_legacy_qualifications_migrate_as_spent(tmp_path: Path) -> None:
    db = tmp_path / "fixture.db"
    reg = ResearchRegistry(db)
    with reg._connect() as conn:
        conn.execute("INSERT INTO qualifications (qual_id,run_id,contract_id,hypothesis_id,lineage_id,approved_by_human,qualified_at) VALUES (?,?,?,?,?,?,?)",
                     ("QUAL-OLD", "RUN-OLD", "CID", "HYP", "LIN-OLD", "HUMAN_OPERATOR", 42.0))
    reg = ResearchRegistry(db)
    reg = ResearchRegistry(db)
    with reg._connect() as conn:
        assert conn.execute("SELECT state,run_id FROM holdout_claims WHERE lineage_id='LIN-OLD'").fetchone()[:] == ("LEGACY_SPENT", "RUN-OLD")
        assert conn.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0] == 1
    with pytest.raises(PermissionError):
        reg.reserve_holdout_lineage("LIN-OLD", "RUN-NEW")
