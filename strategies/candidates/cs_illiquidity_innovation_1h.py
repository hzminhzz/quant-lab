"""Cross-sectional Amihud illiquidity innovation shock factor model with purged walk-forward CV (HYP-2026-032)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSIlliquidityInnovationPipeline:
    """Causal cross-sectional feature pipeline for illiquidity innovations (Acharya & Pedersen 2005, Chordia et al. 2001)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        df = df.sort("timestamp", "asset")
        dvol = pl.col("volume") * pl.col("close")
        ret_abs = pl.col("close").pct_change().abs().over("asset")
        illiq_raw = ret_abs / (dvol + 1.0)

        illiq_24 = illiq_raw.rolling_mean(24).over("asset")
        illiq_168 = illiq_raw.rolling_mean(168).over("asset")
        sigma_168 = illiq_raw.rolling_std(168).over("asset")
        illiq_720 = illiq_raw.rolling_mean(720).over("asset")
        sigma_720 = illiq_raw.rolling_std(720).over("asset")

        innov_30d = ((illiq_168 - illiq_720) / (sigma_720 + 1e-8)).clip(-5.0, 5.0).shift(delay).over("asset")
        innov_7d = ((illiq_24 - illiq_168) / (sigma_168 + 1e-8)).clip(-5.0, 5.0).shift(delay).over("asset")
        level_168 = illiq_168.shift(delay).over("asset")

        df = df.with_columns(
            innov_30d=innov_30d,
            innov_7d=innov_7d,
            level_168=level_168,
        )

        return df.with_columns(
            feat_illiq_innov_30d=(pl.col("innov_30d") - pl.col("innov_30d").mean().over("timestamp"))
            / (pl.col("innov_30d").std().over("timestamp") + 1e-8),
            feat_illiq_innov_7d=(pl.col("innov_7d") - pl.col("innov_7d").mean().over("timestamp"))
            / (pl.col("innov_7d").std().over("timestamp") + 1e-8),
            feat_illiq_level=(pl.col("level_168") - pl.col("level_168").mean().over("timestamp"))
            / (pl.col("level_168").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_illiq_innov_30d', 'feat_illiq_innov_7d', 'feat_illiq_level'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol='BTCUSDT', **kwargs)


pipeline = CSIlliquidityInnovationPipeline()
