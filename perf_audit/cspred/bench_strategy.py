"""Timing + leakage gate: generate_positions median of 5 (1 warm-up) old vs new,
and warden.leakage.verify_prefix_invariance on both modules.

Old modules: perf_audit/cspred/old/ (git show HEAD). New: worktree strategies/.
Panels: read-only exploratory parquet of the contract under test. No registry
writes, no holdout, no qualify.

Run: cd <worktree> && uv run --frozen --project /home/quant/dev/quant-lab \
       python perf_audit/cspred/bench_strategy.py
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import polars as pl

from warden.contracts import load_contract
from warden.leakage import verify_prefix_invariance
from warden.strategy_loader import load_module_from_path

WORK = Path(__file__).resolve().parents[2]
OLD_DIR = Path(__file__).resolve().parent / "old"
REPS = 5
CASES = [
    ("cs_ridge_1h", "strategies/baselines/cs_ridge_1h.py", "contracts/crypto_perps_1h_v2.yaml"),
    ("cs_ridge_1h", "strategies/baselines/cs_ridge_1h.py", "contracts/crypto_perps_top100_1h_v1.yaml"),
    ("cs_lgbm_1h", "strategies/candidates/cs_lgbm_1h.py", "contracts/crypto_perps_1h_v2.yaml"),
    ("cs_lgbm_1h", "strategies/candidates/cs_lgbm_1h.py", "contracts/crypto_perps_1h_v1.yaml"),
    ("cs_amihud_1h", "strategies/candidates/cs_amihud_1h.py", "contracts/crypto_perps_1h_v2.yaml"),
    ("cs_amihud_1h", "strategies/candidates/cs_amihud_1h.py", "contracts/crypto_perps_1h_v1.yaml"),
    ("cs_bab_1h", "strategies/candidates/cs_bab_1h.py", "contracts/crypto_perps_1h_v2.yaml"),
    ("cs_bab_1h", "strategies/candidates/cs_bab_1h.py", "contracts/crypto_perps_1h_v1.yaml"),
    ("cs_vwap_tracking_error_1h", "strategies/candidates/cs_vwap_tracking_error_1h.py",
     "contracts/crypto_perps_1h_v2.yaml"),
    ("cs_vwap_tracking_error_1h", "strategies/candidates/cs_vwap_tracking_error_1h.py",
     "contracts/crypto_perps_top100_1h_v1.yaml"),
]


def timed(fn) -> dict:
    fn()
    times = []
    for _ in range(REPS):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return {"median_s": round(statistics.median(times), 4),
            "min_s": round(min(times), 4), "max_s": round(max(times), 4),
            "reps_s": [round(t, 4) for t in times]}


def main() -> None:
    panels: dict[str, tuple[pl.DataFrame, object]] = {}
    out = []
    for name, path, cfg in CASES:
        contract = load_contract(WORK / cfg)
        if cfg not in panels:
            df = pl.read_parquet(WORK / contract.exploratory_data_path).sort("timestamp", "asset")
            panels[cfg] = (df, contract)
        df, contract = panels[cfg]
        old = load_module_from_path(OLD_DIR / Path(path).name)
        new = load_module_from_path(WORK / path)
        rec = {"module": name, "contract": contract.contract_id,
               "panel_rows": int(df.height), "assets": int(df["asset"].n_unique())}
        for label, mod in (("old", old), ("new", new)):
            rec[label] = timed(lambda m=mod: m.generate_positions(df, contract))
            try:
                t0 = time.perf_counter()
                verify_prefix_invariance(mod.pipeline, df, contract)
                rec[f"{label}_prefix_invariance"] = {"status": "PASS",
                                                     "s": round(time.perf_counter() - t0, 3)}
            except Exception as exc:  # noqa: BLE001 - leakage status must be observed
                rec[f"{label}_prefix_invariance"] = {"status": f"FAIL {type(exc).__name__}: {exc}"}
        rec["speedup_generate_positions"] = round(
            rec["old"]["median_s"] / rec["new"]["median_s"], 3) if rec["new"]["median_s"] else None
        out.append(rec)
        print(json.dumps(rec), flush=True)
    print(json.dumps({"summary": [
        {"module": r["module"], "contract": r["contract"],
         "old_median_s": r["old"]["median_s"], "new_median_s": r["new"]["median_s"],
         "speedup": r["speedup_generate_positions"],
         "prefix_old": r["old_prefix_invariance"]["status"],
         "prefix_new": r["new_prefix_invariance"]["status"]} for r in out]}, indent=1))


if __name__ == "__main__":
    main()
