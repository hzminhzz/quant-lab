"""Unsigned, reproducible source candidates; never release authorization.

Candidate directories are write-once publications, not a hostile-agent security
boundary. The trusted appliance reverifies exact bytes before runtime activation.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from warden_ops.control_contracts import digest

ROOT_FILES = frozenset({"AGENTS.md", "CLAUDE.md", "README.md", "CONTEXT.md", "CONTEXT-MAP.md",
                        "AGENT_RUNBOOK.md", "CHANGELOG.md", "pyproject.toml", "uv.lock", ".gitignore",
                        ".warden_manifest.sha256", ".workspace/memory/LESSONS.md"})
SOURCE_DIRS = frozenset({"src", "scripts", "tests", "contracts", "strategies", "experiments",
                         "docs", ".github"})


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          timeout=30).stdout


def _allowed(name):
    path = Path(name)
    return (not path.is_absolute() and ".." not in path.parts and "\\" not in name
            and "\x00" not in name and "holdout" not in path.parts
            and not any(p in {".env", ".ssh", ".aws", "secrets"} for p in path.parts)
            and path.suffix.lower() not in {".key", ".pem", ".p12", ".pfx"}
            and (name in ROOT_FILES or (len(path.parts) > 1 and path.parts[0] in SOURCE_DIRS)))


def _source_paths(root):
    names = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
    paths = sorted({name for name in names if name and _allowed(name)})
    for name in paths:
        path = root / name
        # Do not follow a source symlink, including a symlinked parent.
        if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
            raise ValueError(f"source symlink refused: {name}")
        if not path.is_file():
            raise ValueError(f"source file missing or not regular: {name}")
    if not {"uv.lock", "pyproject.toml"}.issubset(paths):
        raise ValueError("locked dependency manifest required")
    return paths


def _hash(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _inventory(root, names):
    if any(not (root / name).is_file() for name in names):
        raise ValueError("candidate includes non-regular file")
    return {name: {"sha256": _hash(root / name),
                   "executable": bool((root / name).stat().st_mode & stat.S_IXUSR)}
            for name in names}


def build_candidate(root: Path, destination: Path, *, ledger_min: int, ledger_max: int,
                    rollback_release: str | None = None) -> dict:
    root, destination = Path(root).resolve(), Path(destination).absolute()
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError("candidate destination symlink refused")
    if destination.is_relative_to(root) or "holdout" in destination.parts:
        raise ValueError("candidate destination must be independent of source and sealed data")
    if not 1 <= ledger_min <= ledger_max:
        raise ValueError("invalid ledger compatibility range")
    names = _source_paths(root)
    inventory = _inventory(root, names)
    head = _git(root, "rev-parse", "HEAD").decode().strip()
    tree = _git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    patch = _git(root, "diff", "--binary", "HEAD", "--", *names)
    source = {"commit": head, "tree": tree, "patch_sha256": hashlib.sha256(patch).hexdigest(),
              "dirty": bool(_git(root, "status", "--porcelain", "--", *names))}
    manifest = {"schema_version": 1, "source": source, "inventory": inventory,
                "dependency_lock_sha256": inventory["uv.lock"]["sha256"],
                "ledger_min": ledger_min, "ledger_max": ledger_max,
                "rollback_release": rollback_release, "authorized": False,
                "verification": "not attached; trusted verification is a separate receipt"}
    release_id = digest(manifest)
    target = destination / release_id
    if target.exists():
        checked = verify_candidate(target)
        if not checked["valid"]:
            raise ValueError("existing candidate integrity drift")
        return {**manifest, "release_id": release_id, "path": str(target)}
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    pending = Path(tempfile.mkdtemp(prefix=".build-", dir=destination))
    try:
        for name in names:
            output = pending / name
            output.parent.mkdir(parents=True, exist_ok=True)
            with (root / name).open("rb") as src, output.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            if _hash(output) != inventory[name]["sha256"]:
                raise ValueError("source changed during candidate build")
            output.chmod(0o555 if inventory[name]["executable"] else 0o444)
        if (_source_paths(root) != names or _inventory(root, names) != inventory
                or _git(root, "rev-parse", "HEAD").decode().strip() != head):
            raise ValueError("source changed during candidate build")
        with (pending / "candidate.json").open("x") as handle:
            json.dump({**manifest, "release_id": release_id}, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        (pending / "candidate.json").chmod(0o444)
        os.rename(pending, target)
        fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if pending.exists():
            shutil.rmtree(pending)
    return {**manifest, "release_id": release_id, "path": str(target)}


def verify_materialization(candidate: Path, root: Path) -> dict:
    """Verify a sparse Git runtime against every frozen source byte, excluding state mounts."""
    checked = verify_candidate(candidate)
    if not checked["valid"]:
        raise ValueError("candidate integrity: " + checked["reason"])
    manifest = checked["manifest"]
    if _source_paths(root) != sorted(manifest["inventory"]) or _inventory(root, sorted(manifest["inventory"])) != manifest["inventory"]:
        raise ValueError("runtime source inventory/bytes differ from immutable release")
    if _git(root, "rev-parse", "HEAD").decode().strip() != manifest["source"]["commit"]:
        raise ValueError("runtime Git identity differs from immutable release")
    return manifest


def verify_candidate(path: str | Path) -> dict:
    root = Path(path)
    try:
        if any(p.is_symlink() for p in (root, *root.parents)) or "holdout" in root.parts:
            raise ValueError("protected/symlink candidate refused")
        manifest_path = root / "candidate.json"
        if manifest_path.is_symlink() or manifest_path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("invalid candidate manifest")
        manifest = json.loads(manifest_path.read_text())
        required = {"schema_version", "source", "inventory", "dependency_lock_sha256",
                    "ledger_min", "ledger_max", "rollback_release", "authorized",
                    "verification", "release_id"}
        if set(manifest) != required or manifest["schema_version"] != 1 or manifest["authorized"] is not False:
            raise ValueError("unsupported candidate contract")
        if (type(manifest["ledger_min"]) is not int or type(manifest["ledger_max"]) is not int
                or not 1 <= manifest["ledger_min"] <= manifest["ledger_max"]):
            raise ValueError("invalid ledger compatibility range")
        release_id = manifest["release_id"]
        if root.name != release_id:
            raise ValueError("candidate directory differs from its identity")
        if digest({k: v for k, v in manifest.items() if k != "release_id"}) != release_id:
            raise ValueError("candidate identity changed")
        names = sorted(manifest["inventory"])
        actual = []
        for current, dirs, files in os.walk(root, followlinks=False):
            for child in (*dirs, *files):
                if (Path(current) / child).is_symlink():
                    raise ValueError("candidate symlink refused")
            actual.extend((Path(current) / name).relative_to(root).as_posix()
                          for name in files if (Path(current) / name) != manifest_path)
        if sorted(actual) != names or not all(_allowed(name) for name in names):
            raise ValueError("candidate inventory differs")
        if _inventory(root, names) != manifest["inventory"]:
            raise ValueError("candidate bytes or executable modes changed")
        if manifest["dependency_lock_sha256"] != manifest["inventory"]["uv.lock"]["sha256"]:
            raise ValueError("dependency identity changed")
        return {"valid": True, "release_id": release_id, "manifest": manifest, "authorized": False}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"valid": False, "reason": str(exc), "authorized": False}
