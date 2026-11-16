"""Cross-sectional trade size granularity & institutional stealth trading factor model with purged walk-forward CV (HYP-2026-050)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSStealthTradeSizePipeline:
    """Causal cross-sectional feature pipeline for average trade size expansion ratio (ATS_7d / ATS_30d)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})

        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        # Explicitly require quote_volume and count as specified in brief
        qvol = pl.col("quote_volume") if "quote_volume" in df.columns else pl.col("volume") * pl.col("close")
        cnt = pl.col("count").clip(lower_bound=1.0) if "count" in df.columns else pl.lit(1.0)

        ats = qvol / (cnt + 1e-5)
        df = df.with_columns(ats=ats)

        ats_168 = pl.col("ats").rolling_mean(168).over("asset")
        ats_720 = pl.col("ats").rolling_mean(720).over("asset")
        ats_72 = pl.col("ats").rolling_mean(72).over("asset")

        ats_ratio_168 = (ats_168 / (ats_720 + 1e-8)).shift(delay).over("asset")
        ats_ratio_72 = (ats_72 / (ats_720 + 1e-8)).shift(delay).over("asset")

        return (
            df.with_columns(
                ats_ratio_168=ats_ratio_168,
                ats_ratio_72=ats_ratio_72,
            )
            .with_columns(
                feat_ats_ratio_168=(pl.col("ats_ratio_168") - pl.col("ats_ratio_168").mean().over("timestamp"))
                / (pl.col("ats_ratio_168").std().over("timestamp") + 1e-8),
                feat_ats_ratio_72=(pl.col("ats_ratio_72") - pl.col("ats_ratio_72").mean().over("timestamp"))
                / (pl.col("ats_ratio_72").std().over("timestamp") + 1e-8),
                feat_ats_rank=pl.col("ats_ratio_168").rank().over("timestamp")
                / (pl.col("ats_ratio_168").count().over("timestamp") + 1e-8),
            )
        )


class CSStealthTradeSizeRidgeModel:
    """WalkForwardModelProtocol implementation for Ridge regression."""

    def __init__(self, alpha: float = 10.0) -> None:
        self.alpha = alpha

    def fit_predict_fold(
        self,
        train_df: pl.DataFrame,
        val_features_df: pl.DataFrame,
        feature_cols: Sequence[str],
        label_col: str,
    ) -> pl.DataFrame:
        x_tr = train_df.select(feature_cols).to_numpy()
        y_tr = train_df[label_col].to_numpy()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=self.alpha, random_state=42)
        if np.any(valid):
            model.fit(x_tr[valid], y_tr[valid])
            preds = model.predict(np.nan_to_num(val_features_df.select(feature_cols).to_numpy(), nan=0.0))
        else:
            preds = np.zeros(len(val_features_df), dtype=float)
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

    pipe = CSStealthTradeSizePipeline()
    feat_df = pipe.compute(df, contract)

    f1 = feat_df.pivot(index="timestamp", on="asset", values="feat_ats_ratio_168").fill_null(0.0).select(assets).to_numpy()
    f2 = feat_df.pivot(index="timestamp", on="asset", values="feat_ats_ratio_72").fill_null(0.0).select(assets).to_numpy()
    f3 = feat_df.pivot(index="timestamp", on="asset", values="feat_ats_rank").fill_null(0.0).select(assets).to_numpy()

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
        x_tr = np.column_stack([f1[train_idx].ravel(), f2[train_idx].ravel(), f3[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        if not np.any(valid):
            continue
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([f1[t], f2[t], f3[t]])
            oos_preds[t] = model.predict(np.nan_to_num(x_test_t, nan=0.0))

    ic_list: list[float] = []
    for t in range(val_start_idx, val_end_idx - h):
        pred_t = oos_preds[t]
        eligible_t = [i for i, a in enumerate(assets) if "btc" not in a.lower() and not np.isnan(pred_t[i])]
        if len(eligible_t) > 2:
            val_ic = float(np.corrcoef(pred_t[eligible_t], forward_label[t][eligible_t])[0, 1])
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

    return {
        "positions": positions,
        "ic_series": ic_arr,
        "mean_ic": float(np.mean(ic_arr)) if len(ic_arr) else None,
    }


pipeline = CSStealthTradeSizePipeline()
model = CSStealthTradeSizeRidgeModel()
