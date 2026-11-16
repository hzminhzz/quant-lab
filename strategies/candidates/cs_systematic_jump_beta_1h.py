"""Cross-sectional systematic jump beta factor model with purged walk-forward CV (HYP-2026-053)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSSystematicJumpBetaPipeline:
    """Causal cross-sectional feature pipeline for systematic discontinuous jump beta (Todorov & Bollerslev 2010)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        df = df.sort("timestamp", "asset").with_columns(
            ret_1h=(pl.col("close") / pl.col("close").shift(1)).log().over("asset").fill_null(0.0)
        )

        btc = df.filter(pl.col("asset").is_in(["BTCUSDT", "BTCUSDT_PERP"])).select([
            pl.col("timestamp"),
            pl.col("ret_1h").alias("ret_m"),
        ])
        if btc.height > 0:
            df = df.join(btc, on="timestamp", how="left").sort("timestamp", "asset")
        else:
            mkt = df.group_by("timestamp").agg(pl.col("ret_1h").mean().alias("ret_m"))
            df = df.join(mkt, on="timestamp", how="left").sort("timestamp", "asset")

        df = df.with_columns(
            r_i=pl.col("ret_1h").shift(delay).over("asset"),
            r_m=pl.col("ret_m").shift(delay).over("asset"),
        )

        sigma_m_720 = pl.col("r_m").rolling_std(720, min_samples=24).over("asset")
        jump_thresh = 3.0 * sigma_m_720
        is_jump = (pl.col("r_m").abs() > jump_thresh).cast(pl.Float64)

        df = df.with_columns(is_jump=is_jump)

        cov_jump_720 = (pl.col("r_i") * pl.col("r_m") * pl.col("is_jump")).rolling_sum(720, min_samples=24).over("asset")
        var_jump_720 = (pl.col("r_m").pow(2) * pl.col("is_jump")).rolling_sum(720, min_samples=24).over("asset")
        beta_jump_720 = cov_jump_720 / (var_jump_720 + 1e-8)

        cov_jump_336 = (pl.col("r_i") * pl.col("r_m") * pl.col("is_jump")).rolling_sum(336, min_samples=24).over("asset")
        var_jump_336 = (pl.col("r_m").pow(2) * pl.col("is_jump")).rolling_sum(336, min_samples=24).over("asset")
        beta_jump_336 = cov_jump_336 / (var_jump_336 + 1e-8)

        cov_m_720 = (
            (pl.col("r_i") * pl.col("r_m")).rolling_mean(720, min_samples=24).over("asset")
            - pl.col("r_i").rolling_mean(720, min_samples=24).over("asset")
            * pl.col("r_m").rolling_mean(720, min_samples=24).over("asset")
        )
        var_m_720 = pl.col("r_m").rolling_var(720, min_samples=24).over("asset")
        beta_cont_720 = cov_m_720 / (var_m_720 + 1e-8)

        df = df.with_columns(
            beta_jump_720=beta_jump_720,
            beta_jump_336=beta_jump_336,
            beta_cont_720=beta_cont_720,
        )

        return df.with_columns(
            feat_jump_beta_720=(pl.col("beta_jump_720") - pl.col("beta_jump_720").mean().over("timestamp"))
            / (pl.col("beta_jump_720").std().over("timestamp") + 1e-8),
            feat_jump_beta_336=(pl.col("beta_jump_336") - pl.col("beta_jump_336").mean().over("timestamp"))
            / (pl.col("beta_jump_336").std().over("timestamp") + 1e-8),
            feat_cont_beta_720=(pl.col("beta_cont_720") - pl.col("beta_cont_720").mean().over("timestamp"))
            / (pl.col("beta_cont_720").std().over("timestamp") + 1e-8),
        )


class CSSystematicJumpBetaRidgeModel:
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

    pipe = CSSystematicJumpBetaPipeline()
    feat_df = pipe.compute(df, contract)

    f1 = feat_df.pivot(index="timestamp", on="asset", values="feat_jump_beta_720").fill_null(0.0).select(assets).to_numpy()
    f2 = feat_df.pivot(index="timestamp", on="asset", values="feat_jump_beta_336").fill_null(0.0).select(assets).to_numpy()
    f3 = feat_df.pivot(index="timestamp", on="asset", values="feat_cont_beta_720").fill_null(0.0).select(assets).to_numpy()

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
        train_idx = train_idx[train_idx >= 720]
        if len(train_idx) < 10:
            continue
        x_tr = np.column_stack([f1[train_idx].ravel(), f2[train_idx].ravel(), f3[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([f1[t], f2[t], f3[t]])
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
        if np.all(np.isnan(scores)):
            positions[t] = positions[t - 1]
            continue
        eligible = [i for i in range(n_assets) if assets[i] not in ("BTCUSDT", "BTCUSDT_PERP") and not np.isnan(scores[i])]
        if len(eligible) < 2 * k:
            positions[t] = positions[t - 1]
            continue
        sorted_eligible = sorted(eligible, key=lambda idx: scores[idx])
        w = np.zeros(n_assets, dtype=float)
        for idx in sorted_eligible[-k:]:
            w[idx] = 1.0 / (2.0 * k)
        for idx in sorted_eligible[:k]:
            w[idx] = -1.0 / (2.0 * k)
        positions[t] = w
    return {
        "positions": positions,
        "ic_series": ic_arr,
        "mean_ic": float(np.mean(ic_arr)) if len(ic_arr) else None,
    }


pipeline = CSSystematicJumpBetaPipeline()
model = CSSystematicJumpBetaRidgeModel()
