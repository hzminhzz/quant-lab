"""Feasibility spike (read-only): is batched-per-fold predict bit-identical to the
per-bar `for t in test_idx: model.predict(x_t)` loop, for Ridge and LGBM?

Also: is the per-bar `np.corrcoef(pred_t, label_t)` IC loop bit-identical to a
vectorised row demean/normalise + einsum formulation?

Panel/contract: CRYPTO_PERPS_1H_V2 (data/exploratory, read-only). No registry,
no holdout, no qualify. Feature/label construction below is a verbatim copy of the
HEAD bodies of strategies/baselines/cs_ridge_1h.py and
strategies/candidates/cs_lgbm_1h.py — same loops, same order, so the only thing
that varies between the two arms is the predict/IC call shape.

Run: cd <worktree> && uv run --frozen --project /home/quant/dev/quant-lab \
       python perf_audit/cspred/spike_bitident.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import polars as pl
from lightgbm import LGBMRegressor
from sklearn.linear_model import Ridge

from warden.contracts import load_contract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits

WORK = Path(__file__).resolve().parents[2]
CONTRACT = load_contract(WORK / "contracts/crypto_perps_1h_v2.yaml")
RIDGE_ALPHA = 10.0
LGBM_KW = dict(num_leaves=15, learning_rate=0.05, n_estimators=30, max_depth=3,
               random_state=42, verbose=-1)


def cmp(a: np.ndarray, b: np.ndarray) -> dict:
    eq = np.array_equal(a, b)
    out = {
        "shape_a": list(a.shape), "shape_b": list(b.shape),
        "array_equal": bool(eq),
        "tobytes_equal": bool(a.tobytes() == b.tobytes()),
        "n_elements": int(a.size),
    }
    if not eq:
        d = np.abs(a - b)
        finite = np.isfinite(d)
        out["n_mismatch"] = int(np.count_nonzero(~(a == b)))
        out["max_abs_diff"] = float(np.max(d[finite])) if finite.any() else None
        bad = ~(a == b)
        idx = np.argwhere(bad)[0]
        out["first_mismatch_index"] = [int(i) for i in idx]
        out["a_at_first"] = float(a[tuple(idx)])
        out["b_at_first"] = float(b[tuple(idx)])
        out["n_mismatch_by_col"] = [int(v) for v in bad.reshape(-1, a.shape[-1]).sum(0)]
    return out


def build_panel() -> dict:
    df = pl.read_parquet(WORK / CONTRACT.exploratory_data_path).sort("timestamp", "asset")
    timestamps = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    n_bars, n_assets = len(timestamps), len(assets)
    ts_map = {t: i for i, t in enumerate(timestamps)}
    val_start_idx = ts_map.get(CONTRACT.val_start, int(n_bars * 0.25))
    val_end_idx = ts_map.get(CONTRACT.val_end, n_bars)

    close_pivot = (df.pivot(index="timestamp", on="asset", values="close")
                   .fill_null(strategy="forward").fill_null(0.0))
    close_mat = close_pivot.select(assets).to_numpy()
    prev_close = np.vstack([close_mat[:1], close_mat[:-1]])
    ret_mat = np.divide(close_mat - prev_close, prev_close,
                        out=np.zeros_like(close_mat), where=prev_close > 0)
    fund_pivot = df.pivot(index="timestamp", on="asset", values="funding_rate").fill_null(0.0)
    fund_mat = fund_pivot.select(assets).to_numpy()
    open_pivot = (df.pivot(index="timestamp", on="asset", values="open")
                  .fill_null(strategy="forward").fill_null(0.0))
    open_mat = open_pivot.select(assets).to_numpy()
    return dict(df=df, timestamps=timestamps, assets=assets, n_bars=n_bars,
                n_assets=n_assets, val_start_idx=val_start_idx, val_end_idx=val_end_idx,
                ret_mat=ret_mat, fund_mat=fund_mat, open_mat=open_mat)


def build_features(p: dict, delay: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Exactly the loops in the two HEAD modules (delay=0 ridge, delay=1 lgbm)."""
    ret_mat, fund_mat = p["ret_mat"], p["fund_mat"]
    feat_funding = np.zeros_like(ret_mat)
    feat_mom = np.zeros_like(ret_mat)
    feat_rev = np.zeros_like(ret_mat)
    for t in range(12, p["n_bars"]):
        idx = t - delay
        f1 = fund_mat[idx] - np.mean(fund_mat[idx])
        f2 = np.sum(ret_mat[idx - 6:idx], axis=0)
        f2 = f2 - np.mean(f2)
        f3 = np.sum(ret_mat[idx - 2:idx], axis=0)
        f3 = f3 - np.mean(f3)
        feat_funding[t] = f1 / (np.std(f1) + 1e-8)
        feat_mom[t] = f2 / (np.std(f2) + 1e-8)
        feat_rev[t] = f3 / (np.std(f3) + 1e-8)
    forward_label = compute_open_forward_label(p["open_mat"], CONTRACT.execution_delay_bars,
                                              CONTRACT.label_horizon_bars)
    return feat_funding, feat_mom, feat_rev, forward_label


