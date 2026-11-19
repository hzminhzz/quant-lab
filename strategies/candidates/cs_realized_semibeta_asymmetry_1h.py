"""Cross-sectional realized concordant semibeta asymmetry factor model with purged walk-forward CV (HYP-2026-044)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSRealizedSemibetaAsymmetryPipeline:
    """Causal cross-sectional feature pipeline for realized concordant downside semibeta and asymmetry."""

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

        r_i_neg = pl.min_horizontal(r_i, pl.lit(0.0))
        r_m_neg = pl.min_horizontal(r_m, pl.lit(0.0))
        r_i_pos = pl.max_horizontal(r_i, pl.lit(0.0))
        r_m_pos = pl.max_horizontal(r_m, pl.lit(0.0))

        df = df.with_columns(
            prod_neg=r_i_neg * r_m_neg,
            sq_m_neg=r_m_neg ** 2,
            prod_pos=r_i_pos * r_m_pos,
            sq_m_pos=r_m_pos ** 2,
        )

        new_cols: list[pl.Expr] = []
        for w in [168, 336]:
            sum_prod_neg = pl.col("prod_neg").rolling_sum(w, min_samples=10).over("asset")
            sum_sq_m_neg = pl.col("sq_m_neg").rolling_sum(w, min_samples=10).over("asset")
            beta_neg_neg = sum_prod_neg / pl.max_horizontal(sum_sq_m_neg, pl.lit(1e-8))

            sum_prod_pos = pl.col("prod_pos").rolling_sum(w, min_samples=10).over("asset")
            sum_sq_m_pos = pl.col("sq_m_pos").rolling_sum(w, min_samples=10).over("asset")
            beta_pos_pos = sum_prod_pos / pl.max_horizontal(sum_sq_m_pos, pl.lit(1e-8))

            new_cols.append(beta_neg_neg.alias(f"beta_neg_{w}"))
            new_cols.append(beta_pos_pos.alias(f"beta_pos_{w}"))
            new_cols.append((beta_neg_neg - beta_pos_pos).alias(f"beta_asym_{w}"))

        df = df.with_columns(new_cols)

        return df.with_columns(
            feat_semibeta_neg_168=(pl.col("beta_neg_168") - pl.col("beta_neg_168").mean().over("timestamp"))
            / (pl.col("beta_neg_168").std().over("timestamp") + 1e-8),
            feat_semibeta_asym_168=(pl.col("beta_asym_168") - pl.col("beta_asym_168").mean().over("timestamp"))
            / (pl.col("beta_asym_168").std().over("timestamp") + 1e-8),
            feat_semibeta_neg_336=(pl.col("beta_neg_336") - pl.col("beta_neg_336").mean().over("timestamp"))
            / (pl.col("beta_neg_336").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 4, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_semibeta_neg_168', 'feat_semibeta_asym_168', 'feat_semibeta_neg_336'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSRealizedSemibetaAsymmetryPipeline()
