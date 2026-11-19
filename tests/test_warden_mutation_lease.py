"""Single-writer lease: independent CLI processes share the same research registry."""

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from warden.lease import MutationBusy, mutation_lease


def _hold(path: str, ready: mp.Queue, release: mp.Queue) -> None:
    with mutation_lease(Path(path)):
        ready.put("acquired")
        release.get(timeout=10)


def test_writer_busy_and_reader_unaffected(tmp_path: Path) -> None:
    db = tmp_path / "registry.db"
    ready: mp.Queue = mp.Queue()
    release: mp.Queue = mp.Queue()
    proc = mp.Process(target=_hold, args=(str(db), ready, release))
    proc.start()
    try:
        assert ready.get(timeout=5) == "acquired"
        with pytest.raises(MutationBusy), mutation_lease(db):
            pytest.fail("second writer admitted")
        assert db.parent.is_dir()  # no reader acquisition required
    finally:
        release.put("done")
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join()
    assert proc.exitcode == 0
    with mutation_lease(db):
        pass


def test_real_cli_denies_competing_mutation_but_allows_status(tmp_path: Path) -> None:
    db = tmp_path / "registry" / "registry.db"
    ready: mp.Queue = mp.Queue()
    release: mp.Queue = mp.Queue()
    proc = mp.Process(target=_hold, args=(str(db), ready, release))
    proc.start()
    try:
        assert ready.get(timeout=5) == "acquired"
        cmd = [sys.executable, "-m", "warden.cli"]
        blocked = subprocess.run(
            [*cmd, "verify", "nonexistent.yaml", "--json"],
            cwd=tmp_path, capture_output=True, text=True, timeout=8, check=False,
        )
        assert blocked.returncode == 75
        assert '"status": "BUSY"' in blocked.stdout
        read = subprocess.run(
            [*cmd, "status", "--json"],
            cwd=tmp_path, capture_output=True, text=True, timeout=8, check=False,
        )
        assert read.returncode == 0, read.stderr
        assert '"source": "warden.status"' in read.stdout
    finally:
        release.put("done")
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join()


def test_status_read_does_not_need_write_lock(tmp_path: Path) -> None:
    """WAL readers must remain available during a long uncommitted mutation."""
    from warden.registry import ResearchRegistry

    db = tmp_path / "registry" / "registry.db"
    ResearchRegistry(db)
    conn = sqlite3.connect(db, timeout=1)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE hypotheses SET trials_used=trials_used WHERE 1=0")
    try:
        proc = subprocess.run([sys.executable, "-m", "warden.cli", "status", "--json"],
                              cwd=tmp_path, capture_output=True, text=True, timeout=4, check=False)
        assert proc.returncode == 0, proc.stderr
        assert '"source": "warden.status"' in proc.stdout
    finally:
        conn.rollback()
        conn.close()


def test_sigkill_releases_lease(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX advisory locks")
    db = tmp_path / "registry.db"
    ready: mp.Queue = mp.Queue()
    release: mp.Queue = mp.Queue()
    proc = mp.Process(target=_hold, args=(str(db), ready, release))
    proc.start()
    assert ready.get(timeout=5) == "acquired"
    proc.kill()
    proc.join(timeout=5)
    assert proc.exitcode is not None
    with mutation_lease(db):
        pass


def test_nested_same_process_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "registry.db"
    with mutation_lease(db), pytest.raises(MutationBusy), mutation_lease(db):
        pass
