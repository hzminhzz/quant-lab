"""Cross-sectional range volatility efficiency factor model with purged walk-forward CV (HYP-2026-041)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSRangeEfficiencyPipeline:
    """Causal cross-sectional feature pipeline for Parkinson to close-to-close range efficiency ratio."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        hl_ratio = (pl.col("high") / pl.max_horizontal(pl.col("low"), pl.lit(1e-8))).log()
        sq_parkinson = (hl_ratio ** 2) / (4.0 * float(np.log(2.0)))

        ret_cc = (pl.col("close") / pl.max_horizontal(pl.col("close").shift(1), pl.lit(1e-8))).log().over("asset")
        sq_cc = ret_cc ** 2

        df = df.with_columns(
            sq_parkinson=sq_parkinson,
            sq_cc=sq_cc,
        )

        sigma_p_168 = pl.col("sq_parkinson").rolling_mean(168, min_samples=2).shift(delay).over("asset").sqrt()
        sigma_cc_168 = pl.col("sq_cc").rolling_mean(168, min_samples=2).shift(delay).over("asset").sqrt()
        rvr_168 = sigma_p_168 / pl.max_horizontal(sigma_cc_168, pl.lit(1e-8))

        sigma_p_72 = pl.col("sq_parkinson").rolling_mean(72, min_samples=2).shift(delay).over("asset").sqrt()
        sigma_cc_72 = pl.col("sq_cc").rolling_mean(72, min_samples=2).shift(delay).over("asset").sqrt()
        rvr_72 = sigma_p_72 / pl.max_horizontal(sigma_cc_72, pl.lit(1e-8))

        df = df.with_columns(
            eff_168=-rvr_168,
            eff_72=-rvr_72,
            sigma_cc_168=sigma_cc_168,
        )

        return df.with_columns(
            feat_eff_168=(pl.col("eff_168") - pl.col("eff_168").mean().over("timestamp"))
            / (pl.col("eff_168").std().over("timestamp") + 1e-8),
            feat_eff_72=(pl.col("eff_72") - pl.col("eff_72").mean().over("timestamp"))
            / (pl.col("eff_72").std().over("timestamp") + 1e-8),
            feat_vol_168=(pl.col("sigma_cc_168") - pl.col("sigma_cc_168").mean().over("timestamp"))
            / (pl.col("sigma_cc_168").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 4, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_eff_168', 'feat_eff_72', 'feat_vol_168'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSRangeEfficiencyPipeline()
