"""Cross-sectional information delay and lead-lag catch-up factor model (HYP-2026-033)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSInformationDelayPipeline:
    """Causal cross-sectional feature pipeline for Hou-Moskowitz joint rolling OLS delay and lead-lag backlog."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")
        timestamps = sorted(df["timestamp"].unique().to_list())
        assets = sorted(df["asset"].unique().to_list())
        n_bars, n_assets = len(timestamps), len(assets)

        ret_pivot = df.pivot(index="timestamp", on="asset", values="close").sort("timestamp")
        ret_mat = np.zeros((n_bars, n_assets), dtype=float)
        present = ret_pivot.select([pl.col(a).is_not_null() for a in assets]).to_numpy()
        for j, a in enumerate(assets):
            p = ret_pivot[a].cast(pl.Float64).to_numpy()
            valid = np.isfinite(p[:-1]) & np.isfinite(p[1:]) & (p[:-1] > 0)
            ret_mat[1:, j] = np.divide(
                p[1:] - p[:-1], p[:-1] + 1e-12,
                out=np.zeros(n_bars - 1, dtype=float), where=valid,
            )

        btc_idx = [i for i, a in enumerate(assets) if "btc" in a.lower()]
        if len(btc_idx) > 0:
            ret_mkt = ret_mat[:, btc_idx[0]]
        else:
            ret_mkt = np.mean(ret_mat, axis=1)

        x_cols = np.zeros((n_bars, 6), dtype=float)
        x_cols[:, 0] = 1.0
        x_cols[:, 1] = ret_mkt
        for k in range(1, 5):
            x_cols[k:, k + 1] = ret_mkt[:-k]

        w = 336
        backlog_mat = np.zeros((n_bars, n_assets), dtype=float)
        delay_ratio_mat = np.zeros((n_bars, n_assets), dtype=float)

        for t in range(w, n_bars):
            xt = x_cols[t - w : t]
            yt = ret_mat[t - w : t]
            xtx = xt.T @ xt + 1e-6 * np.eye(6)
            xty = xt.T @ yt
            betas = np.linalg.solve(xtx, xty)
            beta_0 = betas[1]
            beta_lags = betas[2:6]

            mkt_vec = np.array([x_cols[t, 1], x_cols[t, 2], x_cols[t, 3], x_cols[t, 4]])
            delayed_drift = np.dot(beta_lags.T, mkt_vec)
            backlog_mat[t] = delayed_drift - ret_mat[t]
            sum_lag_beta = np.sum(np.abs(beta_lags), axis=0)
            delay_ratio_mat[t] = sum_lag_beta / (np.abs(beta_0) + sum_lag_beta + 1e-6)

        if delay > 0:
            backlog_mat = np.vstack([np.zeros((delay, n_assets)), backlog_mat[:-delay]])
            delay_ratio_mat = np.vstack([np.zeros((delay, n_assets)), delay_ratio_mat[:-delay]])

        # Sparse universe: normalize over instruments observed at each time.
        # Future listings must not change historical cross-sectional features.
        counts = np.maximum(present.sum(axis=1, keepdims=True), 1)

        def observed_zscore(values: np.ndarray) -> np.ndarray:
            mean = np.where(present, values, 0.0).sum(axis=1, keepdims=True) / counts
            var = np.where(present, (values - mean) ** 2, 0.0).sum(axis=1, keepdims=True) / counts
            return np.where(present, (values - mean) / (np.sqrt(var) + 1e-8), 0.0)

        z_backlog = observed_zscore(backlog_mat)
        z_delay = observed_zscore(delay_ratio_mat)

        # Never flatten a dense [time, asset] grid directly onto sparse rows:
        # reconstruct each observed row's time/asset index first.
        time_keys = pl.DataFrame({"timestamp": timestamps, "_t": range(n_bars)})
        asset_keys = pl.DataFrame({"asset": assets, "_a": range(n_assets)})
        idx = (
            df.select("timestamp", "asset")
            .join(time_keys, on="timestamp", how="left")
            .join(asset_keys, on="asset", how="left")
        )
        offsets = idx["_t"].to_numpy() * n_assets + idx["_a"].to_numpy()
        return df.with_columns(
            feat_backlog=pl.Series("feat_backlog", z_backlog.ravel()[offsets]),
            feat_delay_ratio=pl.Series("feat_delay_ratio", z_delay.ravel()[offsets]),
        )


class CSInformationDelayRidgeModel:
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
    rebalance_every: int = 48,
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

    pipe = CSInformationDelayPipeline()
    feat_df = pipe.compute(df, contract)

    f1 = feat_df.pivot(index="timestamp", on="asset", values="feat_backlog").fill_null(0.0).select(assets).to_numpy()
    f2 = feat_df.pivot(index="timestamp", on="asset", values="feat_delay_ratio").fill_null(0.0).select(assets).to_numpy()

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
        x_tr = np.column_stack([f1[train_idx].ravel(), f2[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([f1[t], f2[t]])
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


pipeline = CSInformationDelayPipeline()
model = CSInformationDelayRidgeModel()
