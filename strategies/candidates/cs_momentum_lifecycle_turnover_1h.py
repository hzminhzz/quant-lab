"""Cross-sectional momentum life cycle and volume turnover exhaustion factor model with purged walk-forward CV (HYP-2026-038)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSMomentumLifecycleTurnoverPipeline:
    """Causal cross-sectional feature pipeline for momentum lifecycle and volume exhaustion."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        # 14d and 7d cumulative log returns shifted causally
        r_14d = (pl.col("close") / pl.col("close").shift(336).over("asset")).log().shift(delay).over("asset")
        r_7d = (pl.col("close") / pl.col("close").shift(168).over("asset")).log().shift(delay).over("asset")

        # Volume ratios: VR_i = mean(volume_168h) / mean(volume_720h)
        vol_7d = pl.col("volume").rolling_mean(168).shift(delay).over("asset")
        vol_30d = pl.col("volume").rolling_mean(720).shift(delay).over("asset")
        vr_7_30 = vol_7d / (vol_30d + 1e-8)

        vol_1d = pl.col("volume").rolling_mean(24).shift(delay).over("asset")
        vr_1_7 = vol_1d / (vol_7d + 1e-8)

        # MLC_i = -VR_i, evaluated strictly among positive-momentum altcoins (r_14d > 0).
        # Tokens with non-positive returns are penalized to rank below stealth winners.
        mlc_score = pl.when(r_14d > 0.0).then(-vr_7_30).otherwise(-10.0 - vr_7_30)
        mlc_fast = pl.when(r_7d > 0.0).then(-vr_1_7).otherwise(-10.0 - vr_1_7)
        vr_exhaustion = -vr_7_30

        df = df.with_columns(
            mlc_score=mlc_score,
            mlc_fast=mlc_fast,
            vr_exhaustion=vr_exhaustion,
        )

        return df.with_columns(
            feat_mlc=(pl.col("mlc_score") - pl.col("mlc_score").mean().over("timestamp"))
            / (pl.col("mlc_score").std().over("timestamp") + 1e-8),
            feat_mlc_fast=(pl.col("mlc_fast") - pl.col("mlc_fast").mean().over("timestamp"))
            / (pl.col("mlc_fast").std().over("timestamp") + 1e-8),
            feat_stealth_turnover=(pl.col("vr_exhaustion") - pl.col("vr_exhaustion").mean().over("timestamp"))
            / (pl.col("vr_exhaustion").std().over("timestamp") + 1e-8),
        )


class CSMomentumLifecycleTurnoverRidgeModel:
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

    pipe = CSMomentumLifecycleTurnoverPipeline()
    feat_df = pipe.compute(df, contract)

    f_mlc = feat_df.pivot(index="timestamp", on="asset", values="feat_mlc").fill_null(0.0).select(assets).to_numpy()
    f_fast = feat_df.pivot(index="timestamp", on="asset", values="feat_mlc_fast").fill_null(0.0).select(assets).to_numpy()
    f_turn = feat_df.pivot(index="timestamp", on="asset", values="feat_stealth_turnover").fill_null(0.0).select(assets).to_numpy()

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
        x_tr = np.column_stack([f_mlc[train_idx].ravel(), f_fast[train_idx].ravel(), f_turn[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([f_mlc[t], f_fast[t], f_turn[t]])
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


pipeline = CSMomentumLifecycleTurnoverPipeline()
model = CSMomentumLifecycleTurnoverRidgeModel()
