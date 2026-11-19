"""Cross-sectional funding memory persistence factor model with purged walk-forward CV (HYP-2026-021)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSFundingMemoryPersistencePipeline:
    """Causal cross-sectional feature pipeline for multi-horizon funding memory persistence."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        if "funding_rate" in df.columns:
            rate_expr = pl.col("funding_rate")
        else:
            rate_expr = (pl.col("close") - pl.col("open")) / (pl.col("open") + 1e-12)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                fund_ewm_336=rate_expr.ewm_mean(span=336).shift(delay).over("asset"),
                fund_ewm_168=rate_expr.ewm_mean(span=168).shift(delay).over("asset"),
                fund_mean_24=rate_expr.rolling_mean(24).shift(delay).over("asset"),
            )
            .with_columns(
                # Carry premium: longing negative funding (getting paid) and shorting positive funding (paying carry)
                feat_carry_memory_14d=(-pl.col("fund_ewm_336") - (-pl.col("fund_ewm_336")).mean().over("timestamp"))
                / (pl.col("fund_ewm_336").std().over("timestamp") + 1e-8),
                feat_carry_memory_7d=(-pl.col("fund_ewm_168") - (-pl.col("fund_ewm_168")).mean().over("timestamp"))
                / (pl.col("fund_ewm_168").std().over("timestamp") + 1e-8),
                feat_carry_24h=(-pl.col("fund_mean_24") - (-pl.col("fund_mean_24")).mean().over("timestamp"))
                / (pl.col("fund_mean_24").std().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral carry positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_carry_memory_14d', 'feat_carry_memory_7d', 'feat_carry_24h'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSFundingMemoryPersistencePipeline()
