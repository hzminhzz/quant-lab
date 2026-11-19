"""Rules-based 1-hour London Opening Range Breakout (ORB) strategy (HYP-2026-008)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class LondonORBPipeline:
    """Causal feature pipeline tracking Asian session range (00:00-08:00 UTC) and volume baseline."""

    def __init__(
        self,
        asian_start_hour: int = 0,
        asian_end_hour: int = 8,
        vol_baseline_bars: int = 24,
    ) -> None:
        self.asian_start_hour = asian_start_hour
        self.asian_end_hour = asian_end_hour
        self.vol_baseline_bars = vol_baseline_bars

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        # Extract UTC hour and date for session boundaries
        return (
            df.sort("timestamp", "asset")
            .with_columns(
                hour=pl.col("timestamp").dt.hour(),
                vol_baseline=pl.col("volume").rolling_mean(self.vol_baseline_bars, min_samples=1).over("asset"),
            )
            .with_columns(
                vol_ratio=pl.col("volume") / (pl.col("vol_baseline") + 1e-12),
            )
        )


class LondonORBStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing London Opening Range Breakout."""

    def __init__(
        self,
        asian_start_hour: int = 0,
        asian_end_hour: int = 8,
        entry_hour: int = 8,
        exit_hour: int = 16,
        volume_mult: float = 1.2,
    ) -> None:
        super().__init__()
        self.asian_start_hour = asian_start_hour
        self.asian_end_hour = asian_end_hour
        self.entry_hour = entry_hour
        self.exit_hour = exit_hour
        self.volume_mult = volume_mult
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        self.bar_count += 1


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    asian_start_hour: int = 0,
    asian_end_hour: int = 8,
    entry_hour: int = 8,
    exit_hour: int = 16,
    volume_mult: float = 1.2,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Causal London ORB: Asian range (00:00-08:00 UTC), enter on breakout with expanding volume, exit at 16:00 UTC."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = LondonORBPipeline(
        asian_start_hour=asian_start_hour,
        asian_end_hour=asian_end_hour,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    timestamps = sorted(feat_df["timestamp"].unique().to_list())
    n_bars = len(timestamps)
    n_assets = len(assets)

    high_mat = feat_df.pivot(index="timestamp", on="asset", values="high").fill_null(0.0).select(assets).to_numpy()
    low_mat = feat_df.pivot(index="timestamp", on="asset", values="low").fill_null(0.0).select(assets).to_numpy()
    close_mat = feat_df.pivot(index="timestamp", on="asset", values="close").fill_null(0.0).select(assets).to_numpy()
    vol_ratio_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="vol_ratio").fill_null(0.0).select(assets).to_numpy()
    )

    hours = np.array([ts.hour for ts in timestamps], dtype=int)

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        # Default to BTC and ETH if present, else all assets
        majors = [i for i, sym in enumerate(assets) if "BTC" in sym or "ETH" in sym]
        active_indices = majors if majors else list(range(n_assets))

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    weight = 1.0 / len(active_indices)

    # Track Asian range state sequentially per asset
    for a_idx in active_indices:
        asian_high = -1e9
        asian_low = 1e9
        current_pos = 0.0

        for t in range(n_bars):
            h = hours[t]
            bar_high = high_mat[t, a_idx]
            bar_low = low_mat[t, a_idx]
            bar_close = close_mat[t, a_idx]
            vol_ratio = vol_ratio_mat[t, a_idx]

            # In Asian session: accumulate high and low
            if asian_start_hour <= h < asian_end_hour:
                if h == asian_start_hour:
                    asian_high = bar_high
                    asian_low = bar_low
                else:
                    asian_high = max(asian_high, bar_high)
                    asian_low = min(asian_low, bar_low)

            # Exit condition: emit zero position early enough so that with execution_delay_bars,
            # the position executes the flat exit at exit_hour UTC (e.g. 16:00 UTC).
            emit_exit_hour = exit_hour - contract.execution_delay_bars
            if h >= emit_exit_hour or h < asian_end_hour:
                current_pos = 0.0
            elif (h == entry_hour or h == entry_hour + 1) and current_pos == 0.0 and asian_high > 0:
                # Check breakout condition with volume confirmation
                vol_ok = vol_ratio >= volume_mult
                if bar_close > asian_high and vol_ok:
                    current_pos = weight
                elif bar_close < asian_low and vol_ok:
                    current_pos = -weight

            positions[t, a_idx] = current_pos

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = LondonORBPipeline()
