"""Cross-sectional session drift divergence factor model with purged walk-forward CV (HYP-2026-048)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSSessionDriftDivergencePipeline:
    """Causal cross-sectional feature pipeline for institutional vs retail session drift divergence."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})

        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        ret = pl.col("close").pct_change().over("asset").fill_null(0.0)
        hour = pl.col("timestamp").dt.hour()

        # US institutional session: 13:00 - 21:00 UTC; off-hours / retail: remaining hours
        r_inst = pl.when((hour >= 13) & (hour < 21)).then(ret).otherwise(0.0)
        r_retail = pl.when((hour >= 13) & (hour < 21)).then(0.0).otherwise(ret)

        df = df.with_columns(
            r_inst=r_inst,
            r_retail=r_retail,
        )

        inst_168 = pl.col("r_inst").rolling_sum(168).over("asset")
        retail_168 = pl.col("r_retail").rolling_sum(168).over("asset")
        inst_72 = pl.col("r_inst").rolling_sum(72).over("asset")
        retail_72 = pl.col("r_retail").rolling_sum(72).over("asset")

        div_168 = (inst_168 - retail_168).shift(delay).over("asset")
        div_72 = (inst_72 - retail_72).shift(delay).over("asset")
        inst_drift_168 = inst_168.shift(delay).over("asset")

        return (
            df.with_columns(
                div_168=div_168,
                div_72=div_72,
                inst_drift_168=inst_drift_168,
            )
            .with_columns(
                feat_div_168=(pl.col("div_168") - pl.col("div_168").mean().over("timestamp"))
                / (pl.col("div_168").std().over("timestamp") + 1e-8),
                feat_div_72=(pl.col("div_72") - pl.col("div_72").mean().over("timestamp"))
                / (pl.col("div_72").std().over("timestamp") + 1e-8),
                feat_inst_168=(pl.col("inst_drift_168") - pl.col("inst_drift_168").mean().over("timestamp"))
                / (pl.col("inst_drift_168").std().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_div_168', 'feat_div_72', 'feat_inst_168'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=None, btc_eligible=True, **kwargs)


pipeline = CSSessionDriftDivergencePipeline()
