"""Shared cross-sectional Ridge scaffold: purged walk-forward CV, IC, top-k book.

The cs_* factor modules differ only in their feature pipeline and a handful of
sizing/CV parameters; the training, IC and position conventions live here once.
Loaded as a sibling (warden's strategy_loader puts the module directory on
sys.path), so `from cs_common import cs_positions` works in every harness.

Behavior is bit-identical to the per-module scaffolds this replaces: operation
order, epsilons and random_state=42 are preserved exactly. Variation axes are
explicit parameters, never silent forks.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge
from ts_common import normalize_asset_column

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


def cs_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    *,
    pipeline: Any,
    feature_cols: list[str],
    warmup: int,
    alpha: float = 10.0,
    rebalance_every: int = 4,
    top_k: int = 3,
    min_train: int = 10,
    ic_guard: bool = False,
    predict_rowwise: bool = False,
    invert_factor: bool = False,
    invert_aliases: bool = False,
    fill_missing_assets: bool = False,
    exclude_symbol: str | tuple[str, ...] | None = None,
    btc_eligible: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    should_invert = invert_factor
    if invert_aliases:
        should_invert = invert_factor or kwargs.get("invert", False) or kwargs.get("invert_weights", False)
    panel_df = normalize_asset_column(panel_df)

    df = panel_df.sort("timestamp", "asset")
    timestamps = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    n_bars, n_assets = len(timestamps), len(assets)

    ts_map = {t: i for i, t in enumerate(timestamps)}
    val_start_idx = ts_map.get(contract.val_start, int(n_bars * 0.75))
    val_end_idx = ts_map.get(contract.val_end, n_bars)

    open_pivot = df.pivot(index="timestamp", on="asset", values="open").fill_null(strategy="forward").fill_null(0.0)
    for a in assets:
        if a not in open_pivot.columns:
            open_pivot = open_pivot.with_columns(pl.lit(0.0).alias(a))
    open_mat = open_pivot.select(assets).to_numpy()

    feat_df = pipeline.compute(df, contract)

    feats = []
    for col in feature_cols:
        p = feat_df.pivot(index="timestamp", on="asset", values=col).fill_null(0.0)
        if fill_missing_assets:
            for a in assets:
                if a not in p.columns:
                    p = p.with_columns(pl.lit(0.0).alias(a))
        feats.append(p.select(assets).to_numpy())

    h = contract.label_horizon_bars
    forward_label = compute_open_forward_label(open_mat, contract.execution_delay_bars, h)

    splits = purged_walk_forward_splits(
        n_timestamps=val_end_idx,
        n_folds=contract.n_folds,
        min_train_timestamps=val_start_idx,
        label_horizon=contract.execution_delay_bars + contract.label_horizon_bars,
        embargo=contract.embargo_bars,
    )

    oos_preds = np.full_like(open_mat, np.nan)
    for train_idx, test_idx in splits:
        train_idx = train_idx[train_idx >= warmup]
        if len(train_idx) < min_train:
            continue
        x_tr = np.column_stack([f[train_idx].ravel() for f in feats])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        if btc_eligible and not np.any(valid):
            continue
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        if predict_rowwise:
            for t in test_idx:
                x_test_t = np.column_stack([f[t] for f in feats])
                oos_preds[t] = model.predict(np.nan_to_num(x_test_t, nan=0.0))
        else:
            x_test = np.column_stack([f[test_idx].ravel() for f in feats])
            oos_preds[test_idx] = model.predict(np.nan_to_num(x_test, nan=0.0)).reshape(len(test_idx), n_assets)

    if should_invert:
        oos_preds = -oos_preds

    ic_list: list[float] = []
    for t in range(val_start_idx, val_end_idx - h):
        pred_t = oos_preds[t]
        if btc_eligible:
            # IC measured on the btc-excluded eligible subset, not the full cross-section.
            eligible_t = [i for i, a in enumerate(assets) if "btc" not in a.lower() and not np.isnan(pred_t[i])]
            if len(eligible_t) > 2:
                val_ic = float(np.corrcoef(pred_t[eligible_t], forward_label[t][eligible_t])[0, 1])
                if np.isfinite(val_ic):
                    ic_list.append(val_ic)
        elif not np.any(np.isnan(pred_t)):
            if ic_guard:
                std_p = float(np.std(pred_t))
                std_y = float(np.std(forward_label[t]))
                if std_p > 1e-8 and std_y > 1e-8:
                    val_ic = float(np.corrcoef(pred_t, forward_label[t])[0, 1])
                    if np.isfinite(val_ic):
                        ic_list.append(val_ic)
            else:
                val_ic = float(np.corrcoef(pred_t, forward_label[t])[0, 1])
                if np.isfinite(val_ic):
                    ic_list.append(val_ic)
    ic_arr = np.array(ic_list, dtype=float)

    positions = np.zeros((n_bars, n_assets), dtype=float)
    k = max(1, min(top_k, n_assets // 2)) if btc_eligible else min(top_k, n_assets // 2)
    for t in range(val_start_idx, val_end_idx):
        if t % rebalance_every != 0:
            positions[t] = positions[t - 1]
            continue
        scores = oos_preds[t]
        if btc_eligible:
            eligible = [i for i in range(n_assets) if "btc" not in assets[i].lower() and not np.isnan(scores[i])]
            if len(eligible) < 2 * k:
                positions[t] = positions[t - 1]
                continue
            order = np.argsort(scores[eligible])
            w = np.zeros(n_assets, dtype=float)
            long_idx = [eligible[idx] for idx in order[-k:]]
            short_idx = [eligible[idx] for idx in order[:k]]
            w[long_idx] = 1.0 / (2.0 * k)
            w[short_idx] = -1.0 / (2.0 * k)
            positions[t] = w
        elif exclude_symbol is None:
            if np.any(np.isnan(scores)):
                positions[t] = positions[t - 1]
                continue
            order = np.argsort(scores)
            w = np.zeros(n_assets, dtype=float)
            w[order[-k:]] = 1.0 / (2.0 * k)
            w[order[:k]] = -1.0 / (2.0 * k)
            positions[t] = w
        else:
            if np.all(np.isnan(scores)):
                positions[t] = positions[t - 1]
                continue
            if isinstance(exclude_symbol, str):
                eligible = [i for i in range(n_assets) if assets[i] != exclude_symbol and not np.isnan(scores[i])]
            else:
                eligible = [i for i in range(n_assets) if assets[i] not in exclude_symbol and not np.isnan(scores[i])]
            if len(eligible) < 2 * k:
                positions[t] = positions[t - 1]
                continue
            sorted_eligible = sorted(eligible, key=lambda idx: scores[idx])
            w = np.zeros(n_assets, dtype=float)
            for idx in sorted_eligible[-k:]:
                w[idx] = 1.0 / (2.0 * k)
            for idx in sorted_eligible[:k]:
                w[idx] = -1.0 / (2.0 * k)
            positions[t] = w

    return {
        "positions": positions,
        "ic_series": ic_arr,
        "mean_ic": float(np.mean(ic_arr)) if len(ic_arr) else None,
    }
