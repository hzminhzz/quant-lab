"""Cross-sectional Betting Against Beta factor model with purged walk-forward CV (HYP-2026-005)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import purged_walk_forward_splits


class CSBABPipeline:
    """Causal cross-sectional feature pipeline for Betting Against Beta / Low Volatility."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        ret_1h = pl.col("close").pct_change().over("asset")

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                rv_72=ret_1h.rolling_std(72).shift(delay).over("asset"),
                rv_168=ret_1h.rolling_std(168).shift(delay).over("asset"),
                rev_72=(-pl.col("close").pct_change(72)).shift(delay).over("asset"),
            )
            .with_columns(
                feat_low_vol=(-pl.col("rv_72") - (-pl.col("rv_72")).mean().over("timestamp"))
                / (pl.col("rv_72").std().over("timestamp") + 1e-8),
                feat_low_vol_slow=(-pl.col("rv_168") - (-pl.col("rv_168")).mean().over("timestamp"))
                / (pl.col("rv_168").std().over("timestamp") + 1e-8),
                feat_rev=(pl.col("rev_72") - pl.col("rev_72").mean().over("timestamp"))
                / (pl.col("rev_72").std().over("timestamp") + 1e-8),
            )
        )


class CSBABRidgeModel:
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
    rebalance_every: int = 4,
    top_k: int = 3,
    **kwargs: Any,
) -> dict[str, Any]:
    """Train Ridge across 4 purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
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

    close_pivot = df.pivot(index="timestamp", on="asset", values="close").fill_null(strategy="forward").fill_null(0.0)
    for a in assets:
        if a not in close_pivot.columns:
            close_pivot = close_pivot.with_columns(pl.lit(0.0).alias(a))
    close_mat = close_pivot.select(assets).to_numpy()
    prev_close = np.vstack([close_mat[:1], close_mat[:-1]])
    ret_mat = np.divide(close_mat - prev_close, prev_close, out=np.zeros_like(close_mat), where=prev_close > 0)

    pipe = CSBABPipeline()
    feat_df = pipe.compute(df, contract)

    f_vol = feat_df.pivot(index="timestamp", on="asset", values="feat_low_vol").fill_null(0.0).select(assets).to_numpy()
    f_vol_slow = feat_df.pivot(index="timestamp", on="asset", values="feat_low_vol_slow").fill_null(0.0).select(assets).to_numpy()
    f_rev = feat_df.pivot(index="timestamp", on="asset", values="feat_rev").fill_null(0.0).select(assets).to_numpy()

    # shortcut: label is close-to-close and starts at bar t (ret_mat[t] is t-1->t), while
    # executed PnL uses open-to-open with a 1-bar delay, so IC/positions are scored against a
    # target one bar earlier than the tradeable horizon. Recorded results are invalid for any
    # promotion; fix the basis before spending another trial on this hypothesis.
    h = contract.label_horizon_bars
    forward_label = np.zeros((n_bars, n_assets), dtype=float)
    for t in range(n_bars - h):
        fwd = np.sum(ret_mat[t : t + h], axis=0)
        forward_label[t] = fwd - np.mean(fwd)

    # Train Ridge walk-forward
    splits = purged_walk_forward_splits(
        n_timestamps=val_end_idx,
        n_folds=contract.n_folds,
        min_train_timestamps=val_start_idx,
        label_horizon=contract.label_horizon_bars,
        embargo=contract.embargo_bars,
    )

    oos_preds = np.full_like(ret_mat, np.nan)
    for train_idx, test_idx in splits:
        train_idx = train_idx[train_idx >= 168]
        if len(train_idx) < 10:
            continue
        x_tr = np.column_stack([f_vol[train_idx].ravel(), f_vol_slow[train_idx].ravel(), f_rev[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([f_vol[t], f_vol_slow[t], f_rev[t]])
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


pipeline = CSBABPipeline()
model = CSBABRidgeModel()
