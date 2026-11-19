"""Cross-sectional return-volume correlation factor model with purged walk-forward CV (HYP-2026-060)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from recovery_common import recovery_positions
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract


class CSVolumeReturnCorrelationPipeline:
    """Causal cross-sectional feature pipeline for return-volume correlation."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        ret = (pl.col("close") / pl.col("close").shift(1)).log().over("asset")
        # Missing/zero volume is not an observable log-volume change. Preserve nulls
        # rather than feeding -inf/NaN into cross-sectional selection or leakage checks.
        volume = pl.col("volume")
        prev_volume = volume.shift(1).over("asset")
        vol_chg = (
            pl.when((volume > 0.0) & (prev_volume > 0.0))
            .then((volume / prev_volume).log())
            .otherwise(None)
        )

        df = df.with_columns(
            ret=ret,
            vol_chg=vol_chg,
            ret_x_vol=(ret * vol_chg),
        )

        for w in (336, 168, 72):
            cov_w = (
                pl.col("ret_x_vol").rolling_mean(w, min_samples=12).over("asset")
                - pl.col("ret").rolling_mean(w, min_samples=12).over("asset")
                * pl.col("vol_chg").rolling_mean(w, min_samples=12).over("asset")
            )
            std_r = pl.col("ret").rolling_std(w, min_samples=12).over("asset")
            std_v = pl.col("vol_chg").rolling_std(w, min_samples=12).over("asset")
            corr_w = (cov_w / (std_r * std_v + 1e-8)).shift(delay).over("asset")
            df = df.with_columns(corr_w.alias(f"vrc_{w}"))

        return df.with_columns(
            feat_vrc_336=(pl.col("vrc_336") - pl.col("vrc_336").mean().over("timestamp"))
            / (pl.col("vrc_336").std().over("timestamp") + 1e-8),
            feat_vrc_168=(pl.col("vrc_168") - pl.col("vrc_168").mean().over("timestamp"))
            / (pl.col("vrc_168").std().over("timestamp") + 1e-8),
            feat_vrc_72=(pl.col("vrc_72") - pl.col("vrc_72").mean().over("timestamp"))
            / (pl.col("vrc_72").std().over("timestamp") + 1e-8),
        )


class CSVolumeReturnCorrelationRidgeModel:
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
        pipeline=CSVolumeReturnCorrelationPipeline(),
        feature_cols=['feat_vrc_336', 'feat_vrc_168', 'feat_vrc_72'],
        warmup=72,
        alpha=alpha,
        rebalance_every=rebalance_every,
        top_k=top_k,
        k_floor_one=False,
        ic_std_guard=True,
        fill_missing_feature_assets=True,
        **kwargs,
    )


pipeline = CSVolumeReturnCorrelationPipeline()
model = CSVolumeReturnCorrelationRidgeModel()
