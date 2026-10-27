"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 21 (pp. 389-402).

Breakout: the continuous price-within-range forecast, where the position builds as the
price approaches the edge of a rolling range rather than waiting for the breakout.
For horizon h and daily closing prices p:

    roll_max_t = max(p_{t-h+1}, ..., p_t)      roll_min_t = min(p_{t-h+1}, ..., p_t)
    raw_t      = 40 * (p_t - midpoint_t) / (roll_max_t - roll_min_t)
    forecast_t = clip(EWMA(raw, span = h/4) * 0.73, +/-20)

The extrema are computed strictly from closing prices over the horizon; `high` and `low`
are never read, so an intraday spike cannot move the forecast. There is no volatility
normalisation in the forecast (the range already reflects volatility, p. 393) and no
trailing stop of any kind: position management is strategy 9/8's, meaning cash volatility
sizing plus 10% buffering, executed with Warden's NEXT_BAR delay.

Scalar provenance: Table 91 (p. 395) gives 0.73 for Breakout80. The book notes the +/-20
cap is redundant before scaling since the raw forecast cannot exceed 20 and every scalar
is below 1; it is applied anyway so the contract holds if a scalar above 1 is configured.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

sys.path.insert(0, str(Path(__file__).resolve().parent))  # warden loads modules by path, not as a package
from carver_common import (
    apply_buffer,
    ewma_price_vol,
    observation_mask,
    value_matrix,
)

HORIZON = 80
SCALAR = 0.73


class BreakoutPipeline:
    """Close-price rolling range -> smoothed price-within-range forecast."""

    def __init__(
        self,
        channel_window: int = HORIZON,
        scalar: float = SCALAR,
        vol_span: int = 35,
        vol_target: float = 0.16,
        per_instrument_cap: float = 0.5,
    ) -> None:
        self.channel_window = channel_window
        self.scalar = scalar
        self.vol_span = vol_span
        self.vol_target = vol_target
        self.per_instrument_cap = per_instrument_cap

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        n_assets = max(df["asset"].n_unique(), 1)
        roll_max = pl.col("close").rolling_max(self.channel_window).over("asset")
        roll_min = pl.col("close").rolling_min(self.channel_window).over("asset")
        span = (roll_max - roll_min).clip(1e-12, None)
        raw = (40.0 * (pl.col("close") - (roll_max + roll_min) / 2.0) / span).clip(-20.0, 20.0)
        vol_ann = ewma_price_vol(pl.col("close"), self.vol_span, contract.bars_per_year).over("asset")
        return (
            df.sort("timestamp", "asset")
            .with_columns(vol_ann=vol_ann, raw_forecast=raw)
            .with_columns(
                forecast=(
                    pl.col("raw_forecast").ewm_mean(span=max(self.channel_window // 4, 1)) * self.scalar
                ).clip(-20.0, 20.0).over("asset")
            )
            .with_columns(
                average=((self.vol_target / n_assets) / pl.col("vol_ann")).clip(
                    -self.per_instrument_cap, self.per_instrument_cap
                ).fill_nan(0.0).fill_null(0.0)
            )
            .with_columns(
                weight=((pl.col("forecast") / 10.0) * pl.col("average")).clip(
                    -self.per_instrument_cap, self.per_instrument_cap
                ).fill_nan(0.0).fill_null(0.0)
            )
        )


pipeline = BreakoutPipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    channel_window: int = HORIZON,
    scalar: float = SCALAR,
    vol_span: int = 35,
    vol_target: float = 0.16,
    per_instrument_cap: float = 0.5,
    buffer_fraction: float = 0.10,
    warmup: int = 1,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Buffered breakout book with no stop loss."""
    _ = target_symbols, kwargs
    pipe = BreakoutPipeline(channel_window, scalar, vol_span, vol_target, per_instrument_cap)
    feat = pipe.compute(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    target = value_matrix(feat, "weight", assets)
    average = value_matrix(feat, "average", assets)
    observed = observation_mask(feat, assets)
    target[:warmup] = 0.0

    positions = np.zeros_like(target)
    for t in range(1, target.shape[0]):
        for i in range(target.shape[1]):
            if not observed[t, i]:
                positions[t, i] = positions[t - 1, i]
            else:
                positions[t, i] = apply_buffer(target[t, i], positions[t - 1, i], buffer_fraction, average[t, i])
    return {"positions": positions}
