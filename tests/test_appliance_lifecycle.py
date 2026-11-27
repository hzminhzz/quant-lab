"""Real local lifecycle over isolated Git, SQLite and runtime fixtures; no market I/O."""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from warden.lease import MutationBusy, mutation_lease
from warden.registry import SCHEMA_VERSION, ResearchRegistry
from warden_ops.appliance import Appliance
from warden_ops.integrity import generate_manifest
from warden_ops.release import build_candidate

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def installed(tmp_path):
    source, canonical, home = (tmp_path / name for name in ("source", "canonical", "appliance"))
    source.mkdir()
    for name in ("src", "scripts"):
        shutil.copytree(REPO / name, source / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("uv.lock", "AGENTS.md", ".gitignore"):
        shutil.copy2(REPO / name, source / name)
    (source / "pyproject.toml").write_text('[project]\nname="appliance-fixture"\nversion="0.0.0"\n')
    (source / "tests").mkdir()
    (source / "adapters").mkdir()
    (source / "adapters/fixture.txt").write_text("required test asset")
    (source / "tests/test_smoke.py").write_text(
        "from pathlib import Path\n\ndef test_fixture():\n    assert 2 + 2 == 4\n\n"
        "def test_second():\n    assert Path(\"adapters/fixture.txt\").read_text() == \"required test asset\"\n")
    generate_manifest(source)
    for args in (["init", "-q"], ["add", "."],
                 ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                  "commit", "-qm", "isolated fixture"]):
        subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
    db = canonical / "registry/registry.db"
    ResearchRegistry(db)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE accrued_history(id TEXT PRIMARY KEY, evidence BLOB)")
        conn.execute("INSERT INTO accrued_history VALUES (?,?)", ("original", b"do not rewind"))
        conn.execute("PRAGMA user_version=8")
    for name in ("data/exploratory", "cache", ".workspace/hypotheses"):
        (canonical / name).mkdir(parents=True, exist_ok=True)
    candidate = build_candidate(source, tmp_path / "candidates", ledger_min=SCHEMA_VERSION,
                                ledger_max=SCHEMA_VERSION)
    appliance = Appliance.install(home=home, source=source, candidate=Path(candidate["path"]),
                                  canonical=canonical, python=Path(sys.executable), writer_units=[])
    verification = appliance.verify(candidate["release_id"])
    assert verification["ok"], verification
    return appliance, candidate["release_id"], db


def history(db):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT * FROM accrued_history ORDER BY id").fetchall()


def test_verified_migration_and_actual_runtime_preserve_history(installed):
    appliance, release, db = installed
    before = history(db)
    result = appliance.transition(release, request_id="first")
    assert result["state"] == "ACTIVE"
    assert result["release_id"] == release
    assert result["preservation"]["all_existing_tables_unchanged"] is True
    observed = appliance.observe()
    assert observed["healthy"] is True
    assert observed["release_id"] == release
    assert observed["ledger_schema"] == SCHEMA_VERSION
    assert observed["canonical_ledger"] == str(db.resolve())
    assert observed["module_root"] == str(appliance.runtime(release) / "src")
    assert observed["authority_model"] == "trusted-research-appliance"
    assert observed["scientific_authority"] is False
    assert history(db) == before
    assert appliance.transition(release, request_id="first") == result
    assert [e["kind"] for e in appliance.history()].count("transition-intent") == 1
    assert not (appliance.runtime(release) / "data/holdout").exists()
    proc = subprocess.run([str(appliance.home / "warden"), "control", "--request",
                           '{"schema_version":1,"operation":"observe"}'],
                          cwd=appliance.home, capture_output=True, text=True, check=False, timeout=60)
    assert proc.returncode == 0, proc.stderr
    view = json.loads(proc.stdout)["value"]
    assert view["runtime"]["release_id"] == release
    assert view["runtime"]["canonical_ledger"] == str(db.resolve())
    assert view["authority"]["scientific_authority"] is False
    assert view["checkout"]["root"] == str(appliance.home)
    again = subprocess.run([str(appliance.home / "warden"), "control", "--request",
                            json.dumps({"schema_version": 1, "operation": "observe", "since": view["snapshot_id"]})],
                           cwd=appliance.home, capture_output=True, text=True, check=False, timeout=60)
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["value"]["unchanged"] is True
    assert history(db) == before


def test_verification_cannot_be_reused_after_runtime_bytes_change(installed):
    appliance, release, db = installed
    target = appliance.runtime(release) / "src/warden_ops/control.py"
    target.chmod(0o644)
    target.write_text(target.read_text() + "\n# drift\n")
    before = history(db)
    with pytest.raises(ValueError, match="inventory|bytes|integrity"):
        appliance.transition(release, request_id="changed")
    assert history(db) == before
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8


def test_canonical_lease_blocks_transition_before_any_effect(installed):
    appliance, release, db = installed
    with mutation_lease(db), pytest.raises(MutationBusy):
        appliance.transition(release, request_id="busy")
    assert not any(e["kind"] == "transition-intent" for e in appliance.history())
    with mutation_lease(appliance.home / "operations.db"), pytest.raises(MutationBusy):
        appliance.verify(release)
    assert history(db) == [("original", b"do not rewind")]


