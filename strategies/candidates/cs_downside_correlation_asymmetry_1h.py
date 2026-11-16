"""Cross-sectional downside correlation asymmetry factor model with purged walk-forward CV (HYP-2026-028)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSDownsideCorrelationAsymmetryPipeline:
    """Causal cross-sectional feature pipeline for downside correlation asymmetry (Ang & Chen 2002)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        # Extract benchmark return (BTCUSDT)
        btc = df.filter(pl.col("asset") == "BTCUSDT").select([
            pl.col("timestamp"),
            pl.col("close").pct_change().alias("ret_btc"),
        ])
        if btc.height == 0:
            btc = df.group_by("timestamp").agg(pl.col("close").pct_change().mean().alias("ret_btc"))

        df = df.join(btc, on="timestamp", how="left").sort("timestamp", "asset")
        df = df.with_columns(ret_1h=pl.col("close").pct_change().over("asset"))

        r_i = pl.col("ret_1h")
        r_m = pl.col("ret_btc")
        m_down = (r_m < 0).cast(pl.Float64)
        m_up = (r_m > 0).cast(pl.Float64)

        def calc_components(window: int):
            n_down = m_down.rolling_sum(window).over("asset").clip(lower_bound=10.0)
            n_up = m_up.rolling_sum(window).over("asset").clip(lower_bound=10.0)

            cov_down = (r_i * r_m * m_down).rolling_sum(window).over("asset") / n_down - (
                (r_i * m_down).rolling_sum(window).over("asset") / n_down
            ) * ((r_m * m_down).rolling_sum(window).over("asset") / n_down)
            var_i_down = (
                (r_i.pow(2) * m_down).rolling_sum(window).over("asset") / n_down
                - ((r_i * m_down).rolling_sum(window).over("asset") / n_down).pow(2)
            ).clip(lower_bound=1e-12)
            var_m_down = (
                (r_m.pow(2) * m_down).rolling_sum(window).over("asset") / n_down
                - ((r_m * m_down).rolling_sum(window).over("asset") / n_down).pow(2)
            ).clip(lower_bound=1e-12)
            rho_down = cov_down / (var_i_down.sqrt() * var_m_down.sqrt() + 1e-8)

            cov_up = (r_i * r_m * m_up).rolling_sum(window).over("asset") / n_up - (
                (r_i * m_up).rolling_sum(window).over("asset") / n_up
            ) * ((r_m * m_up).rolling_sum(window).over("asset") / n_up)
            var_i_up = (
                (r_i.pow(2) * m_up).rolling_sum(window).over("asset") / n_up
                - ((r_i * m_up).rolling_sum(window).over("asset") / n_up).pow(2)
            ).clip(lower_bound=1e-12)
            var_m_up = (
                (r_m.pow(2) * m_up).rolling_sum(window).over("asset") / n_up
                - ((r_m * m_up).rolling_sum(window).over("asset") / n_up).pow(2)
            ).clip(lower_bound=1e-12)
            rho_up = cov_up / (var_i_up.sqrt() * var_m_up.sqrt() + 1e-8)

            asym = (rho_down - rho_up).clip(-2.0, 2.0).shift(delay).over("asset")
            return asym, rho_down.clip(-1.0, 1.0).shift(delay).over("asset")

        asym_168, rho_down_168 = calc_components(168)
        asym_336, _ = calc_components(336)

        df = df.with_columns(
            asym_168=asym_168,
            asym_336=asym_336,
            rho_down_168=rho_down_168,
        )

        return df.with_columns(
            feat_asym_168=(pl.col("asym_168") - pl.col("asym_168").mean().over("timestamp"))
            / (pl.col("asym_168").std().over("timestamp") + 1e-8),
            feat_asym_336=(pl.col("asym_336") - pl.col("asym_336").mean().over("timestamp"))
            / (pl.col("asym_336").std().over("timestamp") + 1e-8),
            feat_down_corr=(pl.col("rho_down_168") - pl.col("rho_down_168").mean().over("timestamp"))
            / (pl.col("rho_down_168").std().over("timestamp") + 1e-8),
        )


class CSDownsideCorrelationAsymmetryRidgeModel:
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
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
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

    pipe = CSDownsideCorrelationAsymmetryPipeline()
    feat_df = pipe.compute(df, contract)

    f_asym168 = feat_df.pivot(index="timestamp", on="asset", values="feat_asym_168").fill_null(0.0).select(assets).to_numpy()
    f_asym336 = feat_df.pivot(index="timestamp", on="asset", values="feat_asym_336").fill_null(0.0).select(assets).to_numpy()
    f_down = feat_df.pivot(index="timestamp", on="asset", values="feat_down_corr").fill_null(0.0).select(assets).to_numpy()

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
        train_idx = train_idx[train_idx >= 336]
        if len(train_idx) < 10:
            continue
        x_tr = np.column_stack([f_asym168[train_idx].ravel(), f_asym336[train_idx].ravel(), f_down[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([f_asym168[t], f_asym336[t], f_down[t]])
            oos_preds[t] = model.predict(np.nan_to_num(x_test_t, nan=0.0))

    ic_list: list[float] = []
    for t in range(val_start_idx, val_end_idx - h):
        pred_t = oos_preds[t]
        if not np.any(np.isnan(pred_t)):
            std_p = float(np.std(pred_t))
            std_y = float(np.std(forward_label[t]))
            if std_p > 1e-8 and std_y > 1e-8:
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
        eligible = [i for i in range(n_assets) if assets[i] != "BTCUSDT" and not np.isnan(scores[i])]
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


pipeline = CSDownsideCorrelationAsymmetryPipeline()
model = CSDownsideCorrelationAsymmetryRidgeModel()
