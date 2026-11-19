"""Cross-sectional wick asymmetry and shadow absorption factor model with purged walk-forward CV (HYP-2026-064)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSWickAsymmetryPipeline:
    """Causal cross-sectional feature pipeline for candlestick wick asymmetry (lower vs upper shadow absorption)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        body_high = pl.max_horizontal(pl.col("open"), pl.col("close"))
        body_low = pl.min_horizontal(pl.col("open"), pl.col("close"))
        upper_wick = pl.col("high") - body_high
        lower_wick = body_low - pl.col("low")
        bar_range = pl.max_horizontal(pl.col("high") - pl.col("low"), pl.lit(1e-8))

        wick_asym = (lower_wick - upper_wick) / bar_range

        df = df.with_columns(wick_asym=wick_asym)

        asym_168 = pl.col("wick_asym").rolling_mean(168, min_samples=24).shift(delay).over("asset")
        asym_72 = pl.col("wick_asym").rolling_mean(72, min_samples=12).shift(delay).over("asset")
        asym_24 = pl.col("wick_asym").rolling_mean(24, min_samples=6).shift(delay).over("asset")

        df = df.with_columns(
            asym_168=asym_168,
            asym_72=asym_72,
            asym_24=asym_24,
        )

        return df.with_columns(
            feat_wick_asym_168=(pl.col("asym_168") - pl.col("asym_168").mean().over("timestamp"))
            / (pl.col("asym_168").std().over("timestamp") + 1e-8),
            feat_wick_asym_72=(pl.col("asym_72") - pl.col("asym_72").mean().over("timestamp"))
            / (pl.col("asym_72").std().over("timestamp") + 1e-8),
            feat_wick_asym_24=(pl.col("asym_24") - pl.col("asym_24").mean().over("timestamp"))
            / (pl.col("asym_24").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_wick_asym_168', 'feat_wick_asym_72', 'feat_wick_asym_24'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=True, min_train=10, **kwargs)


pipeline = CSWickAsymmetryPipeline()
