"""Fix 4 proof: vectorised _validate/generate_positions must be bit-identical to HEAD.

Old module  = `git show HEAD:strategies/candidates/soc_wbx_downshock_1h_20261031.py`
              (snapshot at perf_audit/fix4/soc_wbx_old.py)
New module  = worktree strategies/candidates/soc_wbx_downshock_1h_20261031.py

Checks
  1. real exploratory panel (BTC/ETH/LTC/XRP, sorted timestamp,asset): positions equal
     bitwise (array_equal + tobytes) and feature frames equal.
  2. synthetic edge panels (contiguous hourly, 4 assets, valid OHLCV):
     no events / dense consecutive events / event on an exit bar / event inside the
     last 4 bars / event on the next-eligible bar / randomised shock patterns.
  3. fail-closed parity: every malformed panel raises the same exception type and
     message in old and new.
  4. algorithm cross-check: reference per-bar state machine vs the vectorised fill
     over exhaustive small boolean patterns and long random ones.

Read-only: no registry writes, no data/holdout, no qualify.
Run: cd <worktree> && uv run --frozen --project /home/quant/dev/quant-lab \
       python perf_audit/fix4/equiv_check.py
"""
from __future__ import annotations

import itertools
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from warden.contracts import load_contract
from warden.runner import load_module_from_path

WORK = Path(__file__).resolve().parents[2]
CONTRACT = load_contract(
    WORK / "contracts/auto_crypto_perps_top100_1h_v1_h24_e24_f45_s20_v1.yaml"
)
OLD = load_module_from_path(WORK / "perf_audit/fix4/soc_wbx_old.py")
NEW = load_module_from_path(WORK / "strategies/candidates/soc_wbx_downshock_1h_20261031.py")
ASSETS = list(OLD.ASSETS)
HOLD = OLD.HOLD_BARS
WEIGHT = OLD.WEIGHT_PER_ASSET

RESULTS: list[dict] = []


def record(name: str, ok: bool, detail: dict | None = None) -> None:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail or {}})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {json.dumps(detail or {})}", flush=True)
    if not ok:
        raise SystemExit(f"FAILED: {name}")


def positions(mod, panel, contract=CONTRACT, **kwargs):
    return mod.generate_positions(panel, contract, target_symbols=ASSETS, **kwargs)["positions"]


