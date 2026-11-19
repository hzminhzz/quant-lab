"""Reset the worktree registry snapshot from the live (read-only) registry, with fixture hypothesis.

Never opens the live registry for writes: sqlite3 backup() from a mode=ro URI.
Usage: python perf_audit/reset_registry.py [--keep-cache]
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from pathlib import Path

import yaml

LIVE = Path("/home/quant/dev/quant-lab")
WT = Path(__file__).resolve().parent.parent
BENCH_HYP = "HYP-BENCH-ULWPERF"
SPEC_REL = "experiments/active/soc_wbx_downshock_1h_20261031.yaml"
FIELDS = ("title", "economic_mechanism", "counterparty_or_friction", "expected_holding_bars",
          "falsification_criteria", "strategy_family", "contract_id")
FEATURE_CACHE = WT / "cache" / "features"


def reset(keep_cache: bool = False) -> tuple[Path, int]:
    db = WT / "registry" / "registry.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    src = sqlite3.connect(f"file:{LIVE}/registry/registry.db?mode=ro", uri=True)
    dst = sqlite3.connect(db)
    src.backup(dst)
    dst.close()
    src.close()

    cfg = yaml.safe_load((LIVE / SPEC_REL).read_text(encoding="utf-8"))
    conn = sqlite3.connect(db)
    row = conn.execute(
        f"SELECT {', '.join(FIELDS)} FROM hypotheses WHERE hypothesis_id=?", (cfg["hypothesis_id"],)
    ).fetchone()
    conn.execute("DELETE FROM hypotheses WHERE hypothesis_id=?", (BENCH_HYP,))
    conn.execute(
        "INSERT INTO hypotheses (hypothesis_id, contract_id, title, strategy_family, economic_mechanism, "
        "counterparty_or_friction, expected_holding_bars, falsification_criteria, parent_hypothesis_id, "
        "source_provenance, llm_model_id, strategy_module, status, trials_used, created_at, attempts_json) "
        "VALUES (?,?,?,?,?,?,?,?,NULL,'internal_research','ultraperf-bench','', 'ACTIVE',0,?,'{}')",
        (BENCH_HYP, row[6], row[0], row[5], row[1], row[2], row[3], row[4], time.time()),
    )
    conn.commit()
    fixtures = conn.execute(
        "SELECT COUNT(*) FROM trials WHERE hypothesis_id=?", (BENCH_HYP,)).fetchone()[0]
    conn.close()
    if not keep_cache:
        for p in FEATURE_CACHE.glob("*"):
            if p.name != ".gitkeep":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    # Bench run ids must not collide with prior bench returns (the registry refuses to
    # overwrite an existing returns stream). Only RUN-BENCH-* files are ever removed.
    for p in (WT / "registry" / "returns").glob("*/RUN-BENCH-*.parquet"):
        p.unlink()
    return db, fixtures


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-cache", action="store_true")
    a = ap.parse_args()
    path, n = reset(a.keep_cache)
    print(f"reset {path} trials_for_fixture={n}")
