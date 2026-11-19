"""Cross-sectional Corwin-Schultz effective spread factor model with purged walk-forward CV (HYP-2026-027)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSCorwinSchultzSpreadPipeline:
    """Causal cross-sectional feature pipeline for Corwin-Schultz (2012) high-low effective spread."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        hl = (pl.max_horizontal(pl.col("high"), pl.col("low") * 1.00001) / pl.col("low")).log()
        hl_prev = hl.shift(1).over("asset")

        high_prev = pl.col("high").shift(1).over("asset")
        low_prev = pl.col("low").shift(1).over("asset")

        h2 = pl.max_horizontal(pl.col("high"), high_prev)
        l2 = pl.min_horizontal(pl.col("low"), low_prev)
        hl2 = (pl.max_horizontal(h2, l2 * 1.00001) / l2).log()

        beta = hl.pow(2) + hl_prev.pow(2)
        gamma = hl2.pow(2)

        sqrt_2_plus_1 = float(np.sqrt(2.0) + 1.0)
        alpha = sqrt_2_plus_1 * (beta.sqrt() - gamma.sqrt())
        spread = 2.0 * (alpha.exp() - 1.0) / (alpha.exp() + 1.0)
        cs_spread = pl.max_horizontal(spread, pl.lit(0.0))

        df = df.with_columns(cs_spread=cs_spread)

        cs_168 = pl.col("cs_spread").rolling_mean(168, min_samples=2).shift(delay).over("asset")
        cs_72 = pl.col("cs_spread").rolling_mean(72, min_samples=2).shift(delay).over("asset")
        cs_24 = pl.col("cs_spread").rolling_mean(24, min_samples=2).shift(delay).over("asset")

        df = df.with_columns(
            cs_168=cs_168,
            cs_72=cs_72,
            cs_24=cs_24,
        )

        return df.with_columns(
            feat_cs_spread_168=(pl.col("cs_168") - pl.col("cs_168").mean().over("timestamp"))
            / (pl.col("cs_168").std().over("timestamp") + 1e-8),
            feat_cs_spread_72=(pl.col("cs_72") - pl.col("cs_72").mean().over("timestamp"))
            / (pl.col("cs_72").std().over("timestamp") + 1e-8),
            feat_cs_spread_24=(pl.col("cs_24") - pl.col("cs_24").mean().over("timestamp"))
            / (pl.col("cs_24").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_cs_spread_168', 'feat_cs_spread_72', 'feat_cs_spread_24'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSCorwinSchultzSpreadPipeline()
