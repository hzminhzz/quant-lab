"""Warden integrity manifest, protected administrative modules, and approval checks."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

MANIFEST_FILE = ".warden_manifest.sha256"

def compute_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_manifest(repo_root: Path = Path(".")) -> str:
    repo = Path(repo_root)
    protected = (
        sorted(repo.glob("src/warden/**/*.py"))
        + sorted(repo.glob("src/warden_ops/**/*.py"))
        + sorted(repo.glob("src/warden_evidence/**/*.py"))
        + sorted(repo.glob("contracts/*.yaml"))
        + sorted(repo.glob("contracts/*.yml"))
        + sorted(repo.glob("contracts/datasets/*.json"))
    )
    protected += [
        p for p in (
            repo / "AGENTS.md",
            repo / "pyproject.toml",
            repo / "uv.lock",
            repo / "scripts" / "check_loc_budget.py",
            repo / "scripts" / "registry_evidence_snapshot.py",
            repo / "scripts" / "rehearse_state_machine_migration.py",
            repo / "scripts" / "mcp_warden" / "adapter.py",
            repo / "scripts" / "campaign_projection.py",
            repo / "scripts" / "run_carver_daily_campaign.py",
            repo / "scripts" / "research_outcome_memory.py",
            repo / "scripts" / "run_hourly_pipeline.sh",
            repo / "scripts" / "setup_agent_adapter.sh",
            repo / "scripts" / "lock_holdout_permissions.sh",
            repo / "scripts" / "lock_governance_permissions.sh",
            repo / "scripts" / "preflight_trust_boundary.py",
            repo / "scripts" / "apply_trust_boundary.sh",
        ) if p.is_file()
    ]
    protected = list(dict.fromkeys(protected))
    content = "\n".join(f"{compute_file_sha256(p)}  {p.relative_to(repo)}" for p in protected) + "\n"
    (repo / MANIFEST_FILE).write_text(content, encoding="utf-8")
    return content


def manifest_approval_state(repo_root: Path = Path(".")) -> dict:
    """Independent approval state. A self-written checksum is integrity, never authority."""
    repo = Path(repo_root)
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
        state["verified"] = None  # cannot verify without the human-held key
        return state
    digest = hmac.new(key.encode(), manifest.read_bytes(), hashlib.sha256).hexdigest()
    state["verified"] = hmac.compare_digest(digest, sig.read_text(encoding="utf-8").strip())
    return state


def check_worktree(repo_root: Path = Path(".")) -> bool:
    repo = Path(repo_root)
    manifest = repo / MANIFEST_FILE
    if not manifest.is_file():
        return False
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, rel = line.strip().split(maxsplit=1)
        target = repo / rel
        if not target.is_file() or compute_file_sha256(target) != expected:
            return False
    loc = repo / "scripts" / "check_loc_budget.py"
    if not loc.is_file():
        return False
    return subprocess.run([sys.executable, str(loc)], capture_output=True, check=False).returncode == 0
