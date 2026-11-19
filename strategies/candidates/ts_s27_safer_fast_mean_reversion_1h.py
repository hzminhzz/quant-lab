"""Rules-based 1-hour safer fast mean reversion strategy (AF30 Strategy 27).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 27 (Safer Fast Mean Reversion / AF30), aligned with source spec:
- Fast mean: 60-bar rolling mean of close shifted by 1
- Fast std: 60-bar rolling std of close shifted by 1
- Z-score: (close - mean_60) / (std_60 + 1e-12)
- Reversion: -zscore_60
- Volatility regime gating: vol_60 = 60-bar return std shifted by 1,
  vol_slow_60 = 720-bar rolling mean of vol_60 shifted by 1.
  reversion_gate = 1.0 if vol_60 < vol_slow_60 else 0.5
- Forecast: bounded linear scaling (reversion * reversion_gate / 1.0).clip(-20.0, 20.0)
- Sizing: inverse volatility weighting based on 60-hour rolling return std, water-filled to 0.5 cap
- Trailing stop: 3.0x ATR-14
- Rebalance frequency: 24 hours
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ts_common import capped_waterfall_weights, normalize_asset_column, trailing_atr_stop_symmetric

from warden.contracts import EvaluationContract


class FeaturePipeline:
    """Causal feature pipeline computing AF30 Strategy 27 safer fast mean reversion forecasts."""

    def __init__(
        self,
        mean_window: int = 60,
        std_window: int = 60,
        vol_window: int = 60,
        vol_slow_window: int = 720,
        atr_window: int = 14,
        gate_low: float = 0.5,
        gate_high: float = 1.0,
        scale: float = 1.0,
        cap: float = 20.0,
    ) -> None:
        self.mean_window = mean_window
        self.std_window = std_window
        self.vol_window = vol_window
        self.vol_slow_window = vol_slow_window
        self.atr_window = atr_window
        self.gate_low = gate_low
        self.gate_high = gate_high
        self.scale = scale
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
            )
            .with_columns(
                atr_14=pl.col("tr").ewm_mean(span=self.atr_window).over("asset"),
                vol_60=(
                    pl.col("hourly_ret")
                    .rolling_std(window_size=self.vol_window)
                    .shift(1)
                    .over("asset")
                ),
                mean_60=(
                    pl.col("close")
                    .rolling_mean(window_size=self.mean_window)
                    .shift(1)
                    .over("asset")
                ),
                std_60=(
                    pl.col("close")
                    .rolling_std(window_size=self.std_window)
                    .shift(1)
                    .over("asset")
                ),
            )
            .with_columns(
                vol_slow_60=(
                    pl.col("vol_60")
                    .rolling_mean(window_size=self.vol_slow_window)
                    .shift(1)
                    .over("asset")
                ),
                zscore_60=(pl.col("close") - pl.col("mean_60")) / (pl.col("std_60") + 1e-12),
            )
            .with_columns(
                reversion=-pl.col("zscore_60"),
                reversion_gate=pl.when(pl.col("vol_60") < pl.col("vol_slow_60"))
                .then(self.gate_high)
                .otherwise(self.gate_low),
            )
            .with_columns(
                forecast=(pl.col("reversion") * pl.col("reversion_gate") / self.scale).clip(-self.cap, self.cap),
                raw_inv_vol=1.0 / (pl.col("vol_60") + 1e-12),
            )
            .fill_null(0.0)
        )


# Backward-compatible alias
SaferFastMeanReversionPipeline = FeaturePipeline


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    mean_window: int = 60,
    std_window: int = 60,
    vol_window: int = 60,
    vol_slow_window: int = 720,
    atr_window: int = 14,
    gate_low: float = 0.5,
    gate_high: float = 1.0,
    scale: float = 1.0,
    cap: float = 20.0,
    rebalance_bars: int = 24,
    stop_multiple: float = 3.0,
    per_instrument_cap: float = 0.5,
    gross_exposure: float = 1.0,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 27 positions with symmetric trailing ATR-14 stop overlay."""
    _ = kwargs
    panel_df = normalize_asset_column(panel_df)

    pipe = FeaturePipeline(
        mean_window=mean_window,
        std_window=std_window,
        vol_window=vol_window,
        vol_slow_window=vol_slow_window,
        atr_window=atr_window,
        gate_low=gate_low,
        gate_high=gate_high,
        scale=scale,
        cap=cap,
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
    warmup = max(mean_window, vol_window + vol_slow_window) + 2
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
            positions[t] = positions[t - 1].copy()

        trailing_atr_stop_symmetric(active_indices, positions, atr_mat, t, peak_prices, high_mat, stop_multiple, close_mat, trough_prices, low_mat)

    return {"positions": positions, "forecast": fc_mat}


# Default pipeline instance for warden verify
pipeline = FeaturePipeline()
