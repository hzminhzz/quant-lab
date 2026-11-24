"""Reviewed stdio MCP must delegate only typed commands to Warden's own CLI."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from scripts.mcp_warden import adapter
from scripts.mcp_warden.adapter import (
    COMMANDS,
    RestrictedCommandError,
    _handle,
    build_argv,
    execute,
    parse_json_document,
)
from warden.cli import build_parser
from warden.lease import mutation_lease
from warden.registry import ResearchRegistry


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


def test_adapter_commands_and_flags_match_warden_cli_parser() -> None:
    parser = build_parser()
    subparsers_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    cli_choices = subparsers_action.choices

    for cmd, (min_pos, max_pos, allowed_flags) in COMMANDS.items():
        assert cmd in cli_choices, f"Adapter command {cmd!r} missing from warden.cli"
        sub = cli_choices[cmd]
        option_strings = {opt for action in sub._actions for opt in action.option_strings}
        for flag in allowed_flags:
            assert flag in option_strings, f"Adapter flag {flag} on {cmd} missing from warden.cli"
        pos_actions = [a for a in sub._actions if not a.option_strings]
        assert len(pos_actions) <= 1
        if pos_actions:
            nargs = pos_actions[0].nargs
            if nargs == "?":
                assert (min_pos, max_pos) == (0, 1)
            else:
                assert (min_pos, max_pos) == (1, 1)
        else:
            assert (min_pos, max_pos) == (0, 0)
        required_flags = {
            opt
            for action in sub._actions
            if action.required
            for opt in action.option_strings
        }
        assert required_flags <= set(allowed_flags)


def test_parse_json_document_handles_multiline_sentinels_and_partial_output() -> None:
    pretty = "preamble\n" + json.dumps({"status": "PASS", "rows": 12}, indent=2) + "\nWARDEN_RUN_COMPLETE\n"
    assert parse_json_document(pretty) == {"status": "PASS", "rows": 12}
    assert parse_json_document('{"ok": true, "value": ') is None
    assert parse_json_document("plain text only") is None


def test_falsifier_5_verify_causality_continue_and_spec_status_via_mcp(
    fixture_root: Path, monkeypatch
) -> None:
    ResearchRegistry(fixture_root / "registry" / "registry.db")
    # 1. spec-status via build_argv + execute
    spec_argv = build_argv("spec-status", ["experiments/active/test.yaml"], root=fixture_root)
    assert spec_argv[-3:] == ["spec-status", "experiments/active/test.yaml", "--json"]
    spec_res = execute("spec-status", ["experiments/active/test.yaml"], root=fixture_root)
    # Missing contract_id/hypothesis_id fields in minimal fixture -> CLI fails cleanly or returns envelope
    assert "no_auto_retry" in spec_res and spec_res["no_auto_retry"] is True
    (fixture_root / "experiments" / "active" / "valid_spec.yaml").write_text(
        "hypothesis_id: HYP-1\ncontract_id: CRYPTO_PERPS_1H_V1\nstrategy_module: strategies/candidates/foo.py\nparameters: {}\n",
        encoding="utf-8",
    )
    spec_ok = execute("spec-status", ["experiments/active/valid_spec.yaml"], root=fixture_root)
    assert spec_ok["ok"] is True
    assert spec_ok["result"]["source"] == "warden.spec-status"
    assert spec_ok["result"]["value"]["state"] == "NEW"

    # 2. verify-causality --run-id via stdio JSON-RPC _handle
    monkeypatch.setattr(adapter, "ROOT", fixture_root)
    causality_rpc = _handle(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "warden_exec",
                "arguments": {"command": "verify-causality", "flags": {"--run-id": "RUN-T11-NONE"}},
            },
        }
    )
    assert causality_rpc is not None
    causality_body = json.loads(causality_rpc["result"]["content"][0]["text"])
    assert causality_body["exit_code"] == 1
    assert causality_body["result"]["source"] == "warden.verify-causality"
    assert causality_body["result"]["value"]["run_id"] == "RUN-T11-NONE"

    # 3. continue (both omitted path and explicit .workspace/state/current.json path)
    assert build_argv("continue", root=fixture_root)[-2:] == ["continue", "--json"]
    state_file = fixture_root / ".workspace" / "state" / "current.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text('{"schema": 99}', encoding="utf-8")
    cont_argv = build_argv("continue", [".workspace/state/current.json"], root=fixture_root)
    assert cont_argv[-3:] == ["continue", ".workspace/state/current.json", "--json"]

    cont_rpc = _handle(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "warden_exec",
                "arguments": {"command": "continue", "positional": [".workspace/state/current.json"]},
            },
        }
    )
    assert cont_rpc is not None
    cont_body = json.loads(cont_rpc["result"]["content"][0]["text"])
    assert cont_body["exit_code"] == 1  # schema 99 fails closed in warden continue

    # Bad continue paths and holdout symlinks are refused
    with pytest.raises(RestrictedCommandError):
        build_argv("continue", ["experiments/active/test.yaml"], root=fixture_root)
    holdout_target = fixture_root / "data" / "holdout" / "secret.yaml"
    holdout_target.parent.mkdir(parents=True, exist_ok=True)
    holdout_target.write_text("secret: true\n", encoding="utf-8")
    symlink_path = fixture_root / "experiments" / "active" / "alias.yaml"
    symlink_path.symlink_to(holdout_target)
    with pytest.raises(RestrictedCommandError):
        build_argv("spec-status", ["experiments/active/alias.yaml"], root=fixture_root)


def test_execute_forces_non_interactive_and_handles_bad_flags_type(
    fixture_root: Path, monkeypatch
) -> None:
    captured_env: dict[str, str] = {}

    def fake_run(argv, *, cwd, env, capture_output, text, check, timeout):
        captured_env.update(env)
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"ok": True, "value": {}, "source": "warden.status"}), stderr=""
        )

    monkeypatch.setenv("WARDEN_NON_INTERACTIVE", "0")
    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    res = execute("status", root=fixture_root)
    assert res["ok"] is True
    assert captured_env["WARDEN_NON_INTERACTIVE"] == "1"

    bad_flags = _handle(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "warden_exec", "arguments": {"command": "status", "flags": ["bad"]}},
        }
    )
    assert bad_flags is not None
    assert bad_flags["result"]["isError"] is True
    assert "REFUSED" in bad_flags["result"]["content"][0]["text"]

    # End-to-end truncated JSON from CLI leaves result=None cleanly (no crash)
    def truncated_run(argv, *, cwd, env, capture_output, text, check, timeout):
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true, "value": {"state": ', stderr="")

    monkeypatch.setattr(adapter.subprocess, "run", truncated_run)
    monkeypatch.setattr(adapter, "ROOT", fixture_root)
    truncated_rpc = _handle(
        {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {"name": "warden_status"},
        }
    )
    assert truncated_rpc is not None
    truncated_body = json.loads(truncated_rpc["result"]["content"][0]["text"])
    assert truncated_body["result"] is None
    assert truncated_body["no_auto_retry"] is True
