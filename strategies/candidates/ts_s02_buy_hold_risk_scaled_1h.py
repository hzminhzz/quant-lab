"""Rules-based 1-hour buy & hold risk scaled strategy (AF30 Strategy 2).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 2 (Buy & Hold Risk Scaled / AF30), aligned with source spec:
- Long bias: constant 1.0 (constant maximum long forecast 20.0)
- Volatility estimation: 240-hour rolling return std, lagged by 1 bar
- Sizing: inverse volatility weighting based on vol_240, water-filled to 0.5 cap, gross exposure 1.0
- Trailing stop: 3.0x ATR-14
- Rebalance frequency: 168 hours (weekly)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ts_common import capped_waterfall_weights, normalize_asset_column, trailing_atr_stop_long_only

from warden.contracts import EvaluationContract


class FeaturePipeline:
    """Causal feature pipeline computing AF30 Strategy 2 buy & hold risk scaled forecasts."""

    def __init__(
        self,
        atr_window: int = 14,
        vol_window: int = 240,
        cap: float = 20.0,
    ) -> None:
        self.atr_window = atr_window
        self.vol_window = vol_window
        self.cap = cap

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        prev_close = pl.col("close").shift(1).over("asset")
        tr = pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - prev_close).abs(),
            (pl.col("low") - prev_close).abs(),
        )

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                hourly_ret=(pl.col("close") - prev_close) / (prev_close + 1e-12),
                tr=tr,
                long_bias=pl.lit(1.0),
            )
            .with_columns(
                atr_14=pl.col("tr").ewm_mean(span=self.atr_window).over("asset"),
                vol_240=pl.col("hourly_ret").rolling_std(self.vol_window).shift(1).over("asset"),
            )
            .with_columns(
                forecast=pl.lit(float(self.cap)),
                raw_inv_vol=1.0 / (pl.col("vol_240") + 1e-12),
            )
            .fill_null(0.0)
        )


BuyHoldRiskScaledPipeline = FeaturePipeline


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    atr_window: int = 14,
    vol_window: int = 240,
    cap: float = 20.0,
    rebalance_bars: int = 168,
    stop_multiple: float = 3.0,
    per_instrument_cap: float = 0.5,
    gross_exposure: float = 1.0,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 2 risk scaled positions with trailing ATR-14 stop overlay."""
    _ = kwargs
    panel_df = normalize_asset_column(panel_df)

    pipe = FeaturePipeline(
        atr_window=atr_window,
        vol_window=vol_window,
        cap=cap,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    timestamps = sorted(feat_df["timestamp"].unique().to_list())
    n_bars = len(timestamps)
    n_assets = len(assets)

    if n_bars == 0 or n_assets == 0:
        return {"positions": np.zeros((n_bars, n_assets), dtype=float)}

    piv = feat_df.with_columns(_obs=pl.lit(1.0))

    fc_mat = (
        piv.pivot(index="timestamp", on="asset", values="forecast")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )
    iv_mat = (
        piv.pivot(index="timestamp", on="asset", values="raw_inv_vol")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )
    close_mat = (
        piv.pivot(index="timestamp", on="asset", values="close")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )
    high_mat = (
        piv.pivot(index="timestamp", on="asset", values="high")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )
    atr_mat = (
        piv.pivot(index="timestamp", on="asset", values="atr_14")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
    )
    obs_mat = (
        piv.pivot(index="timestamp", on="asset", values="_obs")
        .fill_null(0.0)
        .sort("timestamp")
        .select(assets)
        .to_numpy()
        > 0.5
    )

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        active_indices = list(range(n_assets))

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    warmup = max(vol_window, atr_window) + 1
    peak_prices = np.zeros(n_assets, dtype=float)

    for t in range(warmup, n_bars):
        positions[t] = positions[t - 1].copy()

        if (t - warmup) % rebalance_bars == 0:
            rebal_indices = [a_idx for a_idx in active_indices if obs_mat[t, a_idx]]
            unobs_indices = [a_idx for a_idx in active_indices if not obs_mat[t, a_idx]]
            carried_gross = float(np.sum(np.abs(positions[t, unobs_indices])))
            remaining_gross = max(0.0, gross_exposure - carried_gross)
            if rebal_indices:
                if remaining_gross > 1e-12:
                    active_ivs = iv_mat[t, rebal_indices]
                    weights = capped_waterfall_weights(
                        active_ivs,
                        cap=min(per_instrument_cap, remaining_gross),
                        gross=remaining_gross,
                    )
                    for idx, a_idx in enumerate(rebal_indices):
                        fc = fc_mat[t, a_idx]
                        positions[t, a_idx] = (fc / cap) * weights[idx]
                        peak_prices[a_idx] = high_mat[t, a_idx]
                else:
                    for a_idx in rebal_indices:
                        positions[t, a_idx] = 0.0

        trailing_atr_stop_long_only(active_indices, positions, obs_mat, t, peak_prices, high_mat, stop_multiple, close_mat, atr_mat)

    return {"positions": positions, "forecast": fc_mat}


# Default pipeline instance for warden verify
pipeline = FeaturePipeline()
