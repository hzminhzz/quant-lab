"""Rules-based 1-hour multi-speed EWMAC trend following strategy (HYP-2026-067).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 9 (Multiple Trend Rules / AF30), aligned with source spec:
- Fast EMA pair: 8 and 32
- Slow EMA pair: 16 and 64
- Normalization: percentage spread divided by 0.01 (100 bps)
- Forecast: average of fast and slow normalized spreads, capped at [-20, 20]
- Sizing: inverse volatility weighting based on 240-hour rolling std, capped at 0.5 per asset
- Rebalance frequency: 168 hours (weekly)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class MultiSpeedEWMACPipeline:
    """Causal feature pipeline computing AF30 Strategy 9 dual-speed EWMAC trend forecasts."""

    def __init__(
        self,
        ema_fast_1: int = 8,
        ema_fast_2: int = 32,
        ema_slow_1: int = 16,
        ema_slow_2: int = 64,
        vol_window: int = 240,
        forecast_cap: float = 20.0,
    ) -> None:
        self.ema_fast_1 = ema_fast_1
        self.ema_fast_2 = ema_fast_2
        self.ema_slow_1 = ema_slow_1
        self.ema_slow_2 = ema_slow_2
        self.vol_window = vol_window
        self.forecast_cap = forecast_cap

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        prev_close = pl.col("close").shift(1).over("asset")

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                hourly_ret=(pl.col("close") - prev_close) / (prev_close + 1e-12),
                ema_8=pl.col("close").ewm_mean(span=self.ema_fast_1).over("asset"),
                ema_32=pl.col("close").ewm_mean(span=self.ema_fast_2).over("asset"),
                ema_16=pl.col("close").ewm_mean(span=self.ema_slow_1).over("asset"),
                ema_64=pl.col("close").ewm_mean(span=self.ema_slow_2).over("asset"),
            )
            .with_columns(
                # Shift by 1 so current bar return is excluded from trailing volatility
                vol_240=pl.col("hourly_ret").rolling_std(self.vol_window).shift(1).over("asset"),
                trend_fast=(pl.col("ema_8") - pl.col("ema_32")) / (pl.col("close") + 1e-12),
                trend_slow=(pl.col("ema_16") - pl.col("ema_64")) / (pl.col("close") + 1e-12),
            )
            .with_columns(
                trend_fast_norm=pl.col("trend_fast") / 0.01,
                trend_slow_norm=pl.col("trend_slow") / 0.01,
            )
            .with_columns(
                multi_trend=(pl.col("trend_fast_norm") + pl.col("trend_slow_norm")) / 2.0,
            )
            .with_columns(
                forecast=pl.col("multi_trend").clip(-self.forecast_cap, self.forecast_cap),
                raw_inv_vol=1.0 / (pl.col("vol_240") + 1e-12),
            )
            .fill_null(0.0)
        )


class MultiSpeedEWMACStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing AF30 Strategy 9 multi-speed EWMAC."""

    def __init__(
        self,
        ema_fast_1: int = 8,
        ema_fast_2: int = 32,
        ema_slow_1: int = 16,
        ema_slow_2: int = 64,
        vol_window: int = 240,
        forecast_cap: float = 20.0,
        rebalance_bars: int = 168,
        per_instrument_cap: float = 0.5,
        gross_exposure: float = 1.0,
    ) -> None:
        super().__init__()
        self.ema_fast_1 = ema_fast_1
        self.ema_fast_2 = ema_fast_2
        self.ema_slow_1 = ema_slow_1
        self.ema_slow_2 = ema_slow_2
        self.vol_window = vol_window
        self.forecast_cap = forecast_cap
        self.rebalance_bars = rebalance_bars
        self.per_instrument_cap = per_instrument_cap
        self.gross_exposure = gross_exposure
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        self.bar_count += 1

def capped_waterfall_weights(raw_weights: np.ndarray, cap: float = 0.5, gross: float = 1.0) -> np.ndarray:
    """Enforce exact per-instrument risk budget cap by iterative water-filling.

    Feasibility guard: If universe size n * cap < gross, clamps total gross exposure to n * cap
    so no individual instrument ever exceeds the declared per_instrument_cap.
    """
    n = len(raw_weights)
    if n == 0:
        return np.zeros(0, dtype=float)
    target_gross = min(gross, n * cap)
    if np.sum(raw_weights) <= 0:
        return np.full(n, min(target_gross / n, cap))
    w = (raw_weights / np.sum(raw_weights)) * target_gross
    frozen = np.zeros(n, dtype=bool)
    for _ in range(n):
        over = (w > cap + 1e-12) & ~frozen
        if not np.any(over):
            break
        w[over] = cap
        frozen[over] = True
        remaining = target_gross - cap * float(np.sum(frozen))
        free = ~frozen
        free_sum = float(np.sum(raw_weights[free]))
        if free_sum > 0 and remaining > 0:
            w[free] = (raw_weights[free] / free_sum) * remaining
        else:
            w[free] = 0.0
            break
    return np.clip(w, 0.0, cap)



def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ema_fast_1: int = 8,
    ema_fast_2: int = 32,
    ema_slow_1: int = 16,
    ema_slow_2: int = 64,
    vol_window: int = 240,
    forecast_cap: float = 20.0,
    rebalance_bars: int = 168,
    per_instrument_cap: float = 0.5,
    gross_exposure: float = 1.0,
    long_only: bool = False,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 9 positions: dual-speed EWMAC normalized by 0.01 with inverse vol weighting."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = MultiSpeedEWMACPipeline(
        ema_fast_1=ema_fast_1,
        ema_fast_2=ema_fast_2,
        ema_slow_1=ema_slow_1,
        ema_slow_2=ema_slow_2,
        vol_window=vol_window,
        forecast_cap=forecast_cap,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    n_bars = len(feat_df["timestamp"].unique())
    n_assets = len(assets)

    fc_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="forecast")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    iv_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="raw_inv_vol")
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
    warmup = max(ema_slow_2, vol_window) + 1

    for t in range(warmup, n_bars):
        if t % rebalance_bars == 0:
            active_ivs = iv_mat[t, active_indices]
            weights = capped_waterfall_weights(active_ivs, cap=per_instrument_cap, gross=gross_exposure)
            for idx, a_idx in enumerate(active_indices):
                fc = fc_mat[t, a_idx]
                if long_only and fc < 0.0:
                    fc = 0.0
                positions[t, a_idx] = (fc / forecast_cap) * weights[idx]
        else:
            positions[t] = positions[t - 1]

    return {"positions": positions, "forecast": fc_mat}


# Default pipeline instance for warden verify
pipeline = MultiSpeedEWMACPipeline()
