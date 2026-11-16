"""Cross-sectional systematic funding beta factor model with purged walk-forward CV (HYP-2026-016)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSFundingBetaPipeline:
    """Causal cross-sectional feature pipeline for systematic funding beta."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        # 1. Select 8h settlement timestamps (00:00, 08:00, 16:00 UTC)
        settle_df = df.filter(pl.col("timestamp").dt.hour() % 8 == 0).sort("timestamp", "asset")

        # Market median funding rate across universe at each settlement
        mkt_funding = (
            settle_df.group_by("timestamp")
            .agg(pl.col("funding_rate").median().alias("mkt_funding"))
            .sort("timestamp")
        )
        mkt_funding = mkt_funding.with_columns(
            delta_mkt_funding=pl.col("mkt_funding").diff().fill_null(0.0)
        )

        settle_df = settle_df.join(mkt_funding, on="timestamp", how="left").sort("timestamp", "asset")

        # 8h settlement return per asset
        settle_df = settle_df.with_columns(
            ret_8h=pl.col("close").pct_change().over("asset").fill_null(0.0)
        )

        # 90 settlement periods (30 days) and 21 settlement periods (7 days)
        prod = pl.col("ret_8h") * pl.col("delta_mkt_funding")
        cov_90 = (
            prod.rolling_mean(90).over("asset")
            - pl.col("ret_8h").rolling_mean(90).over("asset")
            * pl.col("delta_mkt_funding").rolling_mean(90).over("asset")
        )
        var_90 = (
            pl.col("delta_mkt_funding").pow(2).rolling_mean(90).over("asset")
            - pl.col("delta_mkt_funding").rolling_mean(90).over("asset").pow(2)
        )
        beta_90 = (cov_90 / (var_90 + 1e-12)).alias("beta_720")

        cov_21 = (
            prod.rolling_mean(21).over("asset")
            - pl.col("ret_8h").rolling_mean(21).over("asset")
            * pl.col("delta_mkt_funding").rolling_mean(21).over("asset")
        )
        var_21 = (
            pl.col("delta_mkt_funding").pow(2).rolling_mean(21).over("asset")
            - pl.col("delta_mkt_funding").rolling_mean(21).over("asset").pow(2)
        )
        beta_21 = (cov_21 / (var_21 + 1e-12)).alias("beta_168")

        settle_features = settle_df.with_columns(
            beta_720=beta_90,
            beta_168=beta_21,
        ).select(["timestamp", "asset", "beta_720", "beta_168"])

        # Left join onto hourly df and forward fill to populate hourly grid causally
        df = df.join(settle_features, on=["timestamp", "asset"], how="left").sort("timestamp", "asset")
        df = df.with_columns(
            beta_720=pl.col("beta_720").forward_fill().shift(delay).over("asset"),
            beta_168=pl.col("beta_168").forward_fill().shift(delay).over("asset"),
            funding_level=pl.col("funding_rate").rolling_mean(24).shift(delay).over("asset"),
        )
        return df.with_columns(
            feat_funding_beta_720=(pl.col("beta_720") - pl.col("beta_720").mean().over("timestamp"))
            / (pl.col("beta_720").std().over("timestamp") + 1e-8),
            feat_funding_beta_168=(pl.col("beta_168") - pl.col("beta_168").mean().over("timestamp"))
            / (pl.col("beta_168").std().over("timestamp") + 1e-8),
            feat_funding_level=(pl.col("funding_level") - pl.col("funding_level").mean().over("timestamp"))
            / (pl.col("funding_level").std().over("timestamp") + 1e-8),
        )


class CSFundingBetaRidgeModel:
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
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
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

    pipe = CSFundingBetaPipeline()
    feat_df = pipe.compute(df, contract)

    f_b720 = feat_df.pivot(index="timestamp", on="asset", values="feat_funding_beta_720").fill_null(0.0).select(assets).to_numpy()
    f_b168 = feat_df.pivot(index="timestamp", on="asset", values="feat_funding_beta_168").fill_null(0.0).select(assets).to_numpy()
    f_level = feat_df.pivot(index="timestamp", on="asset", values="feat_funding_level").fill_null(0.0).select(assets).to_numpy()

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
        train_idx = train_idx[train_idx >= 720]
        if len(train_idx) < 10:
            continue
        x_tr = np.column_stack([f_b720[train_idx].ravel(), f_b168[train_idx].ravel(), f_level[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([f_b720[t], f_b168[t], f_level[t]])
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
    k = min(top_k, n_assets // 2)
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


pipeline = CSFundingBetaPipeline()
model = CSFundingBetaRidgeModel()
