"""Rules-based 1-hour US session intraday momentum surge strategy (HYP-2026-011)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class USSessionMomentumPipeline:
    """Causal feature pipeline computing rolling volume baseline and intraday bar return."""

    def __init__(self, vol_baseline_bars: int = 24) -> None:
        self.vol_baseline_bars = vol_baseline_bars

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        return (
            df.sort("timestamp", "asset")
            .with_columns(
                vol_baseline=pl.col("volume").rolling_mean(self.vol_baseline_bars).over("asset"),
                bar_ret=(pl.col("close") - pl.col("open")) / (pl.col("open") + 1e-12),
            )
            .with_columns(
                vol_ratio=pl.col("volume") / (pl.col("vol_baseline") + 1e-12),
            )
        )


class USSessionMomentumStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing US market open intraday momentum."""

    def __init__(
        self,
        open_hour: int = 13,
        close_hour: int = 21,
        vol_baseline_bars: int = 24,
        volume_mult: float = 1.5,
    ) -> None:
        super().__init__()
        self.open_hour = open_hour
        self.close_hour = close_hour
        self.vol_baseline_bars = vol_baseline_bars
        self.volume_mult = volume_mult
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        self.bar_count += 1


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    open_hour: int = 13,
    close_hour: int = 21,
    vol_baseline_bars: int = 24,
    volume_mult: float = 1.5,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Follow the direction of 13:00 UTC bar if volume exceeds 1.5x rolling average; hold until 21:00 UTC."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = USSessionMomentumPipeline(vol_baseline_bars=vol_baseline_bars)
    feat_df = pipe.compute(panel_df, contract)

    timestamps = sorted(feat_df["timestamp"].unique().to_list())
    assets = sorted(feat_df["asset"].unique().to_list())
    n_bars = len(timestamps)
    n_assets = len(assets)

    vol_ratio_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="vol_ratio")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    ret_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="bar_ret")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )

    hours = np.array([ts.hour for ts in timestamps], dtype=int)

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        majors = [i for i, sym in enumerate(assets) if "BTC" in sym or "ETH" in sym]
        active_indices = majors if majors else list(range(n_assets))

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    weight = 1.0 / len(active_indices)
    delay = getattr(contract, "execution_delay_bars", 1)
    emit_exit_hour = close_hour - delay

    for a_idx in active_indices:
        current_pos = 0.0
        for t in range(n_bars):
            h = hours[t]
            # Exit session window: emit 0 so that at close_hour execution the position is flat
            if h >= emit_exit_hour or h < open_hour:
                current_pos = 0.0
            elif h == open_hour:
                vol_ok = vol_ratio_mat[t, a_idx] >= volume_mult
                ret_val = ret_mat[t, a_idx]
                if vol_ok and ret_val > 0:
                    current_pos = weight
                elif vol_ok and ret_val < 0:
                    current_pos = -weight
                else:
                    current_pos = 0.0

            positions[t, a_idx] = current_pos

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = USSessionMomentumPipeline()
