"""Cross-sectional funding rate velocity dislocation factor model with purged walk-forward CV (HYP-2026-022)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSFundingVelocityDislocationPipeline:
    """Causal cross-sectional feature pipeline for funding rate velocity and price momentum dislocation."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        if "funding_rate" in df.columns:
            rate_expr = pl.col("funding_rate")
        else:
            rate_expr = (pl.col("close") - pl.col("open")) / (pl.col("open") + 1e-12)

        df = df.sort("timestamp", "asset").with_columns(
            vel_24=(rate_expr - rate_expr.shift(24).over("asset")).shift(delay).over("asset"),
            vel_72=(rate_expr - rate_expr.shift(72).over("asset")).shift(delay).over("asset"),
            mom_24=pl.col("close").pct_change(24).shift(delay).over("asset"),
        )

        # Thesis exact specification: momentum filter (price_return_24h <= median_return)
        # Tokens with surging positive velocity whose price momentum has stalled experience severe drag
        stalled = (pl.col("mom_24") <= pl.col("mom_24").median().over("timestamp")).cast(pl.Float64)
        disloc = -pl.col("vel_24") * (stalled * 2.0 - 1.0)

        return (
            df.with_columns(
                stalled_mom=stalled,
                disloc_raw=disloc,
            )
            .with_columns(
                feat_neg_velocity_24h=(-pl.col("vel_24") - (-pl.col("vel_24")).mean().over("timestamp"))
                / (pl.col("vel_24").std().over("timestamp") + 1e-8),
                feat_dislocation=(pl.col("disloc_raw") - pl.col("disloc_raw").mean().over("timestamp"))
                / (pl.col("disloc_raw").std().over("timestamp") + 1e-8),
                feat_mom_24h=(pl.col("mom_24") - pl.col("mom_24").mean().over("timestamp"))
                / (pl.col("mom_24").std().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 48, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral velocity dislocation positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_neg_velocity_24h', 'feat_dislocation', 'feat_mom_24h'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSFundingVelocityDislocationPipeline()
