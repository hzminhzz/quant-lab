"""Hard gate: HEAD module vs refactored module — positions and ic_series must be
bit-identical (np.array_equal + .tobytes()) on the real panel, and every malformed
input must fail closed with the same exception type/message.

Old modules: perf_audit/cspred/old/<name>.py  (git show HEAD:<path>)
New modules: strategies/... (worktree, refactored)

Panels are the read-only exploratory parquets of the contracts under test:
  CRYPTO_PERPS_1H_V2      data/exploratory/crypto_perps_1h_v2.parquet
  CRYPTO_PERPS_1H_V1      data/exploratory/crypto_perps_1h.parquet
  CRYPTO_PERPS_TOP100_1H_V1 data/exploratory/crypto_perps_top100_1h.parquet
No registry writes, no holdout, no qualify.

Run: cd <worktree> && uv run --frozen --project /home/quant/dev/quant-lab \
       python perf_audit/cspred/equiv_check.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import polars as pl

from warden.contracts import load_contract
from warden.strategy_loader import load_module_from_path

WORK = Path(__file__).resolve().parents[2]
OLD_DIR = Path(__file__).resolve().parent / "old"
CONTRACTS = {
    "v2": "contracts/crypto_perps_1h_v2.yaml",
    "v1": "contracts/crypto_perps_1h_v1.yaml",
    "top100": "contracts/crypto_perps_top100_1h_v1.yaml",
}
MODULES = [
    ("cs_ridge_1h", "strategies/baselines/cs_ridge_1h.py", ("v2", "top100")),
    ("cs_lgbm_1h", "strategies/candidates/cs_lgbm_1h.py", ("v2", "v1")),
    ("cs_amihud_1h", "strategies/candidates/cs_amihud_1h.py", ("v2", "v1")),
    ("cs_bab_1h", "strategies/candidates/cs_bab_1h.py", ("v2", "v1")),
    ("cs_vwap_tracking_error_1h", "strategies/candidates/cs_vwap_tracking_error_1h.py",
     ("v2", "top100")),
]
CACHE: dict[str, tuple[pl.DataFrame, object]] = {}
ASSET_RE = re.compile(r'==v \("\w+"\)')


def panel(key: str):
    if key not in CACHE:
        contract = load_contract(WORK / CONTRACTS[key])
        df = pl.read_parquet(WORK / contract.exploratory_data_path).sort("timestamp", "asset")
        CACHE[key] = (df, contract)
    return CACHE[key]


def arr_cmp(a: np.ndarray, b: np.ndarray) -> dict:
    nan_a = np.isnan(a) if a.dtype.kind == "f" else np.zeros(a.shape, bool)
    nan_b = np.isnan(b) if b.dtype.kind == "f" else np.zeros(b.shape, bool)
    nan_mask_equal = bool(np.array_equal(nan_a, nan_b))
    fin = ~(nan_a | nan_b)
    d = np.abs(a[fin].astype(float) - b[fin].astype(float))
    return {
        "shape": list(a.shape),
        "array_equal": bool(np.array_equal(a, b)),
        "array_equal_ignoring_nan": bool(np.array_equal(a[fin], b[fin])) and nan_mask_equal,
        "tobytes_equal": bool(a.tobytes() == b.tobytes()),
        "nan_mask_equal": nan_mask_equal,
        "identical": bool(a.tobytes() == b.tobytes() and nan_mask_equal and a.dtype == b.dtype),
        "dtype_equal": a.dtype == b.dtype,
        "max_abs_diff": float(d.max()) if d.size else 0.0,
        "n_nan_a": int(nan_a.sum()),
        "n_nan_b": int(nan_b.sum()),
    }


def run_pair(path: str, panel_key: str) -> dict:
    old = load_module_from_path(OLD_DIR / Path(path).name)
    new = load_module_from_path(WORK / path)
    df, contract = panel(panel_key)
    res: dict = {"contract": contract.contract_id, "panel_rows": int(df.height),
                 "panel_assets": int(df["asset"].n_unique())}
    got = {}
    for label, mod in (("old", old), ("new", new)):
        try:
            got[label] = ("ok", mod.generate_positions(df, contract))
        except Exception as exc:  # noqa: BLE001 - identical failure is a pass
            got[label] = ("raise", type(exc).__name__, str(exc))
    if got["old"][0] == "raise" or got["new"][0] == "raise":
        res["status"] = {"old": got["old"][:2] if got["old"][0] == "raise" else "ok",
                         "new": got["new"][:2] if got["new"][0] == "raise" else "ok"}
        res["both_raise_identically"] = (
            got["old"][0] == got["new"][0] == "raise" and got["old"][1:] == got["new"][1:])
        res["positions"] = {"array_equal": None, "tobytes_equal": None}
        res["ic_series"] = {"array_equal": None, "tobytes_equal": None}
        return res
    o, n = got["old"][1], got["new"][1]
    res.update({
        "positions": arr_cmp(o["positions"], n["positions"]),
        "ic_series": arr_cmp(o["ic_series"], n["ic_series"]),
        "positions_abs_sum": float(np.abs(n["positions"]).sum()),
        "positions_nonzero": int(np.count_nonzero(n["positions"])),
        "ic_len": int(len(n["ic_series"])),
        "mean_ic_old": o["mean_ic"], "mean_ic_new": n["mean_ic"],
    })
    return res


def fail_closed(path: str) -> dict:
    old = load_module_from_path(OLD_DIR / Path(path).name)
    new = load_module_from_path(WORK / path)
    df, contract = panel("v2")
    base = df
    cases = {
        "empty_panel": base.head(0),
        "missing_funding_rate": base.drop("funding_rate"),
        "missing_open": base.drop("open"),
        "missing_close": base.drop("close"),
        "one_timestamp": base.head(8),
        "twenty_timestamps": base.head(160),
        "duplicated_rows": pl.concat([base, base.head(800)]),
        "nan_funding_rate": base.with_columns(pl.lit(float("nan")).alias("funding_rate")),
        "single_asset": base.filter(pl.col("asset") == "ADAUSDT"),
        "nan_close_block": base.with_columns(
            pl.when(pl.col("timestamp") < base["timestamp"].unique().sort()[500])
            .then(float("nan")).otherwise(pl.col("close")).alias("close")),
        "negative_volume": base.with_columns((pl.col("volume") * -1.0).alias("volume")),
        "unsorted": base.sort("timestamp", "asset", descending=True),
    }
    results = {}
    for name, case in cases.items():
        got = {}
        for label, mod in (("old", old), ("new", new)):
            try:
                out = mod.generate_positions(case, contract)
                got[label] = ("ok", round(float(np.abs(out["positions"]).sum()), 6),
                              int(len(out["ic_series"])))
            except Exception as exc:  # noqa: BLE001 - parity of failures is the point
                got[label] = ("raise", type(exc).__name__, str(exc))
        norm = {k: (v[0], v[1], ASSET_RE.sub('==v "<asset>"', v[2])) if v[0] == "raise" else v
                for k, v in got.items()}
        results[name] = {"old": got["old"], "new": got["new"],
                         "same_raw": got["old"] == got["new"],
                         "same_normalised": norm["old"] == norm["new"]}
    return results


def main() -> None:
    report: dict = {"parity": {}, "fail_closed": {}}
    for name, path, keys in MODULES:
        report["parity"][name] = {k: run_pair(path, k) for k in keys}
    for name, path, _ in MODULES:
        fc = fail_closed(path)
        report["fail_closed"][name] = {
            "cases": len(fc),
            "mismatches_normalised": [k for k, v in fc.items() if not v["same_normalised"]],
            "mismatches_raw": [k for k, v in fc.items() if not v["same_raw"]],
            "detail": fc,
        }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
