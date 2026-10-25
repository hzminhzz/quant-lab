"""Cross-sectional Ridge regression factor model with purged walk-forward CV."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import purged_walk_forward_splits


class CSRidgePipeline:
    """Causal cross-sectional feature pipeline."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        return (
            df.sort("timestamp", "asset")
            .with_columns(
                fund_l1=pl.col("funding_rate").shift(delay).over("asset"),
                mom_6h=pl.col("close").pct_change(6).shift(delay).over("asset"),
                rev_2h=pl.col("close").pct_change(2).shift(delay).over("asset"),
            )
            .with_columns(
                feat_funding=(pl.col("fund_l1") - pl.col("fund_l1").mean().over("timestamp")),
                feat_mom=(pl.col("mom_6h") - pl.col("mom_6h").mean().over("timestamp")),
                feat_rev=(pl.col("rev_2h") - pl.col("rev_2h").mean().over("timestamp")),
            )
        )


class CSRidgeModel:
    """WalkForwardModelProtocol implementation for Ridge regression."""

    def __init__(self, ridge_alpha: float = 10.0):
        self.ridge_alpha = ridge_alpha

    def fit_predict_fold(
        self,
        train_df: pl.DataFrame,
        val_features_df: pl.DataFrame,
        feature_cols: Sequence[str],
        label_col: str,
        seed: int = 42,
    ) -> pl.DataFrame:
        x_tr = train_df.select(feature_cols).to_numpy()
        y_tr = train_df[label_col].to_numpy()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=self.ridge_alpha, fit_intercept=True)
        model.fit(x_tr[valid], y_tr[valid])

        x_val = val_features_df.select(feature_cols).to_numpy()
        preds = model.predict(np.nan_to_num(x_val, nan=0.0))
        return val_features_df.select("timestamp", "asset").with_columns(
            prediction_value=pl.Series(preds)
        )


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ridge_alpha: float = 10.0,
    rebalance_every: int = 8,
    **kwargs,
) -> dict[str, Any]:
    """Train Ridge across 4 purged walk-forward folds and emit top-3/bottom-3 positions."""
    df = panel_df.sort("timestamp", "asset")
    timestamps = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    n_bars, n_assets = len(timestamps), len(assets)

    ts_map = {t: i for i, t in enumerate(timestamps)}
    val_start_idx = ts_map.get(contract.val_start, int(n_bars * 0.25))
    val_end_idx = ts_map.get(contract.val_end, n_bars)

    close_mat = df["close"].to_numpy().reshape((n_bars, n_assets))
    prev_close = np.vstack([close_mat[:1], close_mat[:-1]])
    ret_mat = np.divide(close_mat - prev_close, prev_close, out=np.zeros_like(close_mat), where=prev_close > 0)
    fund_mat = df["funding_rate"].to_numpy().reshape((n_bars, n_assets))

    feat_funding = np.zeros_like(ret_mat)
    feat_mom = np.zeros_like(ret_mat)
    feat_rev = np.zeros_like(ret_mat)
    forward_label = np.zeros_like(ret_mat)

    h = contract.label_horizon_bars
    delay = contract.execution_delay_bars

    for t in range(12, n_bars):
        idx = t - delay
        f1 = fund_mat[idx] - np.mean(fund_mat[idx])
        f2 = np.sum(ret_mat[idx - 6:idx], axis=0)
        f2 = f2 - np.mean(f2)
        f3 = np.sum(ret_mat[idx - 2:idx], axis=0)
        f3 = f3 - np.mean(f3)

        feat_funding[t] = f1 / (np.std(f1) + 1e-8)
        feat_mom[t] = f2 / (np.std(f2) + 1e-8)
        feat_rev[t] = f3 / (np.std(f3) + 1e-8)

        if t + h <= n_bars:
            fwd = np.sum(ret_mat[t:t + h], axis=0)
            forward_label[t] = fwd - np.mean(fwd)

    splits = purged_walk_forward_splits(
        n_timestamps=val_end_idx,
        n_folds=contract.n_folds,
        min_train_timestamps=val_start_idx,
        label_horizon=contract.label_horizon_bars,
        embargo=contract.embargo_bars,
    )

    oos_preds = np.full_like(ret_mat, np.nan)
    for train_idx, test_idx in splits:
        train_idx = train_idx[train_idx >= 12]
        x_tr = np.column_stack([feat_funding[train_idx].ravel(), feat_mom[train_idx].ravel(), feat_rev[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        model = Ridge(alpha=ridge_alpha)
        model.fit(x_tr, y_tr)

        for t in test_idx:
            x_test_t = np.column_stack([feat_funding[t], feat_mom[t], feat_rev[t]])
            oos_preds[t] = model.predict(x_test_t)

    # Compute IC series on OOS validation bars
    ic_list = []
    for t in range(val_start_idx, val_end_idx - h):
        pred_t = oos_preds[t]
        if not np.any(np.isnan(pred_t)):
            val_ic = float(np.corrcoef(pred_t, forward_label[t])[0, 1])
            ic_list.append(val_ic)
    ic_arr = np.array(ic_list, dtype=float)

    # Market-neutral top 3 long (+1/6), bottom 3 short (-1/6)
    positions = np.zeros((n_bars, n_assets), dtype=float)
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
        w[order[-3:]] = 1.0 / 6.0
        w[order[:3]] = -1.0 / 6.0
        positions[t] = w

    return {
        "positions": positions,
        "ic_series": ic_arr,
        "mean_ic": float(np.mean(ic_arr)) if len(ic_arr) else None,
    }


pipeline = CSRidgePipeline()
model = CSRidgeModel()
