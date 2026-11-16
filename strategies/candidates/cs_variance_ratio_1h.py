"""Cross-sectional Lo-MacKinlay variance ratio factor model with purged walk-forward CV (HYP-2026-055)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSVarianceRatioPipeline:
    """Causal cross-sectional feature pipeline for Lo-MacKinlay Variance Ratio (Lo & MacKinlay 1988)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        df = df.with_columns(
            r_1h=(pl.col("close") / pl.col("close").shift(1)).log().over("asset"),
            r_24h=(pl.col("close") / pl.col("close").shift(24)).log().over("asset"),
        )

        var_1h_336 = pl.col("r_1h").rolling_var(336, min_samples=48).over("asset")
        var_24h_336 = pl.col("r_24h").rolling_var(336, min_samples=48).over("asset")
        vr_24_336 = (var_24h_336 / (24.0 * var_1h_336 + 1e-8)).shift(delay).over("asset")

        var_1h_168 = pl.col("r_1h").rolling_var(168, min_samples=24).over("asset")
        var_24h_168 = pl.col("r_24h").rolling_var(168, min_samples=24).over("asset")
        vr_24_168 = (var_24h_168 / (24.0 * var_1h_168 + 1e-8)).shift(delay).over("asset")

        var_1h_720 = pl.col("r_1h").rolling_var(720, min_samples=48).over("asset")
        var_24h_720 = pl.col("r_24h").rolling_var(720, min_samples=48).over("asset")
        vr_24_720 = (var_24h_720 / (24.0 * var_1h_720 + 1e-8)).shift(delay).over("asset")

        df = df.with_columns(
            vr_24_336=vr_24_336,
            vr_24_168=vr_24_168,
            vr_24_720=vr_24_720,
        )

        return df.with_columns(
            feat_vr_24_336=(pl.col("vr_24_336") - pl.col("vr_24_336").mean().over("timestamp"))
            / (pl.col("vr_24_336").std().over("timestamp") + 1e-8),
            feat_vr_24_168=(pl.col("vr_24_168") - pl.col("vr_24_168").mean().over("timestamp"))
            / (pl.col("vr_24_168").std().over("timestamp") + 1e-8),
            feat_vr_24_720=(pl.col("vr_24_720") - pl.col("vr_24_720").mean().over("timestamp"))
            / (pl.col("vr_24_720").std().over("timestamp") + 1e-8),
        )


class CSVarianceRatioRidgeModel:
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

    pipe = CSVarianceRatioPipeline()
    feat_df = pipe.compute(df, contract)

    f1 = feat_df.pivot(index="timestamp", on="asset", values="feat_vr_24_336").fill_null(0.0).select(assets).to_numpy()
    f2 = feat_df.pivot(index="timestamp", on="asset", values="feat_vr_24_720").fill_null(0.0).select(assets).to_numpy()
    f3 = feat_df.pivot(index="timestamp", on="asset", values="feat_vr_24_168").fill_null(0.0).select(assets).to_numpy()

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
        train_idx = train_idx[train_idx >= 336]
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
    k = min(top_k, n_assets // 2)
    for t in range(val_start_idx, val_end_idx):
        if t % rebalance_every != 0:
            positions[t] = positions[t - 1]
            continue
        scores = oos_preds[t]
        if np.all(np.isnan(scores)):
            positions[t] = positions[t - 1]
            continue
        eligible = [i for i in range(n_assets) if assets[i] not in ("BTCUSDT", "BTCUSDT_PERP") and not np.isnan(scores[i])]
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


pipeline = CSVarianceRatioPipeline()
model = CSVarianceRatioRidgeModel()
