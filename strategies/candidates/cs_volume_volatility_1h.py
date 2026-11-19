"""Cross-sectional volume volatility factor model with purged walk-forward CV (HYP-2026-061)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSVolumeVolatilityPipeline:
    """Causal cross-sectional feature pipeline for volume volatility (CVV = std(vol)/mean(vol))."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        cvv_336 = (
            pl.col("volume").rolling_std(336, min_samples=24)
            / (pl.col("volume").rolling_mean(336, min_samples=24) + 1e-8)
        ).shift(delay).over("asset")

        cvv_168 = (
            pl.col("volume").rolling_std(168, min_samples=24)
            / (pl.col("volume").rolling_mean(168, min_samples=24) + 1e-8)
        ).shift(delay).over("asset")

        cvv_72 = (
            pl.col("volume").rolling_std(72, min_samples=12)
            / (pl.col("volume").rolling_mean(72, min_samples=12) + 1e-8)
        ).shift(delay).over("asset")

        df = df.with_columns(
            cvv_336=cvv_336,
            cvv_168=cvv_168,
            cvv_72=cvv_72,
        )

        return df.with_columns(
            feat_cvv_336=(pl.col("cvv_336") - pl.col("cvv_336").mean().over("timestamp"))
            / (pl.col("cvv_336").std().over("timestamp") + 1e-8),
            feat_cvv_168=(pl.col("cvv_168") - pl.col("cvv_168").mean().over("timestamp"))
            / (pl.col("cvv_168").std().over("timestamp") + 1e-8),
            feat_cvv_72=(pl.col("cvv_72") - pl.col("cvv_72").mean().over("timestamp"))
            / (pl.col("cvv_72").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_cvv_336', 'feat_cvv_168', 'feat_cvv_72'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=True, min_train=10, **kwargs)


pipeline = CSVolumeVolatilityPipeline()
