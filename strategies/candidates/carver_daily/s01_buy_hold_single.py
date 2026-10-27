"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 1 (pp. 21-63).

Buy and hold, single contract. No risk scaling, no rebalancing, no stop loss: the book
holds one contract per instrument and never resizes it, so price moves change notional
exposure but not the position.

Dimensionless adaptation on a multi-instrument panel: a constant equal-notional weight
of 1/n_active per instrument. The weight is independent of price, so scaling the whole
price series by any factor leaves positions unchanged -- the same invariance a single
contract has.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import polars as pl

from warden.contracts import EvaluationContract

sys.path.insert(0, str(Path(__file__).resolve().parent))  # warden loads modules by path, not as a package
from carver_common import fill_held, observation_mask, value_matrix


class BuyHoldSinglePipeline:
    """Causal features for the single-contract benchmark: no volatility, no forecast."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        n_assets = max(df["asset"].n_unique(), 1)
        return (
            df.sort("timestamp", "asset")
            .with_columns(
                daily_ret=(pl.col("close") / pl.col("close").shift(1).over("asset") - 1.0).fill_null(0.0),
                weight=pl.lit(1.0 / n_assets),
            )
        )


pipeline = BuyHoldSinglePipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    warmup: int = 1,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Constant equal-notional long book; a 40% crash does not trigger any exit."""
    _ = contract, target_symbols, kwargs
    feat = pipeline.compute(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    target = value_matrix(feat, "weight", assets)
    target[:warmup] = 0.0
    return {"positions": fill_held(target, observation_mask(feat, assets))}
