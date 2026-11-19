"""Cross-sectional quadratic market beta curvature factor model with purged walk-forward CV (HYP-2026-040)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


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

    pipe = CSQuadraticMarketBetaPipeline()
    feat_df = pipe.compute(df, contract)

    f_b2_168 = feat_df.pivot(index="timestamp", on="asset", values="feat_neg_beta2_168").fill_null(0.0).select(assets).to_numpy()
    f_b2_336 = feat_df.pivot(index="timestamp", on="asset", values="feat_neg_beta2_336").fill_null(0.0).select(assets).to_numpy()
    f_b1_168 = feat_df.pivot(index="timestamp", on="asset", values="feat_low_beta1_168").fill_null(0.0).select(assets).to_numpy()

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
        x_tr = np.column_stack([f_b2_168[train_idx].ravel(), f_b2_336[train_idx].ravel(), f_b1_168[train_idx].ravel()])
        y_tr = forward_label[train_idx].ravel()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([f_b2_168[t], f_b2_336[t], f_b1_168[t]])
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


pipeline = CSQuadraticMarketBetaPipeline()
model = CSQuadraticMarketBetaRidgeModel()
