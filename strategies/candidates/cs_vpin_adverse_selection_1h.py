"""Cross-sectional VPIN adverse selection factor model with purged walk-forward CV (HYP-2026-062)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSVpinAdverseSelectionPipeline:
    """Causal cross-sectional feature pipeline for VPIN order flow toxicity."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        range_bar = pl.max_horizontal(pl.col("high") - pl.col("low"), pl.lit(1e-8))
        imbalance = pl.col("volume") * (2.0 * pl.col("close") - pl.col("high") - pl.col("low")).abs() / range_bar

        df = df.with_columns(imbalance=imbalance)

        vpin_168 = (
            pl.col("imbalance").rolling_sum(168, min_samples=24)
            / (pl.col("volume").rolling_sum(168, min_samples=24) + 1e-8)
        ).shift(delay).over("asset")

        vpin_72 = (
            pl.col("imbalance").rolling_sum(72, min_samples=12)
            / (pl.col("volume").rolling_sum(72, min_samples=12) + 1e-8)
        ).shift(delay).over("asset")

        vpin_24 = (
            pl.col("imbalance").rolling_sum(24, min_samples=6)
            / (pl.col("volume").rolling_sum(24, min_samples=6) + 1e-8)
        ).shift(delay).over("asset")

        df = df.with_columns(
            vpin_168=vpin_168,
            vpin_72=vpin_72,
            vpin_24=vpin_24,
        )

        return df.with_columns(
            feat_vpin_168=(pl.col("vpin_168") - pl.col("vpin_168").mean().over("timestamp"))
            / (pl.col("vpin_168").std().over("timestamp") + 1e-8),
            feat_vpin_72=(pl.col("vpin_72") - pl.col("vpin_72").mean().over("timestamp"))
            / (pl.col("vpin_72").std().over("timestamp") + 1e-8),
            feat_vpin_24=(pl.col("vpin_24") - pl.col("vpin_24").mean().over("timestamp"))
            / (pl.col("vpin_24").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_vpin_168', 'feat_vpin_72', 'feat_vpin_24'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=True, min_train=10, **kwargs)


pipeline = CSVpinAdverseSelectionPipeline()
