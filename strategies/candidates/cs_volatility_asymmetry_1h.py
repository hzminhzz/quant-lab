"""Cross-sectional volatility asymmetry factor model with purged walk-forward CV (HYP-2026-057)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSVolatilityAsymmetryPipeline:
    """Causal cross-sectional feature pipeline for downside/upside semivariance asymmetry (Patton & Sheppard 2015)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        df = df.with_columns(
            r_1h=(pl.col("close") / pl.col("close").shift(1)).log().over("asset")
        )

        r_minus = pl.when(pl.col("r_1h") < 0.0).then(pl.col("r_1h")).otherwise(0.0)
        r_plus = pl.when(pl.col("r_1h") > 0.0).then(pl.col("r_1h")).otherwise(0.0)

        df = df.with_columns(r_minus=r_minus, r_plus=r_plus)

        rs_minus_168 = pl.col("r_minus").pow(2).rolling_sum(168, min_samples=24).over("asset")
        rs_plus_168 = pl.col("r_plus").pow(2).rolling_sum(168, min_samples=24).over("asset")
        rs_ratio_168 = rs_minus_168 / (rs_plus_168 + 1e-8)
        rs_diff_168 = (rs_minus_168 - rs_plus_168) / (rs_minus_168 + rs_plus_168 + 1e-8)

        rs_minus_72 = pl.col("r_minus").pow(2).rolling_sum(72, min_samples=12).over("asset")
        rs_plus_72 = pl.col("r_plus").pow(2).rolling_sum(72, min_samples=12).over("asset")
        rs_ratio_72 = rs_minus_72 / (rs_plus_72 + 1e-8)

        df = df.with_columns(
            rs_ratio_168=rs_ratio_168.shift(delay).over("asset"),
            rs_diff_168=rs_diff_168.shift(delay).over("asset"),
            rs_ratio_72=rs_ratio_72.shift(delay).over("asset"),
        )

        return df.with_columns(
            feat_rs_ratio_168=(pl.col("rs_ratio_168") - pl.col("rs_ratio_168").mean().over("timestamp"))
            / (pl.col("rs_ratio_168").std().over("timestamp") + 1e-8),
            feat_rs_diff_168=(pl.col("rs_diff_168") - pl.col("rs_diff_168").mean().over("timestamp"))
            / (pl.col("rs_diff_168").std().over("timestamp") + 1e-8),
            feat_rs_ratio_72=(pl.col("rs_ratio_72") - pl.col("rs_ratio_72").mean().over("timestamp"))
            / (pl.col("rs_ratio_72").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_rs_ratio_168', 'feat_rs_diff_168', 'feat_rs_ratio_72'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=('BTCUSDT', 'BTCUSDT_PERP'), btc_eligible=False, **kwargs)


pipeline = CSVolatilityAsymmetryPipeline()
