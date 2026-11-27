"""AC-04: immutable build identity is distinct from runtime authorization."""
import json
import subprocess

import pytest

from warden_ops.release import build_candidate, verify_candidate


def source(cli_repo):
    (cli_repo / "src/warden").mkdir(parents=True)
    (cli_repo / "src/warden/example.py").write_text("VALUE = 1\n")
    (cli_repo / "uv.lock").write_text("version = 1\n")
    (cli_repo / "pyproject.toml").write_text('[project]\nname = "fixture"\n')
    subprocess.run(["git", "add", "src", "uv.lock", "pyproject.toml"], cwd=cli_repo, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid",
                    "-c", "commit.gpgSign=false", "commit", "-qm", "release fixture"],
                   cwd=cli_repo, check=True)
    return cli_repo


def test_build_is_reproducible_and_unsigned_despite_stale_development_manifest(cli_repo, tmp_path):
    root = source(cli_repo)
    runtime = tmp_path / "active-runtime"
    runtime.write_text("separately-pinned")
    before = runtime.read_bytes()
    candidate = build_candidate(root, tmp_path / "builds", ledger_min=9, ledger_max=9)
    assert candidate["authorized"] is False
    assert build_candidate(root, tmp_path / "builds", ledger_min=9, ledger_max=9) == candidate
    assert verify_candidate(candidate["path"])["valid"]
    assert runtime.read_bytes() == before
    (root / "src/warden/example.py").write_text("VALUE = 2\n")
    different = build_candidate(root, tmp_path / "builds", ledger_min=9, ledger_max=9)
    assert different["release_id"] != candidate["release_id"]
    assert different["source"]["dirty"] is True
    assert verify_candidate(candidate["path"])["valid"]


def test_changed_candidate_bytes_or_manifest_cannot_verify(cli_repo, tmp_path):
    candidate = build_candidate(source(cli_repo), tmp_path / "builds", ledger_min=9, ledger_max=9)
    from pathlib import Path
    artifact = Path(candidate["path"])
    path = artifact / "src/warden/example.py"
    path.chmod(0o600)
    path.write_text("VALUE = 999\n")
    assert not verify_candidate(artifact)["valid"]
    manifest = artifact / "candidate.json"
    manifest.chmod(0o600)
    metadata = json.loads(manifest.read_text())
    metadata["ledger_max"] = 1
    manifest.write_text(json.dumps(metadata))
    assert not verify_candidate(artifact)["valid"]


def test_build_refuses_tracked_symlinks_before_reading_targets(cli_repo, tmp_path):
    root = source(cli_repo)
    target = tmp_path / "do-not-open"
    (root / "src/escape.py").symlink_to(target)
    subprocess.run(["git", "add", "src/escape.py"], cwd=root, check=True)
    with pytest.raises(ValueError, match="symlink"):
        build_candidate(root, tmp_path / "builds", ledger_min=9, ledger_max=9)
