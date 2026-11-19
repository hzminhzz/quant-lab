"""Cross-sectional Betting Against Beta factor model with purged walk-forward CV (HYP-2026-005)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSBABPipeline:
    """Causal cross-sectional feature pipeline for Betting Against Beta / Low Volatility."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        btc = df.filter(pl.col("asset") == "BTCUSDT").select([
            pl.col("timestamp"),
            pl.col("close").pct_change().alias("ret_btc"),
        ])
        df = df.join(btc, on="timestamp", how="left").sort("timestamp", "asset")
        df = df.with_columns(
            ret_1h=pl.col("close").pct_change().over("asset")
        )

        cov_prod = (pl.col("ret_1h") * pl.col("ret_btc")).rolling_mean(168).over("asset")
        mean_prod = pl.col("ret_1h").rolling_mean(168).over("asset") * pl.col("ret_btc").rolling_mean(168).over("asset")
        cov_168 = cov_prod - mean_prod
        var_btc = pl.col("ret_btc").rolling_var(168).over("asset")
        beta_168 = (cov_168 / (var_btc + 1e-8)).shift(delay).over("asset")

        return (
            df.with_columns(
                beta_168=beta_168,
                rv_72=pl.col("ret_1h").rolling_std(72).shift(delay).over("asset"),
                rev_72=(-pl.col("close").pct_change(72)).shift(delay).over("asset"),
            )
            .with_columns(
                feat_low_beta=(-pl.col("beta_168") - (-pl.col("beta_168")).mean().over("timestamp"))
                / (pl.col("beta_168").std().over("timestamp") + 1e-8),
                feat_low_vol=(-pl.col("rv_72") - (-pl.col("rv_72")).mean().over("timestamp"))
                / (pl.col("rv_72").std().over("timestamp") + 1e-8),
                feat_rev=(pl.col("rev_72") - pl.col("rev_72").mean().over("timestamp"))
                / (pl.col("rev_72").std().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 4, top_k: int = 3, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across 4 purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_low_beta', 'feat_low_vol', 'feat_rev'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=False, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSBABPipeline()
