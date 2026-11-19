"""Read-only registry view: status must never acquire a SQLite writer lock.

Normal ResearchRegistry initialization performs additive migrations. Operators
must be able to inspect the last committed WAL snapshot during a mutation,
without blocking, modifying a table or changing scientific eligibility.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from warden.registry import ResearchRegistry


class ReadOnlyResearchRegistry(ResearchRegistry):
    """Status-only adapter; does not run migrations or open writable SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True, timeout=0.2)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            yield conn
        finally:
            conn.close()
