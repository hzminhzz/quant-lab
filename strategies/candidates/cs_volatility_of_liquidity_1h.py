"""Cross-sectional volatility of liquidity factor model with purged walk-forward CV (HYP-2026-059)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSVolatilityOfLiquidityPipeline:
    """Causal cross-sectional feature pipeline for volatility of liquidity (Amihud std)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        dvol = pl.col("volume") * pl.col("close")
        ret_abs = (pl.col("close") / pl.col("close").shift(1)).log().abs().over("asset")
        amihud = ret_abs / (dvol + 1e-5)

        df = df.with_columns(amihud=amihud)

        voliq_336 = pl.col("amihud").rolling_std(336, min_samples=24).shift(delay).over("asset")
        voliq_168 = pl.col("amihud").rolling_std(168, min_samples=24).shift(delay).over("asset")
        voliq_72 = pl.col("amihud").rolling_std(72, min_samples=12).shift(delay).over("asset")

        df = df.with_columns(
            voliq_336=voliq_336,
            voliq_168=voliq_168,
            voliq_72=voliq_72,
        )

        return df.with_columns(
            feat_voliq_336=(pl.col("voliq_336") - pl.col("voliq_336").mean().over("timestamp"))
            / (pl.col("voliq_336").std().over("timestamp") + 1e-8),
            feat_voliq_168=(pl.col("voliq_168") - pl.col("voliq_168").mean().over("timestamp"))
            / (pl.col("voliq_168").std().over("timestamp") + 1e-8),
            feat_voliq_72=(pl.col("voliq_72") - pl.col("voliq_72").mean().over("timestamp"))
            / (pl.col("voliq_72").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_voliq_336', 'feat_voliq_168', 'feat_voliq_72'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=True, min_train=10, **kwargs)


pipeline = CSVolatilityOfLiquidityPipeline()
