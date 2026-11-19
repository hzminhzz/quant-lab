#!/usr/bin/env python3
"""Run one trial spec against a throwaway snapshot of the live registry.

`run_spec` commits the trial budget at `register_trial_start` *before* it reads the
contract-wide selection history, so a `SelectionHistoryError` or a strategy subprocess
error burns a real slot. This preflight spends nothing: it snapshots the registry, runs
the spec against the snapshot, and refuses when the produced return vector does not match
the length of the existing streams for that contract.

The snapshot must use SQLite's backup API, not a file copy: the registry runs in WAL
mode, so `registry.db` alone can be missing committed rows that still live in
`registry.db-wal`. A copy silently gives the preflight a stale selection history, which
is exactly the thing the preflight exists to measure.

Usage:
    python scripts/preflight_trial.py experiments/active/carver_daily/01_buy_hold_single_t1.yaml
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("registry/registry.db")


def snapshot(src: Path, dst: Path) -> Path:
    """Write a transactionally consistent copy of `src` to `dst` via the backup API."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(dst) + suffix).unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return dst


def prior_stream_lengths(db_path: Path, contract_id: str, exclude_run_id: str = "") -> list[int]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT returns_json FROM trials WHERE contract_id=? AND run_id!=? "
            "AND returns_json NOT IN ('', '[]')", (contract_id, exclude_run_id)).fetchall()
        rows += con.execute(
            "SELECT returns_json FROM selection_attempts WHERE contract_id=? AND kind='param_grid' "
            "AND returns_json NOT IN ('', '[]')", (contract_id,)).fetchall()
    finally:
        con.close()
    return sorted({len(json.loads(r[0])) for r in rows})


def check_alignment(candidate_bars: int, prior_lengths: list[int]) -> list[str]:
    """Refuse when the candidate's return vector cannot align with the recorded history."""
    if candidate_bars <= 0:
        return [f"candidate produced {candidate_bars} return bars"]
    if not prior_lengths:
        return []
    if prior_lengths != [candidate_bars]:
        return [(f"candidate has {candidate_bars} bars but the contract's recorded streams are "
                 f"{prior_lengths}; run_spec would raise SelectionHistoryError after spending the trial")]
    return []


def preflight(spec_path: Path, db_path: Path = DEFAULT_DB, workdir: Path | None = None) -> dict[str, Any]:
    """Run `spec_path` against a snapshot and return a summary, or raise on misalignment."""
    from warden.registry import ResearchRegistry  # imported late so --help stays cheap
    from warden.runner import ExperimentRunner

    owned = workdir is None
    workdir = Path(workdir or tempfile.mkdtemp(prefix="warden-preflight-"))
    try:
        snap = snapshot(db_path, workdir / "registry.db")
        runner = ExperimentRunner(registry=ResearchRegistry(snap), repo_root=Path("."))
        result = runner.run_spec(spec_path)
        con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
        try:
            path = con.execute("SELECT returns_parquet_path FROM trials WHERE run_id=?",
                               (result.run_id,)).fetchone()[0]
        finally:
            con.close()
        import polars as pl
        bars = len(pl.read_parquet(Path(path))["net_return"]) if Path(path).is_file() else 0
        problems = check_alignment(bars, prior_stream_lengths(snap, result.contract_id, result.run_id))
        if problems:
            raise ValueError("; ".join(problems))
        return {"run_id": result.run_id, "verdict": result.verdict, "net_sharpe": result.net_sharpe,
                "deflated_sharpe_prob": result.deflated_sharpe_prob, "bars": bars,
                "snapshot": str(snap)}
    finally:
        if owned:
            shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight one trial spec on a registry snapshot.")
    parser.add_argument("spec", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--workdir", type=Path, default=None, help="keep the snapshot here instead of a temp dir")
    args = parser.parse_args(argv)
    try:
        summary = preflight(args.spec, args.db, args.workdir)
    except (ValueError, OSError, KeyError) as exc:
        print(f"PREFLIGHT_FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    print("PREFLIGHT_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