def bit_equal(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and a.dtype == b.dtype and np.array_equal(a, b) and a.tobytes() == b.tobytes()


def frames_equal(a: pl.DataFrame, b: pl.DataFrame) -> bool:
    if a.columns != b.columns or a.height != b.height or a.schema != b.schema:
        return False
    for col in a.columns:
        if not a[col].equals(b[col]):
            return False
    return True


def raised(mod, panel, contract=CONTRACT, **kwargs) -> tuple[str, str, str] | None:
    try:
        mod.generate_positions(panel, contract, **kwargs)
    except Exception as exc:  # noqa: BLE001 - parity capture
        return (type(exc).__name__, str(exc), _origin(mod, exc))
    return None


def _origin(mod, exc) -> str:
    tb = exc.__traceback__
    deepest = None
    while tb is not None:
        if Path(tb.tb_frame.f_code.co_filename).name == Path(mod.__file__).name:
            deepest = tb.tb_frame.f_code.co_name
        tb = tb.tb_next
    return deepest or ""


# ---------------------------------------------------------------- 1. real panel
def check_real_panel() -> None:
    path = WORK / "data" / "exploratory/crypto_perps_top100_1h.parquet"
    panel = pl.read_parquet(path)
    panel = panel.filter(pl.col("asset").is_in(ASSETS)).sort("timestamp", "asset")
    old_pos = positions(OLD, panel)
    new_pos = positions(NEW, panel)
    old_feat = OLD.pipeline.compute(panel, CONTRACT)
    new_feat = NEW.pipeline.compute(panel, CONTRACT)
    events = int(old_feat["shock_event"].sum())
    record(
        "real_panel_positions_bit_identical",
        bit_equal(old_pos, new_pos),
        {
            "data": str(path),
            "rows": panel.height,
            "timestamps": int(panel["timestamp"].n_unique()),
            "columns": panel.columns,
            "shape": list(old_pos.shape),
            "nonzero_bars": int((old_pos.sum(axis=1) > 0).sum()),
            "shock_events": events,
            "sum_old": float(old_pos.sum()),
            "sum_new": float(new_pos.sum()),
        },
    )
    record("real_panel_features_identical", frames_equal(old_feat, new_feat))
    record(
        "real_panel_positions_equal_reference_state_machine",
        bit_equal(old_pos, reference_weights(old_feat, panel)),
    )


# ------------------------------------------------------------ reference machine
def reference_weights(features: pl.DataFrame, panel: pl.DataFrame) -> np.ndarray:
    """The exact HEAD per-bar loop, re-run on the feature frame."""
    timestamps = sorted(panel["timestamp"].unique().to_list())
    weights = np.zeros((len(timestamps), len(ASSETS)), dtype=np.float64)
    for column, asset in enumerate(sorted(ASSETS)):
        sub = features.filter(pl.col("asset") == asset).sort("timestamp")
        assert sub["timestamp"].to_list() == timestamps
        event = sub["shock_event"].to_numpy()
        exit_target_index = -1
        for i in range(len(event)):
            if i < exit_target_index:
                weights[i, column] = WEIGHT
            elif i == exit_target_index:
                weights[i, column] = 0.0
            elif event[i]:
                exit_target_index = i + HOLD
                weights[i, column] = WEIGHT
    return weights


# --------------------------------------------------------- synthetic edge panels
def build_panel(n: int, shock_bars: set[int], seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows: list[dict] = []
    for asset in ASSETS:
        ret = rng.normal(0.0, 0.0015, n)
        for bar in shock_bars:
            ret[bar] = -0.20
        close = 100.0 * np.exp(np.cumsum(ret))
        open_ = np.empty(n)
        open_[0] = 100.0
        open_[1:] = close[:-1]
        high = np.maximum(open_, close) * 1.002
        low = np.minimum(open_, close) * 0.998
        volume = np.full(n, 100.0)
        for bar in shock_bars:
            high[bar] = close[bar] * 1.05
            low[bar] = close[bar] * 0.985  # close location ~0.23 <= 0.35
            volume[bar] = 5000.0
        for i in range(n):
            rows.append({
                "timestamp": start + timedelta(hours=i),
                "asset": asset,
                "open": float(open_[i]),
                "high": float(high[i]),
                "low": float(low[i]),
                "close": float(close[i]),
                "volume": float(volume[i]),
                "funding_rate": 0.0,
            })
    return pl.DataFrame(rows)


def event_layout(panel: pl.DataFrame) -> np.ndarray:
    feat = OLD.pipeline.compute(panel, CONTRACT).filter(pl.col("asset") == "BTCUSDT").sort("timestamp")
    return np.flatnonzero(feat["shock_event"].to_numpy())


def compare_panel(label: str, panel: pl.DataFrame, assert_layout) -> None:
    layout = event_layout(panel)
    ok_layout, layout_detail = assert_layout(layout)
    record(f"{label}_layout", ok_layout, layout_detail)
    old_pos = positions(OLD, panel)
    new_pos = positions(NEW, panel)
    record(
        f"{label}_bit_identical",
        bit_equal(old_pos, new_pos),
        {"bars": panel.height // len(ASSETS), "events": int(layout.size),
         "nonzero_bars": int((new_pos.sum(axis=1) > 0).sum()),
         "sum": float(new_pos.sum())},
    )
    record(
        f"{label}_equals_reference",
        bit_equal(old_pos, reference_weights(OLD.pipeline.compute(panel, CONTRACT), panel)),
    )


def check_edge_panels() -> None:
    n = 240

    def no_events(layout):
        return layout.size == 0, {"events": int(layout.size)}

    compare_panel("edge_no_events", build_panel(n, set(), seed=1), no_events)

    dense = set(range(100, 120))

    def dense_layout(layout):
        run = longest_run(layout)
        return run >= 10 and 100 in set(layout.tolist()), {"run": run, "events": int(layout.size)}

    compare_panel("edge_dense_consecutive", build_panel(n, dense, seed=2), dense_layout)

    def exit_bar_layout(layout):
        s = set(layout.tolist())
        return 100 in s and 104 in s and 105 in s, {"has_100": 100 in s, "has_104": 104 in s, "has_105": 105 in s}

    # 104 is the exit bar of the 100 entry -> ignored; 105 is next eligible -> new entry.
    compare_panel("edge_event_on_exit_bar", build_panel(n, {100, 104, 105}, seed=3), exit_bar_layout)

    def last_bars_layout(layout):
        s = set(layout.tolist())
        return (n - 1) in s and (n - 2) in s and (n - 4) in s, {"tail": sorted(x for x in s if x >= n - 6)}

    compare_panel("edge_event_last_four_bars", build_panel(n, {n - 4, n - 2, n - 1}, seed=4), last_bars_layout)

    for seed, bars in enumerate([
        {90, 95}, {90, 94}, {90, 96}, {80, 85, 90, 95, 100},
        {100, 101, 102, 103, 104, 105, 106}, {70, 120, 200, 239},
    ]):
        compare_panel(f"edge_pattern_{seed}", build_panel(n, bars, seed=10 + seed),
                      lambda layout: (True, {"events": int(layout.size)}))

    # randomised shock patterns on a longer panel
    for seed in range(6):
        rng = np.random.default_rng(500 + seed)
        bars = set(rng.choice(np.arange(90, 500), size=int(rng.integers(3, 45)), replace=False).tolist())
        compare_panel(f"edge_random_{seed}", build_panel(520, bars, seed=50 + seed),
                      lambda layout: (True, {"events": int(layout.size)}))


def longest_run(idx: np.ndarray) -> int:
    if idx.size == 0:
        return 0
    best = run = 1
    for a, b in zip(idx[:-1], idx[1:]):
        run = run + 1 if b == a + 1 else 1
        best = max(best, run)
    return best


# ------------------------------------------------------------- algorithm fuzz
def vec_fill(flags: np.ndarray) -> np.ndarray:
    n = flags.size
    out = np.zeros(n)
    starts = np.flatnonzero(flags)
    cursor = 0
    while cursor < starts.size:
        start = int(starts[cursor])
        stop = start + HOLD
        out[start:min(stop, n)] = WEIGHT
        if stop < n:
            out[stop] = 0.0
        cursor = int(np.searchsorted(starts, start + HOLD + 1, side="left"))
    return out


def ref_fill(flags: np.ndarray) -> np.ndarray:
    n = flags.size
    out = np.zeros(n)
    exit_target_index = -1
    for i in range(n):
        if i < exit_target_index:
            out[i] = WEIGHT
        elif i == exit_target_index:
            out[i] = 0.0
        elif flags[i]:
            exit_target_index = i + HOLD
            out[i] = WEIGHT
    return out


def check_fill_algorithm() -> None:
    bad = []
    total = 0
    for n in (6, 7, 8, 10):
        for combo in itertools.product((False, True), repeat=n):
            flags = np.array(combo, dtype=bool)
            total += 1
            if not np.array_equal(vec_fill(flags), ref_fill(flags)):
                bad.append((n, combo))
    rng = np.random.default_rng(7)
    for n in (64, 257, 1024, 4093):
        for _ in range(40):
            flags = rng.random(n) < 0.15
            total += 1
            if not np.array_equal(vec_fill(flags), ref_fill(flags)):
                bad.append((n, "random"))
    record("fill_algorithm_exhaustive_and_random", not bad, {"cases": total, "mismatches": len(bad)})


# ------------------------------------------------------------ fail-closed parity
def check_fail_closed() -> None:
    base = build_panel(200, {100}, seed=99)
    gap_ts = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=20)
    dup_ts = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=30)
    dup_xrp_row = base.filter(
        (pl.col("asset") == "BTCUSDT") & (pl.col("timestamp") == dup_ts)
    )
    cases: list[tuple[str, "pl.DataFrame", object, dict]] = [
        ("wrong_contract_id", base, replace(CONTRACT, contract_id="CRYPTO_PERPS_1H_V1"), {}),
        ("wrong_frequency", base, replace(CONTRACT, frequency="4h"), {}),
        ("wrong_execution_mode", base, replace(CONTRACT, execution_mode="QUOTE_SIDE"), {}),
        ("wrong_delay", base, replace(CONTRACT, execution_delay_bars=2), {}),
        ("short_label_horizon", base, replace(CONTRACT, label_horizon_bars=HOLD - 1), {}),
        ("missing_column", base.drop("volume"), CONTRACT, {}),
        ("extra_asset", pl.concat([base, base.filter(pl.col("asset") == "BTCUSDT").with_columns(
            pl.lit("SOLUSDT").alias("asset"))]).sort("timestamp", "asset"), CONTRACT, {}),
        ("insufficient_bars", build_panel(60, set(), seed=5), CONTRACT, {}),
        ("timestamp_gap", base.filter(pl.col("timestamp") != gap_ts), CONTRACT, {}),
        ("duplicated_block", pl.concat([base, base.filter(pl.col("timestamp") == dup_ts)]).sort(
            "timestamp", "asset"), CONTRACT, {}),
        ("dropped_single_row", base.filter(~((pl.col("asset") == "XRPUSDT") & (pl.col("timestamp") == dup_ts))),
         CONTRACT, {}),
        ("misaligned_asset_rows", pl.concat([
            base.filter(~((pl.col("asset") == "XRPUSDT") & (pl.col("timestamp") == dup_ts))),
            dup_xrp_row,
        ]).sort("timestamp", "asset"), CONTRACT, {}),
        ("nonfinite_close", base.with_columns(
            pl.when((pl.col("asset") == "ETHUSDT") & (pl.col("timestamp") == dup_ts))
            .then(float("nan")).otherwise(pl.col("close")).alias("close")), CONTRACT, {}),
        ("nonfinite_volume", base.with_columns(
            pl.when((pl.col("asset") == "LTCUSDT") & (pl.col("timestamp") == dup_ts))
            .then(float("inf")).otherwise(pl.col("volume")).alias("volume")), CONTRACT, {}),
        ("close_above_high", base.with_columns(
            pl.when((pl.col("asset") == "BTCUSDT") & (pl.col("timestamp") == dup_ts))
            .then(pl.col("high") * 2).otherwise(pl.col("close")).alias("close")), CONTRACT, {}),
        ("low_below_zero", base.with_columns(
            pl.when((pl.col("asset") == "XRPUSDT") & (pl.col("timestamp") == dup_ts))
            .then(-1.0).otherwise(pl.col("low")).alias("low")), CONTRACT, {}),
        ("high_below_low", base.with_columns(
            pl.when((pl.col("asset") == "ETHUSDT") & (pl.col("timestamp") == dup_ts))
            .then(pl.col("low") * 0.5).otherwise(pl.col("high")).alias("high")), CONTRACT, {}),
        ("negative_volume", base.with_columns(
            pl.when((pl.col("asset") == "LTCUSDT") & (pl.col("timestamp") == dup_ts))
            .then(-5.0).otherwise(pl.col("volume")).alias("volume")), CONTRACT, {}),
        ("non_hourly_spacing", base.with_columns(
            pl.when(pl.col("timestamp") > dup_ts)
            .then(pl.col("timestamp") + pl.duration(minutes=30))
            .otherwise(pl.col("timestamp")).alias("timestamp")), CONTRACT, {}),
    ]
    kwargs_cases = [
        ("subset_universe", base, {"target_symbols": ["BTCUSDT"]}),
        ("none_universe", base, {"target_symbols": None}),
        ("extra_param", base, {"target_symbols": ASSETS, "tuned_stop": 1}),
    ]
    parity_failures = []
    seen = []
    for label, panel, contract, kwargs in cases:
        if label in {"subset_universe", "none_universe", "extra_param"}:
            pass
        old_err = raised(OLD, panel, contract, target_symbols=ASSETS, **kwargs)
        new_err = raised(NEW, panel, contract, target_symbols=ASSETS, **kwargs)
        seen.append({"case": label, "old": old_err, "new": new_err})
        if old_err is None or old_err != new_err:
            parity_failures.append({"case": label, "old": old_err, "new": new_err})
    for label, panel, kwargs in kwargs_cases:
        old_err = raised(OLD, panel, **kwargs)
        new_err = raised(NEW, panel, **kwargs)
        seen.append({"case": label, "old": old_err, "new": new_err})
        if old_err is None or old_err != new_err:
            parity_failures.append({"case": label, "old": old_err, "new": new_err})
    record("fail_closed_parity", not parity_failures,
           {"cases": len(seen), "failures": parity_failures,
            "observed": sorted({(s["new"] or ("NO ERROR",))[0] + ": " + (s["new"] or ("", ""))[1] for s in seen})})


def main() -> None:
    print(json.dumps({"old_md5": md5(WORK / "perf_audit/fix4/soc_wbx_old.py"),
                      "new_md5": md5(WORK / "strategies/candidates/soc_wbx_downshock_1h_20261031.py"),
                      "python": sys.version.split()[0], "polars": pl.__version__}), flush=True)
    check_real_panel()
    check_edge_panels()
    check_fill_algorithm()
    check_fail_closed()
    print(json.dumps({"summary": {"checks": len(RESULTS), "all_ok": all(r["ok"] for r in RESULTS)}}))


def md5(path: Path) -> str:
    import hashlib
    return hashlib.md5(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
