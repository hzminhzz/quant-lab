"""CausalModelStrategy baseline: turnover-penalized LinearFeaturePortfolioModel."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from causal_ridge_1h import CausalRidgePipeline
from ml4t.models import (
    LinearFeaturePortfolioModel,
    LinearPortfolioConfig,
    PortfolioSequenceBatch,
)

from warden.leakage import WalkForwardModelProtocol

MODEL_CLASS = "LinearFeaturePortfolioModel"
MODEL_TIER = "linear"


def _build_portfolio_sequence_batch(
    df: pl.DataFrame,
    feature_cols: Sequence[str],
    label_col: str | None = None,
) -> tuple[PortfolioSequenceBatch, list[Any], list[str]]:
    """Convert a long-format panel frame into a masked PortfolioSequenceBatch."""
    ts_list = sorted(df["timestamp"].unique().to_list())
    assets_list = sorted(df["asset"].unique().to_list())
    ts_map = {ts_val: idx for idx, ts_val in enumerate(ts_list)}
    asset_map = {asset_val: idx for idx, asset_val in enumerate(assets_list)}

    n_periods = len(ts_list)
    n_assets = len(assets_list)
    n_features = len(feature_cols)

    features = np.zeros((1, n_periods, n_assets, n_features), dtype=np.float64)
    mask = np.zeros((1, n_periods, n_assets), dtype=bool)
    returns = (
        np.full((1, n_periods, n_assets), np.nan, dtype=np.float64)
        if label_col is not None
        else None
    )

    select_cols = ["timestamp", "asset", *feature_cols]
    if label_col is not None:
        select_cols.append(label_col)

    for row in df.select(select_cols).iter_rows():
        ti = ts_map[row[0]]
        aj = asset_map[row[1]]
        feat_vals = np.asarray(row[2 : 2 + n_features], dtype=np.float64)
        if np.isfinite(feat_vals).all():
            features[0, ti, aj] = feat_vals
            mask[0, ti, aj] = True
        if returns is not None:
            raw_y = row[2 + n_features]
            if raw_y is not None:
                fy = float(raw_y)
                if np.isfinite(fy):
                    returns[0, ti, aj] = fy

    batch = PortfolioSequenceBatch(
        features=features,
        returns=returns,
        mask=mask,
        timestamps=tuple(ts_list),
        asset_ids=tuple(assets_list),
    )
    return batch, ts_list, assets_list


class CausalLinearPortfolioEstimator:
    """Fold-local LinearFeaturePortfolioModel with causal L2 turnover smoothing."""

    def __init__(
        self,
        *,
        ridge_alpha: float = 10.0,
        turnover_penalty: float = 2.0,
        gross_exposure: float = 1.0,
        net_exposure: float = 0.0,
        max_abs_weight: float | None = None,
    ) -> None:
        self.ridge_alpha = float(ridge_alpha)
        self.turnover_penalty = float(turnover_penalty)
        self.gross_exposure = float(gross_exposure)
        self.net_exposure = float(net_exposure)
        self.max_abs_weight = None if max_abs_weight is None else float(max_abs_weight)

    def fit_predict_fold(
        self,
        train_df: pl.DataFrame,
        val_features_df: pl.DataFrame,
        feature_cols: Sequence[str],
        label_col: str,
        seed: int = 42,
    ) -> pl.DataFrame:
        _ = seed
        train_batch, _, _ = _build_portfolio_sequence_batch(
            train_df, feature_cols, label_col=label_col
        )
        config = LinearPortfolioConfig(
            ridge_alpha=self.ridge_alpha,
            turnover_penalty=self.turnover_penalty,
            fit_intercept=True,
            gross_exposure=self.gross_exposure,
            net_exposure=self.net_exposure,
            max_abs_weight=self.max_abs_weight,
        )
        model = LinearFeaturePortfolioModel(config)
        model.fit(train_batch)

        val_batch, val_ts, val_assets = _build_portfolio_sequence_batch(
            val_features_df, feature_cols, label_col=None
        )
        weights_result = model.predict(val_batch)
        raw_weights = np.asarray(weights_result.weights[0], dtype=np.float64)
        mask_val = (
            np.asarray(val_batch.mask[0], dtype=bool)
            if val_batch.mask is not None
            else np.ones(raw_weights.shape, dtype=bool)
        )

        lam = max(0.0, self.turnover_penalty)
        smoothed = np.zeros_like(raw_weights, dtype=np.float64)
        prev = np.zeros(len(val_assets), dtype=np.float64)
        for t_idx in range(len(val_ts)):
            active_t = mask_val[t_idx]
            prev_active = np.where(active_t, prev, 0.0)
            if lam > 0.0:
                w_t = np.where(
                    active_t, (raw_weights[t_idx] + lam * prev_active) / (1.0 + lam), 0.0
                )
            else:
                w_t = np.where(active_t, raw_weights[t_idx], 0.0)
            smoothed[t_idx] = w_t
            prev = w_t

        ts_map = {ts_val: idx for idx, ts_val in enumerate(val_ts)}
        asset_map = {asset_val: idx for idx, asset_val in enumerate(val_assets)}
        pred_vals: list[float] = []
        target_weights: list[float] = []
        for ts_val, asset_val in val_features_df.select("timestamp", "asset").iter_rows():
            ti = ts_map[ts_val]
            aj = asset_map[asset_val]
            pred_vals.append(float(raw_weights[ti, aj]))
            target_weights.append(float(smoothed[ti, aj]))

        return val_features_df.select("timestamp", "asset").with_columns(
            prediction_value=pl.Series(pred_vals, dtype=pl.Float64),
            target_weight=pl.Series(target_weights, dtype=pl.Float64),
        )


pipeline = CausalRidgePipeline()


def build_estimator(params: dict[str, Any]) -> WalkForwardModelProtocol:
    """Build the turnover-penalized LinearFeaturePortfolioModel fold estimator."""
    max_abs = params.get("max_abs_weight")
    return CausalLinearPortfolioEstimator(
        ridge_alpha=float(params.get("ridge_alpha", 10.0)),
        turnover_penalty=float(params.get("turnover_penalty", 2.0)),
        gross_exposure=float(params.get("gross_exposure", 1.0)),
        net_exposure=float(params.get("net_exposure", 0.0)),
        max_abs_weight=None if max_abs is None else float(max_abs),
    )
