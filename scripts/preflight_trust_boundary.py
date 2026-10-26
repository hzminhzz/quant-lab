#!/usr/bin/env python3
"""Read-only trust-boundary preflight (issue-4 ticket 09).

Assesses whether the coding-agent identity can modify trusted Warden source,
contracts, dataset manifests or the integrity manifest, and whether it could
self-sign them. It never changes ownership, permissions, holdout state or
credentials; `--dry-run` is the only supported mode.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path

MANIFEST_FILE = ".warden_manifest.sha256"
PROTECTED_GLOBS = ("src/warden/*.py", "contracts/*.yaml", "contracts/datasets/*.json")
TRUST_FILES = (MANIFEST_FILE, "scripts/check_loc_budget.py", "pyproject.toml", "uv.lock")


def manifest_approval_state(repo: Path) -> dict:
    """Standalone copy of the warden approval check: no package imports, any identity."""
    manifest, sig = repo / MANIFEST_FILE, repo / (MANIFEST_FILE + ".sig")
    meta = repo / (MANIFEST_FILE + ".meta.json")
    state = {"signed": False, "verified": False, "mode": "unsigned", "approver": ""}
    if meta.is_file():
        state.update({k: v for k, v in json.loads(meta.read_text(encoding="utf-8")).items()
                      if k in {"mode", "approver", "approved_at"}})
    if not sig.is_file():
        return state
    state["signed"] = True
    key = os.environ.get("WARDEN_HUMAN_SIGNING_KEY", "")
    if not key:
        state["verified"] = None
        return state
    digest = hmac.new(key.encode(), manifest.read_bytes(), hashlib.sha256).hexdigest()
    state["verified"] = hmac.compare_digest(digest, sig.read_text(encoding="utf-8").strip())
    return state


def describe(path: Path) -> dict:
    st = path.stat()
    return {"path": str(path), "uid": st.st_uid, "gid": st.st_gid, "mode": oct(st.st_mode & 0o777),
            "agent_owned": st.st_uid == os.getuid(), "agent_writable": os.access(path, os.W_OK)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="the only supported mode")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()

    protected = [p for pattern in PROTECTED_GLOBS for p in sorted(repo.glob(pattern))]
    protected += [repo / name for name in TRUST_FILES if (repo / name).is_file()]
    files = [describe(p) for p in protected]
    approval = manifest_approval_state(repo)
    holdout = repo / "data" / "holdout"
    budget = repo / "scripts" / "check_loc_budget.py"

    findings: list[dict] = []
    agent_owned = [f["path"] for f in files if f["agent_owned"]]
    if agent_owned:
        findings.append({"id": "AGENT_OWNS_TRUSTED_INPUTS", "severity": "high",
                         "detail": f"{len(agent_owned)} trusted files are owned by the agent identity "
                                   f"({getpass.getuser()} uid={os.getuid()}); it can rewrite evaluator "
                                   f"logic or contracts and then re-hash them"})
    if any(f["agent_writable"] for f in files if MANIFEST_FILE in f["path"]):
        findings.append({"id": "AGENT_CAN_SELF_HASH", "severity": "high",
                         "detail": "the integrity manifest is writable by the agent; a self-generated "
                                   "checksum is integrity, not approval"})
    if not approval["signed"]:
        findings.append({"id": "MANIFEST_UNSIGNED", "severity": "high",
                         "detail": "no independent manifest signature / human approval record exists"})
    elif approval["verified"] is not True:
        findings.append({"id": "MANIFEST_SIGNATURE_UNVERIFIED", "severity": "high",
                         "detail": f"signature state {approval.get('verified')!r}; a key is required to verify"})
    if agent_owned and any("check_loc_budget" in p for p in agent_owned):
        findings.append({"id": "AGENT_OWNS_BUDGET_CONTROL", "severity": "medium",
                         "detail": f"LOC budget control is agent-writable (current cap {_budget(budget)}) "
                                   "and was raised during the issue-4 rollout; the operator must ratify it"})
    if not holdout.is_dir():
        findings.append({"id": "HOLDOUT_MISSING", "severity": "medium", "detail": "no holdout directory found"})
    else:
        readable = os.access(holdout, os.R_OK | os.X_OK)
        findings.append({"id": "HOLDOUT_AGENT_ACCESS", "severity": "high" if readable else "info",
                         "detail": f"agent {'can' if readable else 'cannot'} traverse/list {holdout}"})

    report = {"mode": "dry-run", "repo": str(repo), "identity": {
        "user": getpass.getuser(), "uid": os.getuid(), "gid": os.getgid()},
        "files": files, "manifest_approval": approval, "findings": findings,
        "compliant": not any(f["severity"] == "high" for f in findings),
        "note": "Read-only assessment. apply_trust_boundary.sh performs any change; it requires "
                "explicit operator authorization and is never run by the coding agent."}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Trust-boundary preflight ({report['identity']['user']} uid={report['identity']['uid']})")
        print(f"  protected files: {len(files)} | agent-owned: {len(agent_owned)}")
        print(f"  manifest approval: {approval}")
        for finding in findings:
            print(f"  [{finding['severity'].upper()}] {finding['id']}: {finding['detail']}")
        print(f"COMPLIANT={report['compliant']}")
    return 0 if report["compliant"] else 2


def _budget(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("WARDEN_MAX_LOC"):
            return line.split("=", 1)[1].strip()
    return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
