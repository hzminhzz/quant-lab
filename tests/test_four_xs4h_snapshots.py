"""Data and registry isolation tests: never overwrite research evidence."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from scripts.prepare_four_xs4h_snapshot import publish_once, snapshot_registry


def test_source_data_is_digest_pinned_and_write_once(tmp_path) -> None:
    original = tmp_path / "observed.parquet"
    destination = tmp_path / "copied.parquet"
    payload = b"immutable historical exploratory dataset"
    original.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    publish_once(original, destination, expected)
    assert destination.read_bytes() == payload
    publish_once(original, destination, expected)
    destination.write_bytes(b"conflicting bytes")
    with pytest.raises(FileExistsError, match="different dataset"):
        publish_once(original, destination, expected)
    with pytest.raises(ValueError, match="unexpected source data digest"):
        publish_once(original, tmp_path / "unused", "0" * 64)


def make_registry(path, *, seeded: bool) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE hypotheses(hypothesis_id TEXT);
            CREATE TABLE trials(run_id TEXT);
            CREATE TABLE selection_attempts(attempt_id TEXT);
            CREATE TABLE trial_quarantine(run_id TEXT,reason TEXT,quarantined_at REAL);
        """)
        if seeded:
            db.execute(
                "INSERT INTO trial_quarantine VALUES('RUN-ML-001','synthetic',42)"
            )


def test_online_registry_backup_preserves_history(tmp_path) -> None:
    src, dst = tmp_path / "primary.db", tmp_path / "isolated.db"
    make_registry(src, seeded=True)
    make_registry(dst, seeded=True)
    with sqlite3.connect(src) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO hypotheses VALUES('HYP-TEST')")
        db.execute("INSERT INTO trials VALUES('RUN-TEST')")
        db.execute("INSERT INTO selection_attempts VALUES('ATT-TEST')")
    summary = snapshot_registry(src, dst)
    assert summary["wal_consistent"] is True
    assert summary["source_read_only"] is True
    assert summary["counts"] == {
        "hypotheses": 1, "trials": 1, "selection_attempts": 1,
    }
    with sqlite3.connect(src) as db:
        assert db.execute("SELECT count(*) FROM trials").fetchone()[0] == 1
    with sqlite3.connect(dst) as db:
        assert db.execute("SELECT count(*) FROM trials").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM trial_quarantine").fetchone()[0] == 1


def test_populated_isolated_registry_cannot_be_overwritten(tmp_path) -> None:
    src, dst = tmp_path / "primary.db", tmp_path / "isolated.db"
    make_registry(src, seeded=True)
    make_registry(dst, seeded=True)
    with sqlite3.connect(dst) as db:
        db.execute("INSERT INTO selection_attempts VALUES('OTHER-SESSION')")
    with pytest.raises(FileExistsError, match="contains data"):
        snapshot_registry(src, dst)
    with sqlite3.connect(dst) as db:
        assert db.execute("SELECT attempt_id FROM selection_attempts").fetchone()[0] == "OTHER-SESSION"


def test_unexpected_synthetic_quarantine_refuses_reset(tmp_path) -> None:
    src, dst = tmp_path / "primary.db", tmp_path / "isolated.db"
    make_registry(src, seeded=True)
    make_registry(dst, seeded=True)
    with sqlite3.connect(dst) as db:
        db.execute(
            "UPDATE trial_quarantine SET reason='unrelated quarantine' WHERE run_id='RUN-ML-001'"
        )
    with pytest.raises(FileExistsError, match="contains data"):
        snapshot_registry(src, dst)
