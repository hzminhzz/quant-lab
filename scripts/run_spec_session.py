"""Run one experiment spec's hourly funnel — verify, screen, backtest — in ONE process.

The CLI funnel (`warden verify`, `warden screen`, `warden backtest`) paid a fresh
`uv run`, a fresh interpreter and a fresh `warden` import (~1.7 s) for every stage, on
top of re-reading the same registry and re-hashing the same worktree. This session keeps
the governance and audit surface identical by construction:

* every stage still goes through ``warden.policy.authorize`` and is refused (exit 2)
  when policy denies it;
* every stage still calls the ``warden.cli`` command handler, which performs its own
  ``check_worktree`` integrity check and fails closed with exit 2;
* every stage still appends one ``.workspace/audit/toolcalls.jsonl`` entry with its own
  command name, argv, policy decision, exit code, duration and emitted-byte count;
* stage output, exit codes and fail-fast order match the CLI byte for byte (verify ->
  screen -> backtest, halting at the first non-zero code unless ``--force-all``).

Only the process, interpreter and ``ExperimentRunner`` instance are shared.

Usage:
    python -m scripts.run_spec_session experiments/queue/spec.yaml --json
    python -m scripts.run_spec_session spec.yaml --summary-out /tmp/session.json

Exit codes: 0 all attempted stages succeeded (a REVISE/REJECT verdict is a scientific
result, not an error, exactly as with ``warden backtest``), otherwise the failing stage's
own exit code (1 = stage rejection, 2 = policy/worktree refusal).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from pathlib import Path

from warden.audit import record as audit_record
from warden.cli import cmd_backtest, cmd_screen, cmd_verify
from warden.lease import MutationBusy, mutation_lease
from warden.policy import authorize
from warden.runner import ExperimentRunner

STAGES = ("verify", "screen", "backtest")
HANDLERS = {"verify": cmd_verify, "screen": cmd_screen, "backtest": cmd_backtest}
AUDIT_MARKER = "--via=run_spec_session"


def _payload(text: str) -> dict:
    """Whatever JSON a stage printed, as a dict (the CLI prints one document per stage)."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            return data.get("value", data) if isinstance(data, dict) else {}
        except ValueError:
            pass
    for line in stripped.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            return data.get("value", data) if isinstance(data, dict) else {}
    return {}


def run_stage(stage: str, spec: Path, runner: ExperimentRunner, json_mode: bool,
              echo: bool) -> dict:
    """One CLI-equivalent stage: policy -> handler (own worktree check) -> audit entry."""
    decision = authorize(stage)
    argv = [AUDIT_MARKER, stage, str(spec)] + (["--json"] if json_mode else [])
    buffer = io.StringIO()
    started = time.perf_counter()
    if not decision.allow:
        code = 2
        print(f"REFUSED: {decision.reason}", file=sys.stderr)
    elif decision.approval_required:
        code = 2
        print(f"REFUSED: {decision.reason}; a session may not self-approve a human-only gate",
              file=sys.stderr)
    else:
        args = argparse.Namespace(spec_yaml=str(spec), json=json_mode)
        with contextlib.redirect_stdout(buffer):
            try:
                code = int(HANDLERS[stage](args, runner) or 0)
            except SystemExit as exc:
                code = 0 if exc.code is None else int(exc.code or 0)
    text = buffer.getvalue()
    duration_ms = (time.perf_counter() - started) * 1000.0
    if echo and text:
        sys.stdout.write(text)
        sys.stdout.flush()
    try:
        audit_record(Path("."), stage, argv, decision, code, duration_ms,
                     len(text.encode("utf-8", "replace")))
    except OSError as exc:  # observability never decides whether the command worked
        print(f"WARN: audit log unavailable ({exc}); command result unchanged", file=sys.stderr)
    payload = _payload(text)
    return {"stage": stage, "exit": code, "ok": code == 0,
            "wall_ms": round(duration_ms, 1), "payload": payload}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_spec_session",
        description="Run verify+screen+backtest for one spec in a single process.")
    parser.add_argument("spec_yaml")
    parser.add_argument("--json", action="store_true", help="print only the session envelope")
    parser.add_argument("--summary-out", default=None,
                        help="also write the session envelope JSON to this path")
    parser.add_argument("--force-all", action="store_true",
                        help="attempt backtest dispatch after screen failure for diagnostics; "
                             "does not bypass registry admission")
    args = parser.parse_args(argv)

    spec = Path(args.spec_yaml)
    # The CLI resolves governance state from the working directory; refuse loudly rather
    # than audit against an unintended repository.
    if not (Path("registry") / "registry.db").is_file() or not Path(".warden_manifest.sha256").is_file():
        print(f"REFUSED: {Path.cwd()} is not a warden repo root (registry/registry.db and "
              f".warden_manifest.sha256 are required)", file=sys.stderr)
        return 2
    if not spec.is_file():
        print(f"REFUSED: spec not found: {spec}", file=sys.stderr)
        return 2

    # One session is one registry mutation: it must not interleave with the CLI,
    # the MCP adapter or another session. The handlers themselves never take the
    # lease (main() does), so holding it for the whole session stays non-reentrant.
    try:
        lease = mutation_lease(Path("registry") / "registry.db")
        lease.__enter__()
    except MutationBusy as exc:
        busy = {"ok": False, "busy": True, "status": "BUSY", "reason": str(exc),
                "spec": str(spec), "failed_stage": None, "exit": 75, "stages": []}
        if args.summary_out:
            Path(args.summary_out).write_text(json.dumps(busy) + "\n", encoding="utf-8")
        if args.json:
            print(json.dumps(busy))
        else:
            print(f"REFUSED: {exc}", file=sys.stderr)
        return 75

    try:
        runner = ExperimentRunner()
        started = time.perf_counter()
        stages: list[dict] = []
        failed_stage = None
        for stage in STAGES:
            if failed_stage is not None and not (args.force_all and failed_stage == "screen"):
                break
            result = run_stage(stage, spec, runner, args.json, echo=not args.json)
            stages.append(result)
            if not result["ok"]:
                failed_stage = stage
        session = {"ok": failed_stage is None, "spec": str(spec), "failed_stage": failed_stage,
                   "wall_ms": round((time.perf_counter() - started) * 1000.0, 1), "stages": stages}

        by_stage = {s["stage"]: s for s in stages}
        screen = by_stage.get("screen", {}).get("payload", {})
        backtest = by_stage.get("backtest", {}).get("payload", {})
        envelope = {"ok": session["ok"], "spec": str(spec), "failed_stage": failed_stage,
                    "screen_passed": screen.get("passed"), "run_id": backtest.get("run_id"),
                    "verdict": backtest.get("verdict"),
                    "exit": 0 if session["ok"] else by_stage[failed_stage]["exit"],
                    "wall_ms": session["wall_ms"], "stages": session["stages"]}

        if args.summary_out:
            Path(args.summary_out).write_text(json.dumps(envelope) + "\n", encoding="utf-8")
        if args.json:
            print(json.dumps(envelope))
        else:
            verdict = envelope["verdict"] or "n/a"
            print(f"Session: stages={','.join(s['stage'] for s in stages)} "
                  f"failed={failed_stage or 'none'} screen_passed={envelope['screen_passed']} "
                  f"verdict={verdict} wall={envelope['wall_ms'] / 1000.0:.2f}s")
        return envelope["exit"]
    finally:
        lease.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
