"""Cross-sectional VWAP tracking error factor model with purged walk-forward CV (HYP-2026-063)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSVwapTrackingErrorPipeline:
    """Causal cross-sectional feature pipeline for VWAP tracking error (RMS tracking deviation)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        dvol = pl.col("volume") * pl.col("close")
        vwap_24 = (
            dvol.rolling_sum(24, min_samples=6).over("asset")
            / (pl.col("volume").rolling_sum(24, min_samples=6).over("asset") + 1e-8)
        )
        te_sq = ((pl.col("close") - vwap_24) / (vwap_24 + 1e-8)).pow(2)

        df = df.with_columns(te_sq=te_sq)

        vte_168 = pl.col("te_sq").rolling_mean(168, min_samples=24).sqrt().shift(delay).over("asset")
        vte_72 = pl.col("te_sq").rolling_mean(72, min_samples=12).sqrt().shift(delay).over("asset")
        vte_24 = pl.col("te_sq").rolling_mean(24, min_samples=6).sqrt().shift(delay).over("asset")

        df = df.with_columns(
            vte_168=vte_168,
            vte_72=vte_72,
            vte_24=vte_24,
        )

        return df.with_columns(
            feat_vte_168=(pl.col("vte_168") - pl.col("vte_168").mean().over("timestamp"))
            / (pl.col("vte_168").std().over("timestamp") + 1e-8),
            feat_vte_72=(pl.col("vte_72") - pl.col("vte_72").mean().over("timestamp"))
            / (pl.col("vte_72").std().over("timestamp") + 1e-8),
            feat_vte_24=(pl.col("vte_24") - pl.col("vte_24").mean().over("timestamp"))
            / (pl.col("vte_24").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, invert_factor: bool = False, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_vte_168', 'feat_vte_72', 'feat_vte_24'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, invert_factor=invert_factor, ic_guard=True, predict_rowwise=False, invert_aliases=False, fill_missing_assets=True, min_train=10, **kwargs)


pipeline = CSVwapTrackingErrorPipeline()
