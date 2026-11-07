"""Reviewed stdio MCP must delegate only typed commands to Warden's own CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.mcp_warden import adapter
from scripts.mcp_warden.adapter import (
    RestrictedCommandError,
    _handle,
    build_argv,
    execute,
)
from warden.lease import mutation_lease


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "experiments" / "active").mkdir(parents=True)
    (root / "experiments" / "active" / "test.yaml").write_text("contract_id: TEST\n")
    (root / ".workspace" / "hypotheses" / "queue").mkdir(parents=True)
    (root / ".workspace" / "hypotheses" / "queue" / "hyp.yaml").write_text("name: TEST\n")
    return root


def test_typed_allowed_command_argv_matches_native_parser(fixture_root: Path, monkeypatch) -> None:
    # This is only a parser-contract fixture; no strategy is executed.
    monkeypatch.setattr(adapter, "_trusted_spec", lambda root, path: None)
    cmd = build_argv("verify-causality", flags={"--run-id": "RUN-T01"}, root=fixture_root)
    assert cmd[-4:] == ["verify-causality", "--json", "--run-id", "RUN-T01"]
    assert build_argv("screen", ["experiments/active/test.yaml"], root=fixture_root)[-3:] == [
        "screen", "experiments/active/test.yaml", "--json",
    ]
    assert build_argv("register-hypothesis", [".workspace/hypotheses/queue/hyp.yaml"],
                      root=fixture_root)[-3:] == [
        "register-hypothesis", ".workspace/hypotheses/queue/hyp.yaml", "--json",
    ]


def test_real_cli_read_only_and_missing_run_proof_return_typed_json(fixture_root: Path) -> None:
    status = execute("status", root=fixture_root)
    assert status["ok"] is True and status["result"]["source"] == "warden.status"
    proof = execute("verify-causality", flags={"--run-id": "RUN-UNKNOWN"}, root=fixture_root)
    assert proof["ok"] is False and proof["exit_code"] == 1
    assert proof["result"]["source"] == "warden.verify-causality"
    assert proof["result"]["value"]["verified"] is False
    assert proof["result"]["value"]["status"] == "RUN_NOT_FOUND"


@pytest.mark.parametrize("command,args,flags", [
    ("qualify", [], {}),
    ("generate-manifest", [], {}),
    ("reconcile-attempt", [], {"--run-id": "RUN-1"}),
    ("shell", [], {}),
    ("backtest", ["data/holdout/secret.yaml"], {}),
    ("screen", ["../../etc/passwd"], {}),
    ("verify", ["experiments/active/missing.yaml"], {}),
    ("verify-causality", [], {}),
    ("verify-causality", [], {"--run-id": "../../secret"}),
    ("audit-check", [], {"--unseal-holdout": "true"}),
    ("status", [], {"--sql": "SELECT * FROM trials"}),
])
def test_forbidden_routes_paths_and_flags_fail_closed(fixture_root: Path, command, args, flags):
    with pytest.raises(RestrictedCommandError):
        build_argv(command, args, flags, root=fixture_root)


def test_unreviewed_strategy_spec_cannot_be_executed(fixture_root: Path) -> None:
    with pytest.raises(RestrictedCommandError, match="tracked and unchanged"):
        build_argv("backtest", ["experiments/active/test.yaml"], root=fixture_root)


def test_mcp_cli_share_single_writer_and_read_only_status(fixture_root: Path, monkeypatch) -> None:
    monkeypatch.setattr(adapter, "_trusted_spec", lambda root, path: None)
    registry = fixture_root / "registry" / "registry.db"
    with mutation_lease(registry):
        busy = execute("verify", ["experiments/active/test.yaml"], root=fixture_root)
        assert busy["ok"] is False and busy["exit_code"] == 75
        assert busy["status"] == "BUSY" and busy["result"]["value"]["status"] == "BUSY"
        read = execute("status", root=fixture_root)
        assert read["ok"] is True


def test_stdio_json_rpc_exposes_only_reviewed_tools() -> None:
    listed = _handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert {x["name"] for x in listed["result"]["tools"]} == {"warden_status", "warden_exec"}
    refused = _handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "warden_exec", "arguments": {"command": "qualify"}}})
    assert refused["result"]["isError"] is True
    assert "REFUSED" in refused["result"]["content"][0]["text"]
    assert _handle({"method": "notifications/initialized"}) is None
