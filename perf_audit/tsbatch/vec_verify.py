"""Bit-identity + timing gate for ts position-loop vectorisation (perf_audit only).

Compares perf_audit/tsbatch/old/<mod>.py (git HEAD copy) with the worktree module:
  gate1: np.array_equal + tobytes identical on the contract panel and edge panels
  gate2: identical exception type+message on malformed inputs
  gate3: warden.leakage.verify_prefix_invariance with the new module pipeline
  timing: in-process median of 5 generate_positions calls, old vs new

Read-only with respect to data/ (parquet reads only). No registry writes.
Usage: uv run --frozen --project /home/quant/dev/quant-lab python perf_audit/tsbatch/vec_verify.py [module ...]
"""
from __future__ import annotations

import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

WT = Path(__file__).resolve().parents[2]
OLD = WT / "perf_audit" / "tsbatch" / "old"

MODULES = {
    "ts_s06_slow_trend_long_short_1h": {
        "contract": "contracts/multi_asset_1h_v1.yaml",
        "spec": "experiments/active/af30/06_slow_trend_long_short_screen.yaml",
    },
    "ts_s19_xs_momentum_1h": {
        "contract": "contracts/multi_asset_1h_v1.yaml",
        "spec": "experiments/active/af30/19_xs_momentum_screen.yaml",
    },
    "rsi_ema_vwap_pullback_1h": {
        "contract": "contracts/indices_1h_v1.yaml",
        "spec": "experiments/active/hyp_2026_003.yaml",
    },
    "ts_london_orb_1h": {
        "contract": "contracts/crypto_majors_1h_v1.yaml",
        "spec": "experiments/active/failed/hyp_2026_008_t1.yaml",
    },
    "carver_breakout_daily": {
        "contract": "contracts/multi_asset_1d_v1.yaml",
        "spec": "experiments/active/hyp_2026_094_t1.yaml",
    },
}


def load_module(path: Path, tag: str):
    name = f"{tag}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_contract(rel: str):
    from warden.contracts import load_contract as lc

    return lc(WT / rel)


def spec_params(rel: str) -> dict:
    import yaml

    cfg = yaml.safe_load((WT / rel).read_text(encoding="utf-8"))
    return dict(cfg.get("parameters") or {})


def base_panel(n_bars: int, assets: list[str], seed: int = 7) -> pl.DataFrame:
    """Synthetic 1h panel: uptrend+cascade (long stop), downtrend+spike (short stop),
    chop with an 08:00 breakout gap and volume spike (ORB entry)."""
    rng = np.random.default_rng(seed)
    ts = pl.datetime_range(
        start=pl.datetime(2023, 1, 1), end=pl.datetime(2023, 1, 1) + pl.duration(hours=n_bars - 1),
        interval="1h", eager=True, time_zone="UTC",
    )
    step = np.arange(n_bars, dtype=float)
    hour = (step % 24).astype(int)
    gap = np.where((hour == 8) | (hour == 9), 6.0, 0.0)
    shapes = []
    # asset 0: slow rise then a sharp cascade (long entries get stopped mid-window)
    up = 100.0 + 0.05 * step
    crash = np.where(step > n_bars * 0.6, -(step - n_bars * 0.6) * 0.45, 0.0)
    shapes.append(up + crash)
    # asset 1: slow fall then a sharp spike (short entries get stopped mid-window)
    down = 200.0 - 0.06 * step
    spike = np.where(step > n_bars * 0.5, (step - n_bars * 0.5) * 0.6, 0.0)
    shapes.append(down + spike)
    # asset 2: choppy mean reversion with deep dips (RSI/ORB activity)
    shapes.append(150.0 + 6.0 * np.sin(step / 5.0) + rng.normal(0.0, 0.35, n_bars))
    rows = []
    for ai, a in enumerate(assets):
        chevron = gap if ai % 2 else -gap
        close = shapes[ai % len(shapes)] + chevron + rng.normal(0.0, 0.05, n_bars)
        high = close + np.abs(rng.normal(0.4, 0.15, n_bars))
        low = close - np.abs(rng.normal(0.4, 0.15, n_bars))
        vol = 1000.0 + 400.0 * (np.arange(n_bars) % 7) * np.abs(np.sin(step / 3.0))
        vol = np.where((hour == 8) | (hour == 9), vol * 6.0, vol)
        rows.append(
            pl.DataFrame(
                {
                    "timestamp": ts,
                    "asset": [a] * n_bars,
                    "open": close,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": vol,
                    "funding_rate": np.zeros(n_bars),
                }
            )
        )
    return pl.concat(rows).sort("timestamp", "asset")


