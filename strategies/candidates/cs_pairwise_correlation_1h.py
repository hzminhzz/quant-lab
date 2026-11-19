"""Cross-sectional pairwise correlation integration and diversification risk premium factor model with purged walk-forward CV (HYP-2026-039)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSPairwiseCorrelationPipeline:
    """Causal cross-sectional feature pipeline for systemic pairwise correlation and integration."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        log_ret = (pl.col("close") / pl.col("close").shift(1).over("asset")).log().shift(delay).over("asset").fill_null(0.0)
        df = df.with_columns(log_ret=log_ret)

        timestamps = sorted(df["timestamp"].unique().to_list())
        assets = sorted(df["asset"].unique().to_list())
        n_timestamps = len(timestamps)
        n_assets = len(assets)

        if n_assets < 2 or n_timestamps < 5:
            return df.with_columns(
                feat_corr_720=pl.lit(0.0),
                feat_corr_168=pl.lit(0.0),
            )

        pivot = df.pivot(index="timestamp", on="asset", values="log_ret").fill_null(0.0)
        for a in assets:
            if a not in pivot.columns:
                pivot = pivot.with_columns(pl.lit(0.0).alias(a))
        R = pivot.select(assets).to_numpy()

        pw_720 = np.zeros((n_timestamps, n_assets), dtype=float)
        pw_168 = np.zeros((n_timestamps, n_assets), dtype=float)

        denom = max(1.0, float(n_assets - 1))
        for t in range(n_timestamps):
            start_720 = max(0, t - 720 + 1)
            win_720 = R[start_720 : t + 1]
            w_len_720 = len(win_720)
            if w_len_720 >= 10:
                std_720 = np.std(win_720, axis=0)
                valid_mask = std_720 > 1e-10
                if np.sum(valid_mask) > 1:
                    u = np.zeros_like(win_720)
                    u[:, valid_mask] = (win_720[:, valid_mask] - np.mean(win_720[:, valid_mask], axis=0)) / std_720[valid_mask]
                    S = np.sum(u, axis=1)
                    sum_corr = (u.T @ S) / float(w_len_720)
                    pw_720[t] = (sum_corr - 1.0) / denom

            start_168 = max(0, t - 168 + 1)
            win_168 = R[start_168 : t + 1]
            w_len_168 = len(win_168)
            if w_len_168 >= 10:
                std_168 = np.std(win_168, axis=0)
                valid_mask = std_168 > 1e-10
                if np.sum(valid_mask) > 1:
                    u = np.zeros_like(win_168)
                    u[:, valid_mask] = (win_168[:, valid_mask] - np.mean(win_168[:, valid_mask], axis=0)) / std_168[valid_mask]
                    S = np.sum(u, axis=1)
                    sum_corr = (u.T @ S) / float(w_len_168)
                    pw_168[t] = (sum_corr - 1.0) / denom

        ts_list = [t for t in timestamps for _ in range(n_assets)]
        asset_list = assets * n_timestamps
        feat_df = pl.DataFrame(
            {
                "timestamp": ts_list,
                "asset": asset_list,
                "corr_720": pw_720.ravel(),
                "corr_168": pw_168.ravel(),
            },
            schema={
                "timestamp": df.schema["timestamp"],
                "asset": pl.String,
                "corr_720": pl.Float64,
                "corr_168": pl.Float64,
            },
        )

        df = df.join(feat_df, on=["timestamp", "asset"], how="left")

        return df.with_columns(
            feat_corr_720=(pl.col("corr_720") - pl.col("corr_720").mean().over("timestamp"))
            / (pl.col("corr_720").std().over("timestamp") + 1e-8),
            feat_corr_168=(pl.col("corr_168") - pl.col("corr_168").mean().over("timestamp"))
            / (pl.col("corr_168").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_corr_720', 'feat_corr_168'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSPairwiseCorrelationPipeline()
