"""Cross-sectional return-volume correlation factor model with purged walk-forward CV (HYP-2026-060)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSVolumeReturnCorrelationPipeline:
    """Causal cross-sectional feature pipeline for return-volume correlation."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        ret = (pl.col("close") / pl.col("close").shift(1)).log().over("asset")
        vol_chg = (pl.col("volume") / (pl.col("volume").shift(1) + 1e-8)).log().over("asset")

        df = df.with_columns(
            ret=ret,
            vol_chg=vol_chg,
            ret_x_vol=(ret * vol_chg),
        )

        for w in (336, 168, 72):
            cov_w = (
                pl.col("ret_x_vol").rolling_mean(w, min_samples=12).over("asset")
                - pl.col("ret").rolling_mean(w, min_samples=12).over("asset")
                * pl.col("vol_chg").rolling_mean(w, min_samples=12).over("asset")
            )
            std_r = pl.col("ret").rolling_std(w, min_samples=12).over("asset")
            std_v = pl.col("vol_chg").rolling_std(w, min_samples=12).over("asset")
            corr_w = (cov_w / (std_r * std_v + 1e-8)).shift(delay).over("asset")
            df = df.with_columns(corr_w.alias(f"vrc_{w}"))

        return df.with_columns(
            feat_vrc_336=(pl.col("vrc_336") - pl.col("vrc_336").mean().over("timestamp"))
            / (pl.col("vrc_336").std().over("timestamp") + 1e-8),
            feat_vrc_168=(pl.col("vrc_168") - pl.col("vrc_168").mean().over("timestamp"))
            / (pl.col("vrc_168").std().over("timestamp") + 1e-8),
            feat_vrc_72=(pl.col("vrc_72") - pl.col("vrc_72").mean().over("timestamp"))
            / (pl.col("vrc_72").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_vrc_336', 'feat_vrc_168', 'feat_vrc_72'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=True, min_train=10, **kwargs)


pipeline = CSVolumeReturnCorrelationPipeline()
