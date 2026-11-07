"""Rehearse additive Warden evidence migration exclusively on a disposable SQLite copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from warden.registry import ResearchRegistry

LEGACY = ("contracts", "hypotheses", "trials", "selection_attempts", "qualifications",
          "audit_evidence", "audit_reports", "trial_dispositions", "trial_quarantine",
          "contract_search_state", "feature_sets")
ADDITIVE = ("attempt_receipts", "holdout_claims")


def table_hashes(path: Path, tables: tuple[str, ...]) -> dict[str, dict]:
    output = {}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        for name in tables:
            present = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
            if not present:
                output[name] = {"count": None, "sha256": None}
                continue
            sha = hashlib.sha256()
            count = 0
            for row in conn.execute(f"SELECT * FROM {name} ORDER BY rowid"):
                sha.update(json.dumps(row, ensure_ascii=True, default=str, separators=(",", ":")).encode())
                sha.update(b"\n")
                count += 1
            output[name] = {"count": count, "sha256": sha.hexdigest()}
    return output


def rehearse(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(strict=True), destination.resolve()
    if source == destination or destination.exists() or destination.is_relative_to(source.parent):
        raise ValueError("Rehearsal must use a new independent path")
    old_file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    original = table_hashes(source, LEGACY)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o600)
    try:
        ResearchRegistry(destination)
        migrated = table_hashes(destination, LEGACY)
        if original != migrated:
            raise ValueError(f"Migration mutated legacy authority: {[k for k in LEGACY if original[k] != migrated[k]]}")
        first_additive = table_hashes(destination, ADDITIVE)
        ResearchRegistry(destination)
        if table_hashes(destination, LEGACY) != original or table_hashes(destination, ADDITIVE) != first_additive:
            raise ValueError("Migration is not idempotent")
        with sqlite3.connect(destination) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Migrated fixture integrity failed")
        if hashlib.sha256(source.read_bytes()).hexdigest() != old_file_hash:
            raise ValueError("Source changed during migration rehearsal")
        return {"source_sha256": old_file_hash, "legacy_unchanged": True,
                "idempotent": True, "rollback_source": str(source), "migrated_fixture": str(destination),
                "original_counts": {k: v["count"] for k, v in original.items()},
                "added_counts": {k: v["count"] for k, v in first_additive.items()}}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(rehearse(args.source, args.destination), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
