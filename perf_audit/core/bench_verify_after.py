"""Timing for the shipped cache-first `warden verify` step (fix 2).

Mirrors cmd_verify's post-fix call sequence: one compute_and_cache_features call, cold then warm.
Run under the core you want to measure:

  cd <worktree>
  PYTHONPATH=<worktree>/src uv run --frozen --project /home/quant/dev/quant-lab python perf_audit/core/bench_verify_after.py --reps 2
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from functools import wraps
from pathlib import Path

import polars as pl

PERF = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PERF))
import bench_warden as BW  # noqa: E402

import warden  # noqa: E402
import warden.leakage as L  # noqa: E402
from warden.contracts import load_contract  # noqa: E402
from warden.registry import ResearchRegistry  # noqa: E402
from warden.strategy_loader import load_module_from_path  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    args = ap.parse_args()
    contract = load_contract(BW.MAIN / BW.CONTRACT_REL)
    mod = load_module_from_path(BW.MAIN / "strategies/candidates/soc_wbx_downshock_1h_20261031.py")
    panel = pl.read_parquet(BW.MAIN / contract.exploratory_data_path)
    panel = panel.filter(pl.col("asset").is_in(["BTCUSDT", "ETHUSDT", "LTCUSDT", "XRPUSDT"]))
    computes = {"n": 0}
    original = mod.ShockVolumeFeaturePipeline.compute

    @wraps(original)
    def counted(self, bars_df, c):
        computes["n"] += 1
        return original(self, bars_df, c)

    mod.ShockVolumeFeaturePipeline.compute = counted
    rows = []
    for rep in range(args.reps):
        root = BW.fresh_root(f"verifynew{rep}")
        reg = ResearchRegistry(root / "registry" / "registry.db")
        cache = root / "cache" / "features"
        computes["n"] = 0
        t0 = time.perf_counter()
        L.compute_and_cache_features(mod.pipeline, panel, contract, cache_dir=cache, registry=reg)
        cold = time.perf_counter() - t0
        cold_n = computes["n"]
        computes["n"] = 0
        t0 = time.perf_counter()
        L.compute_and_cache_features(mod.pipeline, panel, contract, cache_dir=cache, registry=reg)
        warm = time.perf_counter() - t0
        rows.append({"rep": rep, "core": warden.__file__, "cold_s": round(cold, 3), "warm_s": round(warm, 4),
                     "cold_computes": cold_n, "warm_computes": computes["n"]})
        shutil.rmtree(root, ignore_errors=True)
    measured = rows[1:] or rows
    print(json.dumps({
        "env": BW.env_record(), "core": warden.__file__, "sequence": "cmd_verify post-fix (cache-first)",
        "reps": rows,
        "median_cold_s": sorted(r["cold_s"] for r in measured)[len(measured) // 2],
        "median_warm_s": sorted(r["warm_s"] for r in measured)[len(measured) // 2],
        "cold_computes": rows[0]["cold_computes"], "warm_computes": rows[-1]["warm_computes"],
    }, indent=1))


if __name__ == "__main__":
    main()
