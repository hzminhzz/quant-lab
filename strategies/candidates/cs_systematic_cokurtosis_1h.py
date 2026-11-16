"""Cross-sectional systematic cokurtosis tail contagion factor model with purged walk-forward CV (HYP-2026-051)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label, purged_walk_forward_splits


class CSSystematicCokurtosisPipeline:
    """Causal cross-sectional feature pipeline for fourth-order systematic cokurtosis."""

    def _calc_cokurtosis(self, r_i: pl.Expr, r_m: pl.Expr, window: int) -> pl.Expr:
        # Exact window-wise central co-moment: mean((r_i - mean(r_i)) * (r_m - mean(r_m))^3)
        mu_i = r_i.rolling_mean(window).over("asset")
        mu_m = r_m.rolling_mean(window).over("asset")

        mean_rm2 = r_m.pow(2).rolling_mean(window).over("asset")
        mean_rm3 = r_m.pow(3).rolling_mean(window).over("asset")
        mean_ri_rm = (r_i * r_m).rolling_mean(window).over("asset")
        mean_ri_rm2 = (r_i * r_m.pow(2)).rolling_mean(window).over("asset")
        mean_ri_rm3 = (r_i * r_m.pow(3)).rolling_mean(window).over("asset")

        # Expansion of E[(r_i - mu_i)(r_m - mu_m)^3]:
        # E[r_i*r_m^3] - 3*mu_m*E[r_i*r_m^2] + 3*mu_m^2*E[r_i*r_m] - mu_i*E[r_m^3] + 3*mu_i*mu_m*E[r_m^2] - 3*mu_i*mu_m^3
        num = (
            mean_ri_rm3
            - 3.0 * mu_m * mean_ri_rm2
            + 3.0 * mu_m.pow(2) * mean_ri_rm
            - mu_i * mean_rm3
            + 3.0 * mu_i * mu_m * mean_rm2
            - 3.0 * mu_i * mu_m.pow(3)
        )

        var_i = (r_i.pow(2).rolling_mean(window).over("asset") - mu_i.pow(2)).clip(lower_bound=0.0)
        std_i = var_i.sqrt()

        var_m = (mean_rm2 - mu_m.pow(2)).clip(lower_bound=0.0)
        std_m = var_m.sqrt()

        den = std_i * std_m.pow(3) + 1e-12
        return num / den

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})

        delay = contract.execution_delay_bars
        df = df.sort("timestamp", "asset")

        ret = pl.col("close").pct_change().over("asset").fill_null(0.0)
        df = df.with_columns(ret=ret)

        btc_sub = df.filter(pl.col("asset").str.contains("(?i)btc"))
        if btc_sub.height > 0:
            first_btc = btc_sub["asset"][0]
            mkt = df.filter(pl.col("asset") == first_btc).select([
                pl.col("timestamp"),
                pl.col("ret").alias("ret_mkt"),
            ])
            df = df.join(mkt, on="timestamp", how="left").sort("timestamp", "asset")
        else:
            mkt = df.group_by("timestamp").agg(pl.col("ret").mean().alias("ret_mkt"))
            df = df.join(mkt, on="timestamp", how="left").sort("timestamp", "asset")

        r_i = pl.col("ret").fill_null(0.0)
        r_m = pl.col("ret_mkt").fill_null(0.0)

        cokurt_720 = self._calc_cokurtosis(r_i, r_m, 720).shift(delay).over("asset")
        cokurt_168 = self._calc_cokurtosis(r_i, r_m, 168).shift(delay).over("asset")

        return (
            df.with_columns(
                cokurt_720=cokurt_720,
                cokurt_168=cokurt_168,
            )
            .with_columns(
                feat_cokurtosis_720=(pl.col("cokurt_720") - pl.col("cokurt_720").mean().over("timestamp"))
                / (pl.col("cokurt_720").std().over("timestamp") + 1e-8),
                feat_cokurtosis_168=(pl.col("cokurt_168") - pl.col("cokurt_168").mean().over("timestamp"))
                / (pl.col("cokurt_168").std().over("timestamp") + 1e-8),
                feat_cokurtosis_rank=pl.col("cokurt_720").rank().over("timestamp")
                / (pl.col("cokurt_720").count().over("timestamp") + 1e-8),
            )
        )


class CSSystematicCokurtosisRidgeModel:
    """WalkForwardModelProtocol implementation for Ridge regression."""

    def __init__(self, alpha: float = 10.0) -> None:
        self.alpha = alpha

    def fit_predict_fold(
        self,
        train_df: pl.DataFrame,
        val_features_df: pl.DataFrame,
        feature_cols: Sequence[str],
        label_col: str,
    ) -> pl.DataFrame:
        x_tr = train_df.select(feature_cols).to_numpy()
        y_tr = train_df[label_col].to_numpy()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=self.alpha, random_state=42)
        if np.any(valid):
            model.fit(x_tr[valid], y_tr[valid])
            preds = model.predict(np.nan_to_num(val_features_df.select(feature_cols).to_numpy(), nan=0.0))
        else:
            preds = np.zeros(len(val_features_df), dtype=float)
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

    pipe = CSSystematicCokurtosisPipeline()
    feat_df = pipe.compute(df, contract)

    f1 = feat_df.pivot(index="timestamp", on="asset", values="feat_cokurtosis_720").fill_null(0.0).select(assets).to_numpy()
    f2 = feat_df.pivot(index="timestamp", on="asset", values="feat_cokurtosis_168").fill_null(0.0).select(assets).to_numpy()
    f3 = feat_df.pivot(index="timestamp", on="asset", values="feat_cokurtosis_rank").fill_null(0.0).select(assets).to_numpy()

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
        if not np.any(valid):
            continue
        model = Ridge(alpha=alpha, random_state=42)
        model.fit(x_tr[valid], y_tr[valid])

        for t in test_idx:
            x_test_t = np.column_stack([f1[t], f2[t], f3[t]])
            oos_preds[t] = model.predict(np.nan_to_num(x_test_t, nan=0.0))

    ic_list: list[float] = []
    for t in range(val_start_idx, val_end_idx - h):
        pred_t = oos_preds[t]
        eligible_t = [i for i, a in enumerate(assets) if "btc" not in a.lower() and not np.isnan(pred_t[i])]
        if len(eligible_t) > 2:
            val_ic = float(np.corrcoef(pred_t[eligible_t], forward_label[t][eligible_t])[0, 1])
            if np.isfinite(val_ic):
                ic_list.append(val_ic)
    ic_arr = np.array(ic_list, dtype=float)

    positions = np.zeros((n_bars, n_assets), dtype=float)
    k = max(1, min(top_k, n_assets // 2))
    for t in range(val_start_idx, val_end_idx):
        if t % rebalance_every != 0:
            positions[t] = positions[t - 1]
            continue
        scores = oos_preds[t]
        eligible = [i for i in range(n_assets) if "btc" not in assets[i].lower() and not np.isnan(scores[i])]
        if len(eligible) < 2 * k:
            positions[t] = positions[t - 1]
            continue
        order = np.argsort(scores[eligible])
        w = np.zeros(n_assets, dtype=float)
        long_idx = [eligible[idx] for idx in order[-k:]]
        short_idx = [eligible[idx] for idx in order[:k]]
        w[long_idx] = 1.0 / (2.0 * k)
        w[short_idx] = -1.0 / (2.0 * k)
        positions[t] = w

    return {
        "positions": positions,
        "ic_series": ic_arr,
        "mean_ic": float(np.mean(ic_arr)) if len(ic_arr) else None,
    }


pipeline = CSSystematicCokurtosisPipeline()
model = CSSystematicCokurtosisRidgeModel()