def ic_variants(preds: np.ndarray, labels: np.ndarray) -> dict:
    """Per-bar np.corrcoef loop vs candidate vectorised forms."""
    n = preds.shape[1]
    loop = np.empty(preds.shape[0])
    for i in range(preds.shape[0]):
        loop[i] = float(np.corrcoef(preds[i], labels[i])[0, 1])

    dx = preds - preds.mean(axis=1, keepdims=True)
    dy = labels - labels.mean(axis=1, keepdims=True)
    num = (dx * dy).sum(axis=1)
    den = np.sqrt((dx * dx).sum(axis=1) * (dy * dy).sum(axis=1))
    v_cancel = num / den

    c01 = np.einsum("ij,ij->i", dx, dy) / (n - 1)
    c00 = np.einsum("ij,ij->i", dx, dx) / (n - 1)
    c11 = np.einsum("ij,ij->i", dy, dy) / (n - 1)
    v_cov = c01 / np.sqrt(c00 * c11)

    num_e = np.einsum("ij,ij->i", dx, dy)
    den_e = np.sqrt(np.einsum("ij,ij->i", dx, dx) * np.einsum("ij,ij->i", dy, dy))
    v_einsum = num_e / den_e

    return {"loop": loop, "vec_cancel": v_cancel, "vec_cov_nminus1": v_cov,
            "vec_einsum": v_einsum}


def fold_arms(p: dict, model_name: str, delay: int) -> dict:
    f1, f2, f3, label = build_features(p, delay)
    h, d = CONTRACT.label_horizon_bars, CONTRACT.execution_delay_bars
    lh = h if model_name == "ridge" else d + h
    splits = purged_walk_forward_splits(
        n_timestamps=p["val_end_idx"], n_folds=CONTRACT.n_folds,
        min_train_timestamps=p["val_start_idx"], label_horizon=lh,
        embargo=CONTRACT.embargo_bars,
        **({"execution_delay": d} if model_name == "ridge" else {}),
    )
    n_assets, n_bars = p["n_assets"], p["n_bars"]
    folds = []
    oos_loop = np.full((n_bars, n_assets), np.nan)
    oos_batch = np.full((n_bars, n_assets), np.nan)
    t_loop = t_batch = 0.0
    for train_idx, test_idx in splits:
        train_idx = train_idx[train_idx >= 12]
        x_tr = np.column_stack([f1[train_idx].ravel(), f2[train_idx].ravel(), f3[train_idx].ravel()])
        y_tr = label[train_idx].ravel()
        model = Ridge(alpha=RIDGE_ALPHA) if model_name == "ridge" else LGBMRegressor(**LGBM_KW)
        model.fit(x_tr, y_tr)

        t0 = time.perf_counter()
        for t in test_idx:
            x_test_t = np.column_stack([f1[t], f2[t], f3[t]])
            oos_loop[t] = model.predict(x_test_t)
        t_loop += time.perf_counter() - t0

        t0 = time.perf_counter()
        x_all = np.column_stack([f1[test_idx].ravel(), f2[test_idx].ravel(), f3[test_idx].ravel()])
        oos_batch[test_idx] = model.predict(x_all).reshape(len(test_idx), n_assets)
        t_batch += time.perf_counter() - t0

        folds.append({"n_test_bars": int(len(test_idx)),
                      "rows": int(len(test_idx) * n_assets),
                      "cmp": cmp(oos_loop[test_idx], oos_batch[test_idx])})
    return {"folds": folds, "oos_loop": oos_loop, "oos_batch": oos_batch,
            "label": label, "predict_loop_s": round(t_loop, 5),
            "predict_batch_s": round(t_batch, 5),
            "splits": [(int(train_idx[0]), int(test_idx[0]), int(test_idx[-1]))
                       for train_idx, test_idx in splits]}


def kernel_shape_test(p: dict, model_name: str) -> dict:
    """Same fitted model, same rows, six different call shapes (1..N bars)."""
    f1, f2, f3, label = build_features(p, 0 if model_name == "ridge" else 1)
    tr = np.arange(12, p["val_start_idx"])
    x_tr = np.column_stack([f1[tr].ravel(), f2[tr].ravel(), f3[tr].ravel()])
    y_tr = label[tr].ravel()
    model = Ridge(alpha=RIDGE_ALPHA) if model_name == "ridge" else LGBMRegressor(**LGBM_KW)
    model.fit(x_tr, y_tr)
    bars = list(range(p["val_start_idx"], min(p["val_start_idx"] + 64, p["val_end_idx"])))
    x_all = np.column_stack([f1[bars].ravel(), f2[bars].ravel(), f3[bars].ravel()])
    ref = model.predict(x_all)
    out = {}
    for k in (1, 2, 4, 8, 16, 32, 64):
        nb = min(k, len(bars))
        if k > len(bars):
            continue
        got = np.concatenate([model.predict(x_all[i * p["n_assets"]:(i + 1) * p["n_assets"]])
                              for i in range(nb)])
        out[f"per_bar_chunks_over_{nb}_bars"] = cmp(ref[:nb * p["n_assets"]], got)
    return out


def main() -> None:
    p = build_panel()
    report: dict = {
        "env": {"contract": CONTRACT.contract_id, "rows": int(p["df"].height),
                "timestamps": p["n_bars"], "assets": p["n_assets"],
                "val_start_idx": p["val_start_idx"], "val_end_idx": p["val_end_idx"]},
        "models": {},
    }
    for name, delay in (("ridge", 0), ("lgbm", 1)):
        arms = fold_arms(p, name, delay)
        ic = ic_variants(arms["oos_batch"], arms["label"])
        vs_loop = ic["loop"]
        ic_cmp = {k: cmp(v, vs_loop) for k, v in ic.items() if k != "loop"}
        # IC on the per-bar and the batched prediction matrices must agree too
        ic_pred_cmp = cmp(arms["oos_loop"], arms["oos_batch"])
        report["models"][name] = {
            "fold_predict": arms["folds"],
            "fold_predict_total_cmp": ic_pred_cmp,
            "splits": arms["splits"],
            "predict_loop_s": arms["predict_loop_s"],
            "predict_batch_s": arms["predict_batch_s"],
            "ic_vectorisation": ic_cmp,
            "kernel_shape_matrix": kernel_shape_test(p, name),
            "n_ic_bars": int(len(vs_loop)),
        }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
