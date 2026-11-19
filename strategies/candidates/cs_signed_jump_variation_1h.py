"""Cross-sectional signed jump variation factor model with purged walk-forward CV (HYP-2026-049)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSSignedJumpVariationPipeline:
    """Causal cross-sectional feature pipeline for signed jump variation (RS+ - RS-) / RV."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})

        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        ret = pl.col("close").pct_change().over("asset").fill_null(0.0)
        r_pos_sq = pl.when(ret > 0.0).then(ret.pow(2)).otherwise(0.0)
        r_neg_sq = pl.when(ret < 0.0).then(ret.pow(2)).otherwise(0.0)
        r_sq = ret.pow(2)

        df = df.with_columns(
            r_pos_sq=r_pos_sq,
            r_neg_sq=r_neg_sq,
            r_sq=r_sq,
        )

        rs_plus_168 = pl.col("r_pos_sq").rolling_sum(168).over("asset")
        rs_minus_168 = pl.col("r_neg_sq").rolling_sum(168).over("asset")
        rv_168 = pl.col("r_sq").rolling_sum(168).over("asset")
        sjv_168 = (rs_plus_168 - rs_minus_168) / (rv_168 + 1e-8)

        rs_plus_72 = pl.col("r_pos_sq").rolling_sum(72).over("asset")
        rs_minus_72 = pl.col("r_neg_sq").rolling_sum(72).over("asset")
        rv_72 = pl.col("r_sq").rolling_sum(72).over("asset")
        sjv_72 = (rs_plus_72 - rs_minus_72) / (rv_72 + 1e-8)

        return (
            df.with_columns(
                sjv_168=sjv_168.shift(delay).over("asset"),
                sjv_72=sjv_72.shift(delay).over("asset"),
            )
            .with_columns(
                feat_sjv_168=(pl.col("sjv_168") - pl.col("sjv_168").mean().over("timestamp"))
                / (pl.col("sjv_168").std().over("timestamp") + 1e-8),
                feat_sjv_72=(pl.col("sjv_72") - pl.col("sjv_72").mean().over("timestamp"))
                / (pl.col("sjv_72").std().over("timestamp") + 1e-8),
                feat_sjv_rank=pl.col("sjv_168").rank().over("timestamp")
                / (pl.col("sjv_168").count().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_sjv_168', 'feat_sjv_72', 'feat_sjv_rank'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=None, btc_eligible=True, **kwargs)


pipeline = CSSignedJumpVariationPipeline()
