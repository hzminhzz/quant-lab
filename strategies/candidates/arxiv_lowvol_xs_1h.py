"""Train-only selected low-volatility anomaly with causal pooled Ridge folds.

Derivative of HYP-2026-031; the selection process (10 training-only
probes) is disclosed. No OOS tuning. Warden applies the mandatory one-bar
execution lag to target weights exactly once.
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from warden.contracts import EvaluationContract
from warden.leakage import purged_walk_forward_splits


def _piv(df: pl.DataFrame, col: str, assets: list[str]) -> np.ndarray:
    return df.pivot(index="timestamp", on="asset", values=col).sort("timestamp").select(assets).to_numpy()


def _forward_label(opens: np.ndarray, delay: int, horizon: int) -> np.ndarray:
    """Strict observed-open path; isolated Warden imports this module directly."""
    if delay < 1 or horizon < 1:
        raise ValueError("Noncausal execution or holding horizon")
    n, m = opens.shape
    result = np.full((n, m), np.nan)
    span = delay + horizon
    if n <= span:
        return result
    good = np.isfinite(opens) & (opens > 0)
    complete = good[:n - span].copy()
    for offset in range(1, span + 1):
        complete &= good[offset:n - span + offset]
    with np.errstate(invalid="ignore", divide="ignore"):
        forward = np.log(opens[span:n] / opens[delay:n - horizon])
    result[:n - span] = np.where(complete, forward, np.nan)
    return result


class LowVolatilityPipeline:
    def compute(
        self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract
    ) -> pl.DataFrame:
        _ = contract
        frame = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in frame.columns and "asset" not in frame.columns:
            frame = frame.rename({"symbol": "asset"})
        frame = frame.sort("timestamp", "asset")
        close, timestamp = pl.col("close"), pl.col("timestamp")
        contiguity = (
            (timestamp - timestamp.shift(1).over("asset")).dt.total_seconds() == 3600
        )
        logret = (close / close.shift(1).over("asset")).log()
        return (
            frame.with_columns(
                hour_return=pl.when(contiguity).then(logret).otherwise(None),
                dollar_volume=close * pl.col("volume"),
            )
            .with_columns(
                realized_volatility_24h=pl.col("hour_return").rolling_std(24).over("asset"),
                liquidity_per_hour=pl.col("dollar_volume").rolling_mean(24).over("asset"),
            )
            .drop("hour_return", "dollar_volume")
        )


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    *,
    ridge_alpha: float = 100.0,
    rebalance_every: int = 4,
    top_k: int = 10,
    min_liquidity_usd: float = 25000.0,
    **kwargs: Any,
) -> dict[str, Any]:
    _ = kwargs
    if contract.frequency != "1h" or contract.execution_delay_bars < 1:
        raise ValueError("Requires hourly bars and the frozen next-bar delay")
    if ridge_alpha <= 0 or rebalance_every < 1 or top_k < 2 or min_liquidity_usd <= 0:
        raise ValueError("Invalid preregistered parameters")
    feat = pipeline.compute(panel_df, contract)
    timestamps = sorted(feat["timestamp"].unique().to_list())
    assets = sorted(feat["asset"].unique().to_list())
    start, stop = bisect_left(timestamps, contract.val_start), bisect_left(timestamps, contract.val_end)
    if start < 250 or stop - start < contract.n_folds * 24 or len(assets) < top_k * 2:
        raise ValueError("Insufficient observed data for purged folds")

    vol = _piv(feat, "realized_volatility_24h", assets)
    liquidity = _piv(feat, "liquidity_per_hour", assets)
    opens = _piv(feat, "open", assets)
    targets = _forward_label(opens, contract.execution_delay_bars, contract.label_horizon_bars)
    target_count = np.isfinite(targets).sum(axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        market_mean = np.nansum(targets, axis=1, keepdims=True) / np.maximum(target_count, 1)
    labels = targets - market_mean
    forecasts = np.full_like(vol, np.nan)
    model_coefs = []

    folds = purged_walk_forward_splits(
        n_timestamps=stop,
        n_folds=contract.n_folds,
        min_train_timestamps=start,
        label_horizon=contract.label_horizon_bars,
        execution_delay=contract.execution_delay_bars,
        embargo=contract.embargo_bars,
    )
    for train, test in folds:
        x = vol[train].reshape(-1, 1)
        y = labels[train].reshape(-1)
        liq = liquidity[train].reshape(-1)
        valid = np.isfinite(x[:, 0]) & np.isfinite(y) & np.isfinite(liq) & (liq >= min_liquidity_usd)
        if valid.sum() < 2000:
            raise ValueError("Insufficient training examples after PIT liquidity exclusion")
        scaler = StandardScaler()
        x_fit = scaler.fit_transform(x[valid])
        model = Ridge(alpha=ridge_alpha, fit_intercept=True)
        model.fit(x_fit, y[valid])
        x_test = vol[test].reshape(-1, 1)
        test_liq = liquidity[test].reshape(-1)
        ok = np.isfinite(x_test[:, 0]) & np.isfinite(test_liq) & (test_liq >= min_liquidity_usd)
        pred = np.full(len(x_test), np.nan, dtype=float)
        pred[ok] = model.predict(scaler.transform(x_test[ok]))
        forecasts[test] = pred.reshape(len(test), len(assets))
        model_coefs.append(float(model.coef_[0]))

    ic_series = []
    for t in range(start, stop):
        valid = np.isfinite(forecasts[t]) & np.isfinite(labels[t])
        if valid.sum() < 25:
            continue
        x_t, y_t = forecasts[t, valid], labels[t, valid]
        if np.std(x_t) < 1e-12 or np.std(y_t) < 1e-12:
            continue
        ic = float(spearmanr(x_t, y_t).statistic)
        if np.isfinite(ic):
            ic_series.append(ic)

    positions = np.zeros_like(vol)
    state = np.zeros(len(assets), dtype=float)
    for t in range(start, stop):
        if (t - start) % rebalance_every == 0:
            eligible = np.flatnonzero(np.isfinite(forecasts[t]) & np.isfinite(opens[t]))
            if len(eligible) >= 2 * top_k:
                order = eligible[np.argsort(forecasts[t, eligible])]
                state = np.zeros(len(assets), dtype=float)
                state[order[:top_k]] = -0.5 / top_k
                state[order[-top_k:]] = 0.5 / top_k
            else:
                state = np.zeros(len(assets), dtype=float)
        positions[t] = np.where(np.isfinite(opens[t]) & (opens[t] > 0), state, 0.0)
    return {
        "positions": positions,
        "ic_series": np.asarray(ic_series),
        "oos_fold_ridge_coefficients": model_coefs,
    }


pipeline = LowVolatilityPipeline()
