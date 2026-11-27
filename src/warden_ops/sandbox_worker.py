"""Stateless Linux worker: readonly staged input, isolated network/PIDs, scoped output.

This is an OS compartment for portable staged programs, not the canonical
scientific writer. The Warden owner supervises it and validates output. No host
home, canonical database, key environment or sealed data is mounted.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from warden_ops.control_contracts import Resources, digest
from warden_ops.process_limits import run_bounded


def input_identity(root):
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("worker input symlink refused")
        if path.is_file():
            with path.open("rb") as handle:
                sha = hashlib.file_digest(handle, "sha256").hexdigest()
            rows.append([path.relative_to(root).as_posix(), sha])
    return digest(rows)


def run_worker(staged: Path, output: Path, limits: Resources, *, cpu_ids=None) -> dict:
    staged, output = Path(staged).absolute(), Path(output).absolute()
    if output.exists() or staged == output or output.is_relative_to(staged):
        raise ValueError("worker requires independent new output")
    for root in (staged, output):
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("worker mount symlink refused")
    for root, dirs, files in os.walk(staged, followlinks=False):
        for name in (*dirs, *files):
            path = Path(root) / name
            if path.is_symlink() or name in {"registry", "holdout", ".env", ".ssh"}:
                raise ValueError("worker input includes forbidden state or symlink")
    if not (staged / "program.py").is_file():
        raise ValueError("frozen portable worker program required")
    input_sha256 = input_identity(staged)
    output.mkdir(parents=True, mode=0o700)
    args = ["bwrap", "--unshare-all", "--die-with-parent", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--ro-bind", str(staged), "/input", "--bind", str(output), "/output",
            "--chdir", "/input", "--setenv", "PATH", "/usr/bin:/bin",
            "--", "/usr/bin/prlimit", f"--as={limits.memory_mb * 1024 * 1024}",
            f"--cpu={max(1, limits.cpu * limits.wall_seconds)}",
            f"--fsize={limits.io_mb * 1024 * 1024}", "--",
            "/usr/bin/python3", "-I", "/input/program.py"]
    result = run_bounded(args, cwd=staged, env={"PATH": "/usr/bin:/bin"},
                         output=output, limits=limits, cpu_ids=cpu_ids)
    if input_identity(staged) != input_sha256:
        result.update(state="FAILED", reason="staged input changed; output not admissible")
    return {**result, "input_sha256": input_sha256, "canonical_ledger_mounted": False,
            "host_home_mounted": False, "network": "isolated", "keys_in_environment": False}
