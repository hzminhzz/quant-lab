"""CausalModelStrategy candidate: 4h crypto-perp cross-sectional momentum + funding carry.

Implements the inverted CausalModelStrategy protocol so Warden owns WalkForwardCV,
fold-local scaling, label stripping, execution-aligned forward returns, IC evaluation,
and ml4t.backtest.Engine simulation.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from ml4t.engineer import compute_features
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import WalkForwardModelProtocol


class CausalXSMomentumCarryPipeline:
    """Strictly causal 4h momentum, volatility, and funding-carry features."""

    feature_cols: tuple[str, ...] = (
        "feat_roc6",
        "feat_roc18",
        "feat_natr14",
        "feat_carry6",
    )

    def compute(
        self,
        bars_df: pl.DataFrame | pl.LazyFrame,
        contract: EvaluationContract,
    ) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        df = df.sort("timestamp", "asset")

        engineered = compute_features(
            df,
            [
                {"name": "roc", "params": {"period": 6}, "output": "roc_6"},
                {"name": "roc", "params": {"period": 18}, "output": "roc_18"},
                {"name": "natr", "params": {"period": 14}, "output": "natr_14"},
            ],
            group_col="asset",
            timestamp_col="timestamp",
            calendar=None,
        ).sort("timestamp", "asset")

        carry_expr = (
            pl.col("funding_rate").cast(pl.Float64).fill_null(0.0).rolling_mean(6).over("asset")
            if "funding_rate" in engineered.columns
            else pl.lit(0.0, dtype=pl.Float64)
        )

        return (
            engineered.with_columns(
                pl.col("roc_6").fill_nan(None),
                pl.col("roc_18").fill_nan(None),
                pl.col("natr_14").fill_nan(None),
                carry_expr.alias("carry_6"),
            )
            .with_columns(
                feat_roc6=(pl.col("roc_6") - pl.col("roc_6").mean().over("timestamp")).fill_null(0.0),
                feat_roc18=(pl.col("roc_18") - pl.col("roc_18").mean().over("timestamp")).fill_null(0.0),
                feat_natr14=(pl.col("natr_14") - pl.col("natr_14").mean().over("timestamp")).fill_null(0.0),
                feat_carry6=(pl.col("carry_6") - pl.col("carry_6").mean().over("timestamp")).fill_null(0.0),
            )
        )


class CausalRidgeEstimator:
    """Fold-local Ridge estimator consuming Warden-scaled, label-free validation inputs."""

    def __init__(self, ridge_alpha: float = 10.0) -> None:
        self.ridge_alpha = float(ridge_alpha)

    def fit_predict_fold(
        self,
        train_df: pl.DataFrame,
        val_features_df: pl.DataFrame,
        feature_cols: Sequence[str],
        label_col: str,
        seed: int = 42,
    ) -> pl.DataFrame:
        _ = seed
        x_tr = train_df.select(feature_cols).to_numpy()
        y_tr = train_df[label_col].to_numpy()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        if int(np.sum(valid)) == 0:
            raise ValueError("No finite training observations in fold.")
        model = Ridge(alpha=self.ridge_alpha, fit_intercept=True)
        model.fit(x_tr[valid], y_tr[valid])

        x_val = np.nan_to_num(val_features_df.select(feature_cols).to_numpy(), nan=0.0)
        preds = model.predict(x_val)
        return val_features_df.select("timestamp", "asset").with_columns(
            prediction_value=pl.Series(preds, dtype=pl.Float64)
        )


pipeline = CausalXSMomentumCarryPipeline()


def build_estimator(params: dict[str, Any]) -> WalkForwardModelProtocol:
    """Build the fold estimator for Warden's walk-forward harness."""
    return CausalRidgeEstimator(ridge_alpha=float(params.get("ridge_alpha", 10.0)))
