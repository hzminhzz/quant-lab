"""Cross-sectional funding convexity and skewness factor model with purged walk-forward CV (HYP-2026-018)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSFundingConvexitySkewPipeline:
    """Causal cross-sectional feature pipeline for funding rate skewness and convexity."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        f = pl.col("funding_rate")
        f2 = pl.col("funding_rate").pow(2)
        f3 = pl.col("funding_rate").pow(3)

        def calc_skew(window: int):
            m1 = f.rolling_mean(window).over("asset")
            m2 = f2.rolling_mean(window).over("asset")
            m3 = f3.rolling_mean(window).over("asset")
            var = (m2 - m1.pow(2)).clip(lower_bound=1e-12)
            c3 = m3 - 3.0 * m1 * m2 + 2.0 * m1.pow(3)
            return (c3 / (var.pow(1.5) + 1e-12)).shift(delay).over("asset")

        skew_336 = calc_skew(336)
        skew_168 = calc_skew(168)
        f24 = f.rolling_mean(24).shift(delay).over("asset")

        df = df.with_columns(
            skew_336=skew_336,
            skew_168=skew_168,
            f24=f24,
        )

        return df.with_columns(
            feat_skew_336=(-pl.col("skew_336") - (-pl.col("skew_336")).mean().over("timestamp"))
            / (pl.col("skew_336").std().over("timestamp") + 1e-8),
            feat_skew_168=(-pl.col("skew_168") - (-pl.col("skew_168")).mean().over("timestamp"))
            / (pl.col("skew_168").std().over("timestamp") + 1e-8),
            feat_funding=(-pl.col("f24") - (-pl.col("f24")).mean().over("timestamp"))
            / (pl.col("f24").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_skew_336', 'feat_skew_168', 'feat_funding'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSFundingConvexitySkewPipeline()
