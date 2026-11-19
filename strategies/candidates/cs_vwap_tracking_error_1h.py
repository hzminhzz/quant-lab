"""Cross-sectional VWAP tracking error factor model with purged walk-forward CV (HYP-2026-063)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSVwapTrackingErrorPipeline:
    """Causal cross-sectional feature pipeline for VWAP tracking error (RMS tracking deviation)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        dvol = pl.col("volume") * pl.col("close")
        vwap_24 = (
            dvol.rolling_sum(24, min_samples=6).over("asset")
            / (pl.col("volume").rolling_sum(24, min_samples=6).over("asset") + 1e-8)
        )
        te_sq = ((pl.col("close") - vwap_24) / (vwap_24 + 1e-8)).pow(2)

        df = df.with_columns(te_sq=te_sq)

        vte_168 = pl.col("te_sq").rolling_mean(168, min_samples=24).sqrt().shift(delay).over("asset")
        vte_72 = pl.col("te_sq").rolling_mean(72, min_samples=12).sqrt().shift(delay).over("asset")
        vte_24 = pl.col("te_sq").rolling_mean(24, min_samples=6).sqrt().shift(delay).over("asset")

        df = df.with_columns(
            vte_168=vte_168,
            vte_72=vte_72,
            vte_24=vte_24,
        )

        return df.with_columns(
            feat_vte_168=(pl.col("vte_168") - pl.col("vte_168").mean().over("timestamp"))
            / (pl.col("vte_168").std().over("timestamp") + 1e-8),
            feat_vte_72=(pl.col("vte_72") - pl.col("vte_72").mean().over("timestamp"))
            / (pl.col("vte_72").std().over("timestamp") + 1e-8),
            feat_vte_24=(pl.col("vte_24") - pl.col("vte_24").mean().over("timestamp"))
            / (pl.col("vte_24").std().over("timestamp") + 1e-8),
        )


class CSVwapTrackingErrorRidgeModel:
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
    invert_factor: bool = False,
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

    pipe = CSVwapTrackingErrorPipeline()
    feat_df = pipe.compute(df, contract)

    p1 = feat_df.pivot(index="timestamp", on="asset", values="feat_vte_168").fill_null(0.0)
    p2 = feat_df.pivot(index="timestamp", on="asset", values="feat_vte_72").fill_null(0.0)
    p3 = feat_df.pivot(index="timestamp", on="asset", values="feat_vte_24").fill_null(0.0)

    for a in assets:
        if a not in p1.columns:
            p1 = p1.with_columns(pl.lit(0.0).alias(a))
        if a not in p2.columns:
            p2 = p2.with_columns(pl.lit(0.0).alias(a))
        if a not in p3.columns:
            p3 = p3.with_columns(pl.lit(0.0).alias(a))

    f1 = p1.select(assets).to_numpy()
    f2 = p2.select(assets).to_numpy()
    f3 = p3.select(assets).to_numpy()

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
        train_idx = train_idx[train_idx >= 72]
        if len(train_idx) < 10:
            continue
        x_tr = np.column_stack([f1[train_idx].ravel(), f2[train_idx].ravel(), f3[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        x_test = np.column_stack([
            f1[test_idx].ravel(),
            f2[test_idx].ravel(),
            f3[test_idx].ravel(),
        ])
        oos_preds[test_idx] = model.predict(np.nan_to_num(x_test, nan=0.0)).reshape(
            len(test_idx), n_assets
        )

    if invert_factor:
        oos_preds = -oos_preds

    ic_list: list[float] = []
    for t in range(val_start_idx, val_end_idx - h):
        pred_t = oos_preds[t]
        if not np.any(np.isnan(pred_t)):
            std_p = float(np.std(pred_t))
            std_y = float(np.std(forward_label[t]))
            if std_p > 1e-8 and std_y > 1e-8:
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


pipeline = CSVwapTrackingErrorPipeline()
model = CSVwapTrackingErrorRidgeModel()
