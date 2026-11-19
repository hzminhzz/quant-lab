"""Rules-based 1-hour volatility-managed time series momentum strategy (HYP-2026-012)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract


class VolManagedTSMOMPipeline:
    """Causal feature pipeline computing lookback returns, realized volatility, and volatility scalar."""

    def __init__(
        self,
        lookback_bars: int = 72,
        vol_window: int = 24,
        target_vol: float = 0.015,
        max_leverage: float = 1.5,
    ) -> None:
        self.lookback_bars = lookback_bars
        self.vol_window = vol_window
        self.target_vol = target_vol
        self.max_leverage = max_leverage

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        prev_close_lookback = pl.col("close").shift(self.lookback_bars).over("asset")
        prev_close_1 = pl.col("close").shift(1).over("asset")

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                ret_lookback=(pl.col("close") - prev_close_lookback) / (prev_close_lookback + 1e-12),
                hourly_ret=(pl.col("close") - prev_close_1) / (prev_close_1 + 1e-12),
            )
            .with_columns(
                realized_vol=pl.col("hourly_ret").rolling_std(self.vol_window).over("asset"),
            )
            .with_columns(
                raw_scalar=self.target_vol / (pl.col("realized_vol") + 1e-12),
            )
            .with_columns(
                vol_scalar=pl.when(pl.col("raw_scalar") > self.max_leverage)
                .then(self.max_leverage)
                .otherwise(pl.col("raw_scalar")),
            )
            .with_columns(
                signal=(pl.col("ret_lookback").sign() * pl.col("vol_scalar")).fill_null(0.0),
            )
        )


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    lookback_bars: int = 72,
    vol_window: int = 24,
    target_vol: float = 0.015,
    max_leverage: float = 1.5,
    rebalance_every: int = 4,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal volatility-managed time series momentum positions: sign(r_72h) * min(target_vol / realized_vol_24h, max_leverage)."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = VolManagedTSMOMPipeline(
        lookback_bars=lookback_bars,
        vol_window=vol_window,
        target_vol=target_vol,
        max_leverage=max_leverage,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    n_bars = len(feat_df["timestamp"].unique())
    n_assets = len(assets)

    signal_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="signal")
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
    warmup = max(lookback_bars, vol_window) + 1
    weight = 1.0 / len(active_indices)

    for t in range(warmup, n_bars):
        if t % rebalance_every == 0:
            for a_idx in active_indices:
                positions[t, a_idx] = signal_mat[t, a_idx] * weight
        else:
            positions[t] = positions[t - 1]

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = VolManagedTSMOMPipeline()
