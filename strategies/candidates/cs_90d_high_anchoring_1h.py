"""Cross-sectional 90-day high-water mark anchoring factor model with purged walk-forward CV (HYP-2026-023)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CS90DHighAnchoringPipeline:
    """Causal cross-sectional feature pipeline for 90-day high-water mark anchoring."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        close_del = pl.col("close").shift(delay).over("asset")
        h_90d = pl.col("high").rolling_max(2160, min_samples=1).shift(delay).over("asset")
        h_30d = pl.col("high").rolling_max(720, min_samples=1).shift(delay).over("asset")
        h_7d = pl.col("high").rolling_max(168, min_samples=1).shift(delay).over("asset")

        df = df.with_columns(
            hwa_90d=close_del / (h_90d + 1e-8),
            hwa_30d=close_del / (h_30d + 1e-8),
            hwa_7d=close_del / (h_7d + 1e-8),
        )

        return df.with_columns(
            feat_hwa_90d=(pl.col("hwa_90d") - pl.col("hwa_90d").mean().over("timestamp"))
            / (pl.col("hwa_90d").std().over("timestamp") + 1e-8),
            feat_hwa_30d=(pl.col("hwa_30d") - pl.col("hwa_30d").mean().over("timestamp"))
            / (pl.col("hwa_30d").std().over("timestamp") + 1e-8),
            feat_hwa_7d=(pl.col("hwa_7d") - pl.col("hwa_7d").mean().over("timestamp"))
            / (pl.col("hwa_7d").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_hwa_90d', 'feat_hwa_30d', 'feat_hwa_7d'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CS90DHighAnchoringPipeline()
