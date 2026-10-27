"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 5 (pp. 149-164).

Slow trend following, long only: hold a long position while the EWMAC(64, 256) fast
average is above the slow average, and hold nothing otherwise. Positions are sized for
the average (forecast 10) risk target, not scaled by trend strength. No stop loss.

The book trades back-adjusted futures in discrete contracts; here the position is a
dimensionless weight, so the filter becomes max(trend, 0) on the same signal.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import polars as pl

from warden.contracts import EvaluationContract

sys.path.insert(0, str(Path(__file__).resolve().parent))  # warden loads modules by path, not as a package
from carver_common import ewma_price_vol, fill_held, observation_mask, value_matrix


class SlowTrendLongOnlyPipeline:
    """EWMAC(64, 256) uptrend filter, long-only, average-sized position."""

    def __init__(
        self,
        fast: int = 64,
        slow: int = 256,
        vol_span: int = 35,
        vol_target: float = 0.16,
        per_instrument_cap: float = 0.5,
    ) -> None:
        self.fast = fast
        self.slow = slow
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
            .with_columns(
                forecast=(pl.when(pl.col("trend") > 0).then(pl.lit(10.0)).otherwise(pl.lit(0.0))),
            )
            .with_columns(
                weight=((pl.col("forecast") / 10.0) * (self.vol_target / n_assets) / pl.col("vol_ann")).clip(
                    -self.per_instrument_cap, self.per_instrument_cap
                ).fill_nan(0.0).fill_null(0.0)
            )
        )


pipeline = SlowTrendLongOnlyPipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    fast: int = 64,
    slow: int = 256,
    vol_span: int = 35,
    vol_target: float = 0.16,
    per_instrument_cap: float = 0.5,
    warmup: int = 1,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Long-only trend filter; never short, never stopped out."""
    _ = target_symbols, kwargs
    pipe = SlowTrendLongOnlyPipeline(fast, slow, vol_span, vol_target, per_instrument_cap)
    feat = pipe.compute(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    target = value_matrix(feat, "weight", assets)
    target[:warmup] = 0.0
    return {"positions": fill_held(target, observation_mask(feat, assets))}