def flat_panel(n_bars: int = 300, assets: list[str] | None = None) -> pl.DataFrame:
    assets = assets or ["AAA", "BBB", "CCC"]
    ts = pl.datetime_range(
        start=pl.datetime(2023, 1, 1), end=pl.datetime(2023, 1, 1) + pl.duration(hours=n_bars - 1),
        interval="1h", eager=True, time_zone="UTC",
    )
    rows = []
    for a in assets:
        rows.append(
            pl.DataFrame(
                {
                    "timestamp": ts,
                    "asset": [a] * n_bars,
                    "open": [100.0] * n_bars,
                    "high": [100.0] * n_bars,
                    "low": [100.0] * n_bars,
                    "close": [100.0] * n_bars,
                    "volume": [1000.0] * n_bars,
                    "funding_rate": [0.0] * n_bars,
                }
            )
        )
    return pl.concat(rows).sort("timestamp", "asset")


def async_panel(n_bars: int = 260) -> pl.DataFrame:
    """Staggered sessions: B missing midday hours, C only every other bar."""
    full = base_panel(n_bars, ["AAA", "BBB", "CCC"], seed=11)
    keep = (
        pl.when(pl.col("asset") == "BBB").then(~pl.col("timestamp").dt.hour().is_between(9, 12))
        .when(pl.col("asset") == "CCC").then(((pl.col("timestamp").dt.epoch("s") // 3600) % 2) == 0)
        .otherwise(pl.lit(True))
    )
    return full.filter(keep)


def panel_sets(module: str, contract, params: dict) -> dict[str, pl.DataFrame]:
    real = pl.read_parquet(WT / contract.exploratory_data_path).sort("timestamp", "asset")
    assets = sorted(real["asset"].unique().to_list())
    ts_all = real["timestamp"].unique().sort()
    tail_ts = ts_all.tail(400)
    short_ts = ts_all.head(60)
    single = real.filter(pl.col("asset") == assets[0])
    panels = {
        "real_contract_full": real,
        "synthetic_stop_trigger": base_panel(1600, ["AAA", "BBB", "CCC"]),
        "synthetic_flat": flat_panel(),
        "synthetic_single_asset": single,
        "real_panel_tail400": real.filter(pl.col("timestamp").is_in(tail_ts)),
        "real_panel_head60": real.filter(pl.col("timestamp").is_in(short_ts)),
        "synthetic_async_sessions": async_panel(),
        "synthetic_unsorted": base_panel(220, ["AAA", "BBB"]).sample(fraction=1.0, shuffle=True, seed=3),
    }
    return panels


def run(mod, panel: pl.DataFrame, contract, params: dict) -> dict:
    out = mod.generate_positions(panel, contract, **params)
    return out


def keyed(out: dict) -> dict:
    return {k: np.asarray(v) for k, v in out.items()}


def stats(pos: np.ndarray) -> dict:
    prev = np.vstack([np.zeros((1, pos.shape[1])), pos[:-1]]) if pos.size else pos
    flat = (np.abs(prev) > 1e-6) & (np.abs(pos) <= 1e-6)
    return {
        "shape": list(pos.shape),
        "nonzero": int(np.count_nonzero(pos)),
        "stop_events": int(flat.sum()),
    }


def timing_pair(old, new, panel, contract, params, reps: int = 5) -> dict:
    """Interleaved median timing: (total, feature-pipeline, pivot) per side.

    Interleaving old/new reps cancels machine drift; the position-loop cost is the residual.
    """
    from contextlib import ExitStack

    mods = {"old": old, "new": new}
    state = {"cur": "old", "pipe": {"old": 0.0, "new": 0.0}, "pivot": {"old": 0.0, "new": 0.0}}
    orig_pivot = pl.DataFrame.pivot
    totals = {"old": [], "new": []}
    pipes = {"old": [], "new": []}
    pivots = {"old": [], "new": []}

    def counted_pivot(self, *a, **k):
        t0 = time.perf_counter()
        r = orig_pivot(self, *a, **k)
        state["pivot"][state["cur"]] += time.perf_counter() - t0
        return r

    with ExitStack() as stack:
        for side, mod in mods.items():
            cls = next(o for n, o in vars(mod).items() if isinstance(o, type) and n.endswith("Pipeline"))
            orig_compute = cls.compute

            def make(side_key, fn):
                def counted(self, *a, **k):
                    t0 = time.perf_counter()
                    r = fn(self, *a, **k)
                    state["pipe"][side_key] += time.perf_counter() - t0
                    return r

                return counted

            cls.compute = make(side, orig_compute)
            stack.callback(setattr, cls, "compute", orig_compute)
        pl.DataFrame.pivot = counted_pivot
        stack.callback(setattr, pl.DataFrame, "pivot", orig_pivot)

        for side in ("old", "new"):  # warm
            state["cur"] = side
            run(mods[side], panel, contract, params)
        for _ in range(reps):
            for side in ("old", "new"):
                state["cur"] = side
                state["pipe"][side] = 0.0
                state["pivot"][side] = 0.0
                t0 = time.perf_counter()
                run(mods[side], panel, contract, params)
                totals[side].append(time.perf_counter() - t0)
                pipes[side].append(state["pipe"][side])
                pivots[side].append(state["pivot"][side])

    out = {}
    for side in ("old", "new"):
        t = statistics.median(totals[side])
        p = statistics.median(pipes[side])
        v = statistics.median(pivots[side])
        out[side] = {"total": t, "pipeline": p, "pivot": v, "loop": t - p - v}
    return out


def malformed_cases(panel: pl.DataFrame, contract, params: dict) -> list[tuple[str, pl.DataFrame]]:
    small = panel.filter(pl.col("timestamp") <= sorted(panel["timestamp"].unique().to_list())[299])
    cases = [
        ("drop_close", small.drop("close")),
        ("drop_high", small.drop("high")),
        ("drop_low", small.drop("low")),
        ("drop_volume", small.drop("volume")),
        ("drop_asset", small.rename({"asset": "sym"}).drop("sym")),
        ("empty_frame", small.head(0)),
        ("close_string", small.with_columns(pl.col("close").cast(pl.Utf8))),
    ]
    return cases


def exc_signature(mod, panel, contract, params) -> str:
    try:
        run(mod, panel, contract, params)
        return "OK"
    except BaseException as e:  # noqa: BLE001 - signature capture only
        return f"{type(e).__name__}: {e}"


def verify(module: str) -> dict:
    cfg = MODULES[module]
    contract = load_contract(cfg["contract"])
    params = spec_params(cfg["spec"])
    old = load_module(OLD / f"{module}.py", "old")
    new = load_module(WT / "strategies" / "candidates" / f"{module}.py", "new")
    panels = panel_sets(module, contract, params)

    report: dict = {"module": module, "contract": contract.contract_id, "params": params, "panels": {}, "old_s": None, "new_s": None}
    ok_all = True
    for pname, panel in panels.items():
        try:
            o = keyed(run(old, panel, contract, params))
            o_err = None
        except BaseException as e:  # noqa: BLE001
            o, o_err = None, f"{type(e).__name__}: {e}"
        try:
            n = keyed(run(new, panel, contract, params))
            n_err = None
        except BaseException as e:  # noqa: BLE001
            n, n_err = None, f"{type(e).__name__}: {e}"
        entry: dict = {"old_err": o_err, "new_err": n_err}
        if o_err or n_err:
            entry["identical"] = o_err == n_err
            entry["proofs"] = {"exception_signature": o_err == n_err}
        else:
            entry["keys"] = sorted(o)
            entry["same_keys"] = sorted(o) == sorted(n)
            per_col = {}
            for k in o:
                if k not in n:
                    per_col[k] = {"identical": False, "reason": "missing"}
                    continue
                eq = np.array_equal(o[k], n[k])
                per_col[k] = {
                    "identical": eq,
                    "bytes_identical": o[k].tobytes() == n[k].tobytes(),
                    "dtype": str(o[k].dtype),
                    "mismatches": int(np.count_nonzero(o[k] != n[k])) if o[k].shape == n[k].shape else -1,
                    "shape": list(o[k].shape),
                }
            entry["columns"] = per_col
            entry["identical"] = all(v.get("identical") is True and v.get("bytes_identical", True) for v in per_col.values()) and entry["same_keys"]
            entry["proofs"] = stats(o.get("positions", np.zeros((0, 0))))
        report["panels"][pname] = entry
        ok_all = ok_all and entry["identical"]

    # gate 2: malformed inputs
    mal = {}
    for cname, case in malformed_cases(panels["real_contract_full"], contract, params):
        so = exc_signature(old, case, contract, params)
        sn = exc_signature(new, case, contract, params)
        mal[cname] = {"old": so, "new": sn, "identical": so == sn}
        ok_all = ok_all and so == sn
    # gate 2b: malformed parameter inputs (same panel, bad kwargs)
    small_panel = panels["real_contract_full"].filter(
        pl.col("timestamp") <= sorted(panels["real_contract_full"]["timestamp"].unique().to_list())[299]
    )
    for pname, bad in (
        ("rebalance_bars_0", {"rebalance_bars": 0}),
        ("target_symbols_empty", {"target_symbols": []}),
        ("target_symbols_unknown", {"target_symbols": ["NOTANASSET"]}),
        ("stop_multiple_0", {"stop_multiple": 0.0}),
    ):
        merged = dict(params)
        merged.update(bad)
        so = exc_signature(old, small_panel, contract, merged)
        sn = exc_signature(new, small_panel, contract, merged)
        mal[pname] = {"old": so, "new": sn, "identical": so == sn}
        ok_all = ok_all and so == sn
    report["malformed"] = mal

    # gate 3: prefix invariance with the new pipeline on the real panel
    from warden.leakage import verify_prefix_invariance

    try:
        verify_prefix_invariance(new.pipeline, panels["real_contract_full"], contract)
        report["prefix_invariance"] = {"passed": True, "error": None}
    except BaseException as e:  # noqa: BLE001
        report["prefix_invariance"] = {"passed": False, "error": f"{type(e).__name__}: {e}"}
        ok_all = False

    # timing on the real contract panel (interleaved old/new, median of 5)
    tm = timing_pair(old, new, panels["real_contract_full"], contract, params)
    report["timing"] = {
        side: {k: round(v, 5) for k, v in tm[side].items()} for side in ("old", "new")
    }
    report["old_s"], report["new_s"] = round(tm["old"]["total"], 4), round(tm["new"]["total"], 4)
    report["old_loop_s"], report["new_loop_s"] = round(tm["old"]["loop"], 5), round(tm["new"]["loop"], 5)
    report["speedup"] = round(tm["old"]["total"] / tm["new"]["total"], 3) if tm["new"]["total"] else None
    report["loop_speedup"] = round(tm["old"]["loop"] / tm["new"]["loop"], 2) if tm["new"]["loop"] > 0 else None
    report["kept"] = bool(ok_all)
    return report


def main() -> None:
    mods = sys.argv[1:] or list(MODULES)
    out = []
    for m in mods:
        r = verify(m)
        out.append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "panels"}), flush=True)
        for pname, e in r["panels"].items():
            print(json.dumps({"module": m, "panel": pname, "identical": e["identical"], "proofs": e.get("proofs"),
                              "old_err": e["old_err"], "new_err": e["new_err"]}), flush=True)
        print(json.dumps({"module": m, "malformed": r["malformed"],
                          "prefix_invariance": r["prefix_invariance"],
                          "old_s": r["old_s"], "new_s": r["new_s"],
                          "old_loop_s": r["old_loop_s"], "new_loop_s": r["new_loop_s"],
                          "speedup": r["speedup"], "loop_speedup": r["loop_speedup"], "kept": r["kept"]}), flush=True)
    (WT / "perf_audit" / "tsbatch" / "vec_verify_results.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
