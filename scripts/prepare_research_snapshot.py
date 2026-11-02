"""Materialize only a SHA-pinned exploratory panel and SQLite research snapshot.

The original checkout and its WAL-backed registry are never modified. This is
a research snapshot; worktree screens must not be promoted as master evidence
without an explicit reconciliation against the live registry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from warden.contracts import load_contract


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_exploratory_relpath(rel: str) -> Path:
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 3 or path.parts[:2] != ("data", "exploratory"):
        raise ValueError("Refusing non-exploratory or traversing data path")
    return path


def install_verified_data(source: Path, target: Path, expected_sha: str) -> str:
    if not source.is_file() or not source.resolve().is_relative_to(source.parent.resolve()):
        raise ValueError("Missing or unsafe source")
    digest = file_digest(source)
    if digest != expected_sha:
        raise ValueError(f"Source SHA mismatch: expected {expected_sha}, got {digest}")
    if target.exists():
        if file_digest(target) != expected_sha:
            raise ValueError("Existing target does not match the frozen contract")
        return "existing_verified"

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".verified-", suffix=".parquet", dir=target.parent)
    tmp = Path(name)
    try:
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if file_digest(tmp) != expected_sha:
            raise ValueError("Copied data digest changed")
        os.link(tmp, target)  # exclusive: never overwrite another session's artifact
        return "installed_verified"
    finally:
        tmp.unlink(missing_ok=True)


def snapshot_registry(source: Path, target: Path) -> dict[str, object]:
    if target.exists():
        status = "existing_preserved"
    else:
        if not source.is_file():
            raise FileNotFoundError(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".registry-snapshot-", suffix=".db", dir=target.parent)
        os.close(fd)
        tmp = Path(name)
        try:
            with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as src, sqlite3.connect(tmp) as dst:
                src.backup(dst)  # includes all committed WAL transactions
                if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite snapshot failed quick_check")
            os.link(tmp, target)  # fail rather than replace a previous research snapshot
            status = "snapshot_created"
        finally:
            tmp.unlink(missing_ok=True)
    with sqlite3.connect(f"file:{target.resolve()}?mode=ro", uri=True) as conn:
        counts = {
            "hypotheses": conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0],
            "trials": conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0],
            "attempts": conn.execute("SELECT COUNT(*) FROM selection_attempts").fetchone()[0],
        }
    return {"status": status, "counts": counts, "sha256": file_digest(target)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-checkout", required=True, type=Path)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()

    repo = Path.cwd().resolve()
    source = args.source_checkout.resolve()
    if source == repo:
        raise ValueError("Refusing to snapshot a checkout onto itself")
    contract = load_contract(repo / "contracts" / (args.contract.lower() + ".yaml"))
    if contract.contract_id != args.contract:
        raise ValueError("Contract ID mismatch")
    rel = _checked_exploratory_relpath(contract.exploratory_data_path)
    if not (source / rel).resolve().is_relative_to((source / "data" / "exploratory").resolve()):
        raise ValueError("Source resolves outside exploratory data root")
    result = {
        "contract": args.contract,
        "data_sha256": contract.data_snapshot_sha256,
        "panel": install_verified_data(source / rel, repo / rel, contract.data_snapshot_sha256),
        "registry": snapshot_registry(source / "registry" / "registry.db", repo / "registry" / "registry.db"),
        "warning": "This is a local research snapshot, not the authoritative live Warden registry.",
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
