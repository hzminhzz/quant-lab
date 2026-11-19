"""Cross-sectional volatility-of-volatility risk premium factor model with purged walk-forward CV (HYP-2026-056)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSVolOfVolRiskPremiumPipeline:
    """Causal cross-sectional feature pipeline for Volatility-of-Volatility (Baltussen et al. 2018)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        df = df.with_columns(
            r_1h=(pl.col("close") / pl.col("close").shift(1)).log().over("asset")
        )

        rv_24 = pl.col("r_1h").pow(2).rolling_sum(24, min_samples=12).over("asset").sqrt()
        df = df.with_columns(rv_24=rv_24)

        vov_336 = pl.col("rv_24").rolling_std(336, min_samples=48).over("asset")
        mean_rv_336 = pl.col("rv_24").rolling_mean(336, min_samples=48).over("asset")
        cv_vov_336 = vov_336 / (mean_rv_336 + 1e-8)

        vov_168 = pl.col("rv_24").rolling_std(168, min_samples=24).over("asset")

        df = df.with_columns(
            vov_336=vov_336.shift(delay).over("asset"),
            cv_vov_336=cv_vov_336.shift(delay).over("asset"),
            vov_168=vov_168.shift(delay).over("asset"),
        )

        return df.with_columns(
            feat_vov_336=(pl.col("vov_336") - pl.col("vov_336").mean().over("timestamp"))
            / (pl.col("vov_336").std().over("timestamp") + 1e-8),
            feat_cv_vov_336=(pl.col("cv_vov_336") - pl.col("cv_vov_336").mean().over("timestamp"))
            / (pl.col("cv_vov_336").std().over("timestamp") + 1e-8),
            feat_vov_168=(pl.col("vov_168") - pl.col("vov_168").mean().over("timestamp"))
            / (pl.col("vov_168").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_vov_336', 'feat_cv_vov_336', 'feat_vov_168'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=('BTCUSDT', 'BTCUSDT_PERP'), btc_eligible=False, **kwargs)


pipeline = CSVolOfVolRiskPremiumPipeline()
