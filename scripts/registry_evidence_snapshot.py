"""Read-only, SQLite WAL-consistent evidence snapshot and source-count verification.

Never alter, migrate or recover the source database. The output is a standalone
fixture requiring an independent operator decision before any rollout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

TABLES = ("hypotheses", "trials", "selection_attempts", "qualifications", "audit_evidence", "trial_dispositions")


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    known = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in TABLES if name in known}


def backup_readonly(source: Path, destination: Path, *, allow_snapshot_name=False) -> dict:
    source, destination = source.resolve(strict=True), destination.resolve()
    if source == destination or destination.exists() or destination.is_relative_to(source.parent):
        raise ValueError("Snapshot must be a new file outside the live registry directory")
    if not source.is_file() or (source.name != "registry.db" and not allow_snapshot_name):
        raise ValueError("Only an existing registry.db is accepted as a source")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as upstream, closing(sqlite3.connect(destination)) as copy:
            upstream.execute("PRAGMA query_only=ON")
            upstream.execute("BEGIN")
            before = counts(upstream)
            upstream.backup(copy, pages=256)
            after = counts(copy)
            integrity = copy.execute("PRAGMA integrity_check").fetchone()[0]
        if before != after or integrity != "ok":
            raise ValueError(f"SQLite snapshot not equivalent: counts={before} vs {after}, integrity={integrity}")
        with destination.open("rb") as handle:
            sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
        return {"source": str(source), "snapshot": str(destination), "counts": after,
                "sha256": sha256, "integrity": integrity,
                "opened_source_read_only": True}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main() -> int:
    p = argparse.ArgumentParser(description="Back up a live Warden registry without writing to the source")
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--destination", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(backup_readonly(args.source, args.destination), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
