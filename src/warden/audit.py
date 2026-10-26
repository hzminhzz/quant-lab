"""Append-only audit log: one line per warden invocation with its policy decision."""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

from warden.policy import Decision

AUDIT_LOG = Path(".workspace") / "audit" / "toolcalls.jsonl"
AUDIT_MAX_BYTES = 10 * 1024 * 1024


def fingerprint(path: Path) -> dict:
    """Content hash + line count, so a tracked checkpoint can detect log loss or tampering."""
    if not path.is_file():
        return {"lines": 0, "sha256": ""}
    data = path.read_bytes()
    return {"lines": data.count(b"\n"), "sha256": hashlib.sha256(data).hexdigest()}


def record(
    repo_root: Path,
    cmd: str | None,
    argv: list[str],
    decision: Decision,
    exit_code: int,
    duration_ms: float,
    result_bytes: int,
) -> Path:
    path = Path(repo_root) / AUDIT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size >= AUDIT_MAX_BYTES:
        path.replace(path.with_suffix(".jsonl.1"))
    entry = {
        "cmd": cmd,
        "argv": list(argv),
        "allow": bool(decision.allow),
        "policy": decision.reason,
        "approval_required": bool(decision.approval_required),
        "observed_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "exit_code": int(exit_code),
        "duration_ms": round(float(duration_ms), 1),
        "result_bytes": int(result_bytes),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return path
