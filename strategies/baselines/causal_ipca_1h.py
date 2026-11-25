"""CausalModelStrategy baseline: conditional latent-factor IPCAModel."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from causal_ridge_1h import CausalRidgePipeline
from ml4t.models import (
    BetaLambdaMapper,
    CrossSectionBatch,
    ExpandingMeanFactorForecaster,
    IPCAConfig,
    IPCAModel,
    cross_section_batch_from_long_frame,
    predictions_frame_from_asset_forecast,
)
from pydantic import Field

from warden.leakage import WalkForwardModelProtocol
from warden.params_model import ParamsModel

MODEL_CLASS = "IPCAModel"
MODEL_TIER = "linear"


class CausalIPCAEstimator:
    """Fold-local IPCA + ExpandingMeanFactorForecaster + BetaLambdaMapper."""

    def __init__(
        self,
        *,
        n_factors: int = 2,
        max_iter: int = 200,
        tol: float = 1e-5,
        factor_ridge: float = 1e-3,
        gamma_ridge: float = 1e-3,
    ) -> None:
        self.n_factors = int(n_factors)
        self.max_iter = int(max_iter)
        self.tol = float(tol)
        self.factor_ridge = float(factor_ridge)
        self.gamma_ridge = float(gamma_ridge)

    def fit_predict_fold(
        self,
        train_df: pl.DataFrame,
        val_features_df: pl.DataFrame,
        feature_cols: Sequence[str],
        label_col: str,
        seed: int = 42,
    ) -> pl.DataFrame:
        _ = seed
        train_batch: CrossSectionBatch = cross_section_batch_from_long_frame(
            train_df,
            feature_cols=feature_cols,
            return_col=label_col,
            timestamp_col="timestamp",
            entity_col="asset",
        )
        effective_factors = max(1, min(self.n_factors, len(feature_cols)))
        ipca = IPCAModel(
            IPCAConfig(
                n_factors=effective_factors,
                max_iter=self.max_iter,
                tol=self.tol,
                factor_ridge=self.factor_ridge,
                gamma_ridge=self.gamma_ridge,
            )
        )
        ipca.fit(train_batch)

        # Strictly historical expanding factor premia estimated from training cross-sections only.
        train_state = ipca.extract(train_batch)
        forecaster = ExpandingMeanFactorForecaster()
        forecaster.fit(train_state)

        # Validation batch carries characteristics only (returns=None -> no realized val factor returns).
        sorted_val = val_features_df.sort("timestamp", "asset")
        val_batch: CrossSectionBatch = cross_section_batch_from_long_frame(
            sorted_val,
            feature_cols=feature_cols,
            return_col=None,
            timestamp_col="timestamp",
            entity_col="asset",
        )
        val_state = ipca.extract(val_batch)
        factor_forecast = forecaster.predict(val_state)
        asset_forecast = BetaLambdaMapper().predict(val_state, factor_forecast)
        pred_frame = predictions_frame_from_asset_forecast(asset_forecast)

        # Map both real asset IDs (ml4t-models 0.1.5) and date-local slot_{i} back to (real_ts, real_asset).
        sorted_unique_ts: list[Any] = []
        slot_to_asset: dict[tuple[int, str], str] = {}
        current_ts: Any = None
        t_idx = -1
        slot_idx = 0
        for ts_val, asset_val in sorted_val.select("timestamp", "asset").iter_rows():
            if ts_val != current_ts:
                current_ts = ts_val
                sorted_unique_ts.append(ts_val)
                t_idx += 1
                slot_idx = 0
            slot_to_asset[(t_idx, f"slot_{slot_idx}")] = str(asset_val)
            slot_idx += 1

        batch_ts_to_idx = {bt: i for i, bt in enumerate(asset_forecast.timestamps)}
        pred_by_key: dict[tuple[Any, str], float] = {}
        for row_ts, row_asset, pred_val in pred_frame.rows:
            ti = batch_ts_to_idx.get(row_ts)
            if ti is None or ti >= len(sorted_unique_ts):
                continue
            real_ts = sorted_unique_ts[ti]
            real_asset = slot_to_asset.get((ti, str(row_asset)), str(row_asset))
            fval = float(pred_val)
            if np.isfinite(fval):
                pred_by_key[(real_ts, real_asset)] = fval

        preds = [
            pred_by_key.get((ts_val, str(asset_val)), 0.0)
            for ts_val, asset_val in val_features_df.select("timestamp", "asset").iter_rows()
        ]
        return val_features_df.select("timestamp", "asset").with_columns(
            prediction_value=pl.Series(preds, dtype=pl.Float64)
        )


pipeline = CausalRidgePipeline()


class IPCABaselineParams(ParamsModel):
    """Typed declaration of the IPCA baseline's numeric parameters."""

    n_factors: int = Field(default=2, ge=1)
    max_iter: int = Field(default=200, ge=1)
    tol: float = Field(default=1e-5, gt=0.0)
    factor_ridge: float = Field(default=1e-3, ge=0.0)
    gamma_ridge: float = Field(default=1e-3, ge=0.0)


#: Warden's typed-parameter hook; see ``warden.params_model``.
params_model = IPCABaselineParams


def validate_params(params: dict[str, Any]) -> IPCABaselineParams:
    declared = {k: v for k, v in params.items() if k in IPCABaselineParams.model_fields}
    return IPCABaselineParams.model_validate(declared)


def build_estimator(params: dict[str, Any]) -> WalkForwardModelProtocol:
    """Build the IPCA conditional latent-factor fold estimator."""
    tuned = validate_params(params)
    return CausalIPCAEstimator(
        n_factors=tuned.n_factors,
        max_iter=tuned.max_iter,
        tol=tuned.tol,
        factor_ridge=tuned.factor_ridge,
        gamma_ridge=tuned.gamma_ridge,
    )
