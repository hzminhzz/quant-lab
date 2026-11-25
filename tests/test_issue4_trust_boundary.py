"""Issue #4 tickets 09/10: no autonomous self-certification; human-only cutover."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from warden.cli import MANIFEST_FILE, build_parser, manifest_approval_state

REPO = Path(__file__).resolve().parents[1]
ENV_BASE = {k: v for k, v in os.environ.items() if k not in {"WARDEN_HUMAN_SIGNING_KEY", "WARDEN_NON_INTERACTIVE"}}


def _mini_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini"
    for rel in ("src/warden", "contracts", "pyproject.toml", "uv.lock", "AGENTS.md"):
        src = REPO / rel
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
    return repo


def _cli(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "warden.cli", *args], cwd=repo, capture_output=True, text=True,
                          check=False, env={**ENV_BASE, "PYTHONPATH": str(REPO / "src"), **(env or {})})


def test_generate_manifest_parser_requires_approval_flag():
    parser = build_parser()
    sub = parser._subparsers._group_actions[0].choices["generate-manifest"]
    assert any(a.dest == "human_approve_manifest" for a in sub._actions)


def test_autonomous_manifest_signing_is_refused(tmp_path: Path) -> None:
    repo = _mini_repo(tmp_path)
    before = _cli(repo, "generate-manifest", "--json")
    manifest = repo / MANIFEST_FILE
    assert before.returncode == 2 and "REFUSED" in before.stderr
    assert not manifest.is_file()
    guarded = _cli(repo, "generate-manifest", "--human-approve-manifest",
                   env={"WARDEN_NON_INTERACTIVE": "1"})
    assert guarded.returncode == 2 and "REFUSED" in guarded.stderr
    assert not manifest.is_file()


def test_human_key_signs_and_a_foreign_key_cannot_certify(tmp_path: Path) -> None:
    repo = _mini_repo(tmp_path)
    signed = _cli(repo, "generate-manifest", "--json", env={"WARDEN_HUMAN_SIGNING_KEY": "human-held-key"})
    assert signed.returncode == 0, signed.stderr
    meta = json.loads((repo / (MANIFEST_FILE + ".meta.json")).read_text())
    assert meta["mode"] == "hmac" and meta["approver"] == "WARDEN_HUMAN_SIGNING_KEY"
    assert manifest_approval_state(repo)["verified"] is None  # no key in the agent environment

    preflight = REPO / "scripts" / "preflight_trust_boundary.py"
    as_human = subprocess.run([sys.executable, str(preflight), "--json"], cwd=repo, capture_output=True, text=True,
                              check=False, env={**ENV_BASE, "WARDEN_HUMAN_SIGNING_KEY": "human-held-key"})
    assert json.loads(as_human.stdout)["manifest_approval"]["verified"] is True
    as_agent = subprocess.run([sys.executable, str(preflight), "--json"], cwd=repo, capture_output=True, text=True,
                              check=False, env={**ENV_BASE, "WARDEN_HUMAN_SIGNING_KEY": "guessed-key"})
    assert json.loads(as_agent.stdout)["manifest_approval"]["verified"] is False

    # an agent that edits a protected file is caught by the integrity check ...
    contract = next((repo / "contracts").glob("*.yaml"))
    contract.write_text(contract.read_text() + "\n# agent edit\n")
    integrity = subprocess.run([sys.executable, "-m", "warden.cli", "check-worktree", "--json"], cwd=repo,
                               capture_output=True, text=True, check=False,
                               env={**ENV_BASE, "PYTHONPATH": str(REPO / "src")})
    assert integrity.returncode == 2
    # ... and an agent that re-writes the manifest to match cannot re-certify it
    manifest = repo / MANIFEST_FILE
    manifest.write_text(manifest.read_text() + "\n")
    tampered = subprocess.run([sys.executable, str(preflight), "--json"], cwd=repo, capture_output=True, text=True,
                              check=False, env={**ENV_BASE, "WARDEN_HUMAN_SIGNING_KEY": "human-held-key"})
    assert json.loads(tampered.stdout)["manifest_approval"]["verified"] is False


def test_interactive_human_approval_is_recorded(tmp_path: Path) -> None:
    repo = _mini_repo(tmp_path)
    approved = _cli(repo, "generate-manifest", "--human-approve-manifest", "--json")
    assert approved.returncode == 0, approved.stderr
    meta = json.loads((repo / (MANIFEST_FILE + ".meta.json")).read_text())
    assert meta["mode"] == "interactive" and meta["approver"] == "human-operator"
    assert manifest_approval_state(repo) == {**manifest_approval_state(repo), "signed": False, "mode": "interactive"}


def test_preflight_is_read_only_and_reports_the_agent_trust_gap() -> None:
    target = REPO / "contracts" / "crypto_perps_1h_v1.yaml"
    before = (target.stat().st_mtime_ns, target.stat().st_mode)
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "preflight_trust_boundary.py"), "--json"],
                          cwd=REPO, capture_output=True, text=True, check=False, env=ENV_BASE)
    assert proc.returncode == 2, proc.stderr
    report = json.loads(proc.stdout)
    assert report["mode"] == "dry-run" and report["compliant"] is False
    ids = {f["id"] for f in report["findings"]}
    assert {"AGENT_OWNS_TRUSTED_INPUTS", "AGENT_CAN_SELF_HASH", "MANIFEST_UNSIGNED"} <= ids
    assert (target.stat().st_mtime_ns, target.stat().st_mode) == before


def test_apply_script_defaults_to_dry_run_and_refuses_privileged_apply() -> None:
    script = REPO / "scripts" / "apply_trust_boundary.sh"
    dry = subprocess.run(["bash", str(script)], cwd=REPO, capture_output=True, text=True, check=False, env=ENV_BASE)
    assert dry.returncode == 0
    assert "DRY RUN: chown -R root:warden-trust src/warden" in dry.stdout
    assert "REFUSED" not in dry.stdout and "--apply" in dry.stdout
    mode = (REPO / "src" / "warden" / "contracts.py").stat().st_mode
    apply = subprocess.run(["bash", str(script), "--apply"], cwd=REPO, capture_output=True, text=True,
                           check=False, env=ENV_BASE)
    assert apply.returncode == 3 and "REFUSED: --apply requires root" in apply.stderr
    assert (REPO / "src" / "warden" / "contracts.py").stat().st_mode == mode


def _preflight_report() -> dict:
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "preflight_trust_boundary.py"), "--json"],
                          cwd=REPO, capture_output=True, text=True, check=False, env=ENV_BASE)
    assert proc.stdout, proc.stderr
    return json.loads(proc.stdout)


def test_preflight_enumerates_administrative_and_evidence_packages() -> None:
    report = _preflight_report()
    reported = {Path(f["path"]).resolve() for f in report["files"]}

    expected: set[Path] = set()
    for pattern in (
        "src/warden/**/*.py",
        "src/warden_ops/**/*.py",
        "src/warden_evidence/**/*.py",
        "contracts/*.yaml",
        "contracts/*.yml",
        "contracts/datasets/*.json",
    ):
        expected |= {p.resolve() for p in REPO.glob(pattern) if p.is_file()}

    assert (REPO / "src" / "warden_ops" / "integrity.py").resolve() in expected
    assert (REPO / "src" / "warden_ops" / "checkpoint.py").resolve() in expected
    assert (REPO / "src" / "warden_ops" / "readonly.py").resolve() in expected
    assert (REPO / "src" / "warden_evidence" / "market_validation.py").resolve() in expected
    assert (REPO / "src" / "warden_evidence" / "feature_cache.py").resolve() in expected
    # #106: a protected file outside the globbed directories must still be assessed here.
    assert (REPO / "contracts" / "lifecycle" / "contract_lifecycle.json").resolve() in reported
    assert expected <= reported, sorted(str(p) for p in expected - reported)

    # The preflight's protected view and the integrity manifest must not drift apart.
    assert "PROTECTED_SET_MISMATCH" not in {f["id"] for f in report["findings"]}


def _preflight_module():
    path = REPO / "scripts" / "preflight_trust_boundary.py"
    spec = importlib.util.spec_from_file_location("preflight_trust_boundary_under_test", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_protected_view_matches_the_authoritative_inventory() -> None:
    """The standalone preflight and ``warden_ops.integrity`` must assess the same paths.

    ``protected_manifest_entries`` is the single source of truth for the manifest, but the
    preflight deliberately imports no package code (it must run before the tree is trusted),
    so its globs are a second copy.  This pins the two copies together for every path the
    drift check compares, so a trusted input under a protected directory can no longer be
    added to only one side -- the failure mode that made a valid manifest unsatisfiable.
    """
    from warden_ops.integrity import protected_manifest_entries

    preflight = _preflight_module()
    assessed = {p.relative_to(REPO).as_posix() for p in preflight.protected_paths(REPO)}
    assessed = {rel for rel in assessed if rel.startswith(preflight.PROTECTED_DIR_PREFIXES)}
    inventory = {rel for rel in protected_manifest_entries(REPO)
                 if rel.startswith(preflight.PROTECTED_DIR_PREFIXES)}
    assert assessed == inventory, (
        f"assessed but not protected: {sorted(assessed - inventory)}; "
        f"protected but not assessed: {sorted(inventory - assessed)}")
    assert "contracts/lifecycle/contract_lifecycle.json" in assessed


def test_apply_script_protects_administrative_and_evidence_dirs() -> None:
    proc = subprocess.run(["bash", str(REPO / "scripts" / "apply_trust_boundary.sh")], cwd=REPO,
                          capture_output=True, text=True, check=False, env=ENV_BASE)
    assert proc.returncode == 0, proc.stderr
    for rel in ("src/warden", "src/warden_ops", "src/warden_evidence", "contracts", ".warden_manifest.sha256"):
        assert f"DRY RUN: chown -R root:warden-trust {rel}" in proc.stdout
        assert f"DRY RUN: chmod -R go-w {rel}" in proc.stdout
    assert "DRY RUN: chmod 0555 src/warden src/warden_ops src/warden_evidence contracts" in proc.stdout
    assert "DRY RUN: find src/warden src/warden_ops src/warden_evidence contracts -type f" in proc.stdout
