#!/usr/bin/env python3
"""Read-only trust-boundary preflight (issue-4 tickets 09/13).

Assesses whether the coding-agent identity can modify trusted Warden source,
contracts, dataset manifests or the integrity manifest, and whether it could
self-sign them. It never changes ownership, permissions, holdout state or
credentials; read-only is the only supported mode.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
from pathlib import Path

MANIFEST_FILE = ".warden_manifest.sha256"
PROTECTED_GLOBS = ("src/warden/*.py", "contracts/*.yaml", "contracts/datasets/*.json")
TRUST_FILES = (MANIFEST_FILE, "scripts/check_loc_budget.py", "pyproject.toml", "uv.lock")
PRIVILEGED_GROUPS = ("sudo", "docker", "wheel", "root")
DEFAULT_HOLDOUT = "/data/holdout"


def pinned_public_key(repo: Path) -> str:
    """Read the trust root without importing the package; empty means nothing verifies."""
    source = repo / "src" / "warden" / "trust_root.py"
    try:
        match = re.search(r'^MANIFEST_PUBLIC_KEY_HEX\s*=\s*"([0-9a-fA-F]*)"',
                          source.read_text(encoding="utf-8"), re.MULTILINE)
    except OSError:
        return ""
    return match.group(1) if match else ""


def manifest_approval_state(repo: Path) -> dict:
    manifest, sig = repo / MANIFEST_FILE, repo / (MANIFEST_FILE + ".sig")
    meta = repo / (MANIFEST_FILE + ".meta.json")
    state = {"signed": False, "verified": False, "mode": "unsigned", "approver": ""}
    if meta.is_file():
        state.update({k: v for k, v in json.loads(meta.read_text(encoding="utf-8")).items()
                      if k in {"mode", "approver", "approved_at"}})
    if not manifest.is_file() or not sig.is_file():
        return state
    state["signed"] = True
    key_hex = pinned_public_key(repo)
    if not key_hex:
        state["verified"] = False
        return state
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex)).verify(
            bytes.fromhex(sig.read_text(encoding="utf-8").strip()), manifest.read_bytes())
        state["verified"] = True
    except Exception:  # invalid signature, key, dependency: fail closed
        state["verified"] = False
    return state


def describe(path: Path) -> dict:
    st = path.stat()
    return {"path": str(path), "uid": st.st_uid, "gid": st.st_gid, "mode": oct(st.st_mode & 0o777),
            "agent_owned": st.st_uid == os.getuid(), "agent_writable": os.access(path, os.W_OK)}


def current_groups() -> set[str]:
    try:
        return set(os.getgroups())
    except OSError:
        return set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="the only supported mode")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--agent-uid", type=int, default=None,
                        help="declared unprivileged agent uid (declaration, never proof)")
    parser.add_argument("--operator", default=os.environ.get("WARDEN_OPERATOR", ""))
    parser.add_argument("--holdout-path", type=Path,
                        default=Path(os.environ.get("WARDEN_HOLDOUT_PATH", DEFAULT_HOLDOUT)))
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()

    protected = [p for pattern in PROTECTED_GLOBS for p in sorted(repo.glob(pattern))]
    protected += [repo / name for name in TRUST_FILES if (repo / name).is_file()]
    files = [describe(p) for p in protected]
    approval = manifest_approval_state(repo)
    holdout = args.holdout_path
    budget = repo / "scripts" / "check_loc_budget.py"
    declared_uid = args.agent_uid if args.agent_uid is not None else (
        int(os.environ["WARDEN_AGENT_UID"]) if os.environ.get("WARDEN_AGENT_UID", "").isdigit() else None)

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
                         "detail": "the manifest signature does not verify against the pinned Ed25519 key"})
    privileges = set()
    if os.getuid() == 0:
        privileges.add("uid0")
    try:
        import grp

        for gid in current_groups():
            name = grp.getgrgid(gid).gr_name
            if name in PRIVILEGED_GROUPS:
                privileges.add(name)
    except (KeyError, ImportError):
        pass
    if args.operator and str(os.getuid()) == str(args.operator):
        privileges.add("operator-uid")
    if privileges:
        findings.append({"id": "PRIVILEGED_AGENT_IDENTITY", "severity": "high",
                         "detail": f"the running identity holds {sorted(privileges)}; docker/sudo/root "
                                   "membership is root-equivalent and cannot be the trust boundary"})
    if declared_uid is None:
        findings.append({"id": "AGENT_IDENTITY_UNDECLARED", "severity": "high",
                         "detail": "no unprivileged agent uid is declared (--agent-uid or WARDEN_AGENT_UID)"})
    elif declared_uid == os.getuid():
        findings.append({"id": "AGENT_IDENTITY_IS_CURRENT", "severity": "info",
                         "detail": "the current identity is the declared agent identity"})
    if not holdout.is_dir():
        findings.append({"id": "HOLDOUT_MISSING", "severity": "high",
                         "detail": f"holdout isolation path '{holdout}' does not exist"})
    elif os.access(holdout, os.R_OK | os.X_OK):
        findings.append({"id": "HOLDOUT_AGENT_ACCESS", "severity": "high",
                         "detail": f"agent can traverse/list {holdout}"})
    else:
        findings.append({"id": "HOLDOUT_ISOLATED", "severity": "info",
                         "detail": f"agent cannot traverse/list {holdout}"})
    if agent_owned and any("check_loc_budget" in p for p in agent_owned):
        findings.append({"id": "AGENT_OWNS_BUDGET_CONTROL", "severity": "high",
                         "detail": f"LOC budget control is agent-writable (current cap {_budget(budget)}) "
                                   "and was raised during the issue-4 rollout; the operator must ratify it"})

    report = {"mode": "dry-run", "repo": str(repo), "identity": {
        "user": getpass.getuser(), "uid": os.getuid(), "gid": os.getgid(),
        "declared_agent_uid": declared_uid, "operator": args.operator or None},
        "holdout_path": str(holdout), "files": files, "manifest_approval": approval,
        "findings": findings, "compliant": not any(f["severity"] == "high" for f in findings),
        "note": "Read-only assessment. apply_trust_boundary.sh performs any change; it requires "
                "explicit operator authorization and is never run by the coding agent. The current "
                "privileged identity is not a boundary: see docs/TRUST_BOUNDARY.md."}
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
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("WARDEN_MAX_LOC"):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
