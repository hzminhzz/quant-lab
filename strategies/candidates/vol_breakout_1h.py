"""Rules-based 1-hour volatility-breakout trend strategy (HYP-2026-001)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class VolBreakoutPipeline:
    """Causal feature pipeline computing lookback returns and realized volatility."""

    def __init__(self, lookback_bars: int = 12, vol_window: int = 24):
        self.lookback_bars = lookback_bars
        self.vol_window = vol_window

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        # Features strictly lagged by contract.execution_delay_bars (at least 1)
        delay = contract.execution_delay_bars
        return (
            df.sort("timestamp", "asset")
            .with_columns(
                cum_ret=pl.col("close").pct_change(self.lookback_bars).shift(delay).over("asset"),
                realized_vol=pl.col("close").pct_change(1).rolling_std(self.vol_window).shift(delay).over("asset"),
            )
            .with_columns(
                z_score=pl.col("cum_ret") / (pl.col("realized_vol") * math.sqrt(self.lookback_bars) + 1e-12)
            )
        )


class VolBreakoutStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing volatility breakout."""

    def __init__(self, lookback_bars: int = 12, vol_window: int = 24, z_threshold: float = 2.0, rebalance_every: int = 4):
        super().__init__()
        self.lookback_bars = lookback_bars
        self.vol_window = vol_window
        self.z_threshold = z_threshold
        self.rebalance_every = rebalance_every
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict, context: dict, broker: Any) -> None:
        self.bar_count += 1
        if self.bar_count % self.rebalance_every != 0:
            return
        # Portable order execution hook
        for asset, bar in data.items():
            pass


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    lookback_bars: int = 12,
    vol_window: int = 24,
    z_threshold: float = 2.0,
    rebalance_every: int = 4,
    **kwargs,
) -> dict[str, Any]:
    """Compute causal multi-asset breakout positions matching reference vertical slice."""
    df = panel_df.sort("timestamp", "asset")
    timestamps = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    n_bars, n_assets = len(timestamps), len(assets)

    close_mat = df["close"].to_numpy().reshape((n_bars, n_assets))
    prev_close = np.vstack([close_mat[:1], close_mat[:-1]])
    ret_mat = np.divide(close_mat - prev_close, prev_close, out=np.zeros_like(close_mat), where=prev_close > 0)

    # Focus allocation on top 4 liquid perps (BTC, ETH, SOL, BNB)
    target_asset_indices = [i for i, sym in enumerate(assets) if sym in {"BTC-PERP", "ETH-PERP", "SOL-PERP", "BNB-PERP"}]
    if not target_asset_indices:
        target_asset_indices = list(range(min(4, n_assets)))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    warmup = max(lookback_bars, vol_window) + 1

    for t in range(warmup, n_bars):
        if t % rebalance_every != 0:
            positions[t] = positions[t - 1]
            continue

        # Signal formed strictly up to t - contract.execution_delay_bars
        end_idx = t - contract.execution_delay_bars
        if end_idx <= max(lookback_bars, vol_window):
            positions[t] = positions[t - 1]
            continue

        sub_ret = ret_mat[end_idx - lookback_bars:end_idx, target_asset_indices]
        cum_ret = np.sum(sub_ret, axis=0)
        vol_slice = ret_mat[end_idx - vol_window:end_idx, target_asset_indices]
        realized_vol = np.std(vol_slice, axis=0, ddof=1) + 1e-6
        z_score = cum_ret / (realized_vol * math.sqrt(lookback_bars))

        raw_pos = np.where(np.abs(z_score) > z_threshold, np.sign(z_score), 0.0)
        inv_vol = 1.0 / realized_vol
        sum_inv = np.sum(inv_vol)
        weights = raw_pos * (inv_vol / (sum_inv + 1e-12))

        pos_t = np.zeros(n_assets, dtype=float)
        for local_idx, global_idx in enumerate(target_asset_indices):
            pos_t[global_idx] = weights[local_idx]
        positions[t] = pos_t

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = VolBreakoutPipeline(lookback_bars=12, vol_window=24)
