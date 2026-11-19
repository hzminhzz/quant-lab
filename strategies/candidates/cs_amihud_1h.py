"""Cross-sectional Amihud illiquidity factor model with purged walk-forward CV (HYP-2026-004)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSAmihudPipeline:
    """Causal cross-sectional feature pipeline for Amihud illiquidity."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        dvol = pl.col("volume") * pl.col("close")
        ret_abs = pl.col("close").pct_change().abs().over("asset")
        amihud_raw = ret_abs / (dvol + 1e-5)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                amihud_168=amihud_raw.rolling_mean(168).shift(delay).over("asset"),
                amihud_24=amihud_raw.rolling_mean(24).shift(delay).over("asset"),
                vol_24=dvol.rolling_mean(24).shift(delay).over("asset"),
            )
            .with_columns(
                feat_amihud=(pl.col("amihud_168") - pl.col("amihud_168").mean().over("timestamp"))
                / (pl.col("amihud_168").std().over("timestamp") + 1e-8),
                feat_amihud_fast=(pl.col("amihud_24") - pl.col("amihud_24").mean().over("timestamp"))
                / (pl.col("amihud_24").std().over("timestamp") + 1e-8),
                feat_vol=(pl.col("vol_24") - pl.col("vol_24").mean().over("timestamp"))
                / (pl.col("vol_24").std().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 4, top_k: int = 3, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across 4 purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_amihud', 'feat_amihud_fast', 'feat_vol'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=False, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSAmihudPipeline()
