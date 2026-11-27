"""WAL-consistent snapshots and additive preservation, without market-data access.

Snapshots are evidence. This module deliberately has no database restore operation.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def read_state(path, columns_by_table=None):
    """Hash every existing row/column, including BLOBs and WITHOUT ROWID tables."""
    tables = {}
    with closing(sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        objects = {f"{kind}:{name}": sql for kind, name, sql in conn.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name")}
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in names:
            columns = [list(row) for row in conn.execute(f"PRAGMA table_info({_quote(name)})")]
            selected_names = columns_by_table.get(name, []) if columns_by_table is not None else [c[1] for c in columns]
            if not selected_names:
                continue
            selected = ",".join(_quote(col) for col in selected_names)
            sha, count = hashlib.sha256(), 0
            for row in conn.execute(f"SELECT {selected} FROM {_quote(name)} ORDER BY {selected}"):
                encoded = [{"blob": value.hex()} if isinstance(value, bytes) else value for value in row]
                sha.update(json.dumps(encoded, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode())
                sha.update(b"\n")
                count += 1
            tables[name] = {"columns": columns, "count": count, "sha256": sha.hexdigest()}
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    return {"version": version, "tables": tables, "objects": objects, "integrity": integrity}


def assert_preserved(before, path):
    columns = {name: [c[1] for c in value["columns"]] for name, value in before["tables"].items()}
    projected = read_state(path, columns)
    for name, old in before["tables"].items():
        current = projected["tables"].get(name)
        if current is None or current["columns"][:len(old["columns"])] != old["columns"]:
            raise ValueError(f"Non-additive column change on existing table {name}")
        if (current["count"], current["sha256"]) != (old["count"], old["sha256"]):
            raise ValueError(f"Migration mutated existing authority: {name}")
    for key, sql in before["objects"].items():
        if not key.startswith("table:") and projected["objects"].get(key) != sql:
            raise ValueError(f"Migration changed existing schema object: {key}")
    if projected["integrity"] != "ok":
        raise ValueError("Ledger integrity failed")
    return {"all_existing_tables_unchanged": True, "integrity": "ok",
            "source_schema": before["version"], "target_schema": projected["version"],
            "original_counts": {name: value["count"] for name, value in before["tables"].items()}}


def file_sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def backup_readonly(source, destination, *, allow_snapshot_name=False):
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve()
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
            upstream.backup(copy, pages=256)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Snapshot integrity failed")
        state = read_state(destination)
        return {"source": str(source), "snapshot": str(destination),
                "counts": {name: value["count"] for name, value in state["tables"].items()},
                "sha256": file_sha256(destination), "integrity": "ok", "opened_source_read_only": True}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def physical_identity(path):
    path = Path(path)
    return {file.name: file_sha256(file) for file in (path, path.with_name(path.name + "-wal"))
            if file.is_file() and (file == path or file.stat().st_size)}


def rehearse(source, destination, *, registry_factory=None):
    from warden.registry import ResearchRegistry

    factory = registry_factory or ResearchRegistry
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve()
    original_physical = physical_identity(source)
    backup = backup_readonly(source, destination, allow_snapshot_name=True)
    try:
        original = read_state(destination)
        factory(destination)
        migrated = read_state(destination)
        preserved = assert_preserved(original, destination)
        factory(destination)
        if migrated != read_state(destination):
            raise ValueError("Migration is not idempotent")
        if physical_identity(source) != original_physical:
            raise ValueError("Source database/WAL changed during rehearsal; refresh snapshot")
        added = sorted(migrated["tables"].keys() - original["tables"].keys())
        added_columns = {
            name: [c[1] for c in migrated["tables"][name]["columns"][len(old["columns"]):]]
            for name, old in original["tables"].items()
            if len(migrated["tables"][name]["columns"]) > len(old["columns"])
        }
        return {**preserved, "source_sha256": original_physical[source.name],
                "source_physical": original_physical, "backup": backup,
                "legacy_unchanged": True, "idempotent": True, "source_unchanged": True,
                "rollback_source": str(source), "migrated_fixture": str(destination),
                "rollback_warning": "snapshot is evidence, never permission to rewind newly accrued history",
                "original_tables": original["tables"],
                "new_tables": {name: migrated["tables"][name] for name in added},
                "added_counts": {name: migrated["tables"][name]["count"] for name in added},
                "added_columns": added_columns}
    except BaseException:
        for file in (destination, destination.with_name(destination.name + "-wal"),
                     destination.with_name(destination.name + "-shm")):
            file.unlink(missing_ok=True)
        raise
