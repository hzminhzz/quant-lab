"""Cross-sectional taker order flow imbalance factor model with purged walk-forward CV (HYP-2026-054)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSTakerFlowImbalancePipeline:
    """Causal cross-sectional feature pipeline for taker order flow imbalance (Chordia et al. 2002, Cont et al. 2014)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        if "taker_buy_volume" in df.columns:
            ntv = 2.0 * pl.col("taker_buy_volume") - pl.col("volume")
        else:
            bar_dir = (
                pl.when(pl.col("close") > pl.col("open"))
                .then(1.0)
                .when(pl.col("close") < pl.col("open"))
                .then(-1.0)
                .otherwise(0.0)
            )
            ntv = bar_dir * pl.col("volume")

        df = df.with_columns(ntv=ntv)

        vol_168 = pl.col("volume").rolling_sum(168, min_samples=24).over("asset")
        ntr_168 = (pl.col("ntv").rolling_sum(168, min_samples=24).over("asset") / (vol_168 + 1e-8)).shift(delay).over("asset")

        vol_72 = pl.col("volume").rolling_sum(72, min_samples=12).over("asset")
        ntr_72 = (pl.col("ntv").rolling_sum(72, min_samples=12).over("asset") / (vol_72 + 1e-8)).shift(delay).over("asset")

        vol_24 = pl.col("volume").rolling_sum(24, min_samples=6).over("asset")
        ntr_24 = (pl.col("ntv").rolling_sum(24, min_samples=6).over("asset") / (vol_24 + 1e-8)).shift(delay).over("asset")

        df = df.with_columns(
            ntr_168=ntr_168,
            ntr_72=ntr_72,
            ntr_24=ntr_24,
        )

        return df.with_columns(
            feat_ntr_168=(pl.col("ntr_168") - pl.col("ntr_168").mean().over("timestamp"))
            / (pl.col("ntr_168").std().over("timestamp") + 1e-8),
            feat_ntr_72=(pl.col("ntr_72") - pl.col("ntr_72").mean().over("timestamp"))
            / (pl.col("ntr_72").std().over("timestamp") + 1e-8),
            feat_ntr_24=(pl.col("ntr_24") - pl.col("ntr_24").mean().over("timestamp"))
            / (pl.col("ntr_24").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_ntr_168', 'feat_ntr_72', 'feat_ntr_24'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=('BTCUSDT', 'BTCUSDT_PERP'), btc_eligible=False, **kwargs)


pipeline = CSTakerFlowImbalancePipeline()
