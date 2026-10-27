"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 14 (pp. 308-317).

Spot trend: run the EWMAC filter on a synthetic spot price instead of the back-adjusted
price, so the trend signal reflects spot moves only and is not contaminated by carry.
The synthetic spot is built from returns, not levels (p. 309):

    S_t = S_{t-1} * (1 + (P_t / P_{t-1} - 1) - annual_carry_t * year_fraction)

with the level of S_0 arbitrary, and year fraction ~ 1/256 business days in the book
(1/bars_per_year here). The carry series comes from the panel's `annual_carry` column
when present; a panel without carry data (MULTI_ASSET_1D_V1 has no term structure and an
identically zero `funding_rate`) leaves S_t == P_t, i.e. spot trend collapses to the
ordinary adjusted-price trend. That is a property of the data, not of the formula, so
the deduction path is exercised by tests on a synthetic panel.

Everything else follows strategy 7: EWMAC(64, 256) on the spot series, divided by per-bar
volatility, scaled by 1.91, capped at +/-20, cash-vol sized, no stop.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import polars as pl

from warden.contracts import EvaluationContract

sys.path.insert(0, str(Path(__file__).resolve().parent))  # warden loads modules by path, not as a package
from carver_common import (
    ewma_price_vol,
    fill_held,
    observation_mask,
    value_matrix,
)

SCALAR = 1.91
BASE_LEVEL = 100.0


class SpotTrendPipeline:
    """Synthetic spot price -> EWMAC(64, 256) strength forecast, cash-vol sized."""

    def __init__(
        self,
        fast: int = 64,
        slow: int = 256,
        scalar: float = SCALAR,
        vol_span: int = 35,
        vol_target: float = 0.16,
        per_instrument_cap: float = 0.5,
    ) -> None:
        self.fast = fast
        self.slow = slow
        self.scalar = scalar
        self.vol_span = vol_span
        self.vol_target = vol_target
        self.per_instrument_cap = per_instrument_cap

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        n_assets = max(df["asset"].n_unique(), 1)
        carry = pl.col("annual_carry") if "annual_carry" in df.columns else pl.lit(0.0)
        spot_return = pl.col("close").pct_change() - carry * (1.0 / contract.bars_per_year)
        spot = (pl.lit(BASE_LEVEL) * (1.0 + spot_return).cum_prod()).over("asset")
        trend = ((spot.ewm_mean(span=self.fast) - spot.ewm_mean(span=self.slow)) / spot).over("asset")
        vol_ann = ewma_price_vol(spot, self.vol_span, contract.bars_per_year).over("asset")
        return (
            df.sort("timestamp", "asset")
            .with_columns(spot=spot)
            .with_columns(trend=trend, vol_ann=vol_ann, vol_bar=vol_ann / math.sqrt(contract.bars_per_year))
            .with_columns(forecast=(pl.col("trend") / pl.col("vol_bar") * self.scalar).clip(-20.0, 20.0))
            .with_columns(
                weight=((pl.col("forecast") / 10.0) * (self.vol_target / n_assets) / pl.col("vol_ann")).clip(
                    -self.per_instrument_cap, self.per_instrument_cap
                ).fill_nan(0.0).fill_null(0.0)
            )
        )


pipeline = SpotTrendPipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    fast: int = 64,
    slow: int = 256,
    scalar: float = SCALAR,
    vol_span: int = 35,
    vol_target: float = 0.16,
    per_instrument_cap: float = 0.5,
    warmup: int = 1,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Spot-price trend book; no buffer and no stop."""
    _ = target_symbols, kwargs
    pipe = SpotTrendPipeline(fast, slow, scalar, vol_span, vol_target, per_instrument_cap)
    feat = pipe.compute(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    target = value_matrix(feat, "weight", assets)
    target[:warmup] = 0.0
    return {"positions": fill_held(target, observation_mask(feat, assets))}
