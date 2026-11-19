"""Rules-based 1-hour 90-day high-watermark anchoring momentum strategy (HYP-2026-006)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ts_common import normalize_asset_column

from warden.contracts import EvaluationContract


class HighWatermarkPipeline:
    """Causal feature pipeline computing proximity to rolling high watermark and momentum."""

    def __init__(self, high_window_bars: int = 2160, mom_window_bars: int = 24) -> None:
        self.high_window_bars = high_window_bars
        self.mom_window_bars = mom_window_bars

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        return (
            df.sort("timestamp", "asset")
            .with_columns(
                rolling_high=pl.col("high").rolling_max(self.high_window_bars).over("asset"),
                mom_24h=pl.col("close").pct_change(self.mom_window_bars).over("asset"),
            )
            .with_columns(
                high_ratio=pl.col("close") / (pl.col("rolling_high") + 1e-12),
            )
        )


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    high_window_bars: int = 2160,
    mom_window_bars: int = 24,
    long_threshold: float = 0.95,
    short_threshold: float = 0.70,
    max_holding_bars: int = 120,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal positions matrix: long when close/high_90d >= 0.95 and mom_24h > 0, short when <= 0.70."""
    _ = kwargs
    panel_df = normalize_asset_column(panel_df)

    pipe = HighWatermarkPipeline(high_window_bars=high_window_bars, mom_window_bars=mom_window_bars)
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    n_bars = len(feat_df["timestamp"].unique())
    n_assets = len(assets)

    high_ratio_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="high_ratio")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    mom_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="mom_24h")
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
    warmup = max(high_window_bars, mom_window_bars) + 1
    weight = 1.0 / len(active_indices)

    for a_idx in active_indices:
        current_pos = 0.0
        hold_bars = 0
        for t in range(warmup, n_bars):
            ratio = high_ratio_mat[t, a_idx]
            mom = mom_mat[t, a_idx]

            if current_pos != 0.0:
                hold_bars += 1
                if hold_bars >= max_holding_bars or (current_pos > 0 and ratio < 0.90) or (current_pos < 0 and ratio > 0.80):
                    current_pos = 0.0
                    hold_bars = 0
            else:
                if ratio >= long_threshold and mom > 0:
                    current_pos = weight
                    hold_bars = 0
                elif ratio <= short_threshold and ratio > 0:
                    current_pos = -weight
                    hold_bars = 0

            positions[t, a_idx] = current_pos

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = HighWatermarkPipeline()
