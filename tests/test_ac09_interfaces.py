"""A08/A22: actual CLI, typed MCP and runner preserve domain outcomes/debt."""
import json
import sqlite3
import subprocess

import pytest
import yaml

from tests.conftest import write_fixture_checksums
from tests.test_rg05_research_intent import approve, review
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner
from warden_ops.control import Control


def public_fixture(tiny_repo):
    f = tiny_repo()
    (f.repo / "strategies/candidates").mkdir()
    (f.repo / "strategies/flat.py").rename(f.repo / "strategies/candidates/flat.py")
    doc = yaml.safe_load(f.spec.read_text())
    doc["strategy_module"] = "strategies/candidates/flat.py"
    (f.repo / "experiments/active").mkdir(parents=True)
    f.spec = f.repo / "experiments/active/test.yaml"
    f.spec.write_text(yaml.safe_dump(doc))
    destination = f.repo / "registry/registry.db"
    destination.parent.mkdir()
    with sqlite3.connect(f.registry.db_path) as original, sqlite3.connect(destination) as copy:
        original.backup(copy)
    f.registry = ResearchRegistry(destination)
    runner, _, _ = approve(f)
    review(runner, f)
    write_fixture_checksums(f.repo, *[p.relative_to(f.repo).as_posix()
                                    for p in (f.repo / "contracts").rglob("*") if p.is_file()])
    subprocess.run(["git", "init", "-q", str(f.repo)], check=True)
    subprocess.run(["git", "add", "contracts", "strategies", "experiments"], cwd=f.repo, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid",
                    "-c", "commit.gpgSign=false", "commit", "-qm", "approved fixture"],
                   cwd=f.repo, check=True)
    return f, Control(f.repo)


def prepared(control, kind="screen"):
    reply = control.handle({"schema_version": 1, "operation": "prepare",
                            "action": {"kind": kind, "spec_path": "experiments/active/test.yaml"}})
    assert reply["ok"], reply
    return reply["value"]["plan"]


def mcp(adapter, payload):
    response = adapter._handle({"id": 1, "method": "tools/call",
                                 "params": {"name": "warden_control", "arguments": payload}})
    return json.loads(response["result"]["content"][0]["text"])


def test_typed_mcp_keeps_reviewed_git_boundary_before_science(tiny_repo, monkeypatch):
    from scripts.mcp_warden import adapter
    f, control = public_fixture(tiny_repo)
    monkeypatch.setattr(adapter, "ROOT", f.repo)
    f.spec.write_text(f.spec.read_text() + "\n# not reviewed in HEAD\n")
    refusal = mcp(adapter, {"schema_version": 1, "operation": "act",
                            "request_id": "unreviewed", "plan": prepared(control)})
    assert refusal["status"] == "REFUSED"
    assert "tracked and unchanged" in str(refusal)
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 0
    subprocess.run(["git", "add", "experiments"], cwd=f.repo, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid",
                    "-c", "commit.gpgSign=false", "commit", "-qm", "reviewed comment"],
                   cwd=f.repo, check=True)
    positive = mcp(adapter, {"schema_version": 1, "operation": "act",
                             "request_id": "reviewed", "plan": prepared(control)})
    assert positive["recorded_verdict"] == "REJECT", positive
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1


def test_rejected_screen_stops_cli_runner_and_old_new_mcp(tiny_repo, monkeypatch, capsys):
    from scripts.mcp_warden import adapter
    from warden.cli import main
    f, control = public_fixture(tiny_repo)
    monkeypatch.chdir(f.repo)
    monkeypatch.setattr(adapter, "ROOT", f.repo)
    path = "experiments/active/test.yaml"
    old_exit = main(["screen", path, "--json"])
    old_output = capsys.readouterr().out
    assert old_exit == 1, old_output  # Existing scientific REJECT exit remains 1.
    with pytest.raises(ValueError):
        ExperimentRunner(registry=f.registry, repo_root=f.repo).run_spec(f.spec)
    payload = {"schema_version": 1, "operation": "act", "request_id": "blocked-full",
               "plan": prepared(control, "backtest")}
    assert main(["control", "--request", json.dumps(payload), "--json"]) == 2
    typed_cli = json.loads(capsys.readouterr().out)
    assert typed_cli["status"] == "REFUSED"
    assert mcp(adapter, payload)["status"] == "REFUSED"
    old_mcp = adapter.execute("backtest", [path], root=f.repo)
    assert not old_mcp["ok"]
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1
    with f.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM selection_attempts").fetchone()[0] == 1
