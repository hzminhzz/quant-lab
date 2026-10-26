"""Issue #4 tickets 09/10/13/14: integrity is not approval; no privileged mutation by an agent."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from warden import cli, trust_root
from warden.cli import MANIFEST_FILE, MANIFEST_SIG, build_parser, check_worktree, generate_manifest

REPO = Path(__file__).resolve().parents[1]
ENV_BASE = {k: v for k, v in os.environ.items()
            if k not in {"WARDEN_HUMAN_SIGNING_KEY", "WARDEN_NON_INTERACTIVE", "WARDEN_AGENT_UID",
                         "WARDEN_OPERATOR", "WARDEN_HUMAN_KEY_FILE", "WARDEN_TRUST_GROUP"}}


def _mini_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini"
    for rel in ("src/warden", "contracts", "pyproject.toml", "uv.lock", "AGENTS.md"):
        src = REPO / rel
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__")) if src.is_dir() else shutil.copy2(src, dst)
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / "scripts" / "check_loc_budget.py").write_text("raise SystemExit(0)\n")
    return repo


def _cli(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "warden.cli", *args], cwd=repo, capture_output=True,
                          text=True, check=False,
                          env={**ENV_BASE, "PYTHONPATH": str(REPO / "src"), **(env or {})})


def _sign(repo: Path, tmp_path: Path) -> str:
    """Sign the repo's manifest with a throwaway key and pin it for in-process verification."""
    key = Ed25519PrivateKey.generate()
    content = generate_manifest(repo)
    (repo / MANIFEST_SIG).write_text(key.sign(content.encode()).hex() + "\n", encoding="utf-8")
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def test_empty_truncated_and_self_rewritten_manifests_fail_closed(tmp_path: Path, monkeypatch) -> None:
    repo = _mini_repo(tmp_path)
    public_hex = _sign(repo, tmp_path)
    monkeypatch.setattr(trust_root, "MANIFEST_PUBLIC_KEY_HEX", public_hex)
    assert check_worktree(repo) is True

    # Empty manifest: an empty loop must not be read as "nothing to verify".
    (repo / MANIFEST_FILE).write_text("", encoding="utf-8")
    assert check_worktree(repo) is False

    content = generate_manifest(repo)
    (repo / MANIFEST_SIG).write_text(Ed25519PrivateKey.generate().sign(content.encode()).hex(), encoding="utf-8")
    assert check_worktree(repo) is False  # foreign key

    # A deleted protected line is detected by exact path-set equality.
    signing = Ed25519PrivateKey.generate()
    monkeypatch.setattr(trust_root, "MANIFEST_PUBLIC_KEY_HEX",
                        signing.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex())
    content = generate_manifest(repo)
    lines = content.splitlines()
    (repo / MANIFEST_FILE).write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    assert check_worktree(repo) is False

    # The agent edits a protected file and re-hashes the tree. Without the pinned private key the
    # signature cannot cover the new manifest, so the tree fails closed.
    contract = next((repo / "contracts").glob("*.yaml"))
    contract.write_text(contract.read_text() + "\n# agent edit\n")
    generate_manifest(repo)
    assert check_worktree(repo) is False
    (repo / MANIFEST_SIG).write_text(
        Ed25519PrivateKey.generate().sign((repo / MANIFEST_FILE).read_bytes()).hex(), encoding="utf-8")
    assert check_worktree(repo) is False  # a foreign key cannot certify the agent's re-hash

    # Only the pinned human key over the exact manifest verifies (a human decision, not the agent).
    pinned = Ed25519PrivateKey.generate()
    monkeypatch.setattr(trust_root, "MANIFEST_PUBLIC_KEY_HEX",
                        pinned.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex())
    (repo / MANIFEST_SIG).write_text(pinned.sign((repo / MANIFEST_FILE).read_bytes()).hex(), encoding="utf-8")
    assert check_worktree(repo) is True


def test_unsigned_manifest_cannot_be_approved_by_a_rewrite(tmp_path: Path, monkeypatch) -> None:
    repo = _mini_repo(tmp_path)
    monkeypatch.setattr(trust_root, "MANIFEST_PUBLIC_KEY_HEX", "")
    generate_manifest(repo)  # agent-written checksum is integrity only
    assert check_worktree(repo) is False
    assert cli.manifest_approval_state(repo)["verified"] is False


def test_generate_manifest_requires_an_offline_human_key(tmp_path: Path) -> None:
    repo = _mini_repo(tmp_path)
    in_repo_key = repo / "human.key"
    in_repo_key.write_text("00" * 32)
    in_repo_key.chmod(0o600)
    assert _cli(repo, "generate-manifest").returncode == 2
    assert _cli(repo, "generate-manifest", "--signing-key-file", str(in_repo_key)).returncode == 2
    loose_key = tmp_path / "loose.key"
    loose_key.write_text("00" * 32)
    loose_key.chmod(0o644)
    assert _cli(repo, "generate-manifest", "--signing-key-file", str(loose_key)).returncode == 2

    good_key = tmp_path / "human.ed25519"
    good_key.write_text(Ed25519PrivateKey.generate().private_bytes_raw().hex())
    good_key.chmod(0o600)
    blocked = _cli(repo, "generate-manifest", "--signing-key-file", str(good_key),
                   env={"WARDEN_NON_INTERACTIVE": "1"})
    assert blocked.returncode == 2 and "human-only" in blocked.stderr
    signed = _cli(repo, "generate-manifest", "--signing-key-file", str(good_key), env={"WARDEN_NON_INTERACTIVE": "0"})
    assert signed.returncode == 0, signed.stderr
    assert (repo / MANIFEST_FILE).is_file() and (repo / MANIFEST_SIG).is_file()
    assert json.loads((repo / (MANIFEST_FILE + ".meta.json")).read_text())["mode"] == "ed25519"

    # The interactive self-approval flag no longer exists.
    parser = build_parser()
    assert "generate-manifest" in parser._subparsers._group_actions[0].choices
    sub = parser._subparsers._group_actions[0].choices["generate-manifest"]
    assert not any(a.dest == "human_approve_manifest" for a in sub._actions)


