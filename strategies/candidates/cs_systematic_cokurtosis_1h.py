"""Cross-sectional systematic cokurtosis tail contagion factor model with purged walk-forward CV (HYP-2026-051)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSSystematicCokurtosisPipeline:
    """Causal cross-sectional feature pipeline for fourth-order systematic cokurtosis."""

    def _calc_cokurtosis(self, r_i: pl.Expr, r_m: pl.Expr, window: int) -> pl.Expr:
        # Exact window-wise central co-moment: mean((r_i - mean(r_i)) * (r_m - mean(r_m))^3)
        mu_i = r_i.rolling_mean(window).over("asset")
        mu_m = r_m.rolling_mean(window).over("asset")

        mean_rm2 = r_m.pow(2).rolling_mean(window).over("asset")
        mean_rm3 = r_m.pow(3).rolling_mean(window).over("asset")
        mean_ri_rm = (r_i * r_m).rolling_mean(window).over("asset")
        mean_ri_rm2 = (r_i * r_m.pow(2)).rolling_mean(window).over("asset")
        mean_ri_rm3 = (r_i * r_m.pow(3)).rolling_mean(window).over("asset")

        # Expansion of E[(r_i - mu_i)(r_m - mu_m)^3]:
        # E[r_i*r_m^3] - 3*mu_m*E[r_i*r_m^2] + 3*mu_m^2*E[r_i*r_m] - mu_i*E[r_m^3] + 3*mu_i*mu_m*E[r_m^2] - 3*mu_i*mu_m^3
        num = (
            mean_ri_rm3
            - 3.0 * mu_m * mean_ri_rm2
            + 3.0 * mu_m.pow(2) * mean_ri_rm
            - mu_i * mean_rm3
            + 3.0 * mu_i * mu_m * mean_rm2
            - 3.0 * mu_i * mu_m.pow(3)
        )

        var_i = (r_i.pow(2).rolling_mean(window).over("asset") - mu_i.pow(2)).clip(lower_bound=0.0)
        std_i = var_i.sqrt()

        var_m = (mean_rm2 - mu_m.pow(2)).clip(lower_bound=0.0)
        std_m = var_m.sqrt()

        den = std_i * std_m.pow(3) + 1e-12
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

        cokurt_720 = self._calc_cokurtosis(r_i, r_m, 720).shift(delay).over("asset")
        cokurt_168 = self._calc_cokurtosis(r_i, r_m, 168).shift(delay).over("asset")

        return (
            df.with_columns(
                cokurt_720=cokurt_720,
                cokurt_168=cokurt_168,
            )
            .with_columns(
                feat_cokurtosis_720=(pl.col("cokurt_720") - pl.col("cokurt_720").mean().over("timestamp"))
                / (pl.col("cokurt_720").std().over("timestamp") + 1e-8),
                feat_cokurtosis_168=(pl.col("cokurt_168") - pl.col("cokurt_168").mean().over("timestamp"))
                / (pl.col("cokurt_168").std().over("timestamp") + 1e-8),
                feat_cokurtosis_rank=pl.col("cokurt_720").rank().over("timestamp")
                / (pl.col("cokurt_720").count().over("timestamp") + 1e-8),
            )
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_cokurtosis_720', 'feat_cokurtosis_168', 'feat_cokurtosis_rank'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol=None, btc_eligible=True, **kwargs)


pipeline = CSSystematicCokurtosisPipeline()
