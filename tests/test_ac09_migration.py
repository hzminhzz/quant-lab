"""A25: rehearsal includes committed WAL and every historical user table."""
import sqlite3

import pytest

from scripts import rehearse_state_machine_migration as migration
from warden.registry import SCHEMA_VERSION, ResearchRegistry


def test_rehearsal_preserves_wal_and_unlisted_table_without_rowid(tmp_path):
    source = tmp_path / "source/registry.db"
    ResearchRegistry(source)
    with sqlite3.connect(source) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute('CREATE TABLE "extra history" (id TEXT PRIMARY KEY, receipt BLOB) WITHOUT ROWID')
        writer.execute('INSERT INTO "extra history" VALUES (?,?)', ("one", b"original evidence"))
        writer.execute("PRAGMA user_version=8")
        writer.commit()
        before_db = source.read_bytes()
        wal = source.with_name(source.name + "-wal")
        before_wal = wal.read_bytes()
        report = migration.rehearse(source, tmp_path / "scratch/migrated.db")
        assert report["original_counts"]["extra history"] == 1
        assert report["source_schema"] == 8
        assert report["target_schema"] == SCHEMA_VERSION
        assert report["all_existing_tables_unchanged"] is True
        assert report["backup"]["opened_source_read_only"] is True
        assert report["idempotent"] is True
        assert source.read_bytes() == before_db
        assert wal.read_bytes() == before_wal
        with sqlite3.connect(tmp_path / "scratch/migrated.db") as check:
            assert check.execute('SELECT receipt FROM "extra history"').fetchone()[0] == b"original evidence"


def test_rehearsal_detects_mutation_outside_old_fixed_table_list(tmp_path, monkeypatch):
    source = tmp_path / "source/registry.db"
    ResearchRegistry(source)
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE later_evidence(id TEXT PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO later_evidence VALUES ('immutable','before')")
    actual = migration.ResearchRegistry
    reached = []
    def corrupt_fixture(path):
        registry = actual(path)
        with registry._connect() as conn:
            conn.execute("UPDATE later_evidence SET payload='corrupt'")
            assert conn.execute("SELECT payload FROM later_evidence").fetchone()[0] == "corrupt"
        reached.append(True)
        return registry
    monkeypatch.setattr(migration, "ResearchRegistry", corrupt_fixture)
    with pytest.raises(ValueError, match="mutated.*later_evidence"):
        migration.rehearse(source, tmp_path / "scratch/migrated.db")
    assert reached
    with sqlite3.connect(source) as check:
        assert check.execute("SELECT payload FROM later_evidence").fetchone()[0] == "before"
