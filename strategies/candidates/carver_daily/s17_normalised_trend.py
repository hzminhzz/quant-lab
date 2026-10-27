"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 17 (pp. 344-355).

Normalised trend: normalise the price itself before applying EWMAC, instead of only
normalising the moving-average crossover. The normalised price is generated recursively
from per-bar returns (p. 345):

    PN_t = PN_{t-1} + 100 * (P_t / P_{t-1} - 1) / sigma_bar_t,   PN_0 = 0

so every increment is a risk-adjusted return and the series has roughly constant
volatility. The EWMAC(64, 256) crossover of PN is divided by the standard deviation of PN
differences (in PN units, not by the price level) and multiplied by the same Table 29
scalar as the vanilla filter (1.91 for EWMAC64, p. 205), then capped at +/-20. Sizing is
dynamic cash volatility and there is no stop loss.

The 100 multiplier is the book's arbitrary scaling to put the series in familiar orders of
magnitude; it cancels in the forecast because the divisor is the PN-difference volatility.
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
NORMALISATION_SCALE = 100.0


class NormalisedTrendPipeline:
    """Cumulative normalised price index -> EWMAC(64, 256) forecast on that index."""

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
        vol_ann = ewma_price_vol(pl.col("close"), self.vol_span, contract.bars_per_year).over("asset")
        vol_bar = vol_ann / math.sqrt(contract.bars_per_year)
        step = (
            pl.when(vol_bar > 0)
            .then(NORMALISATION_SCALE * pl.col("close").pct_change() / vol_bar)
            .otherwise(0.0)
        ).over("asset")
        pn = step.cum_sum().over("asset")
        pn_bar_vol = pl.col("pn").diff().pow(2).ewm_mean(span=self.vol_span).sqrt().shift(1).over("asset")
        return (
            df.sort("timestamp", "asset")
            .with_columns(vol_ann=vol_ann, vol_bar=vol_bar, step=step, pn=pn)
            .with_columns(pn_bar_vol=pn_bar_vol)
            .with_columns(
                forecast=(
                    (pl.col("pn").ewm_mean(span=self.fast) - pl.col("pn").ewm_mean(span=self.slow))
                    / pl.col("pn_bar_vol").clip(1e-12, None) * self.scalar
                ).clip(-20.0, 20.0).fill_nan(0.0).fill_null(0.0)
            )
            .with_columns(
                weight=((pl.col("forecast") / 10.0) * (self.vol_target / n_assets) / pl.col("vol_ann")).clip(
                    -self.per_instrument_cap, self.per_instrument_cap
                ).fill_nan(0.0).fill_null(0.0)
            )
        )


pipeline = NormalisedTrendPipeline()


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
    """Normalised-price trend book; no buffer and no stop."""
    _ = target_symbols, kwargs
    pipe = NormalisedTrendPipeline(fast, slow, scalar, vol_span, vol_target, per_instrument_cap)
    feat = pipe.compute(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    target = value_matrix(feat, "weight", assets)
    target[:warmup] = 0.0
    return {"positions": fill_held(target, observation_mask(feat, assets))}
