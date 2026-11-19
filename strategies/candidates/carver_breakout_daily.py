"""Robert Carver Advanced Futures Trading Strategies (2022) - Strategy 21: Breakout 80.

Primary source: Robert Carver, Advanced Futures Trading Strategies, Chapter 21 (pp. 389-402).
- Single-speed canonical breakout rule: 80-day channel lookback (p. 396).
- Continuous price-within-range forecast: 40 * (price - midpoint) / range (p. 391).
- Forecast smoothing: EWMA with span = horizon / 4 = 80 / 4 = 20 days (p. 391).
- Forecast scalar: 0.73 (p. 394, Table 91 for Breakout 80).
- Cash volatility position sizing: (forecast / 10) * (vol_target * weight / asset_vol) (pp. 70-74).
- Position buffering: 10% deadband buffer around optimal position B = 0.10 * weight (pp. 193-195).
- Protective stop: Trailing 3.0x ATR-14 overlay with active-session masking.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl


from warden.contracts import EvaluationContract


class BreakoutPipeline:
    """Causal feature pipeline computing Robert Carver Breakout 80 forecasts."""

    def __init__(
        self,
        channel_window: int = 80,
        smooth_span: int = 20,
        scalar: float = 0.73,
        atr_window: int = 14,
        vol_window: int = 60,
    ) -> None:
        self.channel_window = channel_window
        self.smooth_span = smooth_span
        self.scalar = scalar
        self.atr_window = atr_window
        self.vol_window = vol_window

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})

        prev_close = pl.col("close").shift(1).over("asset")
        tr = pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - prev_close).abs(),
            (pl.col("low") - prev_close).abs(),
        )

        # 1. Rolling extremes over horizon h shifted by 1 bar to strictly exclude current bar
        roll_max = pl.col("high").rolling_max(self.channel_window).shift(1).over("asset")
        roll_min = pl.col("low").rolling_min(self.channel_window).shift(1).over("asset")
        midpoint = (roll_max + roll_min) / 2.0
        range_span = pl.max_horizontal(roll_max - roll_min, pl.lit(1e-12))

        # 2. Raw forecast: 40 * (price - midpoint) / range in [-20, 20]
        raw_forecast = (40.0 * (pl.col("close") - midpoint) / range_span).clip(-20.0, 20.0)

        daily_ret = (pl.col("close") - prev_close) / (prev_close + 1e-12)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                tr=tr,
                daily_ret=daily_ret,
                raw_forecast=raw_forecast,
            )
            .with_columns(
                # 3. EWMA smoothing with span = h / 4 (p. 391)
                smoothed_forecast=pl.col("raw_forecast").ewm_mean(span=self.smooth_span).over("asset"),
                atr_14=pl.col("tr").ewm_mean(span=self.atr_window).over("asset"),
                vol_ann=pl.col("daily_ret").rolling_std(self.vol_window).shift(1).over("asset") * math.sqrt(contract.bars_per_year),
            )
            .with_columns(
                # 4. Scaled forecast with Carver Table 91 scalar (p. 394)
                forecast=(pl.col("smoothed_forecast") * self.scalar).clip(-20.0, 20.0),
            )
            .fill_null(0.0)
        )


pipeline = BreakoutPipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    channel_window: int = 80,
    smooth_span: int = 20,
    scalar: float = 0.73,
    vol_window: int = 60,
    atr_window: int = 14,
    vol_target: float = 0.20,
    per_instrument_cap: float = 0.5,
    stop_multiple: float = 3.0,
    rebalance_bars: int = 1,
    buffer_fraction: float = 0.10,
    warmup: int = 85,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal positions for Robert Carver Breakout 80 with buffering and trailing stops."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = BreakoutPipeline(
        channel_window=channel_window,
        smooth_span=smooth_span,
        scalar=scalar,
        atr_window=atr_window,
        vol_window=vol_window,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    timestamps = sorted(feat_df["timestamp"].unique().to_list())
    n_bars, n_assets = len(timestamps), len(assets)

    if n_bars == 0 or n_assets == 0:
        return {"positions": np.zeros((n_bars, n_assets), dtype=float), "forecast": np.zeros((n_bars, n_assets), dtype=float)}

    # Observation mask for asynchronous market sessions
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
    vol_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="vol_ann")
        .sort("timestamp")
        .fill_null(strategy="forward")
        .fill_null(strategy="backward")
        .fill_null(0.15)
        .select(assets)
        .to_numpy()
    )

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        active_indices = list(range(n_assets))

    if not active_indices:
        active_indices = list(range(n_assets))

    n_active = len(active_indices)
    w_base = 1.0 / max(n_active, 1)

    positions = np.zeros((n_bars, n_assets), dtype=float)

    effective_warmup = min(warmup, max(1, n_bars // 4)) if warmup >= n_bars else warmup

    act = np.asarray(active_indices, dtype=int)
    eps = 1e-6
    vol_floor = 0.05
    buffer_width = buffer_fraction * w_base
    if act.size:
        obs_cols = obs_mat[:, act]
        hi_cols = high_mat[:, act]
        lo_cols = low_mat[:, act]
        cl_cols = close_mat[:, act]
        fc_cols = fc_mat[:, act]
        vol_cols = vol_mat[:, act]
        atr_cols = atr_mat[:, act]
        held = np.zeros(act.size)
        pos = held.copy()
        peak = np.zeros(act.size)
        trough = np.full(act.size, 1e12)
        out = np.zeros((n_bars, act.size))
        for t in range(effective_warmup, n_bars):
            obs = obs_cols[t]
            hi_t = hi_cols[t]
            lo_t = lo_cols[t]
            held[:] = pos
            if t % rebalance_bars == 0:
                vol_i = np.maximum(vol_cols[t], vol_floor)

                # Cash volatility position sizing (Carver Strategy 2/4):
                # target_weight = (forecast / 10) * (vol_target * weight / vol_i)
                target = (fc_cols[t] / 10.0) * (vol_target * w_base / vol_i)
                target = np.clip(target, -per_instrument_cap, per_instrument_cap)

                # Buffering (Carver pp. 193-195): buffer width B = buffer_fraction * average_position
                buffered = np.where(np.abs(target - held) <= buffer_width, held, target)
                pos = np.where(obs, buffered, held)

                # Trail from the extreme reached while in position. Reset when flat and
                # reseed on a side change: a long->short flip leaves no flat bar, so
                # carrying the old peak into the short's stop (or the old trough into the
                # long's) would price the stop off a previous episode. Unobserved sessions
                # skip the whole sizing branch, so the trail state is left untouched there.
                long_both = obs & (held > eps) & (target > eps)
                short_both = obs & (held < -eps) & (target < -eps)
                reseed = obs & ~long_both & ~short_both
                peak = np.where(long_both, np.maximum(peak, hi_t), peak)
                peak = np.where(reseed, np.where(target > eps, hi_t, 0.0), peak)
                trough = np.where(short_both, np.minimum(trough, lo_t), trough)
                trough = np.where(reseed, np.where(target < -eps, lo_t, 1e12), trough)
            else:
                pos = held  # forward-filled row copy, same as positions[t] = positions[t - 1]

            atr = atr_cols[t]
            long_side = obs & (pos > eps)
            short_side = obs & (pos < -eps)
            if long_side.any():
                peak = np.where(long_side, np.maximum(peak, hi_t), peak)
                stopped = long_side & (cl_cols[t] < peak - stop_multiple * atr)
                pos = np.where(stopped, 0.0, pos)
            if short_side.any():
                trough = np.where(short_side, np.minimum(trough, lo_t), trough)
                stopped = short_side & (cl_cols[t] > trough + stop_multiple * atr)
                pos = np.where(stopped, 0.0, pos)
            out[t] = pos
        positions[:, act] = out

    return {"positions": positions, "forecast": fc_mat}
