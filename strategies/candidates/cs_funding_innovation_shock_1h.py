"""Cross-sectional unanticipated funding rate innovation shock factor model with purged walk-forward CV (HYP-2026-019)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSFundingInnovationShockPipeline:
    """Causal cross-sectional feature pipeline for unanticipated funding rate innovation shocks."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        ema_168 = pl.col("funding_rate").ewm_mean(span=168).over("asset")
        std_168 = pl.col("funding_rate").rolling_std(168).over("asset")
        shock_168 = ((pl.col("funding_rate") - ema_168) / (std_168 + 1e-6)).shift(delay).over("asset")

        ema_72 = pl.col("funding_rate").ewm_mean(span=72).over("asset")
        std_72 = pl.col("funding_rate").rolling_std(72).over("asset")
        shock_72 = ((pl.col("funding_rate") - ema_72) / (std_72 + 1e-6)).shift(delay).over("asset")

        f24 = pl.col("funding_rate").rolling_mean(24).shift(delay).over("asset")

        df = df.with_columns(
            shock_168=shock_168,
            shock_72=shock_72,
            f24=f24,
        )

        return df.with_columns(
            feat_innovation_168=(-pl.col("shock_168") - (-pl.col("shock_168")).mean().over("timestamp"))
            / (pl.col("shock_168").std().over("timestamp") + 1e-8),
            feat_innovation_72=(-pl.col("shock_72") - (-pl.col("shock_72")).mean().over("timestamp"))
            / (pl.col("shock_72").std().over("timestamp") + 1e-8),
            feat_carry=(-pl.col("f24") - (-pl.col("f24")).mean().over("timestamp"))
            / (pl.col("f24").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_innovation_168', 'feat_innovation_72', 'feat_carry'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSFundingInnovationShockPipeline()
