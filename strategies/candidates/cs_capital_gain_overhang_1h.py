"""Cross-sectional capital gain overhang factor model with purged walk-forward CV (HYP-2026-026)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSCapitalGainOverhangPipeline:
    """Causal cross-sectional feature pipeline for capital gain overhang and disposition effect."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        dvol = pl.col("volume") * pl.col("close")
        close_del = pl.col("close").shift(delay).over("asset")

        df = df.with_columns(dvol=dvol)

        pv_720 = pl.col("dvol").rolling_sum(720, min_samples=1).shift(delay).over("asset")
        v_720 = pl.col("volume").rolling_sum(720, min_samples=1).shift(delay).over("asset")
        rp_720 = pv_720 / (v_720 + 1e-8)
        cgo_720 = (close_del - rp_720) / (close_del + 1e-8)

        pv_168 = pl.col("dvol").rolling_sum(168, min_samples=1).shift(delay).over("asset")
        v_168 = pl.col("volume").rolling_sum(168, min_samples=1).shift(delay).over("asset")
        rp_168 = pv_168 / (v_168 + 1e-8)
        cgo_168 = (close_del - rp_168) / (close_del + 1e-8)

        pv_24 = pl.col("dvol").rolling_sum(24, min_samples=1).shift(delay).over("asset")
        v_24 = pl.col("volume").rolling_sum(24, min_samples=1).shift(delay).over("asset")
        rp_24 = pv_24 / (v_24 + 1e-8)
        cgo_24 = (close_del - rp_24) / (close_del + 1e-8)

        df = df.with_columns(
            cgo_720=cgo_720,
            cgo_168=cgo_168,
            cgo_24=cgo_24,
        )

        return df.with_columns(
            feat_cgo_720=(pl.col("cgo_720") - pl.col("cgo_720").mean().over("timestamp"))
            / (pl.col("cgo_720").std().over("timestamp") + 1e-8),
            feat_cgo_168=(pl.col("cgo_168") - pl.col("cgo_168").mean().over("timestamp"))
            / (pl.col("cgo_168").std().over("timestamp") + 1e-8),
            feat_cgo_24=(pl.col("cgo_24") - pl.col("cgo_24").mean().over("timestamp"))
            / (pl.col("cgo_24").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_cgo_720', 'feat_cgo_168', 'feat_cgo_24'], warmup=72,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSCapitalGainOverhangPipeline()
