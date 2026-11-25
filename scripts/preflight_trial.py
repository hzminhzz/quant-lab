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

from warden_ops.readonly import ReadOnlyResearchRegistry

DEFAULT_DB = Path("registry/registry.db")


def snapshot(src: Path, dst: Path) -> Path:
    """Write a transactionally consistent copy of `src` to `dst` via the backup API.

    This is the one place in this script that opens SQLite directly: the backup API
    needs a source connection, and a read-only ``mode=ro`` handle is exactly what the
    copy must come from. Every other read below goes through the readonly registry view.
    """
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
    ledger = ReadOnlyResearchRegistry(db_path)
    vectors = ledger.read_recorded_return_vectors(contract_id, exclude_run_id)
    return sorted({len(json.loads(vector)) for vector in vectors})


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
        path = ReadOnlyResearchRegistry(snap).read_trial_returns_path(result.run_id)
        if path is None:
            raise ValueError(f"trial {result.run_id} recorded no returns_parquet_path in the snapshot")
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
