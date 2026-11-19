"""Rules-based 1-hour combined carry and trend strategy (AF30 Strategy 11).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 11 (Combined Carry Trend / AF30), aligned with source spec:
- Fast EMA: 16, Slow EMA: 64
- trend_norm: ((ema_16 - ema_64) / close) / 0.01
- carry_24: 24-bar rolling sum of carry_rate / funding_rate shifted by 1 bar
- carry_norm: carry_24 / 0.0003
- combined_forecast: (trend_norm + carry_norm) / 2.0
- Forecast: (combined_forecast / 1.0).clip(-20.0, 20.0)
- Sizing: inverse volatility on vol_240, water-filled to 0.5 cap
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
    """Causal feature pipeline computing AF30 Strategy 11 combined carry and trend forecasts."""

    def __init__(
        self,
        ema_fast: int = 16,
        ema_slow: int = 64,
        carry_window: int = 24,
        carry_min_samples: int = 24,
        atr_window: int = 14,
        vol_window: int = 240,
        vol_min_samples: int = 60,
        trend_scale: float = 0.01,
        carry_scale: float = 0.0003,
        scale: float = 1.0,
        cap: float = 20.0,
    ) -> None:
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.carry_window = carry_window
        self.carry_min_samples = carry_min_samples
        self.atr_window = atr_window
        self.vol_window = vol_window
        self.vol_min_samples = vol_min_samples
        self.trend_scale = trend_scale
        self.carry_scale = carry_scale
        self.scale = scale
        self.cap = cap

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract | None = None) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})

        actual_carry_col = "carry_rate" if "carry_rate" in df.columns else ("funding_rate" if "funding_rate" in df.columns else None)
        if actual_carry_col is None:
            raise ValueError(
                "Missing required carry data: panel contains neither 'carry_rate' nor 'funding_rate' column"
            )
        carry_std = float(df[actual_carry_col].drop_nulls().std() or 0.0)
        if carry_std == 0.0:
            raise ValueError(
                f"Carry column '{actual_carry_col}' has zero variance (all zero values). "
                "Carry strategies require active financing data (e.g. CRYPTO_MAJORS_1H_V1 or futures term structure)."
            )
        carry_rate_col = pl.col(actual_carry_col)
        prev_close = pl.col("close").shift(1).over("asset")
        tr = pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - prev_close).abs(),
            (pl.col("low") - prev_close).abs(),
        )

        vol_min = min(self.vol_min_samples, self.vol_window)
        carry_min = min(self.carry_min_samples, self.carry_window)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                hourly_ret=(pl.col("close") - prev_close) / (prev_close + 1e-12),
                tr=tr,
                carry_rate=carry_rate_col.cast(pl.Float64).fill_null(0.0),
                ema_16=pl.col("close").ewm_mean(span=self.ema_fast).over("asset"),
                ema_64=pl.col("close").ewm_mean(span=self.ema_slow).over("asset"),
            )
            .with_columns(
                atr_14=pl.col("tr").ewm_mean(span=self.atr_window).over("asset"),
                vol_240=(
                    pl.col("hourly_ret")
                    .rolling_std(window_size=self.vol_window, min_samples=vol_min)
                    .shift(1)
                    .over("asset")
                ),
                carry_24=(
                    pl.col("carry_rate")
                    .rolling_sum(window_size=self.carry_window, min_samples=carry_min)
                    .shift(1)
                    .over("asset")
                ),
                trend_raw=(pl.col("ema_16") - pl.col("ema_64")) / (pl.col("close") + 1e-12),
            )
            .with_columns(
                trend_norm=pl.col("trend_raw") / self.trend_scale,
                carry_norm=pl.col("carry_24") / self.carry_scale,
            )
            .with_columns(
                combined_forecast=(pl.col("trend_norm") + pl.col("carry_norm")) / 2.0,
            )
            .with_columns(
                forecast=(pl.col("combined_forecast") / self.scale).clip(-self.cap, self.cap),
                raw_inv_vol=1.0 / (pl.col("vol_240") + 1e-12),
            )
            .fill_null(0.0)
        )


CombinedCarryTrendPipeline = FeaturePipeline


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract | None = None,
    ema_fast: int = 16,
    ema_slow: int = 64,
    carry_window: int = 24,
    carry_min_samples: int = 24,
    atr_window: int = 14,
    vol_window: int = 240,
    vol_min_samples: int = 60,
    trend_scale: float = 0.01,
    carry_scale: float = 0.0003,
    scale: float = 1.0,
    cap: float = 20.0,
    rebalance_bars: int = 168,
    stop_multiple: float = 3.0,
    per_instrument_cap: float = 0.5,
    gross_exposure: float = 1.0,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 11 combined carry and trend positions with symmetric trailing ATR-14 stop overlay."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = FeaturePipeline(
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        carry_window=carry_window,
        carry_min_samples=carry_min_samples,
        atr_window=atr_window,
        vol_window=vol_window,
        vol_min_samples=vol_min_samples,
        trend_scale=trend_scale,
        carry_scale=carry_scale,
        scale=scale,
        cap=cap,
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
    low_mat = (
        piv.pivot(index="timestamp", on="asset", values="low")
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
    warmup = max(vol_window, ema_slow, carry_window) + 1
    peak_prices = np.zeros(n_assets, dtype=float)
    trough_prices = np.full(n_assets, 1e12, dtype=float)

    for t in range(warmup, n_bars):
        positions[t] = positions[t - 1].copy()

        if t % rebalance_bars == 0:
            rebal_indices = [a_idx for a_idx in active_indices if obs_mat[t, a_idx]]
            if rebal_indices:
                active_ivs = iv_mat[t, rebal_indices]
                weights = capped_waterfall_weights(active_ivs, cap=per_instrument_cap, gross=gross_exposure)
                for idx, a_idx in enumerate(rebal_indices):
                    fc = fc_mat[t, a_idx]
                    positions[t, a_idx] = (fc / cap) * weights[idx]
                    peak_prices[a_idx] = high_mat[t, a_idx]
                    trough_prices[a_idx] = low_mat[t, a_idx]

        for a_idx in active_indices:
            if not obs_mat[t, a_idx]:
                continue
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

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = FeaturePipeline()
