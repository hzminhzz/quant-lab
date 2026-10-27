"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 8 (pp. 191-202).

Fast trend following with trend strength: EWMAC(16, 64) crossover divided by per-bar
price volatility, multiplied by the Table 29 forecast scalar for that speed (4.10,
p. 205) and capped at +/-20, plus the 10% buffering of pp. 194-195, which trades only
when the optimal weight leaves a deadband of 10% of the average position. No stop loss.

Scalar provenance: the book text states "my estimate for the forecast scalar is 4.1 for
this faster crossover" (p. 193), which is Table 29's EWMAC16 entry. An earlier spec draft
carried 3.75; it appears nowhere in the book, so 4.10 is the default here and `scalar`
stays a parameter for the screen YAML.
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
    apply_buffer,
    ewma_price_vol,
    observation_mask,
    value_matrix,
)

SCALAR = 4.10


class FastTrendStrengthPipeline:
    """EWMAC(16, 64) strength / per-bar volatility * 4.10, capped and cash-vol sized."""

    def __init__(
        self,
        fast: int = 16,
        slow: int = 64,
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
        trend = (pl.col("close").ewm_mean(span=self.fast) - pl.col("close").ewm_mean(span=self.slow))
        trend = (trend / pl.col("close")).over("asset")
        vol_ann = ewma_price_vol(pl.col("close"), self.vol_span, contract.bars_per_year).over("asset")
        return (
            df.sort("timestamp", "asset")
            .with_columns(trend=trend, vol_ann=vol_ann, vol_bar=vol_ann / math.sqrt(contract.bars_per_year))
            .with_columns(forecast=(pl.col("trend") / pl.col("vol_bar") * self.scalar).clip(-20.0, 20.0))
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


pipeline = FastTrendStrengthPipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    fast: int = 16,
    slow: int = 64,
    scalar: float = SCALAR,
    vol_span: int = 35,
    vol_target: float = 0.16,
    per_instrument_cap: float = 0.5,
    buffer_fraction: float = 0.10,
    warmup: int = 1,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Buffered fast trend book: trade only when the target leaves the 10% deadband."""
    _ = target_symbols, kwargs
    pipe = FastTrendStrengthPipeline(fast, slow, scalar, vol_span, vol_target, per_instrument_cap)
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
