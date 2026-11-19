"""Cross-sectional systematic coskewness risk premium factor model with purged walk-forward CV (HYP-2026-052)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSSystematicCoskewnessPipeline:
    """Causal cross-sectional feature pipeline for third-order systematic coskewness premium."""

    def _calc_coskewness(self, r_i: pl.Expr, r_m: pl.Expr, window: int) -> pl.Expr:
        # Exact window-wise central co-moment: mean((r_i - mean(r_i)) * (r_m - mean(r_m))^2)
        mu_i = r_i.rolling_mean(window).over("asset")
        mu_m = r_m.rolling_mean(window).over("asset")

        mean_rm2 = r_m.pow(2).rolling_mean(window).over("asset")
        mean_ri_rm = (r_i * r_m).rolling_mean(window).over("asset")
        mean_ri_rm2 = (r_i * r_m.pow(2)).rolling_mean(window).over("asset")

        # Expansion of E[(r_i - mu_i)(r_m - mu_m)^2]:
        # E[r_i*r_m^2] - 2*mu_m*E[r_i*r_m] - mu_i*E[r_m^2] + 2*mu_i*mu_m^2
        num = (
            mean_ri_rm2
            - 2.0 * mu_m * mean_ri_rm
            - mu_i * mean_rm2
            + 2.0 * mu_i * mu_m.pow(2)
        )

        var_i = (r_i.pow(2).rolling_mean(window).over("asset") - mu_i.pow(2)).clip(lower_bound=0.0)
        std_i = var_i.sqrt()

        var_m = (mean_rm2 - mu_m.pow(2)).clip(lower_bound=0.0)

        den = std_i * var_m + 1e-12
        return num / den

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

        coskew_720 = self._calc_coskewness(r_i, r_m, 720)
        neg_coskew_720 = (-coskew_720).shift(delay).over("asset")

        coskew_168 = self._calc_coskewness(r_i, r_m, 168)
        neg_coskew_168 = (-coskew_168).shift(delay).over("asset")

        return (
            df.with_columns(
                neg_coskew_720=neg_coskew_720,
                neg_coskew_168=neg_coskew_168,
            )
            .with_columns(
                feat_neg_coskew_720=(pl.col("neg_coskew_720") - pl.col("neg_coskew_720").mean().over("timestamp"))
                / (pl.col("neg_coskew_720").std().over("timestamp") + 1e-8),
                feat_neg_coskew_168=(pl.col("neg_coskew_168") - pl.col("neg_coskew_168").mean().over("timestamp"))
                / (pl.col("neg_coskew_168").std().over("timestamp") + 1e-8),
                feat_neg_coskew_rank=pl.col("neg_coskew_720").rank().over("timestamp")
                / (pl.col("neg_coskew_720").count().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_neg_coskew_720', 'feat_neg_coskew_168', 'feat_neg_coskew_rank'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=None, btc_eligible=True, **kwargs)


pipeline = CSSystematicCoskewnessPipeline()
