"""Causal pooled Ridge for liquidity-conditioned cross-sectional price pressure.

This is a predeclared, research-only four-hour 100-perpetual experiment. All
features are observed at bar t; executable OOS labels run Open[t+delay] to
Open[t+delay+horizon], and Warden applies the delay once to trading weights.
The research history includes related past crypto reversal/funding hypotheses.
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

FEATURES = (
    "relative_return_1h",
    "relative_return_6h",
    "relative_return_24h",
    "realized_volatility_24h",
    "volume_surprise_168h",
    "close_location_value",
)


class PricePressurePipeline:
    """Per-asset, timestamp-aligned features; no future bars or cohort ranking."""

    def compute(
        self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract
    ) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        df = df.sort("timestamp", "asset")
        c, ts = pl.col("close"), pl.col("timestamp")
        r1 = (c / c.shift(1).over("asset")).log()
        r6 = (c / c.shift(6).over("asset")).log()
        r24 = (c / c.shift(24).over("asset")).log()
        continuous_1h = (ts - ts.shift(1).over("asset")).dt.total_seconds() == 3600
        continuous_6h = (ts - ts.shift(6).over("asset")).dt.total_seconds() == 6 * 3600
        continuous_24h = (ts - ts.shift(24).over("asset")).dt.total_seconds() == 24 * 3600
        dollar_vol = c * pl.col("volume")
        return (
            df.with_columns(
                ret1=pl.when(continuous_1h).then(r1).otherwise(None),
                ret6=pl.when(continuous_6h).then(r6).otherwise(None),
                ret24=pl.when(continuous_24h).then(r24).otherwise(None),
                dollar_volume=dollar_vol,
                close_location_value=(
                    (2.0 * c - pl.col("high") - pl.col("low"))
                    / (pl.col("high") - pl.col("low") + 1e-10)
                ),
            )
            .with_columns(
                realized_volatility_24h=pl.col("ret1").rolling_std(24).over("asset"),
                liquidity_per_hour=pl.col("dollar_volume").rolling_mean(24).over("asset"),
                volume_surprise_168h=(
                    (pl.col("dollar_volume") + 1).log()
                    - (pl.col("dollar_volume").rolling_mean(168).over("asset") + 1).log()
                ),
                relative_return_1h=pl.col("ret1") - pl.col("ret1").mean().over("timestamp"),
                relative_return_6h=pl.col("ret6") - pl.col("ret6").mean().over("timestamp"),
                relative_return_24h=pl.col("ret24") - pl.col("ret24").mean().over("timestamp"),
            )
            .drop("ret1", "ret6", "ret24", "dollar_volume")
        )


def _piv(df: pl.DataFrame, col: str, assets: list[str]) -> np.ndarray:
    return df.pivot(index="timestamp", on="asset", values=col).sort("timestamp").select(assets).to_numpy()


def _forward_label(open_matrix: np.ndarray, delay: int, horizon: int) -> np.ndarray:
    """Only observed full paths are labeled; no fill across missing 1h candles."""
    if delay < 1 or horizon < 1:
        raise ValueError("Noncausal execution/holding horizon")
    n, m = open_matrix.shape
    result = np.full((n, m), np.nan)
    span = delay + horizon
    if n <= span:
        return result
    good = np.isfinite(open_matrix) & (open_matrix > 0)
    for offset in range(1, span + 1):
        # An end-of-exploratory gap must never be silently zero-filled.
        good[:n - offset] &= np.isfinite(open_matrix[offset:])
    complete = good[:n - span]
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.log(
            open_matrix[delay + horizon:n] / open_matrix[delay:n - horizon]
        )
    result[:n - span] = np.where(complete, ratio, np.nan)
    return result


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
    """Purged, fold-local Ridge + executable 4h OOS IC; never tune on OOS."""
    _ = kwargs
    if contract.frequency != "1h" or contract.execution_delay_bars < 1:
        raise ValueError("Hourly observed-price contract with next-bar delay required")
    if ridge_alpha <= 0 or rebalance_every < 1 or top_k < 2 or min_liquidity_usd <= 0:
        raise ValueError("Non-positive frozen parameters")
    df = pipeline.compute(panel_df, contract)
    stamps = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    t_count, n_assets = len(stamps), len(assets)
    start, stop = bisect_left(stamps, contract.val_start), bisect_left(stamps, contract.val_end)
    if start < 300 or stop - start < contract.n_folds * 24 or n_assets < 2 * top_k:
        raise ValueError("Missing research fold coverage or liquid universe")

    feature_mat = np.stack([_piv(df, field, assets) for field in FEATURES], axis=2)
    opens = _piv(df, "open", assets)
    liquid = _piv(df, "liquidity_per_hour", assets)
    label_mat = _forward_label(opens, contract.execution_delay_bars, contract.label_horizon_bars)
    forecasts = np.full((t_count, n_assets), np.nan, dtype=float)
    fold_sizes = []
    splits = purged_walk_forward_splits(
        n_timestamps=stop, n_folds=contract.n_folds, min_train_timestamps=start,
        label_horizon=contract.label_horizon_bars,
        execution_delay=contract.execution_delay_bars, embargo=contract.embargo_bars,
    )
    for train_idx, test_idx in splits:
        x_train = feature_mat[train_idx].reshape(-1, len(FEATURES))
        y_train = label_mat[train_idx].ravel()
        liq_train = liquid[train_idx].ravel()
        valid = np.isfinite(x_train).all(axis=1) & np.isfinite(y_train)
        valid &= np.isfinite(liq_train) & (liq_train >= min_liquidity_usd)
        if valid.sum() < 2000:
            raise ValueError("Insufficient valid PIT liquidity-filtered training examples")
        scaler = StandardScaler()
        x = scaler.fit_transform(x_train[valid])
        reg = Ridge(alpha=ridge_alpha)
        reg.fit(x, y_train[valid])
        test_x = feature_mat[test_idx].reshape(-1, len(FEATURES))
        test_liq = liquid[test_idx].ravel()
        allowed = np.isfinite(test_x).all(axis=1)
        allowed &= np.isfinite(test_liq) & (test_liq >= min_liquidity_usd)
        predictions = np.full(len(test_x), np.nan, dtype=float)
        if allowed.any():
            predictions[allowed] = reg.predict(scaler.transform(test_x[allowed]))
        forecasts[test_idx] = predictions.reshape(len(test_idx), n_assets)
        fold_sizes.append((int(valid.sum()), int(allowed.sum())))

    # An IC per validation hour: predict demeaned executable 4h relative returns
    # on eligible assets, with no training label crossing into the test fold.
    ic = []
    for i in range(start, stop):
        mask = np.isfinite(forecasts[i]) & np.isfinite(label_mat[i])
        if np.count_nonzero(mask) < 25:
            continue
        x, y = forecasts[i, mask], label_mat[i, mask]
        if np.std(x) < 1e-12 or np.std(y) < 1e-12:
            continue
        ic_value = float(spearmanr(x, y).statistic)
        if np.isfinite(ic_value):
            ic.append(ic_value)

    # Long/short fully scaled to 50/50 gross within the qualifying liquid set.
    # No subset resampling into the past; Warden delays all target positions once.
    positions = np.zeros((t_count, n_assets), dtype=float)
    for i in range(start, stop):
        if (i - start) % rebalance_every:
            positions[i] = positions[i - 1]
            continue
        allowed_idx = np.flatnonzero(np.isfinite(forecasts[i]))
        if len(allowed_idx) < 2 * top_k:
            positions[i] = positions[i - 1]
            continue
        order = allowed_idx[np.argsort(forecasts[i, allowed_idx])]
        target = np.zeros(n_assets)
        target[order[:top_k]] = -0.5 / top_k
        target[order[-top_k:]] = 0.5 / top_k
        positions[i] = target
    # Explicit model diagnostic is not required by Warden Stage 1 but supports
    # operational reviews without exposing future features.
    return {
        "positions": positions,
        "ic_series": np.asarray(ic),
        "n_folds": len(splits),
        "fold_samples": fold_sizes,
    }


pipeline = PricePressurePipeline()
