"""A01–A04/A20: cheap observation and operational handoff are scientifically inert."""
import argparse
import json
import sqlite3
import subprocess
from types import SimpleNamespace

from warden_ops.checkpoint import cmd_handoff
from warden_ops.control import Control


def test_missing_legacy_and_dirty_state_are_explicit_without_creating_ledger(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    control = Control(root)
    request = {"schema_version": 1, "operation": "observe", "objective": "AC fixtures"}
    result = control.handle(request)
    assert result["ok"]
    view = result["value"]
    assert view["ledger"]["status"] == "MISSING"
    assert view["ledger"]["counts"]["hypotheses"] is None
    assert view["runtime"]["status"] == "UNKNOWN"
    assert view["authority"]["status"] == "UNVERIFIED"
    assert not (root / "registry").exists()
    assert len(json.dumps(result).encode()) <= 12288
    (root / "registry").mkdir()
    db = root / "registry/registry.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE hypotheses(hypothesis_id TEXT)")
        conn.execute("INSERT INTO hypotheses VALUES ('HYP-OLD')")
    before = db.read_bytes()
    legacy = control.handle(request)["value"]["ledger"]
    assert legacy["schema_version"] == 0
    assert legacy["counts"]["hypotheses"] == 1
    assert legacy["counts"]["selection_attempts"] is None
    assert db.read_bytes() == before


def test_observation_delta_pagination_and_invalid_request(cli_repo):
    control = Control(cli_repo)
    request = {"schema_version": 1, "operation": "observe"}
    first = control.handle(request)
    assert first["ok"]
    assert control.handle({**request, "since": first["value"]["snapshot_id"]})["value"]["unchanged"]
    (cli_repo / "new.txt").write_text("dirty")
    changed = control.handle(request)
    assert changed["value"]["checkout"]["dirty"] is True
    assert changed["value"]["snapshot_id"] != first["value"]["snapshot_id"]
    refused = control.handle({**request, "shell": "anything"})
    assert refused["status"] == "REFUSED"
    assert refused["blockers"][0]["classification"] == "TECHNICAL_PRE_ADMISSION"


def test_cli_and_mcp_observe_share_the_same_application(cli_repo, monkeypatch, capsys):
    from scripts.mcp_warden import adapter
    from warden.cli import main
    monkeypatch.chdir(cli_repo)
    monkeypatch.setattr(adapter, "ROOT", cli_repo)
    payload = {"schema_version": 1, "operation": "observe"}
    assert main(["control", "--request", json.dumps(payload), "--json"]) == 0
    cli = json.loads(capsys.readouterr().out)
    reply = adapter._handle({"id": 1, "method": "tools/call",
                             "params": {"name": "warden_control", "arguments": payload}})
    mcp = json.loads(reply["result"]["content"][0]["text"])
    assert cli["value"]["ledger"] == mcp["value"]["ledger"]
    assert cli["source"] == mcp["source"] == "warden.control"
    assert reply["result"]["isError"] is False


def test_long_hypothesis_details_do_not_stall_pagination(tmp_path):
    db = tmp_path / "registry.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE hypotheses(hypothesis_id TEXT,title TEXT)")
        conn.executemany("INSERT INTO hypotheses VALUES (?,?)", [("HYP-A", "a" * 20000), ("HYP-B", "b" * 20000)])
    control = Control(tmp_path, registry_path=db)
    result = control.handle({"schema_version": 1, "operation": "observe", "limit": 1})
    assert result["ok"]
    ledger = result["value"]["ledger"]
    assert len(ledger["hypotheses"]) == 1
    assert ledger["next_cursor"] == "HYP-A"
    assert ledger["hypotheses"][0]["truncated_fields"] == ["title"]
    second = control.handle({"schema_version": 1, "operation": "observe", "limit": 1, "cursor": "HYP-A"})
    assert second["value"]["ledger"]["hypotheses"][0]["hypothesis_id"] == "HYP-B"


def test_handoff_does_not_initialize_or_migrate_a_legacy_ledger(cli_repo):
    db = cli_repo / "registry/registry.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version=0")
    before = db.read_bytes()
    args = argparse.Namespace(ticket="AC-02", notes="fixture", json=True)
    assert cmd_handoff(args, SimpleNamespace(repo_root=cli_repo)) == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert db.read_bytes() == before
    state = json.loads((cli_repo / ".workspace/state/current.json").read_text())
    assert state["situation"]["ledger"]["schema_version"] == 0
    assert state["situation"]["ledger"]["counts"]["trials"] == 0
