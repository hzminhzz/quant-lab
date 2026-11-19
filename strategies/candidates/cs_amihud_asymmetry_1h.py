"""Cross-sectional Amihud illiquidity asymmetry factor model with purged walk-forward CV (HYP-2026-024)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSAmihudAsymmetryPipeline:
    """Causal cross-sectional feature pipeline for downside vs upside Amihud illiquidity asymmetry."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        dvol = pl.col("volume") * pl.col("close")
        ret = pl.col("close").pct_change().over("asset")

        down_impact = (
            pl.when(ret < 0.0)
            .then(ret.abs() / (dvol + 1e-5))
            .otherwise(0.0)
        )
        up_impact = (
            pl.when(ret > 0.0)
            .then(ret.abs() / (dvol + 1e-5))
            .otherwise(0.0)
        )

        df = df.with_columns(
            down_imp=down_impact,
            up_imp=up_impact,
        )

        down_168 = pl.col("down_imp").rolling_mean(168, min_samples=1).shift(delay).over("asset")
        up_168 = pl.col("up_imp").rolling_mean(168, min_samples=1).shift(delay).over("asset")
        down_72 = pl.col("down_imp").rolling_mean(72, min_samples=1).shift(delay).over("asset")
        up_72 = pl.col("up_imp").rolling_mean(72, min_samples=1).shift(delay).over("asset")

        df = df.with_columns(
            asym_168=(down_168 - up_168) / (down_168 + up_168 + 1e-8),
            asym_72=(down_72 - up_72) / (down_72 + up_72 + 1e-8),
            total_ami_168=(down_168 + up_168),
        )

        return df.with_columns(
            feat_asym_168=(pl.col("asym_168") - pl.col("asym_168").mean().over("timestamp"))
            / (pl.col("asym_168").std().over("timestamp") + 1e-8),
            feat_asym_72=(pl.col("asym_72") - pl.col("asym_72").mean().over("timestamp"))
            / (pl.col("asym_72").std().over("timestamp") + 1e-8),
            feat_total_ami=(pl.col("total_ami_168") - pl.col("total_ami_168").mean().over("timestamp"))
            / (pl.col("total_ami_168").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_asym_168', 'feat_asym_72', 'feat_total_ami'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSAmihudAsymmetryPipeline()
