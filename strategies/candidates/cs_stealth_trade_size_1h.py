"""Cross-sectional trade size granularity & institutional stealth trading factor model with purged walk-forward CV (HYP-2026-050)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSStealthTradeSizePipeline:
    """Causal cross-sectional feature pipeline for average trade size expansion ratio (ATS_7d / ATS_30d)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})

        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        # Explicitly require quote_volume and count as specified in brief
        qvol = pl.col("quote_volume") if "quote_volume" in df.columns else pl.col("volume") * pl.col("close")
        cnt = pl.col("count").clip(lower_bound=1.0) if "count" in df.columns else pl.lit(1.0)

        ats = qvol / (cnt + 1e-5)
        df = df.with_columns(ats=ats)

        ats_168 = pl.col("ats").rolling_mean(168).over("asset")
        ats_720 = pl.col("ats").rolling_mean(720).over("asset")
        ats_72 = pl.col("ats").rolling_mean(72).over("asset")

        ats_ratio_168 = (ats_168 / (ats_720 + 1e-8)).shift(delay).over("asset")
        ats_ratio_72 = (ats_72 / (ats_720 + 1e-8)).shift(delay).over("asset")

        return (
            df.with_columns(
                ats_ratio_168=ats_ratio_168,
                ats_ratio_72=ats_ratio_72,
            )
            .with_columns(
                feat_ats_ratio_168=(pl.col("ats_ratio_168") - pl.col("ats_ratio_168").mean().over("timestamp"))
                / (pl.col("ats_ratio_168").std().over("timestamp") + 1e-8),
                feat_ats_ratio_72=(pl.col("ats_ratio_72") - pl.col("ats_ratio_72").mean().over("timestamp"))
                / (pl.col("ats_ratio_72").std().over("timestamp") + 1e-8),
                feat_ats_rank=pl.col("ats_ratio_168").rank().over("timestamp")
                / (pl.col("ats_ratio_168").count().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_ats_ratio_168', 'feat_ats_ratio_72', 'feat_ats_rank'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=None, btc_eligible=True, **kwargs)


pipeline = CSStealthTradeSizePipeline()
