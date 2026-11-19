"""Cross-sectional Lo-MacKinlay variance ratio factor model with purged walk-forward CV (HYP-2026-055)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSVarianceRatioPipeline:
    """Causal cross-sectional feature pipeline for Lo-MacKinlay Variance Ratio (Lo & MacKinlay 1988)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        df = df.with_columns(
            r_1h=(pl.col("close") / pl.col("close").shift(1)).log().over("asset"),
            r_24h=(pl.col("close") / pl.col("close").shift(24)).log().over("asset"),
        )

        var_1h_336 = pl.col("r_1h").rolling_var(336, min_samples=48).over("asset")
        var_24h_336 = pl.col("r_24h").rolling_var(336, min_samples=48).over("asset")
        vr_24_336 = (var_24h_336 / (24.0 * var_1h_336 + 1e-8)).shift(delay).over("asset")

        var_1h_168 = pl.col("r_1h").rolling_var(168, min_samples=24).over("asset")
        var_24h_168 = pl.col("r_24h").rolling_var(168, min_samples=24).over("asset")
        vr_24_168 = (var_24h_168 / (24.0 * var_1h_168 + 1e-8)).shift(delay).over("asset")

        var_1h_720 = pl.col("r_1h").rolling_var(720, min_samples=48).over("asset")
        var_24h_720 = pl.col("r_24h").rolling_var(720, min_samples=48).over("asset")
        vr_24_720 = (var_24h_720 / (24.0 * var_1h_720 + 1e-8)).shift(delay).over("asset")

        df = df.with_columns(
            vr_24_336=vr_24_336,
            vr_24_168=vr_24_168,
            vr_24_720=vr_24_720,
        )

        return df.with_columns(
            feat_vr_24_336=(pl.col("vr_24_336") - pl.col("vr_24_336").mean().over("timestamp"))
            / (pl.col("vr_24_336").std().over("timestamp") + 1e-8),
            feat_vr_24_168=(pl.col("vr_24_168") - pl.col("vr_24_168").mean().over("timestamp"))
            / (pl.col("vr_24_168").std().over("timestamp") + 1e-8),
            feat_vr_24_720=(pl.col("vr_24_720") - pl.col("vr_24_720").mean().over("timestamp"))
            / (pl.col("vr_24_720").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_vr_24_336', 'feat_vr_24_720', 'feat_vr_24_168'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=('BTCUSDT', 'BTCUSDT_PERP'), btc_eligible=False, **kwargs)


pipeline = CSVarianceRatioPipeline()
