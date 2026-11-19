"""Rules-based 1-hour post-liquidation cascade mean-reversal strategy (HYP-2026-007)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class LiquidationReversalPipeline:
    """Causal feature pipeline detecting extreme volume spikes and lower Bollinger penetration."""

    def __init__(
        self,
        bollinger_window: int = 24,
        bollinger_k: float = 2.5,
        volume_lookback: int = 168,
        volume_quantile: float | None = None,
    ) -> None:
        self.bollinger_window = bollinger_window
        self.bollinger_k = bollinger_k
        self.volume_lookback = volume_lookback
        self.volume_quantile = volume_quantile

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        cols = [
            pl.col("close").rolling_mean(self.bollinger_window).over("asset").alias("rolling_mean"),
            pl.col("close").rolling_std(self.bollinger_window).over("asset").alias("rolling_std"),
            pl.col("volume").rolling_mean(self.volume_lookback).over("asset").alias("vol_baseline"),
        ]
        if self.volume_quantile is not None:
            cols.append(
                pl.col("volume").rolling_quantile(self.volume_quantile, window_size=self.volume_lookback).over("asset").alias("vol_quantile")
            )
        return (
            df.sort("timestamp", "asset")
            .with_columns(cols)
            .with_columns(
                lower_bb=pl.col("rolling_mean") - self.bollinger_k * pl.col("rolling_std"),
                vol_ratio=pl.col("volume") / (pl.col("vol_baseline") + 1e-12),
            )
            .with_columns(
                bb_penetration=(pl.col("close") < pl.col("lower_bb")).fill_null(False),
            )
        )


class LiquidationReversalStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing liquidation cascade mean reversion."""

    def __init__(
        self,
        bollinger_window: int = 24,
        bollinger_k: float = 2.5,
        volume_lookback: int = 168,
        volume_mult: float = 2.0,
        holding_bars: int = 8,
        vol_mult: float | None = None,
        volume_quantile: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.bollinger_window = bollinger_window
        self.bollinger_k = bollinger_k
        self.volume_lookback = volume_lookback
        self.volume_mult = vol_mult if vol_mult is not None else volume_mult
        self.volume_quantile = volume_quantile
        self.holding_bars = holding_bars
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        self.bar_count += 1


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    bollinger_window: int = 24,
    bollinger_k: float = 2.5,
    volume_lookback: int = 168,
    volume_mult: float = 2.0,
    holding_bars: int = 8,
    vol_mult: float | None = None,
    volume_quantile: float | None = None,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Identify extreme volume spikes with drops below rolling lower Bollinger band; take long positions for 6-12 bars."""
    effective_vol_mult = vol_mult if vol_mult is not None else kwargs.get("vol_mult", volume_mult)
    effective_vol_q = volume_quantile if volume_quantile is not None else kwargs.get("volume_quantile", None)
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = LiquidationReversalPipeline(
        bollinger_window=bollinger_window,
        bollinger_k=bollinger_k,
        volume_lookback=volume_lookback,
        volume_quantile=effective_vol_q,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    n_bars = len(feat_df["timestamp"].unique())
    n_assets = len(assets)

    penetration_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="bb_penetration")
        .fill_null(False)
        .select(assets)
        .to_numpy()
    )
    vol_ratio_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="vol_ratio")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    if effective_vol_q is not None and "vol_quantile" in feat_df.columns:
        vol_q_mat = (
            feat_df.pivot(index="timestamp", on="asset", values="vol_quantile")
            .fill_null(0.0)
            .select(assets)
            .to_numpy()
        )
        vol_mat = (
            feat_df.pivot(index="timestamp", on="asset", values="volume")
            .fill_null(0.0)
            .select(assets)
            .to_numpy()
        )
    else:
        vol_q_mat = None
        vol_mat = None
    close_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="close")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    mean_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="rolling_mean")
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
    warmup = max(bollinger_window, volume_lookback) + 1
    weight = 1.0 / len(active_indices)

    for a_idx in active_indices:
        in_pos = False
        hold_count = 0
        for t in range(warmup, n_bars):
            if in_pos:
                hold_count += 1
                # Exit when holding duration reaches limit or price recovers to rolling mean
                if hold_count >= holding_bars or close_mat[t, a_idx] >= mean_mat[t, a_idx]:
                    in_pos = False
                    hold_count = 0
                    positions[t, a_idx] = 0.0
                else:
                    positions[t, a_idx] = weight
            else:
                is_penetrated = penetration_mat[t, a_idx]
                has_vol_spike = vol_ratio_mat[t, a_idx] >= effective_vol_mult
                if vol_q_mat is not None and vol_mat is not None:
                    has_vol_spike = has_vol_spike and (vol_mat[t, a_idx] >= vol_q_mat[t, a_idx])
                if is_penetrated and has_vol_spike:
                    in_pos = True
                    hold_count = 0
                    positions[t, a_idx] = weight

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = LiquidationReversalPipeline()
