"""Bit-identity and reuse proofs for quant-lab protected-core perf fixes 2/3a/3b/3c/5.

Run with the edited core in front of the editable install:

  cd /home/quant/.devspace/worktrees/quant-lab-core-20261119
  PYTHONPATH=$PWD/src uv run --frozen --project /home/quant/dev/quant-lab python perf_audit/core/proof_core_fixes.py

Read-only against live data/registry (registry opened through a scratch sqlite backup).
Writes only perf_audit/core/proof_core_fixes.json.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

WORKTREE = Path(__file__).resolve().parents[2]
PERF = Path(__file__).resolve().parent
SCRATCH = PERF / "tmp_proof"
MAIN = Path("/home/quant/dev/quant-lab")
TOP100 = "data/exploratory/crypto_perps_top100_1h.parquet"
STREAM_CONTRACT = "CRYPTO_PERPS_TOP100_1H_V1"

import warden  # noqa: E402
from warden.leakage import compute_open_forward_label  # noqa: E402
from warden.registry import CORR_DUPLICATE, _eigen_rank, distinct_stream_indices  # noqa: E402

assert str(warden.__file__).startswith(str(WORKTREE)), f"wrong core: {warden.__file__}"
RESULT: dict = {"core": warden.__file__}

OK = {"checked": 0, "failed": []}


def check(name: str, cond: bool, detail: str = "") -> None:
    OK["checked"] += 1
    if not cond:
        OK["failed"].append(f"{name} :: {detail}")
        print(f"FAIL {name} {detail}")


# ---------------------------------------------------------------- HEAD reference
def head_open_forward_label(open_mat: np.ndarray, delay: int, horizon: int) -> np.ndarray:
    """Verbatim HEAD (29ccb57) loop from src/warden/leakage.py:60-74."""
    n_bars, n_assets = open_mat.shape
    forward_label = np.zeros((n_bars, n_assets), dtype=float)
    max_t = n_bars - delay - horizon
    for t in range(max_t):
        entry_open = open_mat[t + delay]
        exit_open = open_mat[t + delay + horizon]
        fwd = np.divide(exit_open - entry_open, entry_open, out=np.zeros_like(entry_open), where=entry_open > 0)
        forward_label[t] = fwd - np.mean(fwd)
    return forward_label


def head_distinct_stream_indices(matrix: np.ndarray, tol: float = CORR_DUPLICATE) -> list[int]:
    """Verbatim HEAD (29ccb57) loop from src/warden/registry.py:56-64."""
    keep: list[int] = []
    for j in range(matrix.shape[1]):
        col = matrix[:, j]
        if any(abs(float(np.corrcoef(col, matrix[:, k])[0, 1])) >= tol for k in keep):
            continue
        keep.append(j)
    return keep


def compare_labels(tag: str, mat: np.ndarray, delay: int, horizon: int) -> dict:
    old = head_open_forward_label(mat, delay, horizon)
    new = compute_open_forward_label(mat, delay, horizon)
    eq = bool(np.array_equal(old, new))
    tb = old.tobytes() == new.tobytes()
    check(f"label/{tag}", eq and tb, f"array_equal={eq} tobytes={tb} shape={mat.shape} delay={delay} h={horizon}")
    return {"case": tag, "shape": list(mat.shape), "delay": delay, "horizon": horizon,
            "array_equal": eq, "tobytes_equal": tb,
            "nonzero_bars": int(np.count_nonzero(new)), "nan_mismatch": int(np.count_nonzero(np.isnan(old) != np.isnan(new)))}


# ---------------------------------------------------------------- A: fix 3b
print("[A] compute_open_forward_label vectorisation", flush=True)
panel = pl.read_parquet(MAIN / TOP100, columns=["timestamp", "asset", "open"])
assets = sorted(panel["asset"].unique().to_list())
wide = panel.pivot(index="timestamp", on="asset", values="open").sort("timestamp").fill_null(0.0)
real_open = wide.select(assets).to_numpy()
rows = [{"case": "top100_open", **compare_labels("top100_d1_h24", real_open, 1, 24)}]
for delay, horizon in ((0, 1), (0, 24), (1, 1), (24, 24), (2, 5)):
    rows.append(compare_labels(f"top100_d{delay}_h{horizon}", real_open, delay, horizon))

rng = np.random.default_rng(20261119)
synthetic = []
for shape, delay, horizon in (((50, 1), 1, 1), ((50, 4), 0, 0), ((300, 7), 3, 21), ((5, 3), 6, 1),
                              ((1, 2), 0, 0), ((257, 5), 0, 24), ((129, 9), 2, 3)):
    m = rng.normal(100.0, 5.0, size=shape)
    mask = rng.random(shape)
    m = np.where(mask < 0.08, 0.0, m)          # zero opens: where=entry>0 must stay 0
    m = np.where(mask > 0.94, -np.abs(m), m)    # negative opens: same semantics
    if shape[0] > 3:
        m[1] = m[0]                            # equal consecutive rows
    synthetic.append(compare_labels(f"rand{shape}_{delay}_{horizon}", m, delay, horizon))
rows.extend(synthetic)
RESULT["fix3b_open_label"] = rows
RESULT["fix3b_summary"] = {"cases": len(rows), "all_identical": all(
    r.get("array_equal") and r.get("tobytes_equal") for r in rows if "array_equal" in r)}

# ---------------------------------------------------------------- B: fix 5
print("[B] distinct_stream_indices single corrcoef pass", flush=True)
SCRATCH.mkdir(parents=True, exist_ok=True)
snap = SCRATCH / f"registry_snapshot_{int(time.time() * 1000)}.db"
src = sqlite3.connect(f"file:{MAIN}/registry/registry.db?mode=ro", uri=True)
dst = sqlite3.connect(snap)
src.backup(dst)
dst.close()
src.close()
conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
q = ("SELECT run_id, returns_json FROM trials WHERE contract_id=? UNION ALL "
     "SELECT attempt_id, returns_json FROM selection_attempts WHERE contract_id=? "
     "AND returns_observed=1 AND returns_json NOT IN ('', '[]')")
streams = []
for run_id, rj in conn.execute(q, (STREAM_CONTRACT, STREAM_CONTRACT)).fetchall():
    if rj and rj != "[]":
        arr = np.array(json.loads(rj), dtype=float)
        if arr.ndim == 1 and np.isfinite(arr).all():
            streams.append((run_id, arr))
conn.close()
by_len: dict[int, list] = {}
for run_id, arr in streams:
    by_len.setdefault(len(arr), []).append((run_id, arr))
best_len = max(by_len, key=lambda n: len(by_len[n]))
group = by_len[best_len]
real_matrix = np.column_stack([a for _, a in group])
t0 = time.perf_counter()
old_keep = head_distinct_stream_indices(real_matrix)
old_s = time.perf_counter() - t0
t0 = time.perf_counter()
new_keep = distinct_stream_indices(real_matrix)
new_s = time.perf_counter() - t0
old_keff = _eigen_rank(real_matrix[:, old_keep]) if old_keep else 0.0
new_keff = _eigen_rank(real_matrix[:, new_keep]) if new_keep else 0.0
check("streams/real", old_keep == new_keep, f"old={old_keep} new={new_keep}")
check("streams/real_keff", old_keff == new_keff, f"{old_keff} != {new_keff}")
corr_old, corr_new = np.corrcoef(real_matrix, rowvar=False), np.corrcoef(real_matrix, rowvar=False)
RESULT["fix5_real_streams"] = {
    "contract": STREAM_CONTRACT, "snapshot": str(snap), "streams_available": len(streams),
    "aligned_len": best_len, "matrix_shape": list(real_matrix.shape),
    "run_ids": [r for r, _ in group], "keep_old": old_keep, "keep_new": new_keep,
    "keff_old": old_keff, "keff_new": new_keff,
    "old_loop_s": round(old_s, 4), "new_s": round(new_s, 4),
    "speedup": round(old_s / new_s, 2) if new_s else None,
}

# estimator agreement: same statistic, last-bit differences only (single-pass matmul vs per-pair dot)
off = ~np.eye(real_matrix.shape[1], dtype=bool)
delta = np.abs(corr_new - corr_new.T)
pairs = [(j, k) for j in range(real_matrix.shape[1]) for k in range(real_matrix.shape[1]) if off[j, k]]
max_delta = 0.0
for j, k in pairs:
    a = float(np.corrcoef(real_matrix[:, j], real_matrix[:, k])[0, 1])
    b = float(corr_new[j, k])
    if not np.isnan(a):
        max_delta = max(max_delta, abs(a - b))
RESULT["fix5_estimator_delta"] = {"max_abs_delta_real_streams": max_delta, "pairs": len(pairs)}
print(f"    max |delta| matrix vs per-pair estimator over {len(pairs)} pairs: {max_delta:.3e}", flush=True)

# near-duplicate injection on the real streams: keep lists must still be identical
inj = []
for eps in (0.02, 0.05, 0.1, 0.3, 0.6):
    r = np.random.default_rng(1000 + int(eps * 100))
    col = real_matrix[:, 0]
    extra = np.column_stack([col, col + eps * np.std(col) * r.normal(0, 1, real_matrix.shape[0]),
                             -(col + eps * np.std(col) * r.normal(0, 1, real_matrix.shape[0])),
                             real_matrix[:, 5]])
    m = np.column_stack([real_matrix, extra])
    ok_, nk_ = head_distinct_stream_indices(m), distinct_stream_indices(m)
    c = abs(float(np.corrcoef(m[:, 0], m[:, real_matrix.shape[1] + 1])[0, 1]))
    inj.append({"eps": eps, "corr_col0_vs_injected": c, "keep_old": ok_, "keep_new": nk_, "identical": ok_ == nk_})
    check(f"real_injected/eps{eps}", ok_ == nk_, f"{ok_} != {nk_}")
RESULT["fix5_real_injected"] = inj

# scan the snapshot for a real contract whose aligned history actually contains duplicates
dupe_case = None
conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
contracts = [r[0] for r in conn.execute(
    "SELECT DISTINCT contract_id FROM trials UNION SELECT DISTINCT contract_id FROM selection_attempts")]
for cid in sorted(contracts):
    qq = ("SELECT returns_json FROM trials WHERE contract_id=? UNION ALL SELECT returns_json FROM selection_attempts "
          "WHERE contract_id=? AND returns_observed=1 AND returns_json NOT IN ('', '[]')")
    by_len_c: dict[int, list] = {}
    for (rj,) in conn.execute(qq, (cid, cid)).fetchall():
        if rj and rj != "[]":
            arr = np.array(json.loads(rj), dtype=float)
            if arr.ndim == 1 and np.isfinite(arr).all() and float(np.std(arr)) >= 1e-12:
                by_len_c.setdefault(len(arr), []).append(arr)
    for n, group_c in by_len_c.items():
        if len(group_c) < 2:
            continue
        m = np.column_stack(group_c)
        ok_, nk_ = head_distinct_stream_indices(m), distinct_stream_indices(m)
        if len(ok_) < m.shape[1]:
            cm = np.corrcoef(m, rowvar=False)
            dupe_case = {"contract": cid, "aligned_len": n, "matrix_shape": list(m.shape),
                         "keep_old": ok_, "keep_new": nk_, "identical": ok_ == nk_,
                         "deduped_pairs_above_tol": int(np.sum(np.abs(np.triu(cm, 1)) >= CORR_DUPLICATE)),
                         "keff_old": _eigen_rank(m[:, ok_]), "keff_new": _eigen_rank(m[:, nk_])}
            check(f"real_dedup/{cid}", ok_ == nk_, f"{ok_} != {nk_}")
            break
    if dupe_case:
        break
conn.close()
RESULT["fix5_real_dedup_case"] = dupe_case
print(f"    real dedup case: {dupe_case and (dupe_case['contract'], dupe_case['matrix_shape'], dupe_case['identical'])}", flush=True)

# decision-flip window: only |corr| within the estimator's last bits of the threshold may differ
def _scale_for(base, noise, target, delta, pick):
    """Scale on base+scale*noise whose corr estimate sits at target+delta for `pick`."""
    lo, hi = 0.0, 80.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        c = pick(base, base + mid * noise)
        lo, hi = (mid, hi) if c >= target + delta else (lo, mid)
    return 0.5 * (lo + hi)


def _corr_pair(x, y):
    return abs(float(np.corrcoef(x, y)[0, 1]))


def _corr_mat(x, y):
    return abs(float(np.corrcoef(np.column_stack([x, y]), rowvar=False)[0, 1]))


flip_window = {}
worst_flip_gap = 0.0
for delta in (0.0, 1e-16, 1e-15, 1e-14, 1e-13, 1e-12, 1e-11, 1e-10):
    flips, worst = 0, 0.0
    for seed in range(40):
        r = np.random.default_rng(seed)
        base, noise = r.normal(0, 1, 400), r.normal(0, 1, 400)
        for sign in (1.0, -1.0):
            scale = _scale_for(base, noise, CORR_DUPLICATE, sign * delta, _corr_pair)
            cp = _corr_pair(base, base + scale * noise)
            cm = _corr_mat(base, base + scale * noise)
            if (cm >= CORR_DUPLICATE) != (cp >= CORR_DUPLICATE):
                flips += 1
                worst = max(worst, abs(cp - CORR_DUPLICATE), abs(cm - CORR_DUPLICATE))
                worst_flip_gap = max(worst_flip_gap, worst)
    flip_window[f"{delta:.0e}"] = {"cases": 80, "flips": flips, "worst_gap_to_tol": worst}
RESULT["fix5_flip_window"] = flip_window
RESULT["fix5_flip_window"]["observed_max_gap_among_flips"] = worst_flip_gap
check("flip_window/1e-12", flip_window["1e-12"]["flips"] == 0, json.dumps(flip_window["1e-12"]))
check("flip_window/1e-10", flip_window["1e-10"]["flips"] == 0, json.dumps(flip_window["1e-10"]))
print(f"    flip window: max |corr-tol| among flips = {worst_flip_gap:.3e}; "
      f"flips at 1e-12 = {flip_window['1e-12']['flips']}", flush=True)


# near-duplicate boundary matrices around |corr| = 0.9995
boundary = []
for eps in (0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0):
    r = np.random.default_rng(7 + int(eps * 100))
    base = r.normal(0, 1, size=500)
    noise = r.normal(0, 1, size=500)
    dup = base + eps * noise
    m = np.column_stack([base, dup, -dup, base + 3 * noise, r.normal(0, 1, 500)])
    ok_, nk_ = head_distinct_stream_indices(m), distinct_stream_indices(m)
    c = float(np.corrcoef(m[:, 0], m[:, 1])[0, 1])
    c_neg = float(np.corrcoef(m[:, 0], m[:, 2])[0, 1])
    boundary.append({"eps": eps, "corr_pos": c, "corr_neg": c_neg, "keep_old": ok_, "keep_new": nk_,
                     "identical": ok_ == nk_})
    check(f"boundary/eps{eps}", ok_ == nk_, f"{ok_} != {nk_} corr={c}")
for tag, m in (("random_small", np.random.default_rng(11).normal(0, 1, (200, 6))),
               ("single_col", np.random.default_rng(12).normal(0, 1, (200, 1))),
               ("constant_col", np.column_stack([np.ones(200), np.random.default_rng(13).normal(0, 1, 200),
                                                 np.ones(200)])),
               ("zero_matrix", np.zeros((200, 4))),
               ("empty_cols", np.zeros((200, 0)))):
    ok_, nk_ = head_distinct_stream_indices(m), distinct_stream_indices(m)
    boundary.append({"case": tag, "keep_old": ok_, "keep_new": nk_, "identical": ok_ == nk_})
    check(f"edge/{tag}", ok_ == nk_, f"{ok_} != {nk_}")
RESULT["fix5_boundary"] = boundary

# ---------------------------------------------------------------- C: fix 3a
print("[C] lean child loader", flush=True)
child_mods = subprocess.run(
    [sys.executable, "-c",
     "import json, sys; import warden.strategy_loader as s; "
     "print(json.dumps({'file': s.__file__, 'heavy': [m for m in ('scipy', 'ml4t', 'warden.runner', "
     "'warden.registry', 'pandas') if m in sys.modules]}))"],
    capture_output=True, text=True, check=True, cwd=WORKTREE,
    env={"PYTHONPATH": str(WORKTREE / "src"), "PATH": "/usr/bin:/bin"})
lean = json.loads(child_mods.stdout)
re_export = subprocess.run(
    [sys.executable, "-c",
     "from warden.runner import load_module_from_path as a; "
     "from warden.strategy_loader import load_module_from_path as b; print(a is b)"],
    capture_output=True, text=True, check=True, cwd=WORKTREE,
    env={"PYTHONPATH": str(WORKTREE / "src"), "PATH": "/usr/bin:/bin"})
check("loader/lean", lean["heavy"] == [], f"heavy imports present: {lean['heavy']}")
check("loader/re_export", re_export.stdout.strip() == "True", re_export.stdout.strip())
RESULT["fix3a_loader"] = {"strategy_loader_file": lean["file"], "heavy_imports_in_child": lean["heavy"],
                          "runner_re_export_identity": re_export.stdout.strip() == "True"}

# ---------------------------------------------------------------- D: fix 3c (real fixture)
print("[D] screen->backtest position reuse on the real fixture spec", flush=True)
sys.path.insert(0, str(PERF.parent))
import bench_warden as BW  # noqa: E402
import warden.runner as R  # noqa: E402

calls = {"n": 0}
_orig_iso = R._run_strategy_isolated


def _counted(*a, **k):
    calls["n"] += 1
    return _orig_iso(*a, **k)


def _rows(root: Path, run_id: str):
    c = sqlite3.connect(f"file:{root}/registry/registry.db?mode=ro", uri=True)
    row = c.execute("SELECT returns_json, verdict, net_sharpe, gross_sharpe, deflated_sharpe_prob, "
                    "effective_trials_keff, stress_1p5_cost_sharpe, stress_2x_cost_sharpe, delay_plus1_sharpe, "
                    "diagnostics_json FROM trials WHERE run_id=?", (run_id,)).fetchone()
    c.close()
    return row


R._run_strategy_isolated = _counted
try:
    # both roots run the same screen first, so registry evidence is identical; the only
    # difference is whether the spec reuses the screen's positions or re-runs the child
    root_a = BW.fresh_root("proof_reuse")
    runner_a = R.ExperimentRunner(repo_root=root_a)
    calls["n"] = 0
    screen = runner_a.run_stage1_screen(root_a / "spec.yaml")
    screen_calls = calls["n"]
    calls["n"] = 0
    res_reuse = runner_a.run_spec(root_a / "spec.yaml", override_run_id="RUN-PROOF-REUSE")
    reuse_calls = calls["n"]

    root_b = BW.fresh_root("proof_fresh")
    runner_b1 = R.ExperimentRunner(repo_root=root_b)
    runner_b1.run_stage1_screen(root_b / "spec.yaml")
    runner_b2 = R.ExperimentRunner(repo_root=root_b)  # fresh runner: empty cache
    calls["n"] = 0
    res_fresh = runner_b2.run_spec(root_b / "spec.yaml", override_run_id="RUN-PROOF-REUSE")
    fresh_calls = calls["n"]
finally:
    R._run_strategy_isolated = _orig_iso

row_a, row_b = _rows(root_a, "RUN-PROOF-REUSE"), _rows(root_b, "RUN-PROOF-REUSE")
fields = ("verdict", "net_sharpe", "gross_sharpe", "deflated_sharpe_prob", "effective_trials_keff",
          "stress_1p5_cost_sharpe", "stress_2x_cost_sharpe", "delay_plus1_sharpe")
diff = {f: (row_a[i + 1], row_b[i + 1]) for i, f in enumerate(fields) if row_a[i + 1] != row_b[i + 1]}
check("reuse/child_skipped", screen_calls == 1 and reuse_calls == 0 and fresh_calls == 1,
      f"screen={screen_calls} spec_with_cache={reuse_calls} spec_fresh_runner={fresh_calls}")
check("reuse/returns_bit_identical", row_a[0] == row_b[0], "returns_json differs")
check("reuse/verdict_and_metrics", not diff and res_reuse.verdict == res_fresh.verdict, json.dumps(diff))
check("reuse/diagnostics_identical", row_a[9] == row_b[9], "diagnostics_json differs")
RESULT["fix3c_reuse"] = {
    "root_reuse": str(root_a), "root_fresh": str(root_b), "screen_passed": bool(screen.passed),
    "child_calls": {"screen": screen_calls, "spec_after_screen": reuse_calls, "spec_fresh_runner": fresh_calls},
    "returns_json_identical": row_a[0] == row_b[0], "returns_len": len(json.loads(row_a[0])),
    "verdict": [res_reuse.verdict, res_fresh.verdict], "field_mismatches": diff,
    "diagnostics_json_identical": row_a[9] == row_b[9],
    "net_sharpe": res_reuse.net_sharpe, "deflated_sharpe_prob": res_reuse.deflated_sharpe_prob,
}

# a different params dict on the same real root must miss the cache (child re-runs)
c = sqlite3.connect(f"{root_a}/registry/registry.db")
import yaml as _yaml
cfg = _yaml.safe_load((root_a / "spec.yaml").read_text())
row = c.execute("SELECT contract_id, title, strategy_family, economic_mechanism, counterparty_or_friction, "
                "expected_holding_bars, falsification_criteria FROM hypotheses WHERE hypothesis_id=?",
                (BW.BENCH_HYP,)).fetchone()
c.execute("INSERT INTO hypotheses (hypothesis_id, contract_id, title, strategy_family, economic_mechanism, "
          "counterparty_or_friction, expected_holding_bars, falsification_criteria, parent_hypothesis_id, "
          "source_provenance, llm_model_id, strategy_module, status, trials_used, created_at, attempts_json) "
          "VALUES (?,?,?,?,?,?,?,?,NULL,'internal_research','proof','', 'ACTIVE',0,?,'{}')",
          ("HYP-PROOF-PARAMS", *row, time.time()))
c.commit()
c.close()
reordered = list(reversed(cfg["parameters"]["target_symbols"]))
cfg2 = dict(cfg, hypothesis_id="HYP-PROOF-PARAMS", parameters={"target_symbols": reordered})
(root_a / "spec_params.yaml").write_text(_yaml.safe_dump(cfg2))
R._run_strategy_isolated = _counted
try:
    calls["n"] = 0
    res_params = runner_a.run_spec(root_a / "spec_params.yaml", override_run_id="RUN-PROOF-PARAMS")
    params_calls = calls["n"]
finally:
    R._run_strategy_isolated = _orig_iso
params_returns = _rows(root_a, "RUN-PROOF-PARAMS")[0]
check("reuse/params_miss", params_calls == 1, f"child calls={params_calls}")
RESULT["fix3c_no_leak_params"] = {"child_calls": params_calls, "verdict": res_params.verdict,
                                  "params_used": {"target_symbols": reordered},
                                  "returns_equal_cached": params_returns == row_a[0],
                                  "note": "same four-name universe, order-normalised by the strategy"}

# ---------------------------------------------------------------- E: fix 2 (real verify path)
print("[E] cache-first verify compute counts", flush=True)
import warden.leakage as L  # noqa: E402
from warden.contracts import load_contract  # noqa: E402
from warden.registry import ResearchRegistry  # noqa: E402

contract = load_contract(MAIN / BW.CONTRACT_REL)
mod = R.load_module_from_path(MAIN / "strategies/candidates/soc_wbx_downshock_1h_20261031.py")
panel2 = pl.read_parquet(MAIN / contract.exploratory_data_path).filter(
    pl.col("asset").is_in(["BTCUSDT", "ETHUSDT", "LTCUSDT", "XRPUSDT"]))
raw_compute = mod.ShockVolumeFeaturePipeline.compute
counted = {"n": 0}


def _counted_compute(self, bars_df, c):
    counted["n"] += 1
    return raw_compute(self, bars_df, c)


mod.ShockVolumeFeaturePipeline.compute = _counted_compute
root_e = BW.fresh_root("proof_verify")
reg_e = ResearchRegistry(root_e / "registry" / "registry.db")
cache_e = root_e / "cache" / "features"
counted["n"] = 0
t0 = time.perf_counter()
cold = L.compute_and_cache_features(mod.pipeline, panel2, contract, cache_dir=cache_e, registry=reg_e)
cold_s = time.perf_counter() - t0
cold_n = counted["n"]
counted["n"] = 0
t0 = time.perf_counter()
warm = L.compute_and_cache_features(mod.pipeline, panel2, contract, cache_dir=cache_e, registry=reg_e)
warm_s = time.perf_counter() - t0
warm_n = counted["n"]
mod.ShockVolumeFeaturePipeline.compute = raw_compute
same = cold.equals(warm)
check("verify/cold_counts", cold_n == 10, f"cold computes={cold_n} (9 invariance + 1 final expected)")
check("verify/warm_counts", warm_n == 0, f"warm computes={warm_n}")
check("verify/warm_identical", same, "warm frame differs from cold frame")
fix2_old_cold = cold_n + 9   # HEAD cmd_verify ran a standalone 9-compute pass before the cache step
RESULT["fix2_verify"] = {"cold_computes": cold_n, "warm_computes": warm_n, "cold_s": round(cold_s, 3),
                         "warm_s": round(warm_s, 4), "warm_frame_equals_cold": same,
                         "head_cmd_verify_cold_computes": fix2_old_cold, "head_cmd_verify_warm_computes": 9}

RESULT["checks"] = {"checked": OK["checked"], "failed": OK["failed"]}
(SCRATCH / "..").mkdir(exist_ok=True)
(PERF / "proof_core_fixes.json").write_text(json.dumps(RESULT, indent=1), encoding="utf-8")
print(json.dumps({k: RESULT[k] for k in ("fix3b_summary", "fix3a_loader", "checks")}, indent=1))
for d in sorted(PERF.parent.glob("tmp_proof_*")):
    shutil.rmtree(d, ignore_errors=True)
shutil.rmtree(SCRATCH, ignore_errors=True)
print(f"total checks={OK['checked']} failed={len(OK['failed'])}")
sys.exit(1 if OK["failed"] else 0)
