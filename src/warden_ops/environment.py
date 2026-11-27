"""Content identity of a release's installed Python environment.

This measures the trusted appliance's executable dependency bytes, not the
integrity of the entire host OS or a sandbox against a malicious host owner.
No persistent metadata-only shortcut is used.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

from warden_ops.control_contracts import digest
from warden_ops.ledger_maintenance import file_sha256


def _file_identity(path):
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        sha = hashlib.file_digest(handle, "sha256").hexdigest()
        after = os.fstat(handle.fileno())
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("dependency file changed during verification")
    return sha, before.st_size


def _inventory(root, *, interpreter=None):
    """Hash the entire installed tree, including unlisted importable files."""
    root = Path(root)
    if (not root.is_absolute() or any("holdout" in p.lower() for p in root.parts)
            or any(p.is_symlink() for p in (root, *root.parents)) or not root.is_dir()):
        raise ValueError("unverified dependency root")
    entries = []
    size = 0

    def walk(directory):
        nonlocal size
        with os.scandir(directory) as scan:
            children = sorted(scan, key=lambda entry: entry.name)
        for entry in children:
            path = Path(entry.path)
            rel = path.relative_to(root).as_posix()
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode):
                # Conventional venv aliases only; never traverse arbitrary links.
                target = os.readlink(path)
                if rel == "lib64" and target == "lib":
                    pass
                elif (interpreter is not None and rel in {
                        "bin/python", "bin/python3", f"bin/python{interpreter['minor']}"}
                        and path.resolve() == interpreter["resolved"]):
                    pass
                else:
                    raise ValueError("unexpected dependency symlink: " + rel)
                entries.append([rel, "link", target])
            elif stat.S_ISDIR(mode):
                walk(path)
            elif stat.S_ISREG(mode):
                sha, length = _file_identity(path)
                entries.append([rel, mode & 0o777, sha])
                size += length
            else:
                raise ValueError("non-regular dependency file: " + rel)

    walk(root)
    return {"sha256": digest(entries), "entries": len(entries), "bytes": size}


def environment_identity(python):
    python = Path(python).absolute()
    code = (
        "import importlib.metadata as m,json,sys,sysconfig;"
        "print(json.dumps({'version':sys.version,'prefix':sys.prefix,"
        "'base_prefix':sys.base_prefix,'path':sys.path,"
        "'stdlib':sysconfig.get_path('stdlib'),"
        "'platstdlib':sysconfig.get_path('platstdlib'),"
        "'minor':f'{sys.version_info.major}.{sys.version_info.minor}',"
        "'packages':sorted((d.metadata['Name'],d.version) for d in m.distributions())}))"
    )
    value = json.loads(subprocess.run(
        [str(python), "-I", "-B", "-c", code], check=True, capture_output=True,
        text=True, timeout=30).stdout)
    prefix = Path(value["prefix"])
    if value["prefix"] == value["base_prefix"] or python.parent != prefix / "bin":
        raise ValueError("a dedicated Python virtual environment is required")
    interpreter = {"minor": value["minor"], "resolved": python.resolve(strict=True)}
    trees = {str(prefix): _inventory(prefix, interpreter=interpreter)}
    base = Path(value["base_prefix"])
    stdlib = Path(value["stdlib"])
    # Shared system interpreter/stdlib is explicitly outside the installed
    # dependency inventory. Record its location and the interpreter's bytes.
    standard = {stdlib, stdlib / "lib-dynload", base / "lib" /
                ("python" + value["minor"].replace(".", "") + ".zip")}
    standard.add(Path(value["platstdlib"]))
    for raw in value["path"]:
        path = Path(raw)
        if path in standard or path.is_relative_to(prefix):
            continue
        # Explicit extra effective package roots (e.g. isolated test fixtures
        # sharing installed dependencies, or development editable source).
        # Arbitrary .pth-injected directories are refused, not searched.
        if path.name not in {"site-packages", "src"}:
            raise ValueError("unverified Python import root: " + str(path))
        if not any(path.is_relative_to(Path(existing)) for existing in trees):
            trees[str(path)] = _inventory(path)
    return {"python": str(python), "python_sha256": file_sha256(python),
            "identity": digest({"metadata": value, "trees": trees}),
            "dependency_bytes": sum(tree["bytes"] for tree in trees.values()),
            "dependency_entries": sum(tree["entries"] for tree in trees.values()),
            "identity_schema": "installed-environment-bytes-v1"}
