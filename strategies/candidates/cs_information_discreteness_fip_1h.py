"""Cross-sectional information discreteness and Frog-in-the-Pan momentum factor model (HYP-2026-034)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSInformationDiscretenessFIPPipeline:
    """Causal cross-sectional feature pipeline for Information Discreteness (Da, Gurun & Warachka, 2014)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        ret = pl.col("close").pct_change().over("asset").fill_null(0.0)
        is_pos = (ret > 0).cast(pl.Float64)
        is_neg = (ret < 0).cast(pl.Float64)

        pct_pos_336 = is_pos.rolling_mean(336).over("asset")
        pct_neg_336 = is_neg.rolling_mean(336).over("asset")
        cum_ret_336 = pl.col("close").pct_change(336).over("asset")

        # FIP score: -ID_i = pct_pos - pct_neg, conditioned strictly on positive trailing return (masked to 0.0 for non-winners)
        fip_336 = pl.when(cum_ret_336 > 0.0).then(pct_pos_336 - pct_neg_336).otherwise(0.0)

        pct_pos_168 = is_pos.rolling_mean(168).over("asset")
        pct_neg_168 = is_neg.rolling_mean(168).over("asset")
        cum_ret_168 = pl.col("close").pct_change(168).over("asset")
        fip_168 = pl.when(cum_ret_168 > 0.0).then(pct_pos_168 - pct_neg_168).otherwise(0.0)

        return (
            df.with_columns(
                fip_336=fip_336.shift(delay).over("asset"),
                fip_168=fip_168.shift(delay).over("asset"),
                cum_ret_336=cum_ret_336.shift(delay).over("asset"),
            )
            .with_columns(
                feat_fip_336=(pl.col("fip_336") - pl.col("fip_336").mean().over("timestamp"))
                / (pl.col("fip_336").std().over("timestamp") + 1e-8),
                feat_fip_168=(pl.col("fip_168") - pl.col("fip_168").mean().over("timestamp"))
                / (pl.col("fip_168").std().over("timestamp") + 1e-8),
                feat_cum_ret_336=(pl.col("cum_ret_336") - pl.col("cum_ret_336").mean().over("timestamp"))
                / (pl.col("cum_ret_336").std().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_fip_336', 'feat_fip_168', 'feat_cum_ret_336'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSInformationDiscretenessFIPPipeline()
