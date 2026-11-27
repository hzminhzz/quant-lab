"""WAL-consistent additive migration rehearsal over every existing table/column.

Only a new disposable snapshot is migrated. Historical rows, indexes and triggers
must survive exactly; new schema/rows are inventoried, never silently accepted as
a production migration or an authorization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from warden.registry import ResearchRegistry

if __package__:
    from scripts.registry_evidence_snapshot import backup_readonly
else:  # Retain the original `python scripts/rehearse_state_machine_migration.py` entry point.
    from registry_evidence_snapshot import backup_readonly


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _read_state(path, columns_by_table=None):
    """One fixed snapshot of all user tables; handles BLOBs and WITHOUT ROWID."""
    tables = {}
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        objects = {(kind, name): sql for kind, name, sql in conn.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name")}
        for kind, name in objects:
            if kind != "table":
                continue
            columns = [list(row) for row in conn.execute(f"PRAGMA table_info({_quote(name)})")]
            names = columns_by_table.get(name, []) if columns_by_table else [row[1] for row in columns]
            if not names:
                continue
            selected = ",".join(_quote(col) for col in names)
            sha, count = hashlib.sha256(), 0
            for row in conn.execute(f"SELECT {selected} FROM {_quote(name)} ORDER BY {selected}"):
                encoded = [{"blob": value.hex()} if isinstance(value, bytes) else value for value in row]
                sha.update(json.dumps(encoded, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode())
                sha.update(b"\n")
                count += 1
            tables[name] = {"columns": columns, "count": count, "sha256": sha.hexdigest()}
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    return {"version": version, "tables": tables, "objects": objects, "integrity": integrity}


def _physical(path):
    result = {}
    for file in (path, path.with_name(path.name + "-wal")):
        # SQLite may materialize an empty WAL on a read-only snapshot open.
        # It carries no committed evidence; non-empty WAL bytes remain exact.
        if file.is_file() and (file == path or file.stat().st_size):
            with file.open("rb") as handle:
                result[file.name] = hashlib.file_digest(handle, "sha256").hexdigest()
    return result


def rehearse(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(strict=True), destination.resolve()
    if source == destination or destination.exists() or destination.is_relative_to(source.parent):
        raise ValueError("Rehearsal must use a new independent path")
    original_physical = _physical(source)
    backup = backup_readonly(source, destination, allow_snapshot_name=True)
    try:
        original = _read_state(destination)
        old_columns = {name: [row[1] for row in value["columns"]] for name, value in original["tables"].items()}
        ResearchRegistry(destination)
        migrated = _read_state(destination)
        projected = _read_state(destination, old_columns)
        for name, old in original["tables"].items():
            current = projected["tables"].get(name)
            if current is None or current["columns"][:len(old["columns"])] != old["columns"]:
                raise ValueError(f"Non-additive column change on existing table {name}")
            if current["count"] != old["count"] or current["sha256"] != old["sha256"]:
                raise ValueError(f"Migration mutated existing authority: {name}")
        for key, sql in original["objects"].items():
            if key[0] != "table" and migrated["objects"].get(key) != sql:
                raise ValueError(f"Migration changed existing schema object: {key}")
        ResearchRegistry(destination)
        if migrated != _read_state(destination):
            raise ValueError("Migration is not idempotent")
        if migrated["integrity"] != "ok":
            raise ValueError("Migrated fixture integrity failed")
        if _physical(source) != original_physical:
            raise ValueError("Source database/WAL changed during rehearsal; refresh snapshot")
        added = sorted(migrated["tables"].keys() - original["tables"].keys())
        added_columns = {
            name: [row[1] for row in migrated["tables"][name]["columns"][len(old["columns"]):]]
            for name, old in original["tables"].items()
            if len(migrated["tables"][name]["columns"]) > len(old["columns"])
        }
        return {
            "source_sha256": original_physical[source.name], "source_physical": original_physical,
            "backup": backup, "source_schema": original["version"], "target_schema": migrated["version"],
            "legacy_unchanged": True, "all_existing_tables_unchanged": True,
            "idempotent": True, "source_unchanged": True, "integrity": migrated["integrity"],
            "rollback_source": str(source), "migrated_fixture": str(destination),
            "rollback_warning": "snapshot is evidence, never permission to rewind newly accrued history",
            "original_counts": {name: value["count"] for name, value in original["tables"].items()},
            "original_tables": original["tables"],
            "new_tables": {name: migrated["tables"][name] for name in added},
            "added_counts": {name: migrated["tables"][name]["count"] for name in added},
            "added_columns": added_columns,
        }
    except BaseException:
        for path in (destination, destination.with_name(destination.name + "-wal"),
                     destination.with_name(destination.name + "-shm")):
            path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(rehearse(args.source, args.destination), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