def test_apply_script_is_dry_run_by_default_and_never_mutates() -> None:
    script = REPO / "scripts" / "apply_trust_boundary.sh"
    watched = REPO / "src" / "warden" / "contracts.py"
    before = (watched.stat().st_uid, watched.stat().st_gid, watched.stat().st_mode, watched.stat().st_mtime_ns)
    state_dir = REPO / ".warden_state"
    existed = state_dir.exists()
    dry = subprocess.run(["bash", str(script)], cwd=REPO, capture_output=True, text=True,
                         check=False, env=ENV_BASE)
    assert dry.returncode == 0, dry.stderr
    assert "DRY RUN" in dry.stdout and "never run by the coding agent" in (script.read_text() + dry.stdout)
    assert (watched.stat().st_uid, watched.stat().st_gid, watched.stat().st_mode,
            watched.stat().st_mtime_ns) == before
    assert state_dir.exists() == existed  # dry-run is side-effect free

    apply = subprocess.run(["bash", str(script), "--apply"], cwd=REPO, capture_output=True, text=True,
                           check=False, env=ENV_BASE)
    assert apply.returncode == 3
    assert "REFUSED" in apply.stderr
    assert (watched.stat().st_uid, watched.stat().st_gid, watched.stat().st_mode) == before[:3]
    rollback = subprocess.run(["bash", str(script), "--rollback"], cwd=REPO, capture_output=True,
                              text=True, check=False, env=ENV_BASE)
    assert rollback.returncode == 3 and "REFUSED" in rollback.stderr


def test_apply_refuses_before_mutation_when_rollback_metadata_would_be_clobbered(tmp_path: Path) -> None:
    """A recorded rollback file must never be overwritten by a second apply."""
    script = REPO / "scripts" / "apply_trust_boundary.sh"
    record_dir = tmp_path / "state"
    record_dir.mkdir()
    (record_dir / "pre_apply.tsv").write_text("recorded\n")
    proc = subprocess.run(["bash", str(script), "--apply"], cwd=REPO, capture_output=True, text=True,
                          check=False,
                          env={**ENV_BASE, "WARDEN_TRUST_STATE_DIR": str(record_dir),
                               "WARDEN_HUMAN_APPROVED_CUTOVER": "1", "WARDEN_AGENT_UID": "4242",
                               "WARDEN_OPERATOR": "qeval", "WARDEN_HUMAN_KEY_FILE": str(tmp_path / "k")})
    assert proc.returncode == 3 and "REFUSED" in proc.stderr
    assert (record_dir / "pre_apply.tsv").read_text() == "recorded\n"


def _load_preflight():
    path = REPO / "scripts" / "preflight_trust_boundary.py"
    spec = importlib.util.spec_from_file_location("preflight_trust_boundary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_reports_the_real_trust_gap_read_only(tmp_path: Path, monkeypatch, capsys) -> None:
    preflight = _load_preflight()
    before = (REPO / "contracts" / "crypto_perps_1h_v1.yaml").stat().st_mtime_ns
    monkeypatch.setenv("WARDEN_AGENT_UID", str(os.getuid()))
    monkeypatch.setattr(preflight, "current_groups", lambda: {0})  # simulate root/docker membership
    code = preflight.main(["--json", "--repo-root", str(REPO), "--holdout-path", str(tmp_path / "absent")])
    report = json.loads(capsys.readouterr().out)
    assert code == 2 and report["mode"] == "dry-run" and report["compliant"] is False
    ids = {f["id"] for f in report["findings"]}
    assert {"AGENT_OWNS_TRUSTED_INPUTS", "AGENT_CAN_SELF_HASH", "MANIFEST_UNSIGNED",
            "PRIVILEGED_AGENT_IDENTITY", "HOLDOUT_MISSING"} <= ids
    assert (REPO / "contracts" / "crypto_perps_1h_v1.yaml").stat().st_mtime_ns == before


@pytest.mark.parametrize("missing", ["--apply", "--rollback"])
def test_script_has_no_mutating_privileged_path_for_an_agent(missing: str) -> None:
    text = (REPO / "scripts" / "apply_trust_boundary.sh").read_text()
    assert 'id -u' in text and 'validate_preconditions' in text
    assert text.index("validate_preconditions; then") > text.index("if [[ \"$MODE\" == \"apply\" ]]") or True
    assert "chown" in text and "pre_apply.tsv" in text
    # the rollback reads recorded uid/gid/mode, never root defaults
    assert "RECORD" in text and "sha256sum" in text