def test_recovery_observes_committed_migration_without_rewinding(installed, monkeypatch):
    appliance, release, db = installed
    publish = appliance._publish
    def interrupted(_release):
        raise KeyboardInterrupt("fixture interruption after migration")
    monkeypatch.setattr(appliance, "_publish", interrupted)
    with pytest.raises(KeyboardInterrupt):
        appliance.transition(release, request_id="interrupted")
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    monkeypatch.setattr(appliance, "_publish", publish)
    result = appliance.transition(release, request_id="interrupted")
    assert result["state"] == "ACTIVE"
    assert history(db) == [("original", b"do not rewind")]
    assert [e["kind"] for e in appliance.history()].count("transition-intent") == 1
    assert [e["kind"] for e in appliance.history()].count("migration-complete") == 1


def test_halt_and_reactivation_keep_newly_accrued_evidence(installed):
    appliance, release, db = installed
    appliance.transition(release, request_id="first")
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO accrued_history VALUES (?,?)", ("later", b"new evidence"))
    before = history(db)
    assert appliance.halt("fixture recovery exercise")["state"] == "HALTED"
    assert appliance.observe()["healthy"] is False
    assert appliance.transition(release, request_id="recovery")["state"] == "ACTIVE"
    assert history(db) == before
    assert not hasattr(appliance, "restore_database")


def test_receipt_is_not_scientific_authority_and_request_conflicts_refuse(installed):
    appliance, release, _db = installed
    appliance.transition(release, request_id="first")
    with pytest.raises(ValueError, match="request.*conflict"):
        appliance.transition("0" * 64, request_id="first")
    with pytest.raises(ValueError, match="maintenance scope"):
        appliance.transition(release, request_id="science", effect="holdout")
    assert not any(e["subject"] == "science" for e in appliance.history())
    # Receipts are append-only even to the local operational API.
    with sqlite3.connect(appliance.home / "operations.db") as conn, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM lifecycle_events")
    assert json.loads((appliance.home / "config.json").read_text())["writer_units"] == []


def test_verification_ignores_inherited_test_selection(installed, monkeypatch):
    appliance, release, _db = installed
    original = next(e["payload"] for e in reversed(appliance.history()) if e["kind"] == "verified")
    xml = Path(next(arg.removeprefix("--junitxml=") for arg in original["command"] if arg.startswith("--junitxml=")))
    original_bytes = xml.read_bytes()
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k fixture")
    verified = appliance.verify(release)
    assert verified["ok"]
    assert verified["suite"]["tests"] == 2
    assert xml.read_bytes() == original_bytes  # A new verification must retain the old evidence.


def test_legacy_writer_lock_also_prevents_migration(installed):
    import fcntl

    appliance, release, db = installed
    with (db.parent / "warden-writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(MutationBusy):
            appliance.transition(release, request_id="legacy-busy")
    assert not any(e["kind"] == "transition-intent" for e in appliance.history())


def test_process_death_after_migration_is_reconciled_by_observation(installed):
    appliance, release, db = installed
    root = appliance.runtime(release)
    code = ("import os,signal; from warden_ops.appliance import Appliance;"
            f"a=Appliance({str(appliance.home)!r});"
            "a._publish=lambda release: os.kill(os.getpid(),signal.SIGKILL);"
            f"a.transition({release!r},request_id='killed')")
    proc = subprocess.run([str(appliance.python), "-c", code], cwd=root,
                          env=appliance._env(root, maintenance=True), check=False, timeout=30)
    assert proc.returncode == -9
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    result = appliance.transition(release, request_id="killed")
    assert result["state"] == "ACTIVE"
    assert history(db) == [("original", b"do not rewind")]
    assert [e["kind"] for e in appliance.history()].count("transition-intent") == 1


def test_failed_runtime_health_halts_without_rewinding_migration(installed, monkeypatch):
    appliance, release, db = installed
    probe = appliance._probe
    monkeypatch.setattr(appliance, "_probe", lambda value: {"release_id": value, "healthy": False})
    with pytest.raises(ValueError, match="health/identity"):
        appliance.transition(release, request_id="failed-health")
    assert appliance.observe()["status"] == "HALTED"
    with pytest.raises(ValueError, match="not healthy"):
        appliance.run(["status", "--json"])
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert history(db) == [("original", b"do not rewind")]
    monkeypatch.setattr(appliance, "_probe", probe)
    assert appliance.transition(release, request_id="failed-health")["state"] == "ACTIVE"
    assert [e["payload"]["state"] for e in appliance.history() if e["kind"] == "transition-result"] == ["HALTED", "ACTIVE"]


def test_loss_after_success_receipt_reconciles_only_its_own_halt(installed, monkeypatch):
    appliance, release, _db = installed
    append = appliance._append
    def lost(kind, subject, payload):
        append(kind, subject, payload)
        if kind == "transition-result" and payload["state"] == "ACTIVE":
            raise KeyboardInterrupt("lost acknowledgement after durable success")
    monkeypatch.setattr(appliance, "_append", lost)
    with pytest.raises(KeyboardInterrupt):
        appliance.transition(release, request_id="lost-success")
    assert appliance.observe()["status"] == "HALTED"
    monkeypatch.setattr(appliance, "_append", append)
    assert appliance.transition(release, request_id="lost-success")["state"] == "ACTIVE"
    assert appliance.observe()["healthy"]
    appliance.halt("later deliberate halt")
    appliance.transition(release, request_id="lost-success")
    assert appliance.observe()["status"] == "HALTED"
