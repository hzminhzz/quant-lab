"""Rules-based 1-hour Donchian channel breakout strategy (HYP-2026-068).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 21 (Breakout / AF30), aligned with source spec:
- Channel lookback: 20 bars
- Trailing stop: 3.0x ATR-14
- Scaling: percentage breakout divided by 0.02, capped at [-20, 20]
- Sizing: inverse volatility weighting based on 240-hour rolling std, water-filled to 0.5 cap
- Rebalance frequency: 24 hours (daily)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ts_common import capped_waterfall_weights_feasibility as capped_waterfall_weights

from warden.contracts import EvaluationContract


class BreakoutPipeline:
    """Causal feature pipeline computing AF30 Strategy 21 Donchian channel breakout forecasts."""

    def __init__(
        self,
        channel_window: int = 20,
        atr_window: int = 14,
        vol_window: int = 240,
        scale: float = 0.02,
        cap: float = 20.0,
    ) -> None:
        self.channel_window = channel_window
        self.atr_window = atr_window
        self.vol_window = vol_window
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
                upper_prev=pl.col("high").rolling_max(self.channel_window).shift(1).over("asset"),
                lower_prev=pl.col("low").rolling_min(self.channel_window).shift(1).over("asset"),
            )
            .with_columns(
                atr_14=pl.col("tr").ewm_mean(span=self.atr_window).over("asset"),
                vol_240=pl.col("hourly_ret").rolling_std(self.vol_window).shift(1).over("asset"),
                breakout_up=(pl.col("close") - pl.col("upper_prev")) / (pl.col("close") + 1e-12),
                breakout_down=(pl.col("close") - pl.col("lower_prev")) / (pl.col("close") + 1e-12),
            )
            .with_columns(
                breakout=pl.when(pl.col("close") > pl.col("upper_prev"))
                .then(pl.col("breakout_up"))
                .when(pl.col("close") < pl.col("lower_prev"))
                .then(pl.col("breakout_down"))
                .otherwise(0.0),
            )
            .with_columns(
                forecast=(pl.col("breakout") / self.scale).clip(-self.cap, self.cap),
                raw_inv_vol=1.0 / (pl.col("vol_240") + 1e-12),
            )
            .fill_null(0.0)
        )


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    channel_window: int = 20,
    atr_window: int = 14,
    vol_window: int = 240,
    scale: float = 0.02,
    cap: float = 20.0,
    rebalance_bars: int = 24,
    stop_multiple: float = 3.0,
    per_instrument_cap: float = 0.5,
    gross_exposure: float = 1.0,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 21 breakout positions with trailing ATR-14 stop overlay."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = BreakoutPipeline(
        channel_window=channel_window,
        atr_window=atr_window,
        vol_window=vol_window,
        scale=scale,
        cap=cap,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    n_bars = len(feat_df["timestamp"].unique())
    n_assets = len(assets)

    obs_mat = (
        feat_df.with_columns(_obs=pl.lit(True))
        .pivot(index="timestamp", on="asset", values="_obs")
        .fill_null(False)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )
    fc_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="forecast")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    iv_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="raw_inv_vol")
        .sort("timestamp")
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    close_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="close")
        .sort("timestamp")
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    high_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="high")
        .sort("timestamp")
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    low_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="low")
        .sort("timestamp")
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    atr_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="atr_14")
        .sort("timestamp")
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
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
    warmup = max(channel_window, vol_window) + 1

    peak_prices = np.zeros(n_assets, dtype=float)
    trough_prices = np.full(n_assets, 1e12, dtype=float)
    stopped_out = np.zeros(n_assets, dtype=bool)
    stopped_dir = np.zeros(n_assets, dtype=int)

    for t in range(warmup, n_bars):
        for a_idx in active_indices:
            if not obs_mat[t, a_idx]:
                continue
            if stopped_out[a_idx]:
                fc = fc_mat[t, a_idx]
                if fc == 0.0 or (stopped_dir[a_idx] > 0 and fc < 0.0) or (stopped_dir[a_idx] < 0 and fc > 0.0):
                    stopped_out[a_idx] = False
                    stopped_dir[a_idx] = 0

        if t % rebalance_bars == 0:
            active_ivs = iv_mat[t, active_indices]
            weights = capped_waterfall_weights(active_ivs, cap=per_instrument_cap, gross=gross_exposure)
            for idx, a_idx in enumerate(active_indices):
                held = positions[t - 1, a_idx]
                if not obs_mat[t, a_idx]:
                    positions[t, a_idx] = held
                else:
                    fc = fc_mat[t, a_idx]
                    if stopped_out[a_idx]:
                        positions[t, a_idx] = 0.0
                    elif fc != 0.0:
                        positions[t, a_idx] = (fc / cap) * weights[idx]
                        peak_prices[a_idx] = high_mat[t, a_idx]
                        trough_prices[a_idx] = low_mat[t, a_idx]
                    else:
                        positions[t, a_idx] = held
        else:
            positions[t] = positions[t - 1]

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
                    stopped_out[a_idx] = True
                    stopped_dir[a_idx] = 1
            elif pos < -1e-6:
                trough_prices[a_idx] = min(trough_prices[a_idx], low_mat[t, a_idx])
                stop_price = trough_prices[a_idx] + stop_multiple * atr
                if close_mat[t, a_idx] > stop_price:
                    positions[t, a_idx] = 0.0
                    stopped_out[a_idx] = True
                    stopped_dir[a_idx] = -1
    return {"positions": positions, "forecast": fc_mat}


# Default pipeline instance for warden verify
pipeline = BreakoutPipeline()
