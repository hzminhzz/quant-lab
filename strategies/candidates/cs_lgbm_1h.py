"""Cross-sectional LightGBM factor model with purged walk-forward CV."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from lightgbm import LGBMRegressor

from warden.contracts import EvaluationContract
from warden.leakage import purged_walk_forward_splits


class CSLGBMPipeline:
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


class CSLGBMModel:
    """WalkForwardModelProtocol implementation for LightGBM."""

    def __init__(self, num_leaves: int = 15, learning_rate: float = 0.05, n_estimators: int = 30):
        self.num_leaves = num_leaves
        self.learning_rate = learning_rate
        self.n_estimators = n_estimators

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
        model = LGBMRegressor(
            num_leaves=self.num_leaves,
            learning_rate=self.learning_rate,
            n_estimators=self.n_estimators,
            max_depth=3,
            random_state=seed,
            verbose=-1,
        )
        model.fit(x_tr[valid], y_tr[valid])

        x_val = val_features_df.select(feature_cols).to_numpy()
        preds = model.predict(np.nan_to_num(x_val, nan=0.0))
        return val_features_df.select("timestamp", "asset").with_columns(
            prediction_value=pl.Series(preds)
        )


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    num_leaves: int = 15,
    learning_rate: float = 0.05,
    n_estimators: int = 30,
    rebalance_every: int = 8,
    **kwargs,
) -> dict[str, Any]:
    """Train LightGBM across 4 purged walk-forward folds and emit top-3/bottom-3 positions."""
    df = panel_df.sort("timestamp", "asset")
    timestamps = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    n_bars, n_assets = len(timestamps), len(assets)

    ts_map = {t: i for i, t in enumerate(timestamps)}
    val_start_idx = ts_map.get(contract.val_start, int(n_bars * 0.25))
    val_end_idx = ts_map.get(contract.val_end, n_bars)

    close_pivot = df.pivot(index="timestamp", on="asset", values="close").fill_null(strategy="forward").fill_null(0.0)
    for a in assets:
        if a not in close_pivot.columns:
            close_pivot = close_pivot.with_columns(pl.lit(0.0).alias(a))
    close_mat = close_pivot.select(assets).to_numpy()
    prev_close = np.vstack([close_mat[:1], close_mat[:-1]])
    ret_mat = np.divide(close_mat - prev_close, prev_close, out=np.zeros_like(close_mat), where=prev_close > 0)
    fund_pivot = df.pivot(index="timestamp", on="asset", values="funding_rate").fill_null(0.0)
    for a in assets:
        if a not in fund_pivot.columns:
            fund_pivot = fund_pivot.with_columns(pl.lit(0.0).alias(a))
    fund_mat = fund_pivot.select(assets).to_numpy()

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
        model = LGBMRegressor(
            num_leaves=num_leaves,
            learning_rate=learning_rate,
            n_estimators=n_estimators,
            max_depth=3,
            random_state=42,
            verbose=-1,
        )
        model.fit(x_tr, y_tr)

        for t in test_idx:
            x_test_t = np.column_stack([feat_funding[t], feat_mom[t], feat_rev[t]])
            oos_preds[t] = model.predict(x_test_t)

    ic_list = []
    for t in range(val_start_idx, val_end_idx - h):
        pred_t = oos_preds[t]
        if not np.any(np.isnan(pred_t)):
            val_ic = float(np.corrcoef(pred_t, forward_label[t])[0, 1])
            ic_list.append(val_ic)
    ic_arr = np.array(ic_list, dtype=float)

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


pipeline = CSLGBMPipeline()
model = CSLGBMModel()
