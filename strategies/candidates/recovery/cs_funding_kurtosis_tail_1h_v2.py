"""Cross-sectional funding rate kurtosis and jump-tail factor model with purged walk-forward CV (HYP-2026-020)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSFundingKurtosisTailPipeline:
    """Causal cross-sectional feature pipeline for funding rate kurtosis and tail risk."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        if "funding_rate" in df.columns:
            rate_expr = pl.col("funding_rate")
        else:
            rate_expr = (pl.col("close") - pl.col("open")) / (pl.col("open") + 1e-12)

        w_long = 168
        mu_168 = rate_expr.rolling_mean(w_long).over("asset")
        m2_168 = (rate_expr**2).rolling_mean(w_long).over("asset")
        m3_168 = (rate_expr**3).rolling_mean(w_long).over("asset")
        m4_168 = (rate_expr**4).rolling_mean(w_long).over("asset")
        var_168 = m2_168 - mu_168**2
        cm4_168 = m4_168 - 4 * mu_168 * m3_168 + 6 * (mu_168**2) * m2_168 - 3 * (mu_168**4)
        kurt_168 = cm4_168 / (var_168**2 + 1e-12)

        w_short = 72
        mu_72 = rate_expr.rolling_mean(w_short).over("asset")
        m2_72 = (rate_expr**2).rolling_mean(w_short).over("asset")
        m3_72 = (rate_expr**3).rolling_mean(w_short).over("asset")
        m4_72 = (rate_expr**4).rolling_mean(w_short).over("asset")
        var_72 = m2_72 - mu_72**2
        cm4_72 = m4_72 - 4 * mu_72 * m3_72 + 6 * (mu_72**2) * m2_72 - 3 * (mu_72**4)
        kurt_72 = cm4_72 / (var_72**2 + 1e-12)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                kurt_168=kurt_168.shift(delay).over("asset"),
                kurt_72=kurt_72.shift(delay).over("asset"),
                # Stable direct rolling standard deviation avoids catastrophic
                # cancellation when observed 8h funding has nearly zero variance.
                funding_std=rate_expr.rolling_std(w_long).shift(delay).over("asset"),
            )
            .with_columns(
                feat_low_kurt_168=(-pl.col("kurt_168") - (-pl.col("kurt_168")).mean().over("timestamp"))
                / (pl.col("kurt_168").std().over("timestamp") + 1e-8),
                feat_low_kurt_72=(-pl.col("kurt_72") - (-pl.col("kurt_72")).mean().over("timestamp"))
                / (pl.col("kurt_72").std().over("timestamp") + 1e-8),
                feat_funding_std=(pl.col("funding_std") - pl.col("funding_std").mean().over("timestamp"))
                / (pl.col("funding_std").std().over("timestamp") + 1e-8),
            )
        )


class CSFundingKurtosisTailRidgeModel:
    """WalkForwardModelProtocol implementation for Ridge regression."""

    def __init__(self, alpha: float = 10.0) -> None:
        self.alpha = alpha

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
        model = Ridge(alpha=self.alpha, random_state=seed)
        model.fit(x_tr[valid], y_tr[valid])

        x_val = val_features_df.select(feature_cols).to_numpy()
        preds = model.predict(np.nan_to_num(x_val, nan=0.0))
        return val_features_df.select(["timestamp", "asset"]).with_columns(pl.Series("pred", preds))


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    alpha: float = 10.0,
    rebalance_every: int = 72,
    top_k: int = 5,
    **kwargs: Any,
) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
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

    pipe = CSFundingKurtosisTailPipeline()
    feat_df = pipe.compute(df, contract)

    f1 = feat_df.pivot(index="timestamp", on="asset", values="feat_low_kurt_168").fill_null(0.0).select(assets).to_numpy()
    f2 = feat_df.pivot(index="timestamp", on="asset", values="feat_low_kurt_72").fill_null(0.0).select(assets).to_numpy()
    f3 = feat_df.pivot(index="timestamp", on="asset", values="feat_funding_std").fill_null(0.0).select(assets).to_numpy()

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
        train_idx = train_idx[train_idx >= 168]
        if len(train_idx) < 10:
            continue
        x_tr = np.column_stack([f1[train_idx].ravel(), f2[train_idx].ravel(), f3[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([f1[t], f2[t], f3[t]])
            oos_preds[t] = model.predict(np.nan_to_num(x_test_t, nan=0.0))

    ic_list: list[float] = []
    for t in range(val_start_idx, val_end_idx - h):
        pred_t = oos_preds[t]
        if not np.any(np.isnan(pred_t)):
            val_ic = float(np.corrcoef(pred_t, forward_label[t])[0, 1])
            if np.isfinite(val_ic):
                ic_list.append(val_ic)
    ic_arr = np.array(ic_list, dtype=float)

    positions = np.zeros((n_bars, n_assets), dtype=float)
    k = max(1, min(top_k, n_assets // 2))
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


pipeline = CSFundingKurtosisTailPipeline()
model = CSFundingKurtosisTailRidgeModel()
