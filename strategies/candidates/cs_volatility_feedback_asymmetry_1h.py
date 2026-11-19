"""Cross-sectional return-volatility feedback asymmetry factor model with purged walk-forward CV (HYP-2026-058)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSVolatilityFeedbackAsymmetryPipeline:
    """Causal cross-sectional feature pipeline for volatility feedback asymmetry (Bekaert & Wu 2000, Campbell & Hentschel 1992)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        sigma = (
            (pl.col("high") / pl.max_horizontal(pl.col("low"), pl.lit(1e-8))).log().pow(2)
            / (4.0 * float(np.log(2)))
        ).sqrt()

        delta_sigma = sigma - sigma.shift(1).over("asset")
        r_1h = (pl.col("close") / pl.col("close").shift(1)).log().over("asset")

        df = df.with_columns(
            sigma=sigma,
            delta_sigma=delta_sigma,
            r_1h=r_1h,
        )

        cov_336 = (
            (pl.col("r_1h") * pl.col("delta_sigma")).rolling_mean(336, min_samples=48).over("asset")
            - pl.col("r_1h").rolling_mean(336, min_samples=48).over("asset")
            * pl.col("delta_sigma").rolling_mean(336, min_samples=48).over("asset")
        )
        var_r_336 = pl.col("r_1h").rolling_var(336, min_samples=48).over("asset")
        var_dsigma_336 = pl.col("delta_sigma").rolling_var(336, min_samples=48).over("asset")
        corr_336 = cov_336 / ((var_r_336 * var_dsigma_336).sqrt() + 1e-8)
        vfs_336 = -corr_336

        cov_168 = (
            (pl.col("r_1h") * pl.col("delta_sigma")).rolling_mean(168, min_samples=24).over("asset")
            - pl.col("r_1h").rolling_mean(168, min_samples=24).over("asset")
            * pl.col("delta_sigma").rolling_mean(168, min_samples=24).over("asset")
        )
        var_r_168 = pl.col("r_1h").rolling_var(168, min_samples=24).over("asset")
        var_dsigma_168 = pl.col("delta_sigma").rolling_var(168, min_samples=24).over("asset")
        corr_168 = cov_168 / ((var_r_168 * var_dsigma_168).sqrt() + 1e-8)
        vfs_168 = -corr_168

        parkinson_336 = pl.col("sigma").rolling_mean(336, min_samples=48).over("asset")

        df = df.with_columns(
            vfs_336=vfs_336.shift(delay).over("asset"),
            vfs_168=vfs_168.shift(delay).over("asset"),
            parkinson_336=parkinson_336.shift(delay).over("asset"),
        )

        return df.with_columns(
            feat_vfs_336=(pl.col("vfs_336") - pl.col("vfs_336").mean().over("timestamp"))
            / (pl.col("vfs_336").std().over("timestamp") + 1e-8),
            feat_vfs_168=(pl.col("vfs_168") - pl.col("vfs_168").mean().over("timestamp"))
            / (pl.col("vfs_168").std().over("timestamp") + 1e-8),
            feat_parkinson_vol_336=(pl.col("parkinson_336") - pl.col("parkinson_336").mean().over("timestamp"))
            / (pl.col("parkinson_336").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_vfs_336', 'feat_vfs_168', 'feat_parkinson_vol_336'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=('BTCUSDT', 'BTCUSDT_PERP'), btc_eligible=False, **kwargs)


pipeline = CSVolatilityFeedbackAsymmetryPipeline()
