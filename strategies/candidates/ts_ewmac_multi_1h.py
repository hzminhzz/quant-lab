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
from ts_common import capped_waterfall_weights_feasibility as capped_waterfall_weights
from ts_common import normalize_asset_column

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
    panel_df = normalize_asset_column(panel_df)

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
