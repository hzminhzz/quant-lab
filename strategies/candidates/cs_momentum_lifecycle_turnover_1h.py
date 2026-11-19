"""Cross-sectional momentum life cycle and volume turnover exhaustion factor model with purged walk-forward CV (HYP-2026-038)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSMomentumLifecycleTurnoverPipeline:
    """Causal cross-sectional feature pipeline for momentum lifecycle and volume exhaustion."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        r_14d = (pl.col("close") / pl.col("close").shift(336).over("asset")).log().shift(delay).over("asset")
        r_7d = (pl.col("close") / pl.col("close").shift(168).over("asset")).log().shift(delay).over("asset")

        vol_7d = pl.col("volume").rolling_mean(168).shift(delay).over("asset")
        vol_30d = pl.col("volume").rolling_mean(720).shift(delay).over("asset")
        vr_7_30 = vol_7d / (vol_30d + 1e-8)

        vol_1d = pl.col("volume").rolling_mean(24).shift(delay).over("asset")
        vr_1_7 = vol_1d / (vol_7d + 1e-8)

        # Tokens with non-positive returns are penalized to rank below stealth winners.
        mlc_score = pl.when(r_14d > 0.0).then(-vr_7_30).otherwise(-10.0 - vr_7_30)
        mlc_fast = pl.when(r_7d > 0.0).then(-vr_1_7).otherwise(-10.0 - vr_1_7)
        vr_exhaustion = -vr_7_30

        df = df.with_columns(
            mlc_score=mlc_score,
            mlc_fast=mlc_fast,
            vr_exhaustion=vr_exhaustion,
        )

        return df.with_columns(
            feat_mlc=(pl.col("mlc_score") - pl.col("mlc_score").mean().over("timestamp"))
            / (pl.col("mlc_score").std().over("timestamp") + 1e-8),
            feat_mlc_fast=(pl.col("mlc_fast") - pl.col("mlc_fast").mean().over("timestamp"))
            / (pl.col("mlc_fast").std().over("timestamp") + 1e-8),
            feat_stealth_turnover=(pl.col("vr_exhaustion") - pl.col("vr_exhaustion").mean().over("timestamp"))
            / (pl.col("vr_exhaustion").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_mlc', 'feat_mlc_fast', 'feat_stealth_turnover'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSMomentumLifecycleTurnoverPipeline()
