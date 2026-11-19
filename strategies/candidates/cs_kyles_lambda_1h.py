"""Cross-sectional Kyle's Lambda order flow price impact factor model (HYP-2026-035)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSKylesLambdaPipeline:
    """Causal cross-sectional feature pipeline for Kyle's Lambda order flow price impact (Kyle 1985, Hasbrouck 1991)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        # Net taker order flow: use taker_buy_volume if available, else Lee-Ready/bar-sign proxy
        if "taker_buy_volume" in df.columns:
            ofi = 2.0 * pl.col("taker_buy_volume") - pl.col("volume")
        else:
            bar_dir = (
                pl.when(pl.col("close") > pl.col("open"))
                .then(1.0)
                .when(pl.col("close") < pl.col("open"))
                .then(-1.0)
                .otherwise(0.0)
            )
            ofi = bar_dir * pl.col("volume")

        ret = pl.col("close").pct_change().over("asset").fill_null(0.0)
        df = df.with_columns(ofi=ofi, ret_1h=ret)

        prod_ofi = pl.col("ret_1h") * pl.col("ofi")

        # 168h (trailing 7 days) Kyle's Lambda: Cov(r, ofi) / Var(ofi)
        cov_168 = (
            prod_ofi.rolling_mean(168).over("asset")
            - pl.col("ret_1h").rolling_mean(168).over("asset") * pl.col("ofi").rolling_mean(168).over("asset")
        )
        var_168 = pl.col("ofi").pow(2).rolling_mean(168).over("asset") - pl.col("ofi").rolling_mean(168).over("asset").pow(2)
        lambda_168 = cov_168 / (var_168 + 1e-12)

        ofi_mean_168 = pl.col("ofi").rolling_mean(168).over("asset")

        return (
            df.with_columns(
                lambda_168=lambda_168.shift(delay).over("asset"),
                ofi_168=ofi_mean_168.shift(delay).over("asset"),
            )
            .with_columns(
                feat_kyles_lambda_168=(pl.col("lambda_168") - pl.col("lambda_168").mean().over("timestamp"))
                / (pl.col("lambda_168").std().over("timestamp") + 1e-8),
                feat_lambda_rank=pl.col("lambda_168").rank().over("timestamp")
                / (pl.col("lambda_168").count().over("timestamp") + 1e-8),
                feat_ofi_168=(pl.col("ofi_168") - pl.col("ofi_168").mean().over("timestamp"))
                / (pl.col("ofi_168").std().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_kyles_lambda_168', 'feat_lambda_rank', 'feat_ofi_168'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSKylesLambdaPipeline()
