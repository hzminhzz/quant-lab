"""Rules-based 1-hour slow trend long/short strategy (AF30 Strategy 6).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 6 (Slow Trend Long Short / AF30), aligned with source spec:
- Fast EMA: 16, Slow EMA: 64
- Symmetric long/short trend: (ema_16 - ema_64) / close
- Normalization & scaling: spread divided by 0.01, capped at [-20, 20]
- Sizing: inverse volatility weighting based on 240-hour rolling return std, water-filled to 0.5 cap
- Trailing stop: 3.0x ATR-14 (symmetric)
- Rebalance frequency: 504 hours
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ts_common import capped_waterfall_weights

from warden.contracts import EvaluationContract


class SlowTrendLongShortPipeline:
    """Causal feature pipeline computing AF30 Strategy 6 slow trend long/short forecasts."""

    def __init__(
        self,
        ema_fast: int = 16,
        ema_slow: int = 64,
        atr_window: int = 14,
        vol_window: int = 240,
        scale: float = 0.01,
        cap: float = 20.0,
    ) -> None:
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
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
                ema_16=pl.col("close").ewm_mean(span=self.ema_fast).over("asset"),
                ema_64=pl.col("close").ewm_mean(span=self.ema_slow).over("asset"),
            )
            .with_columns(
                atr_14=pl.col("tr").ewm_mean(span=self.atr_window).over("asset"),
                vol_240=pl.col("hourly_ret").rolling_std(self.vol_window).shift(1).over("asset"),
                trend_raw=(pl.col("ema_16") - pl.col("ema_64")) / (pl.col("close") + 1e-12),
            )
            .with_columns(
                forecast=(pl.col("trend_raw") / self.scale).clip(-self.cap, self.cap),
                raw_inv_vol=1.0 / (pl.col("vol_240") + 1e-12),
            )
            .fill_null(0.0)
        )


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ema_fast: int = 16,
    ema_slow: int = 64,
    atr_window: int = 14,
    vol_window: int = 240,
    scale: float = 0.01,
    cap: float = 20.0,
    rebalance_bars: int = 504,
    stop_multiple: float = 3.0,
    per_instrument_cap: float = 0.5,
    gross_exposure: float = 1.0,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 6 long/short positions with symmetric trailing ATR-14 stop overlay."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = SlowTrendLongShortPipeline(
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        atr_window=atr_window,
        vol_window=vol_window,
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
    warmup = max(ema_slow, vol_window) + 1
    peak_prices = np.zeros(n_assets, dtype=float)
    trough_prices = np.full(n_assets, 1e12, dtype=float)

    if active_indices:
        act = np.asarray(active_indices, dtype=int)
        eps = 1e-6
        step = abs(rebalance_bars)
        # First rebalance bar at or after warmup (same modulo semantics as the original loop).
        start = warmup + (-warmup) % rebalance_bars
        for seg_start in range(start, n_bars, step):
            seg_end = min(seg_start + step, n_bars)
            length = seg_end - seg_start

            active_ivs = iv_mat[seg_start, act]
            weights = capped_waterfall_weights(active_ivs, cap=per_instrument_cap, gross=gross_exposure)
            entry = (fc_mat[seg_start, act] / cap) * weights

            hi = high_mat[seg_start:seg_end, act]
            lo = low_mat[seg_start:seg_end, act]
            cl = close_mat[seg_start:seg_end, act]
            atr_path = atr_mat[seg_start:seg_end, act]

            # Trail from the rebalance reseed: peak/trough at bar t are exactly the running
            # extremes of the held window, so the path-dependent recursion is a cumulative op.
            peak_path = np.maximum.accumulate(hi, axis=0)
            trough_path = np.minimum.accumulate(lo, axis=0)
            hit = (entry > eps) & (cl < peak_path - stop_multiple * atr_path)
            hit |= (entry < -eps) & (cl > trough_path + stop_multiple * atr_path)

            seg_pos = np.broadcast_to(entry, (length, entry.shape[0])).copy()
            if hit.any():
                first_hit = np.argmax(hit, axis=0)
                rows = np.arange(length)[:, None]
                seg_pos = np.where((rows >= first_hit[None, :]) & hit.any(axis=0)[None, :], 0.0, seg_pos)
            positions[seg_start:seg_end, act] = seg_pos
            peak_prices[act] = peak_path[-1]
            trough_prices[act] = trough_path[-1]

    return {"positions": positions, "forecast": fc_mat}


# Default pipeline instance for warden verify
pipeline = SlowTrendLongShortPipeline()
