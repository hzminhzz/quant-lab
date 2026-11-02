"""Read-only, checksum-verified panel and WAL-consistent registry snapshot.

Never writes to the primary checkout. Run in a fresh isolated worktree
before any Warden registration, preserving contract-wide research debt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from warden.contracts import load_contract

DEFAULT_SOURCE = Path("/home/quant/dev/quant-lab")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as inp:
        for chunk in iter(lambda: inp.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_once(source: Path, dest: Path, expected: str) -> None:
    if not source.is_file() or sha256(source) != expected:
        raise ValueError(f"Missing or unexpected source data digest: {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if sha256(dest) != expected:
            raise FileExistsError(f"Refusing to replace different dataset: {dest}")
        return
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
        temp = Path(tmp.name)
        with source.open("rb") as inp:
            shutil.copyfileobj(inp, tmp, 4 * 1024 * 1024)
        tmp.flush()
        os.fsync(tmp.fileno())
    try:
        if sha256(temp) != expected:
            raise ValueError("Copied data changed bytes")
        os.link(temp, dest)
    finally:
        temp.unlink(missing_ok=True)


def check_quarantine(path: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return [(a, b) for a, b in db.execute(
            "SELECT run_id,reason FROM trial_quarantine"
        )]


def snapshot_registry(source: Path, dest: Path) -> dict:
    if dest.exists():
        # The worktree launcher may initialize a brand-new empty Warden
        # registry before first use. Replacing that empty schema is safe;
        # replacing any observed row, even an unrelated table, is not.
        with sqlite3.connect(f"file:{dest}?mode=ro", uri=True) as check:
            user_tables = [
                x[0] for x in check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            ]
            nonempty = {
                name: check.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                for name in user_tables
                if check.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            }
        # Warden seeds five legacy synthetic quarantines on init with
        # worktree-local timestamps. Their run IDs/reasons must match the
        # primary source exactly; nothing else may be present.
        if set(nonempty) == {"trial_quarantine"}:
            with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original:
                current = {
                    (a, b) for a, b in check_quarantine(dest)
                }
                authoritative = {
                    (a, b) for a, b in original.execute(
                        "SELECT run_id,reason FROM trial_quarantine"
                    )
                }
            if current == authoritative and len(current) == len(authoritative):
                nonempty = {}
        if nonempty:
            raise FileExistsError(
                f"Existing isolated Warden ledger contains data: {nonempty}"
            )
    if not source.is_file():
        raise FileNotFoundError(f"No source Warden registry: {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # SQLite's online backup reads a consistent source view including
    # committed WAL pages. An empty launcher-created target is permitted.
    with (
        sqlite3.connect(f"file:{source}?mode=ro", uri=True) as reader,
        sqlite3.connect(dest) as writer,
    ):
        reader.backup(writer)
        integrity = writer.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Snapshot SQLite integrity failed: {integrity}")
        names = ("hypotheses", "trials", "selection_attempts")
        counts = {
            name: int(writer.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in names
        }
    return {
        "counts": counts,
        "snapshot_sha256": sha256(dest),
        "wal_consistent": True,
        "source_read_only": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--evidence", type=Path, default=Path(
        ".workspace/campaigns/four-xs4h/source-snapshot.json"
    ))
    args = parser.parse_args()
    contract = load_contract("contracts/crypto_perps_top100_1h_v1.yaml")
    source_data = args.source_root / contract.exploratory_data_path
    dest_data = Path(contract.exploratory_data_path)
    publish_once(source_data, dest_data, contract.data_snapshot_sha256)
    info = snapshot_registry(args.source_root / "registry/registry.db",
                             Path("registry/registry.db"))
    report = {
        **info,
        "contract_id": contract.contract_id,
        "dataset_sha256": contract.data_snapshot_sha256,
        "dataset_rows_source": str(source_data),
        "source_registry": str(args.source_root / "registry/registry.db"),
        "source_is_not_modified": True,
        "protected_holdout_read": False,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    if args.evidence.exists():
        raise FileExistsError("Evidence snapshot already exists")
    args.evidence.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
