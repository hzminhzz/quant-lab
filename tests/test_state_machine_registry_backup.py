"""Baseline acceptance: WAL-consistent, read-only source registry backups.

The source is an isolated fixture, never the live research registry. SQLite's
online backup API preserves committed WAL transactions without copying a torn
.db/.db-wal pair or modifying the source.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from warden.registry import ResearchRegistry


def backup_consistent(source: Path, destination: Path) -> None:
    source_uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_conn, sqlite3.connect(destination) as destination_conn:
        source_conn.backup(destination_conn)


def test_wal_committed_counts_and_history_survive_readonly_backup(tmp_path: Path) -> None:
    db = tmp_path / "source.db"
    registry = ResearchRegistry(db)
    with registry._connect() as conn:
        conn.execute("INSERT INTO qualifications (qual_id,run_id,contract_id,hypothesis_id,lineage_id,qualified_at) VALUES (?,?,?,?,?,?)",
                     ("QUAL-OLD", "RUN-OLD", "CID", "HYP", "LIN", 42.0))
    snapshot = tmp_path / "snapshot.db"
    backup_consistent(db, snapshot)
    with sqlite3.connect(f"file:{snapshot.resolve()}?mode=ro", uri=True) as check:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert check.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0] == 1
        assert check.execute("SELECT run_id FROM qualifications WHERE lineage_id='LIN'").fetchone()[0] == "RUN-OLD"
    assert ResearchRegistry(snapshot).reserve_holdout_lineage is not None
    with sqlite3.connect(f"file:{snapshot.resolve()}?mode=ro", uri=True) as check:
        assert check.execute("SELECT state FROM holdout_claims WHERE lineage_id='LIN'").fetchone()[0] == "LEGACY_SPENT"
