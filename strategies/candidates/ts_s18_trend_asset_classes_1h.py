"""Rules-based 1-hour trend asset classes strategy (AF30 Strategy 18).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 18 (Trend Asset Classes / AF30), aligned with source spec:
- Pair 1: EMA 16 and 64 -> trend_raw = (ema_16 - ema_64) / close -> norm1 = trend_raw / 0.01
- Pair 2: EMA 32 and 128 -> trend_slow = (ema_32 - ema_128) / close -> norm2 = trend_slow / 0.02
- multi_horizon_trend: (norm1 + norm2) / 2.0
- Normalization & scaling: bounded linear with scale=1.0, capped at [-20, 20]
- Sizing: inverse volatility weighting based on 240-hour rolling return std, water-filled to 0.5 cap
- Trailing stop: 3.0x ATR-14 (symmetric)
- Rebalance frequency: 504 hours
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ts_common import capped_waterfall_weights, normalize_asset_column, trailing_atr_stop_symmetric

from warden.contracts import EvaluationContract


class TrendAssetClassesPipeline:
    """Causal feature pipeline computing AF30 Strategy 18 multi-horizon trend forecasts."""

    def __init__(
        self,
        ema1_fast: int = 16,
        ema1_slow: int = 64,
        ema2_fast: int = 32,
        ema2_slow: int = 128,
        atr_window: int = 14,
        vol_window: int = 240,
        min_samples: int = 60,
        scale1: float = 0.01,
        scale2: float = 0.02,
        scale: float = 1.0,
        cap: float = 20.0,
    ) -> None:
        self.ema1_fast = ema1_fast
        self.ema1_slow = ema1_slow
        self.ema2_fast = ema2_fast
        self.ema2_slow = ema2_slow
        self.atr_window = atr_window
        self.vol_window = vol_window
        self.min_samples = min_samples
        self.scale1 = scale1
        self.scale2 = scale2
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
                ema_16=pl.col("close").ewm_mean(span=self.ema1_fast).over("asset"),
                ema_64=pl.col("close").ewm_mean(span=self.ema1_slow).over("asset"),
                ema_32=pl.col("close").ewm_mean(span=self.ema2_fast).over("asset"),
                ema_128=pl.col("close").ewm_mean(span=self.ema2_slow).over("asset"),
            )
            .with_columns(
                atr_14=pl.col("tr").ewm_mean(span=self.atr_window).over("asset"),
                vol_240=pl.col("hourly_ret").rolling_std(self.vol_window, min_samples=self.min_samples).shift(1).over("asset"),
                trend_raw=(pl.col("ema_16") - pl.col("ema_64")) / (pl.col("close") + 1e-12),
                trend_slow=(pl.col("ema_32") - pl.col("ema_128")) / (pl.col("close") + 1e-12),
            )
            .with_columns(
                norm1=pl.col("trend_raw") / self.scale1,
                norm2=pl.col("trend_slow") / self.scale2,
            )
            .with_columns(
                multi_trend=(pl.col("norm1") + pl.col("norm2")) / 2.0,
            )
            .with_columns(
                forecast=(pl.col("multi_trend") / self.scale).clip(-self.cap, self.cap),
                raw_inv_vol=1.0 / (pl.col("vol_240") + 1e-12),
            )
            .fill_null(0.0)
        )


FeaturePipeline = TrendAssetClassesPipeline


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ema1_fast: int = 16,
    ema1_slow: int = 64,
    ema2_fast: int = 32,
    ema2_slow: int = 128,
    atr_window: int = 14,
    vol_window: int = 240,
    min_samples: int = 60,
    scale1: float = 0.01,
    scale2: float = 0.02,
    scale: float = 1.0,
    cap: float = 20.0,
    rebalance_bars: int = 504,
    stop_multiple: float = 3.0,
    per_instrument_cap: float = 0.5,
    gross_exposure: float = 1.0,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 18 multi-horizon trend positions with symmetric trailing ATR-14 stop overlay."""
    _ = kwargs
    panel_df = normalize_asset_column(panel_df)

    pipe = TrendAssetClassesPipeline(
        ema1_fast=ema1_fast,
        ema1_slow=ema1_slow,
        ema2_fast=ema2_fast,
        ema2_slow=ema2_slow,
        atr_window=atr_window,
        vol_window=vol_window,
        min_samples=min_samples,
        scale1=scale1,
        scale2=scale2,
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
    warmup = max(ema1_slow, ema2_slow, min_samples) + 1
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

        trailing_atr_stop_symmetric(active_indices, positions, atr_mat, t, peak_prices, high_mat, stop_multiple, close_mat, trough_prices, low_mat)

    return {"positions": positions, "forecast": fc_mat}


# Default pipeline instance for warden verify
pipeline = TrendAssetClassesPipeline()
