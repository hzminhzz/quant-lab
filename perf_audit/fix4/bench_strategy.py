"""Fix 4 in-process timing: strategy-level generate_positions, old vs new module.

Median of 5 measured reps after 1 warm-up per version, same real panel, same
contract. Also re-runs warden.leakage.verify_prefix_invariance on both modules
with a fresh registry snapshot root (tmp dir only) so the leakage status is
observed rather than inferred from a harness exit code.

Read-only: no live registry writes, no holdout, no qualify.
Run: cd <worktree> && uv run --frozen --project /home/quant/dev/quant-lab \
       python perf_audit/fix4/bench_strategy.py
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import numpy as np
import polars as pl

from warden.contracts import load_contract
from warden.runner import load_module_from_path

WORK = Path(__file__).resolve().parents[2]
CONTRACT = load_contract(WORK / "contracts/auto_crypto_perps_top100_1h_v1_h24_e24_f45_s20_v1.yaml")
OLD = load_module_from_path(WORK / "perf_audit/fix4/soc_wbx_old.py")
NEW = load_module_from_path(WORK / "strategies/candidates/soc_wbx_downshock_1h_20261031.py")
ASSETS = list(OLD.ASSETS)
REPS = 5


def timeit(fn, reps: int) -> dict:
    fn()  # warm-up
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - t0)
    return {"median_s": round(statistics.median(times), 5), "min_s": round(min(times), 5),
            "max_s": round(max(times), 5), "reps_s": [round(t, 4) for t in times], "out": out}


def main() -> None:
    panel = pl.read_parquet(WORK / "data" / "exploratory/crypto_perps_top100_1h.parquet")
    panel = panel.filter(pl.col("asset").is_in(ASSETS)).sort("timestamp", "asset")
    plan = {}
    for label, mod in (("old", OLD), ("new", NEW)):
        gen = timeit(lambda m=mod: m.generate_positions(panel, CONTRACT, target_symbols=ASSETS)["positions"], REPS)
        val = timeit(lambda m=mod: m._validate(panel, CONTRACT, ASSETS), REPS)
        comp = timeit(lambda m=mod: m.pipeline.compute(panel, CONTRACT), REPS)
        plan[label] = {
            "generate_positions": {k: v for k, v in gen.items() if k != "out"},
            "_validate": {k: v for k, v in val.items() if k != "out"},
            "pipeline_compute(includes its own _validate)": {k: v for k, v in comp.items() if k != "out"},
        }
    same = np.array_equal(
        OLD.generate_positions(panel, CONTRACT, target_symbols=ASSETS)["positions"],
        NEW.generate_positions(panel, CONTRACT, target_symbols=ASSETS)["positions"],
    )
    plan["positions_bit_identical"] = bool(same)
    plan["speedup_generate_positions"] = round(
        plan["old"]["generate_positions"]["median_s"] / plan["new"]["generate_positions"]["median_s"], 2
    )
    plan["speedup_validate"] = round(plan["old"]["_validate"]["median_s"] / plan["new"]["_validate"]["median_s"], 2)

    # prefix-invariance status per module, fresh scratch root only (bench fixture pattern)
    import shutil
    import sqlite3

    import warden.leakage as L

    leakage = {}
    root = WORK / f"perf_audit/fix4/tmp_prefix_{int(time.time() * 1000)}"
    (root / "registry").mkdir(parents=True)
    src = sqlite3.connect(f"file:{WORK}/registry/registry.db?mode=ro", uri=True)
    dst = sqlite3.connect(root / "registry" / "registry.db")
    src.backup(dst)
    dst.close()
    src.close()
    for label, mod in (("old", OLD), ("new", NEW)):
        t0 = time.perf_counter()
        try:
            L.verify_prefix_invariance(mod.pipeline, panel, CONTRACT)
            leakage[label] = {"status": "PASS", "s": round(time.perf_counter() - t0, 3)}
        except Exception as exc:  # noqa: BLE001 - report, do not hide
            leakage[label] = {"status": f"FAIL {type(exc).__name__}: {exc}", "s": round(time.perf_counter() - t0, 3)}
    shutil.rmtree(root, ignore_errors=True)
    plan["prefix_invariance"] = leakage
    plan["panel"] = {"rows": panel.height, "timestamps": int(panel["timestamp"].n_unique())}
    print(json.dumps(plan, indent=1))


if __name__ == "__main__":
    main()
