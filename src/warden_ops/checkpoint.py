"""Read-only checkpoint generation and typed, no-shell replay for Warden operators.

This is a presentation/recovery adapter; scientific admissions stay in Warden.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from warden.audit import AUDIT_LOG
from warden.audit import fingerprint as audit_fingerprint
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner

STATE_FILE = Path(".workspace") / "state" / "current.json"
HISTORY_FILE = Path(".workspace") / "state" / "history.jsonl"
TRACE_OPS = frozenset({"git_head", "registry_count", "worktree_check"})
REGISTRY_TABLES = frozenset({"trials", "hypotheses"})
LEGACY_TRACE = {
    "git rev-parse --short HEAD": {"op": "git_head"},
    'sqlite3 registry/registry.db "SELECT COUNT(*) FROM trials"': {"op": "registry_count", "table": "trials"},
    'sqlite3 registry/registry.db "SELECT COUNT(*) FROM hypotheses"': {"op": "registry_count", "table": "hypotheses"},
    "uv run warden check-worktree": {"op": "worktree_check"},
}


def _contract_pins(repo_root: Path) -> dict[str, str]:
    import yaml
    pins = {}
    for p in sorted((repo_root / "contracts").glob("*.y*ml")):
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        pins[doc["contract_id"]] = doc.get("data_snapshot_sha256", "")
    return pins


def _build_state(repo_root: Path, ticket: str | None, notes: str) -> dict:
    """Typed, replayable checkpoint: evidence with timestamps, tool trace, explicit gates."""
    from warden.cli import WORKTREE_OK, check_worktree
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=repo_root, text=True).strip()
    registry = ResearchRegistry(repo_root / "registry" / "registry.db")
    with registry._connect() as conn:
        n_trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        n_hyp = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
    pins = _contract_pins(repo_root)
    with registry._connect() as conn:
        candidates = [r[0] for r in conn.execute(
            "SELECT run_id FROM trials WHERE verdict='CANDIDATE_FOR_QUALIFICATION'")]
    eligibility = {r: registry.effective_eligibility(r) for r in candidates}
    trace = [
        {"op": "git_head", "expect": head},
        {"op": "registry_count", "table": "trials", "expect": str(n_trials)},
        {"op": "worktree_check", "expect": WORKTREE_OK},
    ]
    return {
        "schema": 1, "created_at": now, "ticket": ticket or "N/A", "notes": notes,
        "evidence": [
            {"source": "git", "kind": "head", "value": head, "observed_at": now},
            {"source": "registry.db", "kind": "trials", "value": n_trials, "observed_at": now},
            {"source": "registry.db", "kind": "hypotheses", "value": n_hyp, "observed_at": now},
            {"source": "contracts", "kind": "pinned_snapshot_sha256", "value": pins, "observed_at": now},
        {"source": "registry.db", "kind": "effective_eligibility",
         "value": {r: {"eligible": e["eligible"], "reason": e["reason"], "provenance": e["data_provenance"]}
                   for r, e in eligibility.items()}, "observed_at": now},
            {"source": "audit", "kind": "toolcalls", "value": audit_fingerprint(repo_root / AUDIT_LOG), "observed_at": now},
        ],
        "tool_trace": trace,
        "quality_gates": {"worktree_integrity": check_worktree(repo_root), "contracts_pinned": all(pins.values()),
                          "eligibility_surface_reachable": all("reason" in e for e in eligibility.values())},
    }


def _checkpoint_id(state: dict) -> str:
    payload = {k: v for k, v in state.items() if k != "checkpoint_id"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def cmd_handoff(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    state = _build_state(runner.repo_root, args.ticket, args.notes)
    state["checkpoint_id"] = _checkpoint_id(state)
    current = runner.repo_root / STATE_FILE
    current.parent.mkdir(parents=True, exist_ok=True)
    # Append+fsync history *first*; atomic current-ptr publication comes last.
    # A crash between steps is detected by continue, never silently blended.
    with (runner.repo_root / HISTORY_FILE).open("a", encoding="utf-8") as history:
        history.write(json.dumps(state, sort_keys=True) + "\n")
        history.flush()
        os.fsync(history.fileno())
    with tempfile.NamedTemporaryFile("w", dir=current.parent, suffix=".pending", delete=False,
                                     encoding="utf-8") as handle:
        temporary = Path(handle.name)
        try:
            handle.write(json.dumps(state, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, current)
    dfd = os.open(current.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    print(f"Handoff checkpoint recorded: {STATE_FILE} (append-only log: {HISTORY_FILE})")
    print(f"Gates: {json.dumps(state['quality_gates'])}")
    return 0


def _legacy_trace(text: str) -> list[dict]:
    """Adapter for pre-schema-1 Markdown transitions: map known literals to typed ops."""
    block = (re.findall(r"```bash\n(.*?)\n```", text, re.DOTALL) or [""])[0].splitlines()
    entries, i = [], 0
    while i < len(block):
        cmd = block[i].strip()
        i += 1
        if not cmd or cmd.startswith("#"):
            continue
        expect = block[i].strip()[9:].strip() if i < len(block) and block[i].strip().startswith("# expect:") else None
        if expect is not None:
            i += 1
        entries.append({**LEGACY_TRACE.get(cmd, {"op": None, "raw": cmd}), "expect": expect})
    return entries


def _replay(entries: list[dict], runner: ExperimentRunner) -> bool:
    """Re-execute typed trace ops. SQL text is never executed, so writefile() is unreachable."""
    from warden.cli import WORKTREE_OK, check_worktree
    drift = False
    for entry in entries:
        op, expected = entry.get("op"), entry.get("expect")
        if op not in TRACE_OPS:
            shown = entry.get("raw") or op
            print(f"REFUSED: trace op '{shown}' is not in the typed allowlist.", file=sys.stderr)
            drift = True
            continue
        if op == "git_head":
            actual = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=runner.repo_root, text=True).strip()
        elif op == "registry_count":
            table = entry.get("table")
            if table not in REGISTRY_TABLES:
                print(f"REFUSED: registry table '{table}' is not allowlisted.", file=sys.stderr)
                drift = True
                continue
            with runner.registry._connect() as conn:
                actual = str(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        else:
            actual = WORKTREE_OK if check_worktree(runner.repo_root) else "worktree integrity FAILED"
        if expected and str(actual) != str(expected):
            hint = ""
            if op == "registry_count" and str(actual).isdigit() and str(expected).isdigit() and int(actual) > int(expected):
                hint = " (state advanced; regenerate with `uv run warden handoff` if legitimate)"
            print(f"DRIFT: {op} expected '{expected}', got '{actual}'{hint}", file=sys.stderr)
            drift = True
    return drift


def cmd_continue(args: argparse.Namespace, runner: ExperimentRunner) -> int:
    from warden.cli import envelope
    if args.transition_path:
        target = Path(args.transition_path)
    elif (runner.repo_root / STATE_FILE).is_file():
        target = runner.repo_root / STATE_FILE
    else:
        legacy = sorted(runner.repo_root.glob(".workspace/transitions/*/*.md"))
        target = legacy[-1] if legacy else None
    if not target or not target.is_file():
        print("No handoff checkpoint found.", file=sys.stderr)
        return 1
    history_drift = False
    if target.suffix == ".json":
        state = json.loads(target.read_text(encoding="utf-8"))
        if target.resolve() == (runner.repo_root / STATE_FILE).resolve():
            history = runner.repo_root / HISTORY_FILE
            try:
                last = json.loads(history.read_text(encoding="utf-8").splitlines()[-1])
                history_drift = (state != last or (state.get("checkpoint_id") is not None and
                                 state["checkpoint_id"] != _checkpoint_id(state)))
            except (OSError, IndexError, ValueError):
                history_drift = True
            if history_drift:
                print("DRIFT: checkpoint/history mismatch or interrupted publication", file=sys.stderr)
        if state.get("schema") != 1:
            print(f"REFUSED: unsupported state schema {state.get('schema')!r}", file=sys.stderr)
            return 1
        trace = [(LEGACY_TRACE.get(e["cmd"], e) if "op" not in e and "cmd" in e else e) for e in state.get("tool_trace", [])]
        trace = [dict(t) for t in trace]
        drift = _replay(trace, runner) or history_drift
        failed = sorted(k for k, v in state.get("quality_gates", {}).items() if not v)
        if failed:
            print(f"DRIFT: recorded quality gates failed at handoff: {failed}", file=sys.stderr)
            drift = True
    else:
        drift = _replay(_legacy_trace(target.read_text(encoding="utf-8")), runner)
    status = "FAILED" if drift else "SUCCESS"
    if getattr(args, "json", False):
        print(json.dumps(envelope(not drift, {"drift": drift, "checkpoint": target.name,
                                         "evidence_source": "live-replayed" if not drift else "cached-review-required",
                                         "history_consistent": not history_drift}, f"warden.continue.{status}")))
    else:
        print(f"Continue {status}: {'Drift detected.' if drift else f'zero drift from {target.name}.'}")
    return 1 if drift else 0
