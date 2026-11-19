"""Cross-sectional quadratic market beta curvature factor model with purged walk-forward CV (HYP-2026-040)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from recovery_common import recovery_positions
from sklearn.linear_model import Ridge

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

        # Materialize lagged inputs and intermediate windows. The original
        # expression tree nests rolling windows in other rolling windows and
        # recomputes overlapping histories repeatedly on each 1h prefix.
        # Algebra remains the same Frisch-Waugh-Lovell curvature estimate.
        df = df.with_columns(
            ri=r_i,
            rm=r_m,
        )
        df = df.with_columns(
            rm2=pl.col("rm").pow(2),
            rm3=pl.col("rm").pow(3),
            rm_ri=pl.col("rm") * pl.col("ri"),
        )
        for w in (168, 336, 72):
            cov_m_m2 = (
                pl.col("rm3").rolling_mean(w).over("asset")
                - pl.col("rm").rolling_mean(w).over("asset")
                * pl.col("rm2").rolling_mean(w).over("asset")
            )
            var_m = pl.col("rm").rolling_var(w).over("asset")
            cov_y_m = (
                pl.col("rm_ri").rolling_mean(w).over("asset")
                - pl.col("ri").rolling_mean(w).over("asset")
                * pl.col("rm").rolling_mean(w).over("asset")
            )
            df = df.with_columns(
                (cov_m_m2 / (var_m + 1e-8)).alias(f"gamma_{w}"),
                (cov_y_m / (var_m + 1e-8)).alias(f"beta1_{w}"),
            )
            df = df.with_columns(
                (pl.col("rm2") - pl.col(f"gamma_{w}") * pl.col("rm")).alias(f"res_m2_{w}"),
            )
            df = df.with_columns(
                (pl.col("ri") * pl.col(f"res_m2_{w}")).alias(f"ri_res_{w}"),
            )
            cov_y_res = (
                pl.col(f"ri_res_{w}").rolling_mean(w).over("asset")
                - pl.col("ri").rolling_mean(w).over("asset")
                * pl.col(f"res_m2_{w}").rolling_mean(w).over("asset")
            )
            var_res = pl.col(f"res_m2_{w}").rolling_var(w).over("asset")
            df = df.with_columns(
                (cov_y_res / (var_res + 1e-8)).alias(f"beta2_{w}"),
            )

        # Negate beta_2 so concave assets (negative beta_2) have high positive signal scores.
        return df.with_columns(
            feat_neg_beta2_168=(-pl.col("beta2_168") - (-pl.col("beta2_168")).mean().over("timestamp"))
            / (pl.col("beta2_168").std().over("timestamp") + 1e-8),
            feat_neg_beta2_336=(-pl.col("beta2_336") - (-pl.col("beta2_336")).mean().over("timestamp"))
            / (pl.col("beta2_336").std().over("timestamp") + 1e-8),
            feat_low_beta1_168=(-pl.col("beta1_168") - (-pl.col("beta1_168")).mean().over("timestamp"))
            / (pl.col("beta1_168").std().over("timestamp") + 1e-8),
        )


class CSQuadraticMarketBetaRidgeModel:
    """WalkForwardModelProtocol implementation for Ridge regression."""

    def __init__(self, alpha: float = 10.0) -> None:
        self.alpha = alpha

    def fit_predict_fold(
        self,
        train_df: pl.DataFrame,
        val_features_df: pl.DataFrame,
        feature_cols: Sequence[str],
        label_col: str,
        seed: int = 42,
    ) -> pl.DataFrame:
        x_tr = train_df.select(feature_cols).to_numpy()
        y_tr = train_df[label_col].to_numpy()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=self.alpha, random_state=seed)
        model.fit(x_tr[valid], y_tr[valid])

        x_val = val_features_df.select(feature_cols).to_numpy()
        preds = model.predict(np.nan_to_num(x_val, nan=0.0))
        return val_features_df.select(["timestamp", "asset"]).with_columns(pl.Series("pred", preds))


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    alpha: float = 10.0,
    rebalance_every: int = 72,
    top_k: int = 5,
    **kwargs: Any,
) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    return recovery_positions(
        panel_df,
        contract,
        pipeline=CSQuadraticMarketBetaPipeline(),
        feature_cols=['feat_neg_beta2_168', 'feat_neg_beta2_336', 'feat_low_beta1_168'],
        warmup=336,
        alpha=alpha,
        rebalance_every=rebalance_every,
        top_k=top_k,
        k_floor_one=False,
        ic_std_guard=False,
        fill_missing_feature_assets=False,
        **kwargs,
    )


pipeline = CSQuadraticMarketBetaPipeline()
model = CSQuadraticMarketBetaRidgeModel()
