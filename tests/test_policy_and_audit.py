"""Tool-contract + governance control plane: policy, audit envelope, typed trace replay."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from warden.audit import AUDIT_LOG
from warden.cli import _legacy_trace, _replay, build_parser, main
from warden.policy import authorize
from warden.runner import ExperimentRunner

ROOT = Path(".")


def test_authorize_allowlist_deny_and_approval():
    assert authorize("status").allow and not authorize("status").approval_required
    assert authorize("qualify").allow and authorize("qualify").approval_required
    denied = authorize("shell")
    assert not denied.allow and "allowlist" in denied.reason
    assert not authorize(None).allow


def test_every_subcommand_has_json_flag():
    parser = build_parser()
    for name, sub in parser._subparsers._group_actions[0].choices.items():
        assert "json" in [a.dest for a in sub._actions], f"{name} lost --json"


def test_audit_log_records_one_line_per_invocation():
    real = Path(AUDIT_LOG)
    before = len(real.read_text().splitlines()) if real.is_file() else 0
    assert main(["status", "--json"]) == 0
    lines = real.read_text().splitlines()
    assert len(lines) == before + 1, "exactly one audit line per invocation"
    entry = json.loads(lines[-1])
    assert {"cmd", "argv", "allow", "policy", "observed_at", "exit_code", "duration_ms", "result_bytes"} <= set(entry)
    assert entry["cmd"] == "status" and entry["allow"] is True and entry["exit_code"] == 0


def test_audit_failure_does_not_break_the_cli(monkeypatch, capsys):
    """A read-only or missing audit dir must not turn a successful command into a failure."""
    def boom(*a, **k):
        raise PermissionError("audit dir read-only")

    monkeypatch.setattr("warden.cli.audit_record", boom)
    assert main(["status", "--json"]) == 0
    assert "audit log unavailable" in capsys.readouterr().err


def test_status_json_envelope_shape():
    out = subprocess.run([sys.executable, "-m", "warden.cli", "status", "--json"], capture_output=True, text=True, check=False)
    assert out.returncode == 0
    env = json.loads(out.stdout)
    assert env["ok"] is True and env["source"] == "warden.status" and env["policy"] == "allow"
    assert env["value"]["trials_count"] >= 1
    assert env["observed_at"].endswith("+00:00")


def test_unknown_subcommand_denied_before_dispatch():
    # argparse rejects unparsed subcommands (exit 2); the policy layer denies anything
    # that parses but is not allowlisted (unit-tested above).
    out = subprocess.run([sys.executable, "-m", "warden.cli", "shell", "--json"], capture_output=True, text=True, check=False)
    assert out.returncode == 2
    assert not authorize("shell").allow


def test_qualify_refused_without_unseal_flag():
    out = subprocess.run([sys.executable, "-m", "warden.cli", "qualify", "--run-id", "RUN-ML-001", "--json"], capture_output=True, text=True, check=False)
    assert out.returncode == 2
    assert "human-only gate" in out.stderr
    entry = json.loads(Path(AUDIT_LOG).read_text().splitlines()[-1])
    assert entry["approval_required"] is True and entry["exit_code"] == 2


# --- Phase 4: typed trace ops. SQL text is never executed. -------------------

REFUSED = [
    'sqlite3 registry/registry.db "SELECT writefile(\'/tmp/warden_probe\',\'x\') FROM trials"',
    'sqlite3 registry/registry.db "SELECT load_extension(\'/tmp/evil\')"',
    'sqlite3 registry/registry.db "SELECT * FROM trials"',
    'sqlite3 registry/registry.db "DROP TABLE trials"',
    'sqlite3 registry/registry.db "ATTACH DATABASE \'/tmp/x\' AS y"',
    "touch /tmp/warden_pwn",
]


@pytest.mark.parametrize("cmd", REFUSED)
def test_replay_refuses_non_typed_trace_commands(cmd, tmp_path, capsys):
    probe = Path("/tmp/warden_probe")
    probe.unlink(missing_ok=True)
    entries = _legacy_trace(f"```bash\n{cmd}\n```\n")
    assert _replay(entries, ExperimentRunner(repo_root=ROOT)) is True
    assert "REFUSED" in capsys.readouterr().err
    assert not probe.exists(), "SELECT writefile() must be unreachable"
    assert not Path("/tmp/warden_pwn").exists()


def test_replay_accepts_legacy_count_query_and_typed_ops():
    runner = ExperimentRunner(repo_root=ROOT)
    legacy = _legacy_trace('```bash\nsqlite3 registry/registry.db "SELECT COUNT(*) FROM trials"\n```\n')
    assert legacy[0]["op"] == "registry_count" and legacy[0]["table"] == "trials"
    assert _replay(legacy, runner) is False
    with runner.registry._connect() as conn:
        n_hyp = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
    assert _replay([{"op": "registry_count", "table": "hypotheses", "expect": str(n_hyp)}], runner) is False
    assert _replay([{"op": "bogus", "expect": "x"}], runner) is True
    assert _replay([{"op": "registry_count", "table": "sqlite_master", "expect": "1"}], runner) is True


def test_continue_json_envelope(tmp_path):
    """Self-contained checkpoint: other tests add trials, so never rely on live state."""
    import sqlite3

    with sqlite3.connect("registry/registry.db") as conn:
        n_trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
    head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    state = {
        "schema": 1, "created_at": "2026-01-01T00:00:00+00:00", "ticket": "T", "notes": "",
        "evidence": [{"source": "git", "kind": "head", "value": head, "observed_at": "2026-01-01T00:00:00+00:00"}],
        "tool_trace": [{"op": "registry_count", "table": "trials", "expect": str(n_trials)}],
        "quality_gates": {"worktree_integrity": True},
    }
    checkpoint = tmp_path / "state.json"
    checkpoint.write_text(json.dumps(state))
    out = subprocess.run([sys.executable, "-m", "warden.cli", "continue", str(checkpoint), "--json"],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    env = json.loads(out.stdout)
    assert env["ok"] is True and env["value"]["drift"] is False
    assert env["source"] == "warden.continue.SUCCESS"


def test_untrusted_source_banner_on_register(tmp_path, capsys, monkeypatch):
    """Focused unit test: temp registry, temp hypothesis, stubbed integrity check."""
    from warden.contracts import load_contract
    from warden.registry import ResearchRegistry

    reg = ResearchRegistry(tmp_path / "registry.db")

    class _Runner:
        repo_root = ROOT

        def __init__(self, registry):
            self.registry = registry

        def resolve_contract(self, contract_id):
            return load_contract(Path("contracts/indices_1h_v2.yaml"))

    monkeypatch.setattr("warden.cli.ExperimentRunner", lambda: _Runner(reg))
    monkeypatch.setattr("warden.cli.check_worktree", lambda *a, **k: True)

    hyp = tmp_path / "HYP-UNTRUSTED.md"
    hyp.write_text(
        "---\nhypothesis_id: HYP-UNTRUSTED-999\ncontract_id: INDICES_1H_V2\ntitle: Untrusted provenance test\n"
        "strategy_family: rules_ts\neconomic_mechanism: Mechanism explaining 20 chars\n"
        "counterparty_or_friction: Friction explaining 20 chars\nexpected_holding_bars: 8\nfalsification_criteria: Criteria explaining 20 chars\n"
        "source_provenance: 'https://quantifiedstrategies.substack.com/p/example'\n---\n"
    )
    code = main(["register-hypothesis", str(hyp)])
    assert code == 0
    assert "UNTRUSTED SOURCE" in capsys.readouterr().err
    with reg._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 1


def test_internal_provenance_is_not_flagged():
    from warden.cli import UNTRUSTED_SOURCE

    assert UNTRUSTED_SOURCE.search("https://quantifiedstrategies.substack.com/p/x")
    assert not UNTRUSTED_SOURCE.search("internal_research")
