"""Shared older walk-forward scaffold for the recovery/cs_*_v2 historical variants.

These predate the current ``cs_common.cs_positions`` scheme: they split at
``contract.val_start``/``val_end``, filter training rows by a per-factor warmup, and
emit a top-k/bottom-k dollar-neutral book on a ``rebalance_every`` cadence. The four
recovery factors differ only in their feature pipeline and a handful of named axes;
the training, IC and position conventions live here once.

Structural differences between the original copies are explicit parameters, never
silent forks: ``k_floor_one`` (floor k at 1), ``ic_std_guard`` (skip zero-variance
IC timestamps at 1e-8), and ``fill_missing_feature_assets`` (zero-fill absent asset
columns before selecting). Behavior is bit-identical to the per-module scaffolds
this replaces: operation order, epsilons and random_state=42 are preserved exactly.

Loaded as a sibling (warden's strategy_loader puts the module directory on sys.path),
so ``from recovery_common import recovery_positions`` works in every harness.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


def recovery_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    *,
    pipeline: Any,
    feature_cols: Sequence[str],
    warmup: int,
    alpha: float = 10.0,
    rebalance_every: int = 72,
    top_k: int = 5,
    min_train: int = 10,
    k_floor_one: bool = True,
    ic_std_guard: bool = False,
    fill_missing_feature_assets: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit a dollar-neutral top-k book."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

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

    feat_mats = []
    for fc in feature_cols:
        piv = feat_df.pivot(index="timestamp", on="asset", values=fc).fill_null(0.0)
        if fill_missing_feature_assets:
            for a in assets:
                if a not in piv.columns:
                    piv = piv.with_columns(pl.lit(0.0).alias(a))
        feat_mats.append(piv.select(assets).to_numpy())

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
        x_tr = np.column_stack([fm[train_idx].ravel() for fm in feat_mats])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([fm[t] for fm in feat_mats])
            oos_preds[t] = model.predict(np.nan_to_num(x_test_t, nan=0.0))

    ic_list: list[float] = []
    for t in range(val_start_idx, val_end_idx - h):
        pred_t = oos_preds[t]
        if not np.any(np.isnan(pred_t)):
            if ic_std_guard:
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
    k = min(top_k, n_assets // 2)
    if k_floor_one:
        k = max(1, k)
    for t in range(val_start_idx, val_end_idx):
        if t % rebalance_every != 0:
            positions[t] = positions[t - 1]
            continue
        scores = oos_preds[t]
        if np.any(np.isnan(scores)):
            positions[t] = positions[t - 1]
            continue
        order = np.argsort(scores)
        w = np.zeros(n_assets, dtype=float)
        w[order[-k:]] = 1.0 / (2.0 * k)
        w[order[:k]] = -1.0 / (2.0 * k)
        positions[t] = w

    return {
        "positions": positions,
        "ic_series": ic_arr,
        "mean_ic": float(np.mean(ic_arr)) if len(ic_arr) else None,
    }
