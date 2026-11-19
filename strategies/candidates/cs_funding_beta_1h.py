"""Cross-sectional systematic funding beta factor model with purged walk-forward CV (HYP-2026-016)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSFundingBetaPipeline:
    """Causal cross-sectional feature pipeline for systematic funding beta."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        # 1. Select 8h settlement timestamps (00:00, 08:00, 16:00 UTC)
        settle_df = df.filter(pl.col("timestamp").dt.hour() % 8 == 0).sort("timestamp", "asset")

        mkt_funding = (
            settle_df.group_by("timestamp")
            .agg(pl.col("funding_rate").median().alias("mkt_funding"))
            .sort("timestamp")
        )
        mkt_funding = mkt_funding.with_columns(
            delta_mkt_funding=pl.col("mkt_funding").diff().fill_null(0.0)
        )

        settle_df = settle_df.join(mkt_funding, on="timestamp", how="left").sort("timestamp", "asset")

        settle_df = settle_df.with_columns(
            ret_8h=pl.col("close").pct_change().over("asset").fill_null(0.0)
        )

        prod = pl.col("ret_8h") * pl.col("delta_mkt_funding")
        cov_90 = (
            prod.rolling_mean(90).over("asset")
            - pl.col("ret_8h").rolling_mean(90).over("asset")
            * pl.col("delta_mkt_funding").rolling_mean(90).over("asset")
        )
        var_90 = (
            pl.col("delta_mkt_funding").pow(2).rolling_mean(90).over("asset")
            - pl.col("delta_mkt_funding").rolling_mean(90).over("asset").pow(2)
        )
        beta_90 = (cov_90 / (var_90 + 1e-12)).alias("beta_720")

        cov_21 = (
            prod.rolling_mean(21).over("asset")
            - pl.col("ret_8h").rolling_mean(21).over("asset")
            * pl.col("delta_mkt_funding").rolling_mean(21).over("asset")
        )
        var_21 = (
            pl.col("delta_mkt_funding").pow(2).rolling_mean(21).over("asset")
            - pl.col("delta_mkt_funding").rolling_mean(21).over("asset").pow(2)
        )
        beta_21 = (cov_21 / (var_21 + 1e-12)).alias("beta_168")

        settle_features = settle_df.with_columns(
            beta_720=beta_90,
            beta_168=beta_21,
        ).select(["timestamp", "asset", "beta_720", "beta_168"])

        df = df.join(settle_features, on=["timestamp", "asset"], how="left").sort("timestamp", "asset")
        df = df.with_columns(
            beta_720=pl.col("beta_720").forward_fill().shift(delay).over("asset"),
            beta_168=pl.col("beta_168").forward_fill().shift(delay).over("asset"),
            funding_level=pl.col("funding_rate").rolling_mean(24).shift(delay).over("asset"),
        )
        return df.with_columns(
            feat_funding_beta_720=(pl.col("beta_720") - pl.col("beta_720").mean().over("timestamp"))
            / (pl.col("beta_720").std().over("timestamp") + 1e-8),
            feat_funding_beta_168=(pl.col("beta_168") - pl.col("beta_168").mean().over("timestamp"))
            / (pl.col("beta_168").std().over("timestamp") + 1e-8),
            feat_funding_level=(pl.col("funding_level") - pl.col("funding_level").mean().over("timestamp"))
            / (pl.col("funding_level").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_funding_beta_720', 'feat_funding_beta_168', 'feat_funding_level'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSFundingBetaPipeline()
