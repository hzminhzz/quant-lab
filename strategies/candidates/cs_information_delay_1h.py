"""Cross-sectional information delay and lead-lag catch-up factor model (HYP-2026-033)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


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
        for j, a in enumerate(assets):
            if a in ret_pivot.columns:
                p = ret_pivot[a].to_numpy()
                ret_mat[1:, j] = np.diff(p) / (p[:-1] + 1e-12)

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

        mean_b = np.mean(backlog_mat, axis=1, keepdims=True)
        std_b = np.std(backlog_mat, axis=1, keepdims=True) + 1e-8
        z_backlog = (backlog_mat - mean_b) / std_b

        mean_d = np.mean(delay_ratio_mat, axis=1, keepdims=True)
        std_d = np.std(delay_ratio_mat, axis=1, keepdims=True) + 1e-8
        z_delay = (delay_ratio_mat - mean_d) / std_d

        return df.with_columns(
            feat_backlog=pl.Series("feat_backlog", z_backlog.ravel()),
            feat_delay_ratio=pl.Series("feat_delay_ratio", z_delay.ravel()),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 48, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_backlog', 'feat_delay_ratio'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSInformationDelayPipeline()
