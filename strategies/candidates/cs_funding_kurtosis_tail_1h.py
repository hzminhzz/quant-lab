"""Cross-sectional funding rate kurtosis and jump-tail factor model with purged walk-forward CV (HYP-2026-020)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSFundingKurtosisTailPipeline:
    """Causal cross-sectional feature pipeline for funding rate kurtosis and tail risk."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        if "funding_rate" in df.columns:
            rate_expr = pl.col("funding_rate")
        else:
            rate_expr = (pl.col("close") - pl.col("open")) / (pl.col("open") + 1e-12)

        w_long = 168
        mu_168 = rate_expr.rolling_mean(w_long).over("asset")
        m2_168 = (rate_expr**2).rolling_mean(w_long).over("asset")
        m3_168 = (rate_expr**3).rolling_mean(w_long).over("asset")
        m4_168 = (rate_expr**4).rolling_mean(w_long).over("asset")
        var_168 = m2_168 - mu_168**2
        cm4_168 = m4_168 - 4 * mu_168 * m3_168 + 6 * (mu_168**2) * m2_168 - 3 * (mu_168**4)
        kurt_168 = cm4_168 / (var_168**2 + 1e-12)

        w_short = 72
        mu_72 = rate_expr.rolling_mean(w_short).over("asset")
        m2_72 = (rate_expr**2).rolling_mean(w_short).over("asset")
        m3_72 = (rate_expr**3).rolling_mean(w_short).over("asset")
        m4_72 = (rate_expr**4).rolling_mean(w_short).over("asset")
        var_72 = m2_72 - mu_72**2
        cm4_72 = m4_72 - 4 * mu_72 * m3_72 + 6 * (mu_72**2) * m2_72 - 3 * (mu_72**4)
        kurt_72 = cm4_72 / (var_72**2 + 1e-12)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                kurt_168=kurt_168.shift(delay).over("asset"),
                kurt_72=kurt_72.shift(delay).over("asset"),
                funding_std=(var_168.sqrt()).shift(delay).over("asset"),
            )
            .with_columns(
                feat_low_kurt_168=(-pl.col("kurt_168") - (-pl.col("kurt_168")).mean().over("timestamp"))
                / (pl.col("kurt_168").std().over("timestamp") + 1e-8),
                feat_low_kurt_72=(-pl.col("kurt_72") - (-pl.col("kurt_72")).mean().over("timestamp"))
                / (pl.col("kurt_72").std().over("timestamp") + 1e-8),
                feat_funding_std=(pl.col("funding_std") - pl.col("funding_std").mean().over("timestamp"))
                / (pl.col("funding_std").std().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_low_kurt_168', 'feat_low_kurt_72', 'feat_funding_std'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSFundingKurtosisTailPipeline()
