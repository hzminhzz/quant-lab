"""Rules-based 1-hour spot-perp basis dislocation mean reversal via funding rate spikes (HYP-2026-010)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract


class BasisDislocationPipeline:
    """Causal feature pipeline tracking rolling funding rate mean, standard deviation, and z-score."""

    def __init__(self, rolling_window: int = 168) -> None:
        self.rolling_window = rolling_window

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "funding_rate" in df.columns:
            rate_expr = pl.col("funding_rate")
        else:
            rate_expr = (pl.col("close") - pl.col("open")) / (pl.col("open") + 1e-12)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                funding_mean=rate_expr.rolling_mean(self.rolling_window).over("asset"),
                funding_std=rate_expr.rolling_std(self.rolling_window).over("asset"),
            )
            .with_columns(
                funding_z=(rate_expr - pl.col("funding_mean")) / (pl.col("funding_std") + 1e-12),
            )
        )


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    rolling_window: int = 168,
    z_threshold: float = 3.0,
    holding_bars: int = 16,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Proxy basis via funding rate spikes; when funding rate exceeds 3-sigma rolling window, take counter-trend position."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = BasisDislocationPipeline(rolling_window=rolling_window)
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    n_bars = len(feat_df["timestamp"].unique())
    n_assets = len(assets)

    z_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="funding_z")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        majors = [i for i, sym in enumerate(assets) if "BTC" in sym or "ETH" in sym]
        active_indices = majors if majors else list(range(n_assets))

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    warmup = rolling_window + 1
    weight = 1.0 / len(active_indices)

    for a_idx in active_indices:
        current_dir = 0
        hold_count = 0
        for t in range(warmup, n_bars):
            z_val = z_mat[t, a_idx]
            if current_dir != 0:
                hold_count += 1
                if hold_count >= holding_bars or abs(z_val) <= 0.5:
                    current_dir = 0
                    hold_count = 0
                    positions[t, a_idx] = 0.0
                else:
                    positions[t, a_idx] = current_dir * weight
            else:
                # Funding positive spike > +3-sigma: perp over-leveraged long -> take counter-trend SHORT
                if z_val >= z_threshold:
                    current_dir = -1
                    hold_count = 0
                    positions[t, a_idx] = -weight
                # Funding negative spike < -3-sigma: perp oversold discount -> take counter-trend LONG
                elif z_val <= -z_threshold:
                    current_dir = 1
                    hold_count = 0
                    positions[t, a_idx] = weight

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = BasisDislocationPipeline()
