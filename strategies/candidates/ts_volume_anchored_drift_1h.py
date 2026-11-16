"""Rules-based 1-hour volume-anchored post-spike drift strategy (HYP-2026-014)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class VolumeAnchoredDriftPipeline:
    """Causal feature pipeline computing rolling volume quantiles and post-spike returns."""

    def __init__(
        self,
        vol_window: int = 168,
        vol_quantile: float = 0.95,
        atr_window: int = 24,
    ) -> None:
        self.vol_window = vol_window
        self.vol_quantile = vol_quantile
        self.atr_window = atr_window

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        prev_close = pl.col("close").shift(1).over("asset")
        tr1 = pl.col("high") - pl.col("low")
        tr2 = (pl.col("high") - prev_close).abs()
        tr3 = (pl.col("low") - prev_close).abs()
        tr = pl.max_horizontal(tr1, tr2, tr3)

        bar_ret = (pl.col("close") - pl.col("open")) / (pl.col("open") + 1e-12)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                tr=tr,
                bar_ret=bar_ret,
                vol_q=pl.col("volume").rolling_quantile(self.vol_quantile, window_size=self.vol_window).over("asset"),
                vol_mean=pl.col("volume").rolling_mean(self.vol_window).over("asset"),
            )
            .with_columns(
                atr=pl.col("tr").rolling_mean(self.atr_window).over("asset"),
                is_vol_spike=(pl.col("volume") >= pl.col("vol_q")) & (pl.col("volume") > 0),
            )
        )


class VolumeAnchoredDriftStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing volume-anchored drift."""

    def __init__(
        self,
        vol_window: int = 168,
        vol_quantile: float = 0.95,
        ret_threshold: float = 0.005,
        holding_bars: int = 24,
    ) -> None:
        super().__init__()
        self.vol_window = vol_window
        self.vol_quantile = vol_quantile
        self.ret_threshold = ret_threshold
        self.holding_bars = holding_bars
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict, context: dict, broker: Any) -> None:
        self.bar_count += 1
        for asset, bar in data.items():
            pass


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    vol_window: int = 168,
    vol_quantile: float = 0.95,
    ret_threshold: float = 0.005,
    holding_bars: int = 24,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal multi-asset positions following post-volume-spike drift."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = VolumeAnchoredDriftPipeline(
        vol_window=vol_window,
        vol_quantile=vol_quantile,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    timestamps = sorted(feat_df["timestamp"].unique().to_list())
    n_bars = len(timestamps)
    n_assets = len(assets)

    spike_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="is_vol_spike")
        .sort("timestamp")
        .fill_null(False)
        .select(assets)
        .to_numpy()
    )
    ret_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="bar_ret")
        .sort("timestamp")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        major_names = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "BTC-PERP", "ETH-PERP", "SOL-PERP", "BNB-PERP"}
        active_indices = [i for i, sym in enumerate(assets) if sym in major_names]

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    warmup = vol_window + 1
    weight = 1.0 / len(active_indices)

    for a_idx in active_indices:
        current_pos = 0.0
        hold_bars = 0
        for t in range(warmup, n_bars):
            is_spike = bool(spike_mat[t, a_idx])
            bar_r = float(ret_mat[t, a_idx])

            if is_spike and abs(bar_r) >= ret_threshold:
                current_pos = weight if bar_r > 0 else -weight
                hold_bars = 0
            elif current_pos != 0.0:
                hold_bars += 1
                if hold_bars >= holding_bars:
                    current_pos = 0.0
                    hold_bars = 0

            positions[t, a_idx] = current_pos

    return {"positions": positions}


# Default pipeline singleton for warden verify
pipeline = VolumeAnchoredDriftPipeline()
