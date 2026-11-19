"""Cross-sectional relative downside beta factor model with purged walk-forward CV (HYP-2026-046)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSRelativeDownsideBetaPipeline:
    """Causal cross-sectional feature pipeline for relative downside beta (beta^- - beta)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        log_ret = (pl.col("close") / pl.max_horizontal(pl.col("close").shift(1), pl.lit(1e-8))).log().over("asset")
        df = df.with_columns(ret_1h=log_ret)

        btc = df.filter(pl.col("asset") == "BTCUSDT").select([
            pl.col("timestamp"),
            pl.col("ret_1h").alias("ret_m"),
        ])
        if btc.height > 0:
            df = df.join(btc, on="timestamp", how="left").sort("timestamp", "asset")
        else:
            mkt = df.group_by("timestamp").agg(pl.col("ret_1h").mean().alias("ret_m"))
            df = df.join(mkt, on="timestamp", how="left").sort("timestamp", "asset")

        r_i = pl.col("ret_1h").shift(delay).over("asset")
        r_m = pl.col("ret_m").shift(delay).over("asset")

        is_down = pl.when(r_m < 0.0).then(1.0).otherwise(0.0)

        df = df.with_columns(
            r_i=r_i,
            r_m=r_m,
            r_i_m=r_i * r_m,
            r_m_sq=r_m ** 2,
            r_i_m_down=r_i * r_m * is_down,
            r_m_sq_down=(r_m ** 2) * is_down,
        )

        new_cols: list[pl.Expr] = []
        for w in [720, 336]:
            mean_ri = pl.col("r_i").rolling_mean(w, min_samples=20).over("asset")
            mean_rm = pl.col("r_m").rolling_mean(w, min_samples=20).over("asset")
            cov_im = pl.col("r_i_m").rolling_mean(w, min_samples=20).over("asset") - mean_ri * mean_rm
            var_m = pl.col("r_m_sq").rolling_mean(w, min_samples=20).over("asset") - mean_rm ** 2
            beta_std = cov_im / pl.max_horizontal(var_m, pl.lit(1e-8))

            sum_down_im = pl.col("r_i_m_down").rolling_sum(w, min_samples=20).over("asset")
            sum_down_m2 = pl.col("r_m_sq_down").rolling_sum(w, min_samples=20).over("asset")
            beta_down = sum_down_im / pl.max_horizontal(sum_down_m2, pl.lit(1e-8))

            rel_down_beta = beta_down - beta_std

            new_cols.append(rel_down_beta.alias(f"rel_down_beta_{w}"))
            new_cols.append(beta_down.alias(f"beta_down_{w}"))

        df = df.with_columns(new_cols)

        return df.with_columns(
            feat_rel_downside_beta_720=(pl.col("rel_down_beta_720") - pl.col("rel_down_beta_720").mean().over("timestamp"))
            / (pl.col("rel_down_beta_720").std().over("timestamp") + 1e-8),
            feat_rel_downside_beta_336=(pl.col("rel_down_beta_336") - pl.col("rel_down_beta_336").mean().over("timestamp"))
            / (pl.col("rel_down_beta_336").std().over("timestamp") + 1e-8),
            feat_downside_beta_720=(pl.col("beta_down_720") - pl.col("beta_down_720").mean().over("timestamp"))
            / (pl.col("beta_down_720").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 4, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_rel_downside_beta_720', 'feat_rel_downside_beta_336', 'feat_downside_beta_720'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSRelativeDownsideBetaPipeline()
