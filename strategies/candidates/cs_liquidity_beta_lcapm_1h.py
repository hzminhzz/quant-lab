"""Cross-sectional liquidity beta and LCAPM premium factor model (HYP-2026-036)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSLiquidityBetaLCAPMPipeline:
    """Causal cross-sectional feature pipeline for Liquidity-Adjusted CAPM (Acharya & Pedersen 2005, Pastor & Stambaugh 2003)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        dvol = pl.col("volume") * pl.col("close")
        ret_abs = pl.col("close").pct_change().abs().over("asset")
        illiq_raw = ret_abs / (dvol + 1e-5)
        df = df.with_columns(illiq=illiq_raw)

        btc_sub = df.filter(pl.col("asset").str.contains("(?i)btc"))
        if btc_sub.height > 0:
            first_btc = btc_sub["asset"][0]
            mkt = df.filter(pl.col("asset") == first_btc).select([
                pl.col("timestamp"),
                pl.col("illiq").alias("illiq_mkt"),
            ])
            df = df.join(mkt, on="timestamp", how="left").sort("timestamp", "asset")
        else:
            mkt = df.group_by("timestamp").agg(pl.col("illiq").mean().alias("illiq_mkt"))
            df = df.join(mkt, on="timestamp", how="left").sort("timestamp", "asset")

        illiq_i = pl.col("illiq").fill_null(0.0)
        illiq_m = pl.col("illiq_mkt").fill_null(0.0)
        prod_illiq = illiq_i * illiq_m

        # 720h (trailing 30 days) liquidity beta: Cov(illiq_i, illiq_m) / Var(illiq_m)
        cov_720 = (
            prod_illiq.rolling_mean(720).over("asset")
            - illiq_i.rolling_mean(720).over("asset") * illiq_m.rolling_mean(720).over("asset")
        )
        var_720 = illiq_m.pow(2).rolling_mean(720).over("asset") - illiq_m.rolling_mean(720).over("asset").pow(2)
        beta_l_720 = cov_720 / (var_720 + 1e-12)

        illiq_mean_720 = illiq_i.rolling_mean(720).over("asset")

        return (
            df.with_columns(
                beta_l_720=beta_l_720.shift(delay).over("asset"),
                illiq_720=illiq_mean_720.shift(delay).over("asset"),
            )
            .with_columns(
                feat_liquidity_beta_720=(pl.col("beta_l_720") - pl.col("beta_l_720").mean().over("timestamp"))
                / (pl.col("beta_l_720").std().over("timestamp") + 1e-8),
                feat_liquidity_beta_rank=pl.col("beta_l_720").rank().over("timestamp")
                / (pl.col("beta_l_720").count().over("timestamp") + 1e-8),
                feat_illiq_720=(pl.col("illiq_720") - pl.col("illiq_720").mean().over("timestamp"))
                / (pl.col("illiq_720").std().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_liquidity_beta_720', 'feat_liquidity_beta_rank', 'feat_illiq_720'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSLiquidityBetaLCAPMPipeline()
