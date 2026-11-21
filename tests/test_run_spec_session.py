"""The one-process spec session must be observationally identical to the CLI funnel.

Two layers:
* orchestration (policy refusal, fail-fast order, exit codes, one audit entry per stage)
  is pinned with stub stage handlers, so it cannot silently drift;
* scientific output is pinned end to end on the tiny fixture repo: session stage text must
  match the text the three standalone CLI handlers produce from the same registry state.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sqlite3
from pathlib import Path

import pytest

import scripts.run_spec_session as S
from warden.cli import cmd_backtest, cmd_screen, cmd_verify, generate_manifest
from warden.policy import Decision as decision


def audit_entries(root: Path) -> list[dict]:
    path = root / ".workspace" / "audit" / "toolcalls.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def stub_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A repo root that satisfies the session guard, with no real registry or data."""
    root = tmp_path / "repo"
    (root / "registry").mkdir(parents=True)
    (root / "registry" / "registry.db").write_bytes(b"")
    (root / ".warden_manifest.sha256").write_text("", encoding="utf-8")
    (root / "spec.yaml").write_text("contract_id: X\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def stub(payload: dict, code: int = 0):
    def handler(args: argparse.Namespace, runner) -> int:
        print(json.dumps(payload))
        return code
    return handler


OK_PAYLOAD = {"status": "PASS", "passed": True, "run_id": "RUN-STUB-001", "verdict": "REVISE"}


def install_stubs(monkeypatch: pytest.MonkeyPatch, codes: dict[str, int] | None = None) -> None:
    codes = codes or {}
    for stage in S.STAGES:
        monkeypatch.setitem(S.HANDLERS, stage, stub(OK_PAYLOAD, codes.get(stage, 0)))


def run_session(argv: list[str]) -> tuple[int, dict, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = S.main(argv)
    text = out.getvalue()
    return code, json.loads(text.strip().splitlines()[-1]), text


def test_all_stages_ok_emits_one_audit_entry_per_stage(stub_repo: Path, monkeypatch) -> None:
    install_stubs(monkeypatch)
    code, envelope, _ = run_session(["spec.yaml", "--json"])

    assert code == 0 and envelope["ok"] is True and envelope["failed_stage"] is None
    assert [s["stage"] for s in envelope["stages"]] == list(S.STAGES)
    assert envelope["run_id"] == "RUN-STUB-001" and envelope["verdict"] == "REVISE"
    entries = audit_entries(stub_repo)
    assert [e["cmd"] for e in entries] == list(S.STAGES)
    assert all(e["allow"] and e["exit_code"] == 0 for e in entries)
    assert all(e["argv"][0] == S.AUDIT_MARKER for e in entries)
    assert all(e["duration_ms"] >= 0 and e["result_bytes"] > 0 for e in entries)


def test_screen_rejection_halts_before_backtest(stub_repo: Path, monkeypatch) -> None:
    install_stubs(monkeypatch, {"screen": 1})
    code, envelope, _ = run_session(["spec.yaml", "--json"])

    assert code == 1 and envelope["ok"] is False and envelope["failed_stage"] == "screen"
    assert [s["stage"] for s in envelope["stages"]] == ["verify", "screen"]
    assert [e["cmd"] for e in audit_entries(stub_repo)] == ["verify", "screen"]


def test_force_all_runs_backtest_after_screen_rejection(stub_repo: Path, monkeypatch) -> None:
    install_stubs(monkeypatch, {"screen": 1})
    code, envelope, _ = run_session(["spec.yaml", "--json", "--force-all"])

    assert code == 1 and envelope["failed_stage"] == "screen" and envelope["ok"] is False
    assert [s["stage"] for s in envelope["stages"]] == list(S.STAGES)
    assert envelope["verdict"] == "REVISE"


def test_verify_failure_halts_before_screen(stub_repo: Path, monkeypatch) -> None:
    install_stubs(monkeypatch, {"verify": 1})
    code, envelope, _ = run_session(["spec.yaml", "--json", "--force-all"])

    assert code == 1 and envelope["failed_stage"] == "verify"
    assert [s["stage"] for s in envelope["stages"]] == ["verify"]


def test_stage_exit_two_is_recorded_as_a_refusal(stub_repo: Path, monkeypatch) -> None:
    install_stubs(monkeypatch, {"backtest": 2})
    code, envelope, _ = run_session(["spec.yaml", "--json"])

    assert code == 2 and envelope["failed_stage"] == "backtest"
    assert [e["exit_code"] for e in audit_entries(stub_repo)] == [0, 0, 2]


def test_policy_refusal_skips_the_handler_and_stops_the_session(stub_repo: Path, monkeypatch) -> None:
    install_stubs(monkeypatch)
    called: list[str] = []

    def deny_verify(handler):
        def wrapper(args, runner):
            called.append("verify")
            return handler(args, runner)
        return wrapper

    monkeypatch.setitem(S.HANDLERS, "verify", deny_verify(S.HANDLERS["verify"]))
    real_authorize = S.authorize
    monkeypatch.setattr(S, "authorize",
                        lambda stage: decision(False, "verify is not on the warden allowlist")
                        if stage == "verify" else real_authorize(stage))
    code, envelope, _ = run_session(["spec.yaml", "--json"])

    assert code == 2 and envelope["failed_stage"] == "verify" and called == []
    entry = audit_entries(stub_repo)[0]
    assert entry["cmd"] == "verify" and entry["allow"] is False and entry["exit_code"] == 2


def test_summary_out_matches_stdout_envelope(stub_repo: Path, monkeypatch) -> None:
    install_stubs(monkeypatch)
    out_path = stub_repo / "summary.json"
    code, envelope, _ = run_session(["spec.yaml", "--json", "--summary-out", str(out_path)])

    assert code == 0 and json.loads(out_path.read_text()) == envelope


def test_refuses_a_directory_that_is_not_a_warden_repo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "spec.yaml").write_text("contract_id: X\n", encoding="utf-8")
    with contextlib.redirect_stderr(io.StringIO()):
        code = S.main(["spec.yaml", "--json"])
    assert code == 2


def test_missing_spec_refuses(stub_repo: Path) -> None:
    with contextlib.redirect_stderr(io.StringIO()):
        code = S.main(["nope.yaml", "--json"])
    assert code == 2


def test_human_mode_streams_stage_output_then_a_summary_line(stub_repo: Path, monkeypatch) -> None:
    install_stubs(monkeypatch)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = S.main(["spec.yaml"])
    lines = [line for line in out.getvalue().strip().splitlines() if line.strip()]

    assert code == 0
    assert lines.count(json.dumps(OK_PAYLOAD)) == len(S.STAGES)
    assert lines[-1].startswith("Session: stages=verify,screen,backtest")
    assert "verdict=REVISE" in lines[-1]


# --- end to end: same scientific output as the standalone CLI handlers -------------------

TINY_MODULE = '''
import numpy as np
import polars as pl


class _Momentum:
    def compute(self, bars_df, contract):
        return bars_df.with_columns(
            mom=pl.col("close") / pl.col("close").shift(1).over("asset") - 1.0
        ).with_columns(mom=pl.col("mom").fill_null(0.0))


pipeline = _Momentum()


def generate_positions(panel_df, contract, **kwargs):
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    close = panel_df.pivot(index="timestamp", on="asset", values="close").sort("timestamp").select(assets).to_numpy().astype(float)
    mom = np.zeros_like(close)
    mom[1:] = close[1:] / close[:-1] - 1.0
    pos = np.sign(mom) * 0.1
    pos[~np.isfinite(pos)] = 0.0
    return {"positions": pos, "ic_series": np.full(max(len(ts) - 8, 0), 0.01)}
'''

def parse_text(text: str) -> dict:
    """The single JSON document a warden CLI stage prints (verify uses indent=2)."""
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except ValueError:
        return json.loads(stripped.splitlines()[0])


def _pristine(db: Path, backup: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    db.write_bytes(backup.read_bytes())
    for p in (db.parent / "returns").glob("**/*"):
        if p.is_file():
            p.unlink()


def _cli_stage(stage: str, spec: Path) -> tuple[int, str]:
    handlers = {"verify": cmd_verify, "screen": cmd_screen, "backtest": cmd_backtest}
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = handlers[stage](argparse.Namespace(spec_yaml=str(spec), json=True), runner=S.ExperimentRunner())
    return code, out.getvalue()


def test_tiny_repo_session_matches_cli_handlers_stage_by_stage(tiny_repo, tmp_path: Path,
                                                              monkeypatch) -> None:
    case = tiny_repo(module_source=TINY_MODULE, module_name="tiny_momentum")
    repo = case.repo
    (repo / "registry").mkdir(exist_ok=True)
    db = repo / "registry" / "registry.db"
    with sqlite3.connect(case.registry.db_path) as src, sqlite3.connect(db) as dst:
        src.backup(dst)
    generate_manifest(repo)
    backup = tmp_path / "pristine.db"
    backup.write_bytes(db.read_bytes())

    # session (one process, backtest forced so a rejecting screen still exercises Stage 2/3)
    _pristine(db, backup)
    monkeypatch.chdir(repo)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        session_code = S.main([str(case.spec), "--json", "--force-all"])
    envelope = json.loads(out.getvalue().strip().splitlines()[-1])
    assert session_code == envelope["exit"]
    session_text = {s["stage"]: s["payload"] for s in envelope["stages"]}

    # the same three handlers, run in the same order from the same pristine state (as the
    # pre-fix pipeline did with three separate processes)
    _pristine(db, backup)
    cli_text, cli_codes = {}, {}
    for stage in S.STAGES:
        cli_codes[stage], cli_text[stage] = _cli_stage(stage, case.spec)

    assert [s["stage"] for s in envelope["stages"]] == list(S.STAGES)
    assert [s["exit"] for s in envelope["stages"]] == [cli_codes[s] for s in S.STAGES]
    assert envelope["screen_passed"] == session_text["screen"].get("passed")
    assert envelope["verdict"] == session_text["backtest"].get("verdict")
    for stage in S.STAGES:
        assert session_text[stage] == parse_text(cli_text[stage])
