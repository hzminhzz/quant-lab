"""Rules-based 1-hour fast trend strength strategy (AF30 Strategy 8).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 8 (Fast Trend Trend Strength / AF30), aligned with source spec:
- Fast EMA: 8, Slow EMA: 32
- Normalized trend: ((ema_8 - ema_32) / close) / 0.01
- Trend strength: trend_fast_norm * abs(trend_fast_norm)
- Forecast: 20.0 * tanh(trend_fast_strength / 1.0)
- Sizing: inverse volatility weighting based on 240-hour rolling return std, water-filled to 0.5 cap
- Trailing stop: 3.0x ATR-14 (symmetric)
- Rebalance frequency: 168 hours
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ts_common import capped_waterfall_weights

from warden.contracts import EvaluationContract


class FeaturePipeline:
    """Causal feature pipeline computing AF30 Strategy 8 fast trend strength forecasts."""

    def __init__(
        self,
        ema_fast: int = 8,
        ema_slow: int = 32,
        atr_window: int = 14,
        vol_window: int = 240,
        vol_min_samples: int = 60,
        norm_scale: float = 0.01,
        tanh_scale: float = 1.0,
        cap: float = 20.0,
    ) -> None:
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.atr_window = atr_window
        self.vol_window = vol_window
        self.vol_min_samples = vol_min_samples
        self.norm_scale = norm_scale
        self.tanh_scale = tanh_scale
        self.cap = cap

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        prev_close = pl.col("close").shift(1).over("asset")
        tr = pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - prev_close).abs(),
            (pl.col("low") - prev_close).abs(),
        )

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                hourly_ret=(pl.col("close") - prev_close) / (prev_close + 1e-12),
                tr=tr,
                ema_8=pl.col("close").ewm_mean(span=self.ema_fast).over("asset"),
                ema_32=pl.col("close").ewm_mean(span=self.ema_slow).over("asset"),
            )
            .with_columns(
                atr_14=pl.col("tr").ewm_mean(span=self.atr_window).over("asset"),
                vol_240=(
                    pl.col("hourly_ret")
                    .rolling_std(window_size=self.vol_window, min_samples=self.vol_min_samples)
                    .shift(1)
                    .over("asset")
                ),
                trend_fast=(pl.col("ema_8") - pl.col("ema_32")) / (pl.col("close") + 1e-12),
            )
            .with_columns(
                trend_fast_norm=pl.col("trend_fast") / self.norm_scale,
            )
            .with_columns(
                trend_fast_strength=pl.col("trend_fast_norm") * pl.col("trend_fast_norm").abs(),
            )
            .with_columns(
                forecast=self.cap * (pl.col("trend_fast_strength") / self.tanh_scale).tanh(),
                raw_inv_vol=1.0 / (pl.col("vol_240") + 1e-12),
            )
            .fill_null(0.0)
        )


# Backward-compatible alias
FastTrendStrengthPipeline = FeaturePipeline


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ema_fast: int = 8,
    ema_slow: int = 32,
    atr_window: int = 14,
    vol_window: int = 240,
    vol_min_samples: int = 60,
    norm_scale: float = 0.01,
    tanh_scale: float = 1.0,
    cap: float = 20.0,
    rebalance_bars: int = 168,
    stop_multiple: float = 3.0,
    per_instrument_cap: float = 0.5,
    gross_exposure: float = 1.0,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 8 positions with symmetric trailing ATR-14 stop overlay."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = FeaturePipeline(
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        atr_window=atr_window,
        vol_window=vol_window,
        vol_min_samples=vol_min_samples,
        norm_scale=norm_scale,
        tanh_scale=tanh_scale,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    n_bars = len(feat_df["timestamp"].unique())
    n_assets = len(assets)

    fc_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="forecast")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    iv_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="raw_inv_vol")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    close_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="close")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    high_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="high")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    low_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="low")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    atr_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="atr_14")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        active_indices = list(range(n_assets))

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    warmup = max(ema_slow, vol_min_samples) + 1
    peak_prices = np.zeros(n_assets, dtype=float)
    trough_prices = np.full(n_assets, 1e12, dtype=float)

    for t in range(warmup, n_bars):
        if t % rebalance_bars == 0:
            active_ivs = iv_mat[t, active_indices]
            weights = capped_waterfall_weights(active_ivs, cap=per_instrument_cap, gross=gross_exposure)
            for idx, a_idx in enumerate(active_indices):
                fc = fc_mat[t, a_idx]
                positions[t, a_idx] = (fc / cap) * weights[idx]
                peak_prices[a_idx] = high_mat[t, a_idx]
                trough_prices[a_idx] = low_mat[t, a_idx]
        else:
            positions[t] = positions[t - 1]

        for a_idx in active_indices:
            pos = positions[t, a_idx]
            atr = atr_mat[t, a_idx]
            if pos > 1e-6:
                peak_prices[a_idx] = max(peak_prices[a_idx], high_mat[t, a_idx])
                stop_price = peak_prices[a_idx] - stop_multiple * atr
                if close_mat[t, a_idx] < stop_price:
                    positions[t, a_idx] = 0.0
            elif pos < -1e-6:
                trough_prices[a_idx] = min(trough_prices[a_idx], low_mat[t, a_idx])
                stop_price = trough_prices[a_idx] + stop_multiple * atr
                if close_mat[t, a_idx] > stop_price:
                    positions[t, a_idx] = 0.0

    return {"positions": positions, "forecast": fc_mat}


# Default pipeline instance for warden verify
pipeline = FeaturePipeline()
