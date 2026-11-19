"""Cross-sectional realized kurtosis and jump-tail risk factor model with purged walk-forward CV (HYP-2026-042)."""

from __future__ import annotations

from typing import Any

import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


class CSRealizedKurtosisTailPipeline:
    """Causal cross-sectional feature pipeline for realized 4th moment kurtosis tail risk."""

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
            r_4=r_eff ** 4,
        )

        new_cols: list[pl.Expr] = []
        for w in [168, 72]:
            m1 = pl.col("r_1").rolling_mean(w, min_samples=10).over("asset")
            m2 = pl.col("r_2").rolling_mean(w, min_samples=10).over("asset")
            m3 = pl.col("r_3").rolling_mean(w, min_samples=10).over("asset")
            m4 = pl.col("r_4").rolling_mean(w, min_samples=10).over("asset")

            var_val = pl.max_horizontal(m2 - m1 ** 2, pl.lit(1e-8))
            mu4 = pl.max_horizontal(m4 - 4.0 * m1 * m3 + 6.0 * (m1 ** 2) * m2 - 3.0 * (m1 ** 4), pl.lit(0.0))
            kurt = mu4 / (var_val ** 2 + 1e-8)

            new_cols.append(kurt.alias(f"kurt_{w}"))
            if w == 168:
                new_cols.append(var_val.sqrt().alias("rvol_168"))

        df = df.with_columns(new_cols)

        return df.with_columns(
            feat_kurt_168=(pl.col("kurt_168") - pl.col("kurt_168").mean().over("timestamp"))
            / (pl.col("kurt_168").std().over("timestamp") + 1e-8),
            feat_kurt_72=(pl.col("kurt_72") - pl.col("kurt_72").mean().over("timestamp"))
            / (pl.col("kurt_72").std().over("timestamp") + 1e-8),
            feat_vol_168=(pl.col("rvol_168") - pl.col("rvol_168").mean().over("timestamp"))
            / (pl.col("rvol_168").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 4, top_k: int = 5, invert_factor: bool = False, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_kurt_168', 'feat_kurt_72', 'feat_vol_168'], warmup=168,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, invert_factor=invert_factor, ic_guard=True, predict_rowwise=True, invert_aliases=True, fill_missing_assets=False, min_train=10, **kwargs)


pipeline = CSRealizedKurtosisTailPipeline()
