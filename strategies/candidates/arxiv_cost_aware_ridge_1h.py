"""Causal BTC/ETH hourly Ridge forecast with optional cost-aware position-change filter.

Independent adaptation of Bysik & Slepaczuk (arXiv:2606.00060): using the
existing frozen 4h label horizon, NOT a claim to reproduce their 1h XGBoost
results. Warden alone introduces the mandatory one-bar execution delay.
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Any

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from warden.contracts import EvaluationContract
from warden.leakage import purged_walk_forward_splits

FEATURES = ("return_1h", "return_6h", "return_24h", "return_168h",
            "volatility_24h", "range_frac", "volume_ratio")
SYMBOLS = ("BTCUSDT", "ETHUSDT")


class CostAwareRidgePipeline:
    """Only bars already completed at decision time enter each feature."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        df = df.sort("timestamp", "asset")
        logret = pl.col("close").log().diff().over("asset")
        return df.with_columns(
            return_1h=logret,
            return_6h=(pl.col("close") / pl.col("close").shift(6).over("asset")).log(),
            return_24h=(pl.col("close") / pl.col("close").shift(24).over("asset")).log(),
            return_168h=(pl.col("close") / pl.col("close").shift(168).over("asset")).log(),
            volatility_24h=logret.rolling_std(24).over("asset"),
            range_frac=(pl.col("high") - pl.col("low")) / pl.col("close"),
            volume_ratio=pl.col("volume") / (pl.col("volume").rolling_mean(168).over("asset") + 1e-8) - 1.0,
        )


def position_decision(
    forecast: float, current: float, cost: float, cost_aware: bool, cost_multiplier: float
) -> float:
    """Per-asset 0/1 target; filter only *changes*, never retroactively erase exposure."""
    if not np.isfinite(forecast):
        return current
    desired = 1.0 if forecast > 0.0 else 0.0
    if cost_aware and desired != current and abs(forecast) <= cost_multiplier * cost * abs(desired - current):
        return current
    return desired


def _aligned_feature(frame: pl.DataFrame, col: str, timestamps: list, assets: list[str]) -> np.ndarray:
    return frame.pivot(index="timestamp", on="asset", values=col).sort("timestamp").select(assets).to_numpy()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    *,
    ridge_alpha: float = 10.0,
    cost_aware: bool = True,
    cost_multiplier: float = 2.0,
    rebalance_every: int = 4,
    target_symbols: tuple[str, str] | list[str] = SYMBOLS,
    **kwargs: Any,
) -> dict[str, Any]:
    """4h forecast from fold-local Ridge; never fit on validation or protected holdout."""
    _ = kwargs
    if contract.frequency != "1h" or contract.execution_delay_bars < 1:
        raise ValueError("Requires hourly bars and a positive Warden execution delay")
    if rebalance_every < 1 or ridge_alpha < 0.0 or cost_multiplier <= 0.0:
        raise ValueError("Invalid frozen model/execution parameters")

    df = pipeline.compute(panel_df, contract)
    all_timestamps = sorted(df["timestamp"].unique().to_list())
    all_assets = sorted(df["asset"].unique().to_list())
    selected = list(target_symbols)
    if len(selected) != 2 or len(set(selected)) != 2 or not set(selected).issubset(set(all_assets)):
        raise ValueError(f"Expected explicit BTC/ETH mapping {selected}; observed {all_assets}")
    subset = df.filter(pl.col("asset").is_in(selected)).sort("timestamp", "asset")
    timestamps = sorted(subset["timestamp"].unique().to_list())
    n = len(timestamps)
    begin = bisect_left(timestamps, contract.val_start)
    end = bisect_left(timestamps, contract.val_end)
    if begin < 250 or end - begin < contract.n_folds * 30:
        raise ValueError("Insufficient pre-validation history or fold coverage")

    feature_mat = np.stack([_aligned_feature(subset, col, timestamps, selected) for col in FEATURES], axis=-1)
    opens = _aligned_feature(subset, "open", timestamps, selected)
    horizon, delay = contract.label_horizon_bars, contract.execution_delay_bars
    labels = np.full((n, 2), np.nan, dtype=float)
    t_last = n - horizon - delay
    if t_last > 0:
        # At time t choose target; Warden enters at Open[t+delay], exits h bars later.
        with np.errstate(divide="ignore", invalid="ignore"):
            labels[:t_last] = np.log(opens[delay + horizon:delay + horizon + t_last] /
                                     opens[delay:delay + t_last])
    forecasts = np.full((n, 2), np.nan, dtype=float)
    splits = purged_walk_forward_splits(
        n_timestamps=end, n_folds=contract.n_folds, min_train_timestamps=begin,
        label_horizon=horizon, embargo=contract.embargo_bars, execution_delay=delay,
    )
    for train_idx, test_idx in splits:
        x = feature_mat[train_idx].reshape(-1, len(FEATURES))
        y = labels[train_idx].reshape(-1)
        keep = np.isfinite(x).all(axis=1) & np.isfinite(y)
        if keep.sum() < 200:
            raise ValueError("Insufficient finite training observations")
        scaler = StandardScaler()
        train_x = scaler.fit_transform(x[keep])
        model = Ridge(alpha=ridge_alpha)
        model.fit(train_x, y[keep])
        x_test = feature_mat[test_idx].reshape(-1, len(FEATURES))
        ok = np.isfinite(x_test).all(axis=1)
        pred = np.full(len(x_test), np.nan, dtype=float)
        if ok.any():
            pred[ok] = model.predict(scaler.transform(x_test[ok]))
        forecasts[test_idx] = pred.reshape(len(test_idx), 2)

    positions = np.zeros((len(all_timestamps), len(all_assets)), dtype=float)
    mapping = {ts: i for i, ts in enumerate(all_timestamps)}
    indices = [all_assets.index(sym) for sym in selected]
    state = np.zeros(2, dtype=float)
    fee = contract.total_one_way_cost_decimal()

    for t in range(begin, end):
        if (t - begin) % rebalance_every == 0:
            for j in range(2):
                state[j] = position_decision(
                    forecasts[t, j], state[j], fee, cost_aware, cost_multiplier
                )
        pos_t = positions[mapping[timestamps[t]]]
        for j, idx in enumerate(indices):
            pos_t[idx] = state[j] / 2.0
    return {"positions": positions}


pipeline = CostAwareRidgePipeline()
