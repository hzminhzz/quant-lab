"""Rules-based 1-hour buy & hold portfolio scaling strategy (AF30 Strategy 4).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 4 (Buy & Hold Portfolio Scaling / AF30), aligned with source spec:
- Long bias: 1.0 (constant long bias)
- vol_240: 240-hour rolling return std, shifted by 1 to prevent lookahead
- vol_scaled_long: 0.1 / (vol_240 + 1e-12)
- Normalization & scaling: bounded linear, scale 0.5, cap 20.0
  forecast = (vol_scaled_long / 0.5).clip(0.0, 20.0)
- Sizing: forecast-weighted allocation (weights proportional to forecast * raw_inv_vol), water-filled to 0.5 cap, gross exposure 1.0
- Trailing stop: 3.0x ATR-14 (long-only)
- Rebalance frequency: 168 hours
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ts_common import normalize_asset_column, trailing_atr_stop_long_only

from warden.contracts import EvaluationContract


class FeaturePipeline:
    """Causal feature pipeline computing AF30 Strategy 4 buy & hold portfolio scaling forecasts."""

    def __init__(
        self,
        vol_target: float = 0.1,
        scale: float = 0.5,
        cap: float = 20.0,
        vol_window: int = 240,
        vol_min_samples: int = 60,
        atr_window: int = 14,
    ) -> None:
        self.vol_target = vol_target
        self.scale = scale
        self.cap = cap
        self.vol_window = vol_window
        self.vol_min_samples = vol_min_samples
        self.atr_window = atr_window

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract | None = None) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        if "high" not in df.columns:
            df = df.with_columns(high=pl.col("close"))
        if "low" not in df.columns:
            df = df.with_columns(low=pl.col("close"))

        prev_close = pl.col("close").shift(1).over("asset")
        tr = pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - prev_close).abs(),
            (pl.col("low") - prev_close).abs(),
        )

        min_samples = min(self.vol_min_samples, self.vol_window)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                hourly_ret=(pl.col("close") - prev_close) / (prev_close + 1e-12),
                tr=tr,
            )
            .with_columns(
                atr_14=pl.col("tr").ewm_mean(span=self.atr_window).over("asset"),
                vol_240=(
                    pl.col("hourly_ret")
                    .rolling_std(window_size=self.vol_window, min_samples=min_samples)
                    .shift(1)
                    .over("asset")
                ),
            )
            .with_columns(
                vol_scaled_long=self.vol_target / (pl.col("vol_240") + 1e-12),
            )
            .with_columns(
                forecast=(pl.col("vol_scaled_long") / self.scale).clip(0.0, self.cap),
                raw_inv_vol=1.0 / (pl.col("vol_240") + 1e-12),
            )
            .fill_null(0.0)
        )


# Backward-compatible alias
BuyHoldPortfolioScalingPipeline = FeaturePipeline


def capped_waterfall_weights(raw_weights: np.ndarray, cap: float = 0.5, gross: float = 1.0) -> np.ndarray:
    """Enforce exact per-instrument risk budget cap by iterative water-filling."""
    n = len(raw_weights)
    if n == 0 or np.sum(raw_weights) <= 0:
        return np.zeros(n, dtype=float)
    w = (raw_weights / np.sum(raw_weights)) * gross
    frozen = np.zeros(n, dtype=bool)
    for _ in range(n):
        over = (w > cap + 1e-12) & ~frozen
        if not np.any(over):
            break
        w[over] = cap
        frozen[over] = True
        remaining = gross - cap * float(np.sum(frozen))
        free = ~frozen
        free_sum = float(np.sum(raw_weights[free]))
        if free_sum > 0 and remaining > 0:
            w[free] = (raw_weights[free] / free_sum) * remaining
        else:
            w[free] = 0.0
            break
    return w


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract | None = None,
    vol_target: float = 0.1,
    scale: float = 0.5,
    cap: float = 20.0,
    vol_window: int = 240,
    vol_min_samples: int = 60,
    atr_window: int = 14,
    rebalance_bars: int = 168,
    stop_multiple: float = 3.0,
    per_instrument_cap: float = 0.5,
    gross_exposure: float = 1.0,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 4 buy & hold portfolio scaling positions with trailing ATR-14 stop overlay."""
    _ = kwargs
    panel_df = normalize_asset_column(panel_df)

    pipe = FeaturePipeline(
        vol_target=vol_target,
        scale=scale,
        cap=cap,
        vol_window=vol_window,
        vol_min_samples=vol_min_samples,
        atr_window=atr_window,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    n_bars = len(feat_df["timestamp"].unique())
    n_assets = len(assets)

    if n_bars == 0 or n_assets == 0:
        return {"positions": np.zeros((n_bars, n_assets), dtype=float)}

    piv = feat_df.with_columns(_obs=pl.lit(1.0))

    fc_mat = (
        piv.pivot(index="timestamp", on="asset", values="forecast")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )
    iv_mat = (
        piv.pivot(index="timestamp", on="asset", values="raw_inv_vol")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )
    close_mat = (
        piv.pivot(index="timestamp", on="asset", values="close")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )
    high_mat = (
        piv.pivot(index="timestamp", on="asset", values="high")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )
    atr_mat = (
        piv.pivot(index="timestamp", on="asset", values="atr_14")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )
    obs_mat = (
        piv.pivot(index="timestamp", on="asset", values="_obs")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
        > 0.5
    )

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        active_indices = list(range(n_assets))

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    warmup = max(atr_window, min(vol_min_samples, vol_window)) + 1
    peak_prices = np.zeros(n_assets, dtype=float)

    for t in range(warmup, n_bars):
        positions[t] = positions[t - 1].copy()

        if t % rebalance_bars == 0:
            rebal_indices = [a_idx for a_idx in active_indices if obs_mat[t, a_idx]]
            if rebal_indices:
                raw_weights = np.array([fc_mat[t, a_idx] * iv_mat[t, a_idx] for a_idx in rebal_indices], dtype=float)
                if np.sum(raw_weights) > 0:
                    weights = capped_waterfall_weights(raw_weights, cap=per_instrument_cap, gross=gross_exposure)
                    for idx, a_idx in enumerate(rebal_indices):
                        positions[t, a_idx] = weights[idx]
                        if weights[idx] > 1e-6:
                            peak_prices[a_idx] = high_mat[t, a_idx]
                else:
                    for a_idx in rebal_indices:
                        positions[t, a_idx] = 0.0
        trailing_atr_stop_long_only(active_indices, positions, obs_mat, t, peak_prices, high_mat, stop_multiple, close_mat, atr_mat)

    return {"positions": positions, "forecast": fc_mat}


# Default pipeline instance for warden verify
pipeline = FeaturePipeline()
