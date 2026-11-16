"""Cross-sectional funding carry and crowding exhaustion factor model with purged walk-forward CV (HYP-2026-017)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSFundingCarryExhaustionPipeline:
    """Causal cross-sectional feature pipeline for funding carry and crowding exhaustion."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        # 24h trailing funding rate and 24h price return
        f24 = pl.col("funding_rate").rolling_mean(24).shift(delay).over("asset")
        f72 = pl.col("funding_rate").rolling_mean(72).shift(delay).over("asset")
        ret24 = pl.col("close").pct_change(24).shift(delay).over("asset")
        df = df.with_columns(
            f24=f24,
            f72=f72,
            ret24=ret24,
        )

        # Cross-sectional median of 24h return across active assets at each timestamp
        med_ret24 = pl.col("ret24").median().over("timestamp")

        # Conditioned carry:
        # If funding > 0 and ret24 <= median -> amplified short signal
        # If funding < 0 and ret24 >= median -> amplified long signal
        cond_score = (
            pl.when((pl.col("f24") > 0) & (pl.col("ret24") <= med_ret24))
            .then(-pl.col("f24") * 2.0)
            .when((pl.col("f24") < 0) & (pl.col("ret24") >= med_ret24))
            .then(-pl.col("f24") * 2.0)
            .otherwise(-pl.col("f24"))
        )

        df = df.with_columns(cond_score=cond_score)

        return df.with_columns(
            feat_carry=(-pl.col("f24") - (-pl.col("f24")).mean().over("timestamp"))
            / (pl.col("f24").std().over("timestamp") + 1e-8),
            feat_exhaustion=(pl.col("cond_score") - pl.col("cond_score").mean().over("timestamp"))
            / (pl.col("cond_score").std().over("timestamp") + 1e-8),
            feat_carry_slow=(-pl.col("f72") - (-pl.col("f72")).mean().over("timestamp"))
            / (pl.col("f72").std().over("timestamp") + 1e-8),
        )


class CSFundingCarryExhaustionRidgeModel:
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
    rebalance_every: int = 48,
    top_k: int = 5,
    **kwargs: Any,
) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit top-k/bottom-k dollar-neutral positions."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    df = panel_df.sort("timestamp", "asset")
    timestamps = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    n_bars, n_assets = len(timestamps), len(assets)

    ts_map = {t: i for i, t in enumerate(timestamps)}
    val_start_idx = ts_map.get(contract.val_start, int(n_bars * 0.75))
    val_end_idx = ts_map.get(contract.val_end, n_bars)

    open_pivot = df.pivot(index="timestamp", on="asset", values="open").fill_null(strategy="forward").fill_null(0.0)
    for a in assets:
        if a not in open_pivot.columns:
            open_pivot = open_pivot.with_columns(pl.lit(0.0).alias(a))
    open_mat = open_pivot.select(assets).to_numpy()

    pipe = CSFundingCarryExhaustionPipeline()
    feat_df = pipe.compute(df, contract)

    f_carry = feat_df.pivot(index="timestamp", on="asset", values="feat_carry").fill_null(0.0).select(assets).to_numpy()
    f_exh = feat_df.pivot(index="timestamp", on="asset", values="feat_exhaustion").fill_null(0.0).select(assets).to_numpy()
    f_slow = feat_df.pivot(index="timestamp", on="asset", values="feat_carry_slow").fill_null(0.0).select(assets).to_numpy()

    h = contract.label_horizon_bars
    forward_label = compute_open_forward_label(open_mat, contract.execution_delay_bars, h)

    splits = purged_walk_forward_splits(
        n_timestamps=val_end_idx,
        n_folds=contract.n_folds,
        min_train_timestamps=val_start_idx,
        label_horizon=contract.execution_delay_bars + contract.label_horizon_bars,
        embargo=contract.embargo_bars,
    )

    oos_preds = np.full_like(open_mat, np.nan)
    for train_idx, test_idx in splits:
        train_idx = train_idx[train_idx >= 72]
        if len(train_idx) < 10:
            continue
        x_tr = np.column_stack([f_carry[train_idx].ravel(), f_exh[train_idx].ravel(), f_slow[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([f_carry[t], f_exh[t], f_slow[t]])
            oos_preds[t] = model.predict(np.nan_to_num(x_test_t, nan=0.0))

    ic_list: list[float] = []
    for t in range(val_start_idx, val_end_idx - h):
        pred_t = oos_preds[t]
        if not np.any(np.isnan(pred_t)):
            val_ic = float(np.corrcoef(pred_t, forward_label[t])[0, 1])
            if np.isfinite(val_ic):
                ic_list.append(val_ic)
    ic_arr = np.array(ic_list, dtype=float)

    positions = np.zeros((n_bars, n_assets), dtype=float)
    k = min(top_k, n_assets // 2)
    for t in range(val_start_idx, val_end_idx):
        if t % rebalance_every != 0:
            positions[t] = positions[t - 1]
            continue
        scores = oos_preds[t]
        if np.any(np.isnan(scores)):
            positions[t] = positions[t - 1]
            continue
        order = np.argsort(scores)
        w = np.zeros(n_assets, dtype=float)
        w[order[-k:]] = 1.0 / (2.0 * k)
        w[order[:k]] = -1.0 / (2.0 * k)
        positions[t] = w

    return {
        "positions": positions,
        "ic_series": ic_arr,
        "mean_ic": float(np.mean(ic_arr)) if len(ic_arr) else None,
    }


pipeline = CSFundingCarryExhaustionPipeline()
model = CSFundingCarryExhaustionRidgeModel()
