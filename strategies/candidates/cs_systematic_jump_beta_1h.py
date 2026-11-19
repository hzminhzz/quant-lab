"""Cross-sectional systematic jump beta factor model with purged walk-forward CV (HYP-2026-053)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSSystematicJumpBetaPipeline:
    """Causal cross-sectional feature pipeline for systematic discontinuous jump beta (Todorov & Bollerslev 2010)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        df = df.sort("timestamp", "asset").with_columns(
            ret_1h=(pl.col("close") / pl.col("close").shift(1)).log().over("asset").fill_null(0.0)
        )

        btc = df.filter(pl.col("asset").is_in(["BTCUSDT", "BTCUSDT_PERP"])).select([
            pl.col("timestamp"),
            pl.col("ret_1h").alias("ret_m"),
        ])
        if btc.height > 0:
            df = df.join(btc, on="timestamp", how="left").sort("timestamp", "asset")
        else:
            mkt = df.group_by("timestamp").agg(pl.col("ret_1h").mean().alias("ret_m"))
            df = df.join(mkt, on="timestamp", how="left").sort("timestamp", "asset")

        df = df.with_columns(
            r_i=pl.col("ret_1h").shift(delay).over("asset"),
            r_m=pl.col("ret_m").shift(delay).over("asset"),
        )

        sigma_m_720 = pl.col("r_m").rolling_std(720, min_samples=24).over("asset")
        jump_thresh = 3.0 * sigma_m_720
        is_jump = (pl.col("r_m").abs() > jump_thresh).cast(pl.Float64)

        df = df.with_columns(is_jump=is_jump)

        cov_jump_720 = (pl.col("r_i") * pl.col("r_m") * pl.col("is_jump")).rolling_sum(720, min_samples=24).over("asset")
        var_jump_720 = (pl.col("r_m").pow(2) * pl.col("is_jump")).rolling_sum(720, min_samples=24).over("asset")
        beta_jump_720 = cov_jump_720 / (var_jump_720 + 1e-8)

        cov_jump_336 = (pl.col("r_i") * pl.col("r_m") * pl.col("is_jump")).rolling_sum(336, min_samples=24).over("asset")
        var_jump_336 = (pl.col("r_m").pow(2) * pl.col("is_jump")).rolling_sum(336, min_samples=24).over("asset")
        beta_jump_336 = cov_jump_336 / (var_jump_336 + 1e-8)

        cov_m_720 = (
            (pl.col("r_i") * pl.col("r_m")).rolling_mean(720, min_samples=24).over("asset")
            - pl.col("r_i").rolling_mean(720, min_samples=24).over("asset")
            * pl.col("r_m").rolling_mean(720, min_samples=24).over("asset")
        )
        var_m_720 = pl.col("r_m").rolling_var(720, min_samples=24).over("asset")
        beta_cont_720 = cov_m_720 / (var_m_720 + 1e-8)

        df = df.with_columns(
            beta_jump_720=beta_jump_720,
            beta_jump_336=beta_jump_336,
            beta_cont_720=beta_cont_720,
        )

        return df.with_columns(
            feat_jump_beta_720=(pl.col("beta_jump_720") - pl.col("beta_jump_720").mean().over("timestamp"))
            / (pl.col("beta_jump_720").std().over("timestamp") + 1e-8),
            feat_jump_beta_336=(pl.col("beta_jump_336") - pl.col("beta_jump_336").mean().over("timestamp"))
            / (pl.col("beta_jump_336").std().over("timestamp") + 1e-8),
            feat_cont_beta_720=(pl.col("beta_cont_720") - pl.col("beta_cont_720").mean().over("timestamp"))
            / (pl.col("beta_cont_720").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_jump_beta_720', 'feat_jump_beta_336', 'feat_cont_beta_720'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=('BTCUSDT', 'BTCUSDT_PERP'), btc_eligible=False, **kwargs)


pipeline = CSSystematicJumpBetaPipeline()
