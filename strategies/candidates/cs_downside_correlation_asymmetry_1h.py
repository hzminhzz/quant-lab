"""Cross-sectional downside correlation asymmetry factor model with purged walk-forward CV (HYP-2026-028)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSDownsideCorrelationAsymmetryPipeline:
    """Causal cross-sectional feature pipeline for downside correlation asymmetry (Ang & Chen 2002)."""

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
        m_down = (r_m < 0).cast(pl.Float64)
        m_up = (r_m > 0).cast(pl.Float64)

        def calc_components(window: int):
            n_down = m_down.rolling_sum(window).over("asset").clip(lower_bound=10.0)
            n_up = m_up.rolling_sum(window).over("asset").clip(lower_bound=10.0)

            cov_down = (r_i * r_m * m_down).rolling_sum(window).over("asset") / n_down - (
                (r_i * m_down).rolling_sum(window).over("asset") / n_down
            ) * ((r_m * m_down).rolling_sum(window).over("asset") / n_down)
            var_i_down = (
                (r_i.pow(2) * m_down).rolling_sum(window).over("asset") / n_down
                - ((r_i * m_down).rolling_sum(window).over("asset") / n_down).pow(2)
            ).clip(lower_bound=1e-12)
            var_m_down = (
                (r_m.pow(2) * m_down).rolling_sum(window).over("asset") / n_down
                - ((r_m * m_down).rolling_sum(window).over("asset") / n_down).pow(2)
            ).clip(lower_bound=1e-12)
            rho_down = cov_down / (var_i_down.sqrt() * var_m_down.sqrt() + 1e-8)

            cov_up = (r_i * r_m * m_up).rolling_sum(window).over("asset") / n_up - (
                (r_i * m_up).rolling_sum(window).over("asset") / n_up
            ) * ((r_m * m_up).rolling_sum(window).over("asset") / n_up)
            var_i_up = (
                (r_i.pow(2) * m_up).rolling_sum(window).over("asset") / n_up
                - ((r_i * m_up).rolling_sum(window).over("asset") / n_up).pow(2)
            ).clip(lower_bound=1e-12)
            var_m_up = (
                (r_m.pow(2) * m_up).rolling_sum(window).over("asset") / n_up
                - ((r_m * m_up).rolling_sum(window).over("asset") / n_up).pow(2)
            ).clip(lower_bound=1e-12)
            rho_up = cov_up / (var_i_up.sqrt() * var_m_up.sqrt() + 1e-8)

            asym = (rho_down - rho_up).clip(-2.0, 2.0).shift(delay).over("asset")
            return asym, rho_down.clip(-1.0, 1.0).shift(delay).over("asset")

        asym_168, rho_down_168 = calc_components(168)
        asym_336, _ = calc_components(336)

        df = df.with_columns(
            asym_168=asym_168,
            asym_336=asym_336,
            rho_down_168=rho_down_168,
        )

        return df.with_columns(
            feat_asym_168=(pl.col("asym_168") - pl.col("asym_168").mean().over("timestamp"))
            / (pl.col("asym_168").std().over("timestamp") + 1e-8),
            feat_asym_336=(pl.col("asym_336") - pl.col("asym_336").mean().over("timestamp"))
            / (pl.col("asym_336").std().over("timestamp") + 1e-8),
            feat_down_corr=(pl.col("rho_down_168") - pl.col("rho_down_168").mean().over("timestamp"))
            / (pl.col("rho_down_168").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_asym_168', 'feat_asym_336', 'feat_down_corr'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol='BTCUSDT', **kwargs)


pipeline = CSDownsideCorrelationAsymmetryPipeline()
