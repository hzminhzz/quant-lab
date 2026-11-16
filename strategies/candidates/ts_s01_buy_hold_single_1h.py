"""Rules-based 1-hour buy & hold single / baseline strategy (AF30 Strategy 1).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 1 (Buy & Hold Single / AF30), aligned with source spec:
- Long bias: constant 1.0 (constant maximum long forecast 20.0)
- Equal-weight allocation across active assets, capped at 0.5 per instrument, gross exposure 1.0
- Trailing stop: 3.0x ATR-14
- Rebalance frequency: 720 hours (monthly)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class FeaturePipeline:
    """Causal feature pipeline computing AF30 Strategy 1 buy & hold single forecasts."""

    def __init__(
        self,
        atr_window: int = 14,
        cap: float = 20.0,
    ) -> None:
        self.atr_window = atr_window
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
            )
            .with_columns(
                forecast=pl.lit(float(self.cap)),
                raw_inv_vol=pl.lit(1.0),
            )
            .fill_null(0.0)
        )


BuyHoldSinglePipeline = FeaturePipeline


class BuyHoldSingleStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing AF30 Strategy 1 buy & hold single."""

    def __init__(
        self,
        atr_window: int = 14,
        cap: float = 20.0,
        rebalance_bars: int = 720,
        stop_multiple: float = 3.0,
        per_instrument_cap: float = 0.5,
        gross_exposure: float = 1.0,
    ) -> None:
        super().__init__()
        self.atr_window = atr_window
        self.cap = cap
        self.rebalance_bars = rebalance_bars
        self.stop_multiple = stop_multiple
        self.per_instrument_cap = per_instrument_cap
        self.gross_exposure = gross_exposure
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        self.bar_count += 1


def capped_waterfall_weights(raw_weights: np.ndarray, cap: float = 0.5, gross: float = 1.0) -> np.ndarray:
    """Enforce exact per-instrument risk budget cap by iterative water-filling."""
    n = len(raw_weights)
    if n == 0 or np.sum(raw_weights) <= 0:
        return np.full(n, gross / max(1, n))
    w = (raw_weights / np.sum(raw_weights)) * gross
    frozen = np.zeros(n, dtype=bool)
    for _ in range(n):
        over = (w > cap + 1e-12) & ~frozen
        if not np.any(over):
            break
        w[over] = cap
        frozen[over] = True
        remaining = gross - cap * float(np.sum(frozen))
        free = ~frozen
        free_sum = float(np.sum(raw_weights[free]))
        if free_sum > 0 and remaining > 0:
            w[free] = (raw_weights[free] / free_sum) * remaining
        else:
            w[free] = 0.0
            break
    return w


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    atr_window: int = 14,
    cap: float = 20.0,
    rebalance_bars: int = 720,
    stop_multiple: float = 3.0,
    per_instrument_cap: float = 0.5,
    gross_exposure: float = 1.0,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 1 buy & hold positions with trailing ATR-14 stop overlay."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = FeaturePipeline(
        atr_window=atr_window,
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
    warmup = atr_window + 1
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

        # Apply trailing ATR stop for long positions only on observed bars
        for a_idx in active_indices:
            if not obs_mat[t, a_idx]:
                continue
            pos = positions[t, a_idx]
            if pos > 1e-6:
                peak_prices[a_idx] = max(peak_prices[a_idx], high_mat[t, a_idx])
                stop_price = peak_prices[a_idx] - stop_multiple * atr_mat[t, a_idx]
                if close_mat[t, a_idx] < stop_price:
                    positions[t, a_idx] = 0.0

    return {"positions": positions, "forecast": fc_mat}


# Default pipeline instance for warden verify
pipeline = FeaturePipeline()
