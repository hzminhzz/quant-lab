"""Cross-sectional funding carry and crowding exhaustion factor model with purged walk-forward CV (HYP-2026-017)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSFundingCarryExhaustionPipeline:
    """Causal cross-sectional feature pipeline for funding carry and crowding exhaustion."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        f24 = pl.col("funding_rate").rolling_mean(24).shift(delay).over("asset")
        f72 = pl.col("funding_rate").rolling_mean(72).shift(delay).over("asset")
        ret24 = pl.col("close").pct_change(24).shift(delay).over("asset")
        df = df.with_columns(
            f24=f24,
            f72=f72,
            ret24=ret24,
        )

        med_ret24 = pl.col("ret24").median().over("timestamp")

        cond_score = (
            pl.when((pl.col("f24") > 0) & (pl.col("ret24") <= med_ret24))
            .then(-pl.col("f24") * 2.0)
            .when((pl.col("f24") < 0) & (pl.col("ret24") >= med_ret24))
            .then(-pl.col("f24") * 2.0)
            .otherwise(-pl.col("f24"))
        )

        df = df.with_columns(cond_score=cond_score)

        return df.with_columns(
            feat_carry=(-pl.col("f24") - (-pl.col("f24")).mean().over("timestamp"))
            / (pl.col("f24").std().over("timestamp") + 1e-8),
            feat_exhaustion=(pl.col("cond_score") - pl.col("cond_score").mean().over("timestamp"))
            / (pl.col("cond_score").std().over("timestamp") + 1e-8),
            feat_carry_slow=(-pl.col("f72") - (-pl.col("f72")).mean().over("timestamp"))
            / (pl.col("f72").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 48, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_carry', 'feat_exhaustion', 'feat_carry_slow'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSFundingCarryExhaustionPipeline()
