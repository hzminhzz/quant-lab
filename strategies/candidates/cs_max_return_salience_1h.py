"""Cross-sectional maximum return and salience overpricing factor model with purged walk-forward CV (HYP-2026-037)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSMaxReturnSaliencePipeline:
    """Causal cross-sectional feature pipeline for maximum daily return salience."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        ret_24h = pl.col("close").pct_change(24).shift(delay).over("asset")
        ret_1h = pl.col("close").pct_change(1).shift(delay).over("asset")
        df = df.with_columns(
            ret_24h=ret_24h,
            ret_1h=ret_1h,
        )

        max_24h_14d = pl.col("ret_24h").rolling_max(336).over("asset")
        max_24h_7d = pl.col("ret_24h").rolling_max(168).over("asset")
        max_1h_72h = pl.col("ret_1h").rolling_max(72).over("asset")

        df = df.with_columns(
            max_24h_14d=max_24h_14d,
            max_24h_7d=max_24h_7d,
            max_1h_72h=max_1h_72h,
        )

        # Negated so lower MAX (disciplined assets) yields higher signal score.
        return df.with_columns(
            feat_max_14d=(-pl.col("max_24h_14d") - (-pl.col("max_24h_14d")).mean().over("timestamp"))
            / (pl.col("max_24h_14d").std().over("timestamp") + 1e-8),
            feat_max_7d=(-pl.col("max_24h_7d") - (-pl.col("max_24h_7d")).mean().over("timestamp"))
            / (pl.col("max_24h_7d").std().over("timestamp") + 1e-8),
            feat_max_fast=(-pl.col("max_1h_72h") - (-pl.col("max_1h_72h")).mean().over("timestamp"))
            / (pl.col("max_1h_72h").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_max_14d', 'feat_max_7d', 'feat_max_fast'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSMaxReturnSaliencePipeline()
