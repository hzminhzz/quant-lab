"""Observed prototype: verify+screen+backtest in ONE process (no repo edits).
Same warden functions the CLI calls; one interpreter, one env sync, shared panel read for verify.
"""
import json
import time
from pathlib import Path

import polars as pl

import bench_warden as B

B.instrument("verify")
import warden.leakage as L
from warden.cli import check_worktree
from warden.runner import ExperimentRunner, load_module_from_path

results = []
for rep in range(2):
    root = B.fresh_root(f"composite{rep}")
    runner = ExperimentRunner(repo_root=root)
    contract = runner.resolve_contract("AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1")
    mod = load_module_from_path(root / "strategies/candidates/soc_wbx_downshock_1h_20261031.py")
    t0 = time.perf_counter()
    wt = check_worktree(B.MAIN)
    panel = pl.read_parquet(root / contract.exploratory_data_path)
    panel = panel.filter(pl.col("asset").is_in(["BTCUSDT", "ETHUSDT", "LTCUSDT", "XRPUSDT"]))
    L.verify_prefix_invariance(mod.pipeline, panel, contract)
    L.compute_and_cache_features(mod.pipeline, panel, contract,
                                 cache_dir=root / "cache/features", registry=runner.registry)
    t_verify = time.perf_counter() - t0
    t0 = time.perf_counter()
    rep_obj = runner.run_stage1_screen(root / "spec.yaml")
    t_screen = time.perf_counter() - t0
    t0 = time.perf_counter()
    res = runner.run_spec(root / "spec.yaml", override_run_id=f"RUN-BENCH-COMP-{rep}")
    t_backtest = time.perf_counter() - t0
    results.append({"rep": rep, "worktree_ok": wt,
                    "verify_s": round(t_verify, 3), "screen_s": round(t_screen, 3),
                    "backtest_s": round(t_backtest, 3),
                    "composite_wall_s": round(t_verify + t_screen + t_backtest, 3),
                    "screen_passed": rep_obj.passed, "verdict": res.verdict})
print(json.dumps(results, indent=1))
