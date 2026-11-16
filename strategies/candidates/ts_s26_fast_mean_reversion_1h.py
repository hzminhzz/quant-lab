"""Rules-based 1-hour fast mean reversion strategy (AF30 Strategy 26).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 26 (Fast Mean Reversion / AF30), aligned with source spec:
- Fast mean: 60-bar rolling mean of close shifted by 1
- Fast std: 60-bar rolling std of close shifted by 1
- Z-score: (close - mean_60) / (std_60 + 1e-12)
- Reversion: -zscore_60
- Forecast: bounded linear scaling (reversion / 1.0).clip(-20.0, 20.0)
- Sizing: inverse volatility weighting based on 60-hour rolling return std, water-filled to 0.5 cap
- Trailing stop: 3.0x ATR-14
- Rebalance frequency: 24 hours
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class FeaturePipeline:
    """Causal feature pipeline computing AF30 Strategy 26 fast mean reversion forecasts."""

    def __init__(
        self,
        mean_window: int = 60,
        std_window: int = 60,
        vol_window: int = 60,
        atr_window: int = 14,
        scale: float = 1.0,
        cap: float = 20.0,
    ) -> None:
        self.mean_window = mean_window
        self.std_window = std_window
        self.vol_window = vol_window
        self.atr_window = atr_window
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
                zscore_60=(pl.col("close") - pl.col("mean_60")) / (pl.col("std_60") + 1e-12),
            )
            .with_columns(
                reversion=-pl.col("zscore_60"),
            )
            .with_columns(
                forecast=(pl.col("reversion") / self.scale).clip(-self.cap, self.cap),
                raw_inv_vol=1.0 / (pl.col("vol_60") + 1e-12),
            )
            .fill_null(0.0)
        )


# Backward-compatible alias
FastMeanReversionPipeline = FeaturePipeline


class FastMeanReversionStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing AF30 Strategy 26 fast mean reversion."""

    def __init__(
        self,
        mean_window: int = 60,
        std_window: int = 60,
        vol_window: int = 60,
        atr_window: int = 14,
        scale: float = 1.0,
        cap: float = 20.0,
        rebalance_bars: int = 24,
        stop_multiple: float = 3.0,
        per_instrument_cap: float = 0.5,
        gross_exposure: float = 1.0,
    ) -> None:
        super().__init__()
        self.mean_window = mean_window
        self.std_window = std_window
        self.vol_window = vol_window
        self.atr_window = atr_window
        self.scale = scale
        self.cap = cap
        self.rebalance_bars = rebalance_bars
        self.stop_multiple = stop_multiple
        self.per_instrument_cap = per_instrument_cap
        self.gross_exposure = gross_exposure
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        self.bar_count += 1


def capped_waterfall_weights(raw_weights: np.ndarray, cap: float = 0.5, gross: float = 1.0) -> np.ndarray:
    """Enforce exact per-instrument risk budget cap by iterative water-filling."""
    n = len(raw_weights)
    if n == 0 or np.sum(raw_weights) <= 0:
        return np.full(n, gross / max(1, n))
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
    contract: EvaluationContract,
    mean_window: int = 60,
    std_window: int = 60,
    vol_window: int = 60,
    atr_window: int = 14,
    scale: float = 1.0,
    cap: float = 20.0,
    rebalance_bars: int = 24,
    stop_multiple: float = 3.0,
    per_instrument_cap: float = 0.5,
    gross_exposure: float = 1.0,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 26 positions with symmetric trailing ATR-14 stop overlay."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = FeaturePipeline(
        mean_window=mean_window,
        std_window=std_window,
        vol_window=vol_window,
        atr_window=atr_window,
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
    warmup = max(mean_window, vol_window) + 1
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

        # Apply trailing ATR stop symmetrically
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
