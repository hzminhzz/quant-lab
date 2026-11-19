"""Cross-sectional realized skewness and lottery overpricing factor model with purged walk-forward CV (HYP-2026-045)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSRealizedSkewnessPipeline:
    """Causal cross-sectional feature pipeline for realized 3rd moment skewness."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        log_ret = (pl.col("close") / pl.max_horizontal(pl.col("close").shift(1), pl.lit(1e-8))).log().over("asset")
        r_eff = log_ret.shift(delay).over("asset")

        df = df.with_columns(
            r_1=r_eff,
            r_2=r_eff ** 2,
            r_3=r_eff ** 3,
        )

        new_cols: list[pl.Expr] = []
        for w in [168, 72]:
            m1 = pl.col("r_1").rolling_mean(w, min_samples=10).over("asset")
            m2 = pl.col("r_2").rolling_mean(w, min_samples=10).over("asset")
            m3 = pl.col("r_3").rolling_mean(w, min_samples=10).over("asset")

            var_val = pl.max_horizontal(m2 - m1 ** 2, pl.lit(1e-8))
            mu3 = m3 - 3.0 * m1 * m2 + 2.0 * (m1 ** 3)
            skew = mu3 / (var_val.sqrt() ** 3 + 1e-8)

            # Negate skewness: low/negative skewness assets are long targets, high lottery skewness are short targets
            new_cols.append((-skew).alias(f"neg_skew_{w}"))
            if w == 168:
                new_cols.append(var_val.sqrt().alias("rvol_168"))

        df = df.with_columns(new_cols)

        return df.with_columns(
            feat_neg_skew_168=(pl.col("neg_skew_168") - pl.col("neg_skew_168").mean().over("timestamp"))
            / (pl.col("neg_skew_168").std().over("timestamp") + 1e-8),
            feat_neg_skew_72=(pl.col("neg_skew_72") - pl.col("neg_skew_72").mean().over("timestamp"))
            / (pl.col("neg_skew_72").std().over("timestamp") + 1e-8),
            feat_vol_168=(pl.col("rvol_168") - pl.col("rvol_168").mean().over("timestamp"))
            / (pl.col("rvol_168").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 4, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_neg_skew_168', 'feat_neg_skew_72', 'feat_vol_168'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=True, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSRealizedSkewnessPipeline()
