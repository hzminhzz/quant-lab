"""Cross-sectional realized quarticity and volatility estimation uncertainty factor model with purged walk-forward CV (HYP-2026-043)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSRealizedQuarticityPipeline:
    """Causal cross-sectional feature pipeline for realized quarticity (sampling variance of realized volatility)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        log_ret = (pl.col("close") / pl.max_horizontal(pl.col("close").shift(1), pl.lit(1e-8))).log().over("asset")
        r_eff = log_ret.shift(delay).over("asset")

        df = df.with_columns(
            r_sq=r_eff ** 2,
            r_quad=r_eff ** 4,
        )

        rq_168 = (168.0 / 3.0) * pl.col("r_quad").rolling_sum(168, min_samples=10).over("asset")
        rq_72 = (72.0 / 3.0) * pl.col("r_quad").rolling_sum(72, min_samples=10).over("asset")
        rv_168 = pl.col("r_sq").rolling_sum(168, min_samples=10).over("asset")

        df = df.with_columns(
            log_rq_168=(pl.max_horizontal(rq_168, pl.lit(1e-12))).log(),
            log_rq_72=(pl.max_horizontal(rq_72, pl.lit(1e-12))).log(),
            rvol_168=rv_168.sqrt(),
        )

        return df.with_columns(
            feat_log_rq_168=(pl.col("log_rq_168") - pl.col("log_rq_168").mean().over("timestamp"))
            / (pl.col("log_rq_168").std().over("timestamp") + 1e-8),
            feat_log_rq_72=(pl.col("log_rq_72") - pl.col("log_rq_72").mean().over("timestamp"))
            / (pl.col("log_rq_72").std().over("timestamp") + 1e-8),
            feat_vol_168=(pl.col("rvol_168") - pl.col("rvol_168").mean().over("timestamp"))
            / (pl.col("rvol_168").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 4, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_log_rq_168', 'feat_log_rq_72', 'feat_vol_168'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSRealizedQuarticityPipeline()
