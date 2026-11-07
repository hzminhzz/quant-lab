"""Process-wide, fail-closed advisory lease for Warden registry mutations.

Each registry has exactly one lock identity. The kernel releases flock on process
exit or SIGKILL. Readers never acquire this lease. This is not a substitute for
SQLite's transaction isolation or for the human-owned holdout boundary.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class MutationBusy(RuntimeError):
    """Another Warden mutation (or a nested mutation) holds the registry lease."""


_active: set[str] = set()
_guard = threading.Lock()


def lease_path(db_path: Path) -> Path:
    """A stable pathname across entrypoints addressing the same registry."""
    identity = str(Path(db_path).resolve())
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return Path(db_path).resolve().parent / f".warden-mutation-{suffix}.lock"


@contextmanager
def mutation_lease(db_path: Path) -> Iterator[None]:
    """Admit a single registry mutation, or raise without waiting or retrying.

    Deliberately non-reentrant: scheduler and adapters must call the CLI without
    holding this lease themselves. Never unlink a lockfile after releasing it.
    """
    lock = lease_path(db_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    name = str(lock)
    with _guard:
        if name in _active:
            raise MutationBusy(f"WARDEN_BUSY: nested mutation at {name}")
        _active.add(name)
    fd = None
    try:
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MutationBusy(f"WARDEN_BUSY: registry mutation already running at {name}") from exc
        yield
    finally:
        if fd is not None:
            os.close(fd)
        with _guard:
            _active.remove(name)
