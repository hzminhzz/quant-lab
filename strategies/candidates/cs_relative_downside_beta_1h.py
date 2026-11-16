"""Cross-sectional relative downside beta factor model with purged walk-forward CV (HYP-2026-046)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSRelativeDownsideBetaPipeline:
    """Causal cross-sectional feature pipeline for relative downside beta (beta^- - beta)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        log_ret = (pl.col("close") / pl.max_horizontal(pl.col("close").shift(1), pl.lit(1e-8))).log().over("asset")
        df = df.with_columns(ret_1h=log_ret)

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

        is_down = pl.when(r_m < 0.0).then(1.0).otherwise(0.0)

        df = df.with_columns(
            r_i=r_i,
            r_m=r_m,
            r_i_m=r_i * r_m,
            r_m_sq=r_m ** 2,
            r_i_m_down=r_i * r_m * is_down,
            r_m_sq_down=(r_m ** 2) * is_down,
        )

        new_cols: list[pl.Expr] = []
        for w in [720, 336]:
            mean_ri = pl.col("r_i").rolling_mean(w, min_samples=20).over("asset")
            mean_rm = pl.col("r_m").rolling_mean(w, min_samples=20).over("asset")
            cov_im = pl.col("r_i_m").rolling_mean(w, min_samples=20).over("asset") - mean_ri * mean_rm
            var_m = pl.col("r_m_sq").rolling_mean(w, min_samples=20).over("asset") - mean_rm ** 2
            beta_std = cov_im / pl.max_horizontal(var_m, pl.lit(1e-8))

            sum_down_im = pl.col("r_i_m_down").rolling_sum(w, min_samples=20).over("asset")
            sum_down_m2 = pl.col("r_m_sq_down").rolling_sum(w, min_samples=20).over("asset")
            beta_down = sum_down_im / pl.max_horizontal(sum_down_m2, pl.lit(1e-8))

            rel_down_beta = beta_down - beta_std

            new_cols.append(rel_down_beta.alias(f"rel_down_beta_{w}"))
            new_cols.append(beta_down.alias(f"beta_down_{w}"))

        df = df.with_columns(new_cols)

        return df.with_columns(
            feat_rel_downside_beta_720=(pl.col("rel_down_beta_720") - pl.col("rel_down_beta_720").mean().over("timestamp"))
            / (pl.col("rel_down_beta_720").std().over("timestamp") + 1e-8),
            feat_rel_downside_beta_336=(pl.col("rel_down_beta_336") - pl.col("rel_down_beta_336").mean().over("timestamp"))
            / (pl.col("rel_down_beta_336").std().over("timestamp") + 1e-8),
            feat_downside_beta_720=(pl.col("beta_down_720") - pl.col("beta_down_720").mean().over("timestamp"))
            / (pl.col("beta_down_720").std().over("timestamp") + 1e-8),
        )


class CSRelativeDownsideBetaRidgeModel:
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
    rebalance_every: int = 4,
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

    pipe = CSRelativeDownsideBetaPipeline()
    feat_df = pipe.compute(df, contract)

    f1 = feat_df.pivot(index="timestamp", on="asset", values="feat_rel_downside_beta_720").fill_null(0.0).select(assets).to_numpy()
    f2 = feat_df.pivot(index="timestamp", on="asset", values="feat_rel_downside_beta_336").fill_null(0.0).select(assets).to_numpy()
    f3 = feat_df.pivot(index="timestamp", on="asset", values="feat_downside_beta_720").fill_null(0.0).select(assets).to_numpy()

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


pipeline = CSRelativeDownsideBetaPipeline()
model = CSRelativeDownsideBetaRidgeModel()
