"""Cross-sectional realized bipower jump variation factor model with purged walk-forward CV (HYP-2026-025)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSBipowerJumpVariationPipeline:
    """Causal cross-sectional feature pipeline for realized bipower jump variation ratio."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        log_ret = (pl.col("close") / pl.col("close").shift(1)).log().over("asset")
        sq_ret = log_ret.pow(2)
        bp_term = (log_ret.abs() * log_ret.shift(1).abs()).over("asset")

        df = df.with_columns(
            sq_ret=sq_ret,
            bp_term=bp_term,
        )

        rv_168 = pl.col("sq_ret").rolling_sum(168, min_samples=2).shift(delay).over("asset")
        bv_168 = (float(np.pi) / 2.0) * pl.col("bp_term").rolling_sum(167, min_samples=2).shift(delay).over("asset")
        jv_168 = pl.max_horizontal(rv_168 - bv_168, pl.lit(0.0))
        jr_168 = jv_168 / pl.max_horizontal(rv_168, pl.lit(1e-8))

        rv_72 = pl.col("sq_ret").rolling_sum(72, min_samples=2).shift(delay).over("asset")
        bv_72 = (float(np.pi) / 2.0) * pl.col("bp_term").rolling_sum(71, min_samples=2).shift(delay).over("asset")
        jv_72 = pl.max_horizontal(rv_72 - bv_72, pl.lit(0.0))
        jr_72 = jv_72 / pl.max_horizontal(rv_72, pl.lit(1e-8))

        df = df.with_columns(
            jr_168=jr_168,
            jr_72=jr_72,
            rvol_168=rv_168.sqrt(),
        )

        return df.with_columns(
            feat_jump_ratio_168=(pl.col("jr_168") - pl.col("jr_168").mean().over("timestamp"))
            / (pl.col("jr_168").std().over("timestamp") + 1e-8),
            feat_jump_ratio_72=(pl.col("jr_72") - pl.col("jr_72").mean().over("timestamp"))
            / (pl.col("jr_72").std().over("timestamp") + 1e-8),
            feat_realized_vol=(pl.col("rvol_168") - pl.col("rvol_168").mean().over("timestamp"))
            / (pl.col("rvol_168").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, invert_factor: bool = False, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_jump_ratio_168', 'feat_jump_ratio_72', 'feat_realized_vol'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, invert_factor=invert_factor, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSBipowerJumpVariationPipeline()
