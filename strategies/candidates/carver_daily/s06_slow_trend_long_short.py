"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 6 (pp. 164-178).

Slow trend following, long and short: hold a position with sign(EWMA(64) - EWMA(256)),
sized for the average risk target, so a downtrend is traded as a short of the same risk
as an uptrend is a long. No stop loss.
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


class SlowTrendLongShortPipeline:
    """EWMAC(64, 256) filter traded symmetrically at average size."""

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
            .with_columns(forecast=(pl.col("trend").sign() * 10.0))
            .with_columns(
                weight=((pl.col("forecast") / 10.0) * (self.vol_target / n_assets) / pl.col("vol_ann")).clip(
                    -self.per_instrument_cap, self.per_instrument_cap
                ).fill_nan(0.0).fill_null(0.0)
            )
        )


pipeline = SlowTrendLongShortPipeline()


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
    """Symmetric trend filter; a reversal flips the sign, nothing else exits."""
    _ = target_symbols, kwargs
    pipe = SlowTrendLongShortPipeline(fast, slow, vol_span, vol_target, per_instrument_cap)
    feat = pipe.compute(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    target = value_matrix(feat, "weight", assets)
    target[:warmup] = 0.0
    return {"positions": fill_held(target, observation_mask(feat, assets))}
