"""Cross-sectional extreme left-tail beta crash risk factor model with purged walk-forward CV (HYP-2026-029)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSExtremeTailBetaPipeline:
    """Causal cross-sectional feature pipeline for extreme tail beta (Kelly & Jiang 2014, Atilgan et al. 2020)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        btc = df.filter(pl.col("asset") == "BTCUSDT").select([
            pl.col("timestamp"),
            pl.col("close").pct_change().alias("ret_btc"),
        ])
        if btc.height == 0:
            btc = df.group_by("timestamp").agg(pl.col("close").pct_change().mean().alias("ret_btc"))

        btc = btc.with_columns(
            q05_btc=pl.col("ret_btc").rolling_quantile(0.05, window_size=336).fill_null(-0.02)
        ).with_columns(
            is_crash=(pl.col("ret_btc") <= pl.col("q05_btc")).cast(pl.Float64)
        )

        df = df.join(btc, on="timestamp", how="left").sort("timestamp", "asset")
        df = df.with_columns(ret_1h=pl.col("close").pct_change().over("asset"))

        r_i = pl.col("ret_1h")
        r_m = pl.col("ret_btc")
        m_crash = pl.col("is_crash")

        def calc_tail_beta(window: int):
            n_crash = m_crash.rolling_sum(window).over("asset").clip(lower_bound=5.0)

            cov_crash = (r_i * r_m * m_crash).rolling_sum(window).over("asset") / n_crash - (
                (r_i * m_crash).rolling_sum(window).over("asset") / n_crash
            ) * ((r_m * m_crash).rolling_sum(window).over("asset") / n_crash)

            var_m_crash = (
                (r_m.pow(2) * m_crash).rolling_sum(window).over("asset") / n_crash
                - ((r_m * m_crash).rolling_sum(window).over("asset") / n_crash).pow(2)
            ).clip(lower_bound=1e-12)

            return (cov_crash / (var_m_crash + 1e-8)).clip(-5.0, 5.0).shift(delay).over("asset")

        cov_mkt = (r_i * r_m).rolling_mean(168).over("asset") - (
            r_i.rolling_mean(168).over("asset") * r_m.rolling_mean(168).over("asset")
        )
        var_mkt = r_m.rolling_var(168).over("asset").clip(lower_bound=1e-12)
        beta_mkt = (cov_mkt / (var_mkt + 1e-8)).clip(-5.0, 5.0).shift(delay).over("asset")

        tail_336 = calc_tail_beta(336)
        tail_168 = calc_tail_beta(168)

        df = df.with_columns(
            tail_beta_336=tail_336,
            tail_beta_168=tail_168,
            beta_mkt=beta_mkt,
        )

        return df.with_columns(
            feat_tail_beta_336=(pl.col("tail_beta_336") - pl.col("tail_beta_336").mean().over("timestamp"))
            / (pl.col("tail_beta_336").std().over("timestamp") + 1e-8),
            feat_tail_beta_168=(pl.col("tail_beta_168") - pl.col("tail_beta_168").mean().over("timestamp"))
            / (pl.col("tail_beta_168").std().over("timestamp") + 1e-8),
            feat_market_beta=(pl.col("beta_mkt") - pl.col("beta_mkt").mean().over("timestamp"))
            / (pl.col("beta_mkt").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_tail_beta_336', 'feat_tail_beta_168', 'feat_market_beta'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol='BTCUSDT', **kwargs)


pipeline = CSExtremeTailBetaPipeline()
