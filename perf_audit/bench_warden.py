"""ultraperf evaluator for quant-lab (read-only vs live repo; writes only to scratch tmp roots).

Governance: never opens the live registry for writes (sqlite backup from mode=ro source),
never touches data/holdout, never runs qualify. Fixture hypothesis HYP-BENCH-ULWPERF is
inserted into each scratch registry copy so trial budgets on the copy are not exhausted.

Modes: micro | screen | backtest | verify | profile | selhist
Run:  cd /home/quant/dev/quant-lab && uv run --frozen python <this file> <mode> [--reps N]
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import os
import pickle
import pstats
import shutil
import sqlite3
import subprocess
import sys
import time
from functools import wraps
from pathlib import Path

import polars as pl
import yaml

MAIN = Path("/home/quant/dev/quant-lab")
SCRATCH = Path(__file__).resolve().parent
SPEC_REL = "experiments/active/soc_wbx_downshock_1h_20261031.yaml"
CONTRACT_REL = "contracts/auto_crypto_perps_top100_1h_v1_h24_e24_f45_s20_v1.yaml"
BENCH_HYP = "HYP-BENCH-ULWPERF"

TIMES: dict[str, list] = {}


def timed(name, obj, attr):
    orig = getattr(obj, attr)

    @wraps(orig)
    def w(*a, **k):
        t0 = time.perf_counter()
        r = orig(*a, **k)
        e = TIMES.setdefault(name, [0.0, 0])
        e[0] += time.perf_counter() - t0
        e[1] += 1
        return r

    setattr(obj, attr, w)


def instrument(mode: str):
    import warden.runner as R
    import warden.provenance as P
    import warden.gates as G
    import warden.registry as REG
    import warden.diagnostics as D
    import warden.leakage as L

    timed("resolve_dataset(incl sha256)", R, "resolve_dataset")
    timed("resolve_dataset(incl sha256)", P, "resolve_dataset")
    timed("pl.read_parquet", pl, "read_parquet")
    timed("strategy_isolated_total", R, "_run_strategy_isolated")
    timed("child_subprocess_wall", R.subprocess, "run")
    timed("pickle_dump_panel", R.pickle, "dump")
    timed("pickle_load_result", R.pickle, "load")
    timed("execution_returns", R, "execution_returns")
    timed("aligned_matrix", R, "aligned_matrix")
    timed("simulate_positions", R, "simulate_positions")
    timed("funding_mask_pyloop", R, "funding_settlement_mask")
    timed("stage1_rules_gate", R, "evaluate_stage1_rules_ts")
    timed("stage1_rules_gate", G, "evaluate_stage1_rules_ts")
    timed("selection_report_DSR", R, "compute_selection_report")
    timed("selection_report_DSR", REG, "compute_selection_report")
    timed("shift_skill_diag", R, "compute_shift_skill_pvalue")
    timed("shift_skill_diag", D, "compute_shift_skill_pvalue")
    timed("trade_metrics", R, "compute_trade_metrics")
    timed("code_closure", R, "code_closure")
    timed("registry.selection_history", REG.ResearchRegistry, "selection_history")
    timed("registry.record_trial", REG.ResearchRegistry, "record_trial")
    timed("registry.record_selection_attempt", REG.ResearchRegistry, "record_selection_attempt")
    timed("registry.register_trial_start", REG.ResearchRegistry, "register_trial_start")
    if mode == "verify":
        timed("verify_prefix_invariance", L, "verify_prefix_invariance")
        timed("compute_and_cache_features", L, "compute_and_cache_features")


def fresh_root(tag: str) -> Path:
    root = SCRATCH / f"tmp_{tag}_{int(time.time()*1000)}"
    (root / "registry").mkdir(parents=True)
    src = sqlite3.connect(f"file:{MAIN}/registry/registry.db?mode=ro", uri=True)
    dst = sqlite3.connect(root / "registry" / "registry.db")
    src.backup(dst)
    dst.close()
    src.close()
    for name in ("contracts", "data"):
        os.symlink(MAIN / name, root / name)
    shutil.copytree(MAIN / "strategies", root / "strategies",
                    ignore=shutil.ignore_patterns("__pycache__"))
    # fixture hypothesis with fresh budget, same economics as the campaign hypothesis
    cfg = yaml.safe_load((MAIN / SPEC_REL).read_text())
    conn = sqlite3.connect(root / "registry" / "registry.db")
    row = conn.execute(
        "SELECT title, economic_mechanism, counterparty_or_friction, expected_holding_bars, "
        "falsification_criteria, strategy_family, contract_id FROM hypotheses WHERE hypothesis_id=?",
        (cfg["hypothesis_id"],)).fetchone()
    conn.execute(
        "INSERT INTO hypotheses (hypothesis_id, contract_id, title, strategy_family, economic_mechanism, "
        "counterparty_or_friction, expected_holding_bars, falsification_criteria, parent_hypothesis_id, "
        "source_provenance, llm_model_id, strategy_module, status, trials_used, created_at, attempts_json) "
        "VALUES (?,?,?,?,?,?,?,?,NULL,'internal_research','ultraperf-bench','', 'ACTIVE',0,?,'{}')",
        (BENCH_HYP, row[6], row[0], row[5], row[1], row[2], row[3], row[4], time.time()))
    conn.commit()
    conn.close()
    cfg["hypothesis_id"] = BENCH_HYP
    cfg.pop("run_id", None)
    spec = root / "spec.yaml"
    spec.write_text(yaml.safe_dump(cfg))
    return root


def env_record() -> dict:
    import platform
    return {
        "python": sys.version.split()[0], "polars": pl.__version__,
        "cpus": os.cpu_count(), "loadavg": os.getloadavg(),
        "git_head": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=MAIN, text=True).strip(),
        "spec": SPEC_REL,
    }


def run_rep(mode: str, rep: int) -> dict:
    from warden.runner import ExperimentRunner
    root = fresh_root(f"{mode}{rep}")
    TIMES.clear()
    runner = ExperimentRunner(repo_root=root)
    t0 = time.perf_counter()
    err = None
    verdict = None
    try:
        if mode == "screen":
            rep_obj = runner.run_stage1_screen(root / "spec.yaml")
            verdict = f"passed={rep_obj.passed}"
        else:
            res = runner.run_spec(root / "spec.yaml", override_run_id=f"RUN-BENCH-ULWPERF-{rep}")
            verdict = res.verdict
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    wall = time.perf_counter() - t0
    out = {"mode": mode, "rep": rep, "wall_s": round(wall, 3), "verdict": verdict, "error": err,
           "phases": {k: {"s": round(v[0], 3), "calls": v[1]} for k, v in sorted(TIMES.items(), key=lambda kv: -kv[1][0])}}
    shutil.rmtree(root, ignore_errors=True)
    return out


def mode_micro():
    import warden.provenance as P
    import warden.runner as R
    from warden.contracts import load_contract
    contract = load_contract(MAIN / CONTRACT_REL)
    data_path = MAIN / contract.exploratory_data_path
    out = {}
    t0 = time.perf_counter()
    sha = P.file_sha256(data_path)
    out["file_sha256_read_bytes"] = {"s": round(time.perf_counter() - t0, 3), "mb": round(data_path.stat().st_size / 1e6, 1)}
    t0 = time.perf_counter()
    panel = pl.read_parquet(data_path)
    out["read_parquet_full"] = {"s": round(time.perf_counter() - t0, 3), "rows": panel.height, "cols": panel.width}
    t0 = time.perf_counter()
    p2 = panel.filter(pl.col("asset").is_in(["BTCUSDT", "ETHUSDT", "LTCUSDT", "XRPUSDT"]))
    out["filter_4_symbols"] = {"s": round(time.perf_counter() - t0, 3), "rows": p2.height}
    t0 = time.perf_counter()
    p2 = p2.sort("timestamp", "asset")
    out["sort_filtered"] = {"s": round(time.perf_counter() - t0, 3)}
    t0 = time.perf_counter()
    pfull = panel.sort("timestamp", "asset")
    out["sort_full_panel"] = {"s": round(time.perf_counter() - t0, 3)}
    assets = sorted(p2["asset"].unique().to_list())
    t0 = time.perf_counter()
    ret = R.execution_returns(p2, contract, assets)
    out["execution_returns_4sym"] = {"s": round(time.perf_counter() - t0, 3)}
    ts = sorted(p2["timestamp"].unique().to_list())
    t0 = time.perf_counter()
    mask = R.funding_settlement_mask(ts, contract)
    out["funding_mask_pyloop"] = {"s": round(time.perf_counter() - t0, 3), "n_ts": len(ts)}
    t0 = time.perf_counter()
    subprocess.run([sys.executable, "-c", "import warden.runner"], capture_output=True, check=True)
    out["child_import_warden_runner"] = {"s": round(time.perf_counter() - t0, 3)}
    # pickle round-trip cost for the filtered panel (what _run_strategy_isolated pays)
    t0 = time.perf_counter()
    blob = pickle.dumps((str(MAIN / "strategies/candidates/soc_wbx_downshock_1h_20261031.py"), p2, contract, {}))
    dump_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    pickle.loads(blob)
    out["pickle_panel_roundtrip"] = {"dump_s": round(dump_s, 3), "load_s": round(time.perf_counter() - t0, 3), "mb": round(len(blob) / 1e6, 2)}
    print(json.dumps(out, indent=1))


def mode_selhist():
    """Raw cost of loading+JSON-parsing every stored return stream, per contract."""
    conn = sqlite3.connect(f"file:{MAIN}/registry/registry.db?mode=ro", uri=True)
    out = {}
    for cid in ("AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1", "CRYPTO_PERPS_TOP100_1H_V1", "CRYPTO_PERPS_1H_V1"):
        t0 = time.perf_counter()
        rows = conn.execute(
            "SELECT run_id, returns_json FROM trials WHERE contract_id=? UNION ALL "
            "SELECT attempt_id, returns_json FROM selection_attempts WHERE contract_id=? "
            "AND returns_observed=1 AND returns_json NOT IN ('', '[]')", (cid, cid)).fetchall()
        fetch_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        n = 0
        for _, rj in rows:
            if rj and rj != "[]":
                json.loads(rj)
                n += 1
        out[cid] = {"rows": len(rows), "streams_parsed": n, "fetch_s": round(fetch_s, 3),
                    "json_parse_s": round(time.perf_counter() - t0, 3),
                    "json_mb": round(sum(len(r[1] or "") for r in rows) / 1e6, 2)}
    conn.close()
    print(json.dumps(out, indent=1))


def mode_verify(reps: int):
    import warden.leakage as L
    from warden.contracts import load_contract
    from warden.strategy_loader import load_module_from_path
    from warden.registry import ResearchRegistry
    instrument("verify")
    contract = load_contract(MAIN / CONTRACT_REL)
    mod = load_module_from_path(MAIN / "strategies/candidates/soc_wbx_downshock_1h_20261031.py")
    panel = pl.read_parquet(MAIN / contract.exploratory_data_path)
    panel = panel.filter(pl.col("asset").is_in(["BTCUSDT", "ETHUSDT", "LTCUSDT", "XRPUSDT"]))
    computes = {"n": 0, "s": 0.0}
    orig_compute = mod.ShockVolumeFeaturePipeline.compute

    @wraps(orig_compute)
    def counted(self, bars_df, c):
        t0 = time.perf_counter()
        r = orig_compute(self, bars_df, c)
        computes["n"] += 1
        computes["s"] += time.perf_counter() - t0
        return r

    mod.ShockVolumeFeaturePipeline.compute = counted
    results = []
    for rep in range(reps):
        root = fresh_root(f"verify{rep}")
        cache = root / "cache" / "features"
        reg = ResearchRegistry(root / "registry" / "registry.db")
        TIMES.clear()
        computes.update(n=0, s=0.0)
        t0 = time.perf_counter()
        L.verify_prefix_invariance(mod.pipeline, panel, contract)
        inv_s = time.perf_counter() - t0
        n_inv = computes["n"]
        t0 = time.perf_counter()
        L.compute_and_cache_features(mod.pipeline, panel, contract, cache_dir=cache, registry=reg)
        cache_s = time.perf_counter() - t0
        cold_computes = computes["n"]
        # warm pass: cmd_verify still runs verify_prefix_invariance before the cache check
        computes.update(n=0, s=0.0)
        t0 = time.perf_counter()
        L.verify_prefix_invariance(mod.pipeline, panel, contract)
        inv_s2 = time.perf_counter() - t0
        t0 = time.perf_counter()
        L.compute_and_cache_features(mod.pipeline, panel, contract, cache_dir=cache, registry=reg)
        warm_cache_s = time.perf_counter() - t0
        results.append({
            "rep": rep,
            "cold_verify_wall_s": round(inv_s + cache_s, 3),
            "cold_prefix_invariance_s": round(inv_s, 3),
            "cold_cache_step_s": round(cache_s, 3),
            "pipeline_computes_cold": cold_computes,
            "pipeline_computes_warm": computes["n"],
            "warm_verify_wall_s": round(inv_s2 + warm_cache_s, 3),
            "warm_prefix_invariance_s": round(inv_s2, 3),
            "warm_cache_hit_s": round(warm_cache_s, 3),
        })
        shutil.rmtree(root, ignore_errors=True)
    print(json.dumps(results, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["micro", "screen", "backtest", "verify", "profile", "selhist"])
    ap.add_argument("--reps", type=int, default=4, help="1 warm-up + (reps-1) measured")
    args = ap.parse_args()
    print(json.dumps({"env": env_record()}), flush=True)
    if args.mode == "micro":
        mode_micro()
    elif args.mode == "selhist":
        mode_selhist()
    elif args.mode == "verify":
        mode_verify(args.reps)
    elif args.mode in ("screen", "backtest"):
        instrument(args.mode)
        results = [run_rep(args.mode, i) for i in range(args.reps)]
        for r in results:
            print(json.dumps(r), flush=True)
        measured = [r["wall_s"] for r in results[1:] if r["error"] is None]
        if measured:
            measured.sort()
            print(json.dumps({"summary": {"mode": args.mode, "warmup": 1, "measured_reps": len(measured),
                                          "median_s": measured[len(measured)//2], "min_s": measured[0], "max_s": measured[-1]}}))
    elif args.mode == "profile":
        instrument("backtest")
        from warden.runner import ExperimentRunner
        root = fresh_root("profile")
        runner = ExperimentRunner(repo_root=root)
        pr = cProfile.Profile()
        pr.enable()
        runner.run_spec(root / "spec.yaml", override_run_id="RUN-BENCH-ULWPERF-PROF")
        pr.disable()
        s = io.StringIO()
        ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
        ps.print_stats(35)
        print(s.getvalue())
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
