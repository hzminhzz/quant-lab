"""Cross-sectional return synchronicity & information content factor model with purged walk-forward CV (HYP-2026-047)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSReturnSynchronicityPipeline:
    """Causal cross-sectional feature pipeline for return synchronicity (1 - R^2)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})

        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        ret = pl.col("close").pct_change().over("asset").fill_null(0.0)
        df = df.with_columns(ret=ret)

        btc_sub = df.filter(pl.col("asset").str.contains("(?i)btc"))
        if btc_sub.height > 0:
            first_btc = btc_sub["asset"][0]
            mkt = df.filter(pl.col("asset") == first_btc).select([
                pl.col("timestamp"),
                pl.col("ret").alias("ret_mkt"),
            ])
            df = df.join(mkt, on="timestamp", how="left").sort("timestamp", "asset")
        else:
            mkt = df.group_by("timestamp").agg(pl.col("ret").mean().alias("ret_mkt"))
            df = df.join(mkt, on="timestamp", how="left").sort("timestamp", "asset")

        r_i = pl.col("ret").fill_null(0.0)
        r_m = pl.col("ret_mkt").fill_null(0.0)
        prod = r_i * r_m

        cov_168 = prod.rolling_mean(168).over("asset") - r_i.rolling_mean(168).over("asset") * r_m.rolling_mean(168).over("asset")
        var_i_168 = r_i.pow(2).rolling_mean(168).over("asset") - r_i.rolling_mean(168).over("asset").pow(2)
        var_m_168 = r_m.pow(2).rolling_mean(168).over("asset") - r_m.rolling_mean(168).over("asset").pow(2)
        r2_168 = (cov_168.pow(2) / (var_i_168 * var_m_168 + 1e-12)).clip(0.0, 1.0)

        cov_720 = prod.rolling_mean(720).over("asset") - r_i.rolling_mean(720).over("asset") * r_m.rolling_mean(720).over("asset")
        var_i_720 = r_i.pow(2).rolling_mean(720).over("asset") - r_i.rolling_mean(720).over("asset").pow(2)
        var_m_720 = r_m.pow(2).rolling_mean(720).over("asset") - r_m.rolling_mean(720).over("asset").pow(2)
        r2_720 = (cov_720.pow(2) / (var_i_720 * var_m_720 + 1e-12)).clip(0.0, 1.0)

        idio_168 = (1.0 - r2_168).shift(delay).over("asset")
        idio_720 = (1.0 - r2_720).shift(delay).over("asset")

        return (
            df.with_columns(
                idio_168=idio_168,
                idio_720=idio_720,
            )
            .with_columns(
                feat_idio_168=(pl.col("idio_168") - pl.col("idio_168").mean().over("timestamp"))
                / (pl.col("idio_168").std().over("timestamp") + 1e-8),
                feat_idio_720=(pl.col("idio_720") - pl.col("idio_720").mean().over("timestamp"))
                / (pl.col("idio_720").std().over("timestamp") + 1e-8),
                feat_idio_rank=pl.col("idio_168").rank().over("timestamp")
                / (pl.col("idio_168").count().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_idio_168', 'feat_idio_720', 'feat_idio_rank'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=None, btc_eligible=True, **kwargs)


pipeline = CSReturnSynchronicityPipeline()
