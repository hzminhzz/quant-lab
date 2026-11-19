"""Cross-sectional quadratic market beta curvature factor model with purged walk-forward CV (HYP-2026-040)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSQuadraticMarketBetaPipeline:
    """Causal cross-sectional feature pipeline for non-linear quadratic market beta curvature."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        df = df.sort("timestamp", "asset").with_columns(
            ret_1h=pl.col("close").pct_change().over("asset").fill_null(0.0)
        )

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
        r_m2 = (r_m ** 2)

        # Frisch-Waugh-Lovell regression of r_i on (r_m, r_m^2):
        # Orthogonalize r_m^2 on r_m, then regress r_i on the residual to isolate curvature beta_2
        new_cols: list[pl.Expr] = []
        for w in [168, 336, 72]:
            cov_m_m2 = (r_m * r_m2).rolling_mean(w).over("asset") - r_m.rolling_mean(w).over("asset") * r_m2.rolling_mean(w).over("asset")
            var_m = r_m.rolling_var(w).over("asset")
            gamma = cov_m_m2 / (var_m + 1e-8)
            res_m2 = r_m2 - gamma * r_m

            cov_y_res = (r_i * res_m2).rolling_mean(w).over("asset") - r_i.rolling_mean(w).over("asset") * res_m2.rolling_mean(w).over("asset")
            var_res = res_m2.rolling_var(w).over("asset")
            beta_2 = cov_y_res / (var_res + 1e-8)

            cov_y_m = (r_i * r_m).rolling_mean(w).over("asset") - r_i.rolling_mean(w).over("asset") * r_m.rolling_mean(w).over("asset")
            beta_1 = cov_y_m / (var_m + 1e-8)

            new_cols.append(beta_2.alias(f"beta2_{w}"))
            new_cols.append(beta_1.alias(f"beta1_{w}"))

        df = df.with_columns(new_cols)

        # Negate beta_2 so concave assets (negative beta_2) have high positive signal scores.
        return df.with_columns(
            feat_neg_beta2_168=(-pl.col("beta2_168") - (-pl.col("beta2_168")).mean().over("timestamp"))
            / (pl.col("beta2_168").std().over("timestamp") + 1e-8),
            feat_neg_beta2_336=(-pl.col("beta2_336") - (-pl.col("beta2_336")).mean().over("timestamp"))
            / (pl.col("beta2_336").std().over("timestamp") + 1e-8),
            feat_low_beta1_168=(-pl.col("beta1_168") - (-pl.col("beta1_168")).mean().over("timestamp"))
            / (pl.col("beta1_168").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_neg_beta2_168', 'feat_neg_beta2_336', 'feat_low_beta1_168'], warmup=336,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSQuadraticMarketBetaPipeline()
