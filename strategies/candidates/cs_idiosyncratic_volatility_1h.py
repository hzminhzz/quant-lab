"""Cross-sectional idiosyncratic volatility discount factor model with purged walk-forward CV (HYP-2026-031)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSIdiosyncraticVolatilityPipeline:
    """Causal cross-sectional feature pipeline for idiosyncratic volatility discount (Ang et al. 2006, Stambaugh et al. 2015)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        btc = df.filter(pl.col("asset") == "BTCUSDT").select([
            pl.col("timestamp"),
            pl.col("close").pct_change().alias("ret_btc"),
        ])
        if btc.height == 0:
            btc = df.group_by("timestamp").agg(pl.col("close").pct_change().mean().alias("ret_btc"))

        df = df.join(btc, on="timestamp", how="left").sort("timestamp", "asset")
        df = df.with_columns(ret_1h=pl.col("close").pct_change().over("asset"))

        r_i = pl.col("ret_1h")
        r_m = pl.col("ret_btc")

        def calc_low_ivol(window: int):
            cov = (r_i * r_m).rolling_mean(window).over("asset") - (
                r_i.rolling_mean(window).over("asset") * r_m.rolling_mean(window).over("asset")
            )
            var_m = (
                (r_m.pow(2)).rolling_mean(window).over("asset")
                - (r_m.rolling_mean(window).over("asset")).pow(2)
            ).clip(lower_bound=1e-12)
            var_i = (
                (r_i.pow(2)).rolling_mean(window).over("asset")
                - (r_i.rolling_mean(window).over("asset")).pow(2)
            ).clip(lower_bound=1e-12)

            var_res = (var_i - cov.pow(2) / (var_m + 1e-12)).clip(lower_bound=1e-12)
            ivol = var_res.sqrt()
            # Inverted IVOL: long low-IVOL (underpriced), short high-IVOL (retail lottery overpriced)
            return (-ivol).shift(delay).over("asset")

        low_ivol_168 = calc_low_ivol(168)
        low_ivol_336 = calc_low_ivol(336)
        low_tot_vol = (-r_i.rolling_std(72).over("asset")).shift(delay).over("asset")

        df = df.with_columns(
            low_ivol_168=low_ivol_168,
            low_ivol_336=low_ivol_336,
            low_tot_vol=low_tot_vol,
        )

        return df.with_columns(
            feat_low_ivol_168=(pl.col("low_ivol_168") - pl.col("low_ivol_168").mean().over("timestamp"))
            / (pl.col("low_ivol_168").std().over("timestamp") + 1e-8),
            feat_low_ivol_336=(pl.col("low_ivol_336") - pl.col("low_ivol_336").mean().over("timestamp"))
            / (pl.col("low_ivol_336").std().over("timestamp") + 1e-8),
            feat_low_total_vol=(pl.col("low_tot_vol") - pl.col("low_tot_vol").mean().over("timestamp"))
            / (pl.col("low_tot_vol").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_low_ivol_168', 'feat_low_ivol_336', 'feat_low_total_vol'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol='BTCUSDT', **kwargs)


pipeline = CSIdiosyncraticVolatilityPipeline()
