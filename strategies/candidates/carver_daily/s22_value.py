"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 22 (pp. 404-409).

Value: a slow mean-reversion rule on the strategy-19 relative price, R = PN_i - A_class(i).
Average daily outperformance over a five-year horizon is negated (value expects the
outperformer to give it back), smoothed with an EWMA of span 30, scaled by 7.27 and capped
at +/-20 (p. 407). Sizing is dynamic cash volatility with the 10% buffer and no stop.

Book detail kept: 5 years is 5 * 256 business days in the text. This panel has 312 bars a
year, so the horizon is expressed in years and converted with the contract's bars_per_year
rather than hard-coding 1,260 rows; the default is therefore 1,560 bars here. Pass
`horizon_bars` to force a value.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

sys.path.insert(0, str(Path(__file__).resolve().parent))  # warden loads modules by path, not as a package
from carver_common import (
    ASSET_CLASSES,
    apply_buffer,
    class_index,
    ewma_price_vol,
    normalised_price,
    observation_mask,
    value_matrix,
)

HORIZON_YEARS = 5.0
SMOOTH_SPAN = 30
SCALAR = 7.27


class ValuePipeline:
    """Negated long-horizon relative outperformance -> value forecast."""

    def __init__(
        self,
        horizon_bars: int | None = None,
        smooth_span: int = SMOOTH_SPAN,
        scalar: float = SCALAR,
        vol_span: int = 35,
        vol_target: float = 0.16,
        per_instrument_cap: float = 0.5,
    ) -> None:
        self.horizon_bars = horizon_bars
        self.smooth_span = smooth_span
        self.scalar = scalar
        self.vol_span = vol_span
        self.vol_target = vol_target
        self.per_instrument_cap = per_instrument_cap

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        n_assets = max(df["asset"].n_unique(), 1)
        horizon = self.horizon_bars or max(1, round(HORIZON_YEARS * contract.bars_per_year))
        vol_ann = ewma_price_vol(pl.col("close"), self.vol_span, contract.bars_per_year).over("asset")
        vol_bar = vol_ann / math.sqrt(contract.bars_per_year)
        instruments = (
            df.sort("timestamp", "asset")
            .with_columns(vol_ann=vol_ann, vol_bar=vol_bar)
            .with_columns(pn=normalised_price(pl.col("close"), pl.col("vol_bar")).over("asset"))
        )
        index = class_index(instruments)
        mapping = pl.DataFrame({"asset": list(ASSET_CLASSES), "cls": list(ASSET_CLASSES.values())})
        feat = (
            instruments.join(mapping, on="asset", how="left")
            .join(index.select("timestamp", "cls", "level"), on=["timestamp", "cls"], how="left")
            .with_columns(relative=pl.col("pn") - pl.col("level"))
            .with_columns(
                outperformance=((pl.col("relative") - pl.col("relative").shift(horizon)) / horizon)
                .ewm_mean(span=self.smooth_span).over("asset")
            )
            .with_columns(forecast=(-pl.col("outperformance") * self.scalar).clip(-20.0, 20.0).fill_nan(0.0).fill_null(0.0))
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
            .drop("cls")  # the leakage verifier compares every non-key column numerically
        )
        return feat


pipeline = ValuePipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    horizon_bars: int | None = None,
    smooth_span: int = SMOOTH_SPAN,
    scalar: float = SCALAR,
    vol_span: int = 35,
    vol_target: float = 0.16,
    per_instrument_cap: float = 0.5,
    buffer_fraction: float = 0.10,
    warmup: int = 1,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Buffered value book with no stop loss."""
    _ = target_symbols, kwargs
    pipe = ValuePipeline(horizon_bars, smooth_span, scalar, vol_span, vol_target, per_instrument_cap)
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
