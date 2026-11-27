"""Development entry points must not acquire the operational ledger."""
from __future__ import annotations

import sqlite3

from warden.cli import main
from warden.registry import SCHEMA_VERSION
from warden_ops.control import Control


def test_development_cli_refuses_linked_stale_ledger_before_migration(tmp_path, monkeypatch, capsys):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    db = canonical / "registry.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE history (value TEXT)")
        conn.execute("INSERT INTO history VALUES ('retain')")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION - 1}")
    root = tmp_path / "development"
    root.mkdir()
    (root / "registry").symlink_to(canonical, target_is_directory=True)
    monkeypatch.chdir(root)
    assert main(["register-hypothesis", "absent.md", "--json"]) == 2
    assert "installed appliance" in capsys.readouterr().err
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION - 1
        assert conn.execute("SELECT value FROM history").fetchall() == [("retain",)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("history",)]


def test_cli_refuses_incompatible_unlinked_existing_ledger(tmp_path, monkeypatch, capsys):
    registry = tmp_path / "registry"
    registry.mkdir()
    db = registry / "registry.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE history (value TEXT)")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    before = db.read_bytes()
    monkeypatch.chdir(tmp_path)
    assert main(["register-hypothesis", "absent.md", "--json"]) == 2
    assert "newer than this warden build" in capsys.readouterr().err
    assert db.read_bytes() == before


def test_control_refuses_mutation_of_linked_current_ledger(tmp_path):
    from warden.registry import ResearchRegistry
    canonical = tmp_path / "canonical"
    db = canonical / "registry.db"
    ResearchRegistry(db)
    root = tmp_path / "development"
    root.mkdir()
    (root / "registry").symlink_to(canonical, target_is_directory=True)
    result = Control(root).handle({"schema_version": 1, "operation": "cancel-job", "job_id": "missing"})
    assert not result["ok"]
    assert "installed appliance" in str(result["blockers"])
