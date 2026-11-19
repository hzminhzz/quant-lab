"""Feasibility spike part 2 (read-only): is the per-bar `np.corrcoef` IC loop
bit-identical to a vectorised row-demean/normalise formulation?

Compares four vectorised forms against the literal HEAD loop, on
(a) the real CRYPTO_PERPS_1H_V2 fold predictions, restricted to the bars the
module actually uses (finite preds) and (b) synthetic panels of several widths,
including widths used by other contracts (4, 19, 100 assets).

Run: cd <worktree> && uv run --frozen --project /home/quant/dev/quant-lab \
       python perf_audit/cspred/spike_ic.py
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("spike_bitident", _HERE / "spike_bitident.py")
spike = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spike)


def ulp(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ai, bi = a.view(np.int64), b.view(np.int64)
    ai = np.where(ai < 0, np.int64(-(2**63)) - ai, ai)
    bi = np.where(bi < 0, np.int64(-(2**63)) - bi, bi)
    return np.abs(ai - bi)


def variants(preds: np.ndarray, labels: np.ndarray) -> dict:
    n = preds.shape[1]
    loop = np.empty(preds.shape[0])
    for i in range(preds.shape[0]):
        loop[i] = float(np.corrcoef(preds[i], labels[i])[0, 1])
    dx = preds - preds.mean(axis=1, keepdims=True)
    dy = labels - labels.mean(axis=1, keepdims=True)
    num = (dx * dy).sum(axis=1)
    den = np.sqrt((dx * dx).sum(axis=1) * (dy * dy).sum(axis=1))
    mat = np.matmul(dx[:, None, :], dy[:, :, None])[:, 0, 0] / (n - 1)
    c00 = np.matmul(dx[:, None, :], dx[:, :, None])[:, 0, 0] / (n - 1)
    c11 = np.matmul(dy[:, None, :], dy[:, :, None])[:, 0, 0] / (n - 1)
    return {
        "loop": loop,
        "vec_sum_cancel_nminus1": num / den,
        "vec_einsum_cancel_nminus1": np.einsum("ij,ij->i", dx, dy)
        / np.sqrt(np.einsum("ij,ij->i", dx, dx) * np.einsum("ij,ij->i", dy, dy)),
        "vec_matmul_cov_nminus1": mat / np.sqrt(c00 * c11),
    }


def digest(preds: np.ndarray, labels: np.ndarray) -> dict:
    v = variants(preds, labels)
    loop = v["loop"]
    out = {"n_bars": int(preds.shape[0]), "n_assets": int(preds.shape[1])}
    for k, a in v.items():
        if k == "loop":
            continue
        eq = np.array_equal(a, loop)
        fa, fl = np.isfinite(a), np.isfinite(loop)
        both = fa & fl
        d = np.abs(a[both] - loop[both])
        n_diff = int(np.count_nonzero(a[both] != loop[both]))
        rec = {"array_equal": bool(eq), "tobytes_equal": bool(a.tobytes() == loop.tobytes()),
               "n_bars_both_finite": int(both.sum()),
               "n_nan_both": int((~fa & ~fl).sum()),
               "n_nan_one_sided": int((fa != fl).sum()),
               "n_mismatch": n_diff,
               "frac_mismatch_finite": round(n_diff / max(int(both.sum()), 1), 4),
               "max_abs_diff": float(d.max()) if len(d) else 0.0,
               "max_ulp": int(ulp(a[both], loop[both]).max()) if len(d) else 0}
        if not eq:
            diff = np.flatnonzero(a[both] != loop[both])
            i = int(diff[0]) if len(diff) else 0
            rec["first_mismatch_bar"] = i
            rec["loop_value"] = float(loop[i])
            rec["vec_value"] = float(a[i])
            rec["ulp_at_first"] = int(ulp(a[i:i + 1], loop[i:i + 1])[0])
        out[k] = rec
    return out


def main() -> None:
    p = spike.build_panel()
    report: dict = {"real_panel": {}, "synthetic": {}}
    for name, delay in (("ridge", 0), ("lgbm", 1)):
        arms = spike.fold_arms(p, name, delay)
        preds, labels = arms["oos_batch"], arms["label"]
        h = spike.CONTRACT.label_horizon_bars
        rows = [t for t in range(p["val_start_idx"], p["val_end_idx"] - h)
                if not np.any(np.isnan(preds[t]))]
        idx = np.array(rows)
        rec = digest(preds[idx], labels[idx])
        t0 = time.perf_counter()
        for i in range(len(idx)):
            np.corrcoef(preds[idx[i]], labels[idx[i]])
        rec["ic_loop_s"] = round(time.perf_counter() - t0, 4)
        rec["models_agree_on_oos_preds"] = bool(
            np.array_equal(preds[idx], arms["oos_loop"][idx]))
        report["real_panel"][name] = rec

    rng = np.random.default_rng(7)
    for n_assets in (3, 4, 8, 19, 100):
        preds = rng.standard_normal((4000, n_assets))
        labels = 0.3 * preds + rng.standard_normal((4000, n_assets))
        d = digest(preds, labels)
        report["synthetic"][f"n_assets_{n_assets}"] = {
            k: {"array_equal": v["array_equal"], "frac_mismatch_finite": v["frac_mismatch_finite"],
                "max_abs_diff": v["max_abs_diff"], "max_ulp": v["max_ulp"]}
            for k, v in d.items() if k not in ("n_bars", "n_assets")}
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
