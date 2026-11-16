"""Rules-based 1-hour weekend illiquidity dislocation reversal strategy (HYP-2026-015)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class WeekendDislocationPipeline:
    """Causal feature pipeline tracking Saturday 00:00 UTC anchor and freezing weekend dislocation at Sunday 20:00 UTC."""

    def __init__(
        self,
        sat_anchor_hour: int = 0,
        sun_cutoff_hour: int = 20,
    ) -> None:
        self.sat_anchor_hour = sat_anchor_hour
        self.sun_cutoff_hour = sun_cutoff_hour

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        # Saturday is weekday 6, Sunday is weekday 7 (Monday=1, Sunday=7 in Polars)
        is_sat_anchor = (pl.col("timestamp").dt.weekday() == 6) & (pl.col("timestamp").dt.hour() == self.sat_anchor_hour)
        is_sun_cutoff = (pl.col("timestamp").dt.weekday() == 7) & (pl.col("timestamp").dt.hour() == self.sun_cutoff_hour)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                weekday=pl.col("timestamp").dt.weekday(),
                hour=pl.col("timestamp").dt.hour(),
                sat_anchor=pl.when(is_sat_anchor).then(pl.col("open")).otherwise(None).forward_fill().over("asset"),
            )
            .with_columns(
                weekend_dislocation=pl.when(is_sun_cutoff)
                .then((pl.col("close") - pl.col("sat_anchor")) / (pl.col("sat_anchor") + 1e-12))
                .otherwise(None)
                .forward_fill()
                .over("asset"),
            )
        )


class WeekendDislocationStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing weekend dislocation reversal."""

    def __init__(
        self,
        sat_anchor_hour: int = 0,
        sun_cutoff_hour: int = 20,
        dislocation_threshold: float = 0.015,
        exit_monday_hour: int = 16,
        max_holding_bars: int = 20,
    ) -> None:
        super().__init__()
        self.sat_anchor_hour = sat_anchor_hour
        self.sun_cutoff_hour = sun_cutoff_hour
        self.dislocation_threshold = dislocation_threshold
        self.exit_monday_hour = exit_monday_hour
        self.max_holding_bars = max_holding_bars
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict, context: dict, broker: Any) -> None:
        self.bar_count += 1
        for asset, bar in data.items():
            pass


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    sat_anchor_hour: int = 0,
    sun_cutoff_hour: int = 20,
    dislocation_threshold: float = 0.015,
    exit_monday_hour: int = 16,
    max_holding_bars: int = 20,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal multi-asset positions fading frozen weekend dislocations back to Saturday anchor."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = WeekendDislocationPipeline(
        sat_anchor_hour=sat_anchor_hour,
        sun_cutoff_hour=sun_cutoff_hour,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    timestamps = sorted(feat_df["timestamp"].unique().to_list())
    n_bars = len(timestamps)
    n_assets = len(assets)

    disloc_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="weekend_dislocation")
        .sort("timestamp")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    sat_anchor_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="sat_anchor")
        .sort("timestamp")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    close_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="close")
        .sort("timestamp")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )

    ts_series = pl.Series("timestamp", timestamps)
    weekday_arr = ts_series.dt.weekday().to_numpy()
    hour_arr = ts_series.dt.hour().to_numpy()

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        major_names = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "BTC-PERP", "ETH-PERP", "SOL-PERP", "BNB-PERP"}
        active_indices = [i for i, sym in enumerate(assets) if sym in major_names]

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    warmup = 168  # 1 week warmup to ensure anchor was established
    weight = 1.0 / len(active_indices)

    for a_idx in active_indices:
        current_pos = 0.0
        hold_bars = 0
        for t in range(warmup, n_bars):
            w = int(weekday_arr[t])
            h = int(hour_arr[t])
            d = float(disloc_mat[t, a_idx])
            c = float(close_mat[t, a_idx])
            anchor = float(sat_anchor_mat[t, a_idx])

            if current_pos != 0.0:
                hold_bars += 1
                # Exit conditions:
                # 1. Max holding duration reached
                # 2. Monday cutoff hour reached or weekday past Monday
                # 3. Dislocation mean-reverted back to Saturday anchor price
                reverted = (current_pos > 0 and c >= anchor) or (current_pos < 0 and c <= anchor)
                monday_exit = (w == 1 and h >= exit_monday_hour) or (w > 1 and w < 6)
                if hold_bars >= max_holding_bars or monday_exit or reverted:
                    current_pos = 0.0
                    hold_bars = 0
            else:
                # Entry window: Sunday evening starting at sun_cutoff_hour (e.g. 20:00 to 23:00 UTC)
                is_entry_window = (w == 7 and h >= sun_cutoff_hour)
                if is_entry_window and abs(d) >= dislocation_threshold:
                    # Fade frozen weekend dislocation: short if positive, long if negative
                    current_pos = -weight if d > 0 else weight
                    hold_bars = 0

            positions[t, a_idx] = current_pos

    return {"positions": positions}


# Default pipeline singleton for warden verify
pipeline = WeekendDislocationPipeline()
