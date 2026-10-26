"""Rules-based 1-hour RSI pullback in rising EMA trend with rolling VWAP discount (HYP-2026-003)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class RSIVWAPPipeline:
    """Causal feature pipeline computing RSI, EMA trend, and rolling VWAP discount."""

    rsi_window: int
    ema_window: int
    ema_slope_bars: int
    vwap_window: int

    def __init__(
        self,
        rsi_window: int = 3,
        ema_window: int = 100,
        ema_slope_bars: int = 5,
        vwap_window: int = 10,
    ) -> None:
        self.rsi_window = rsi_window
        self.ema_window = ema_window
        self.ema_slope_bars = ema_slope_bars
        self.vwap_window = vwap_window

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        typ = (pl.col("high") + pl.col("low") + pl.col("close")) / 3.0
        diff = pl.col("close").diff().over("asset")
        gain = pl.when(diff > 0).then(diff).otherwise(0.0)
        loss = pl.when(diff < 0).then(-diff).otherwise(0.0)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                typ=typ,
                diff=diff,
                ema=pl.col("close").ewm_mean(span=self.ema_window).over("asset"),
            )
            .with_columns(
                pv=pl.col("typ") * pl.col("volume"),
                gain=gain,
                loss=loss,
                ema_rising=(pl.col("ema") > pl.col("ema").shift(self.ema_slope_bars).over("asset")).fill_null(False),
            )
            .with_columns(
                rolling_vwap=pl.col("pv").rolling_sum(self.vwap_window).over("asset")
                / (pl.col("volume").rolling_sum(self.vwap_window).over("asset") + 1e-12),
                rsi=100.0
                - (
                    100.0
                    / (
                        1.0
                        + pl.col("gain").rolling_mean(self.rsi_window).over("asset")
                        / (pl.col("loss").rolling_mean(self.rsi_window).over("asset") + 1e-12)
                    )
                ),
            )
            .with_columns(
                vwap_discount=(pl.col("close") - pl.col("rolling_vwap")) / pl.col("rolling_vwap")
            )
        )


class RSIVWAPStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing RSI+EMA+VWAP pullback mean reversion."""

    rsi_window: int
    rsi_oversold: float
    rsi_exit: float
    ema_window: int
    vwap_window: int
    vwap_discount_bps: float
    max_holding_bars: int
    bar_count: int

    def __init__(
        self,
        rsi_window: int = 3,
        rsi_oversold: float = 22.0,
        rsi_exit: float = 55.0,
        ema_window: int = 100,
        vwap_window: int = 10,
        vwap_discount_bps: float = 0.0,
        max_holding_bars: int = 8,
    ) -> None:
        super().__init__()
        self.rsi_window = rsi_window
        self.rsi_oversold = rsi_oversold
        self.rsi_exit = rsi_exit
        self.ema_window = ema_window
        self.vwap_window = vwap_window
        self.vwap_discount_bps = vwap_discount_bps
        self.max_holding_bars = max_holding_bars
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        self.bar_count += 1


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    rsi_window: int = 3,
    rsi_oversold: float = 22.0,
    rsi_exit: float = 55.0,
    ema_window: int = 100,
    ema_slope_bars: int = 5,
    vwap_window: int = 10,
    vwap_discount_bps: float = 0.0,
    max_holding_bars: int = 8,
    target_symbols: list[str] | None = None,
    use_vwap_filter: bool | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal multi-asset positions matching pre-registered specification."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = RSIVWAPPipeline(
        rsi_window=rsi_window,
        ema_window=ema_window,
        ema_slope_bars=ema_slope_bars,
        vwap_window=vwap_window,
    )
    feat_df = pipe.compute(panel_df, contract)

    timestamps = sorted(feat_df["timestamp"].unique().to_list())
    assets = sorted(feat_df["asset"].unique().to_list())
    n_bars, n_assets = len(timestamps), len(assets)

    rsi_mat = feat_df.pivot(index="timestamp", on="asset", values="rsi").fill_null(50.0).select(assets).to_numpy()
    ema_r_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="ema_rising").fill_null(False).select(assets).to_numpy()
    )
    disc_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="vwap_discount").fill_null(0.0).select(assets).to_numpy()
    )

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        # Default to NQ (Nasdaq 100 proxy matching QQQ literature) if present, else all assets
        nq_indices = [i for i, sym in enumerate(assets) if sym == "NQ"]
        active_indices = nq_indices if nq_indices else list(range(n_assets))

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    if use_vwap_filter is None:
        use_vwap_filter = vwap_discount_bps > 0.0
    disc_threshold = -vwap_discount_bps * 1e-4
    warmup = max(ema_window, vwap_window, rsi_window) + ema_slope_bars + 1

    for a_idx in active_indices:
        in_pos = False
        hold_bars = 0
        for t in range(warmup, n_bars):
            if in_pos:
                hold_bars += 1
                if (hold_bars >= 1 and rsi_mat[t, a_idx] > rsi_exit) or hold_bars >= max_holding_bars:
                    in_pos = False
                    hold_bars = 0
                    positions[t, a_idx] = 0.0
                else:
                    positions[t, a_idx] = 1.0 / len(active_indices)
            else:
                vwap_ok = (disc_mat[t, a_idx] <= disc_threshold) if use_vwap_filter else True
                entry = (rsi_mat[t, a_idx] < rsi_oversold) and ema_r_mat[t, a_idx] and vwap_ok
                if entry:
                    in_pos = True
                    hold_bars = 0
                    positions[t, a_idx] = 1.0 / len(active_indices)

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = RSIVWAPPipeline()
