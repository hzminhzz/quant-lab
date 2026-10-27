"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 2 (pp. 67-92).

Buy and hold with risk scaling: each instrument is sized to hit a fixed annualised cash
volatility target (tau = 16% in the book's worked example, p. 80), so a riskier
instrument gets a smaller position. No stop loss, no forecast.

Fidelity note: the book sets sigma_% to the average standard deviation over the whole
sample, which is unknowable ex ante and would leak the future into a causal backtest.
The causal analogue used here is a long 1,260-day (5-year) trailing window lagged one
bar: the book's long-run statistic, truncated to information available at each bar.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import polars as pl

from warden.contracts import EvaluationContract

sys.path.insert(0, str(Path(__file__).resolve().parent))  # warden loads modules by path, not as a package
from carver_common import fill_held, observation_mask, price_vol, value_matrix


class BuyHoldRiskScaledPipeline:
    """Long-term historical volatility -> fixed risk target weight, re-evaluated daily."""

    def __init__(
        self,
        vol_target: float = 0.16,
        vol_window: int = 1260,
        min_samples: int = 60,
        per_instrument_cap: float = 0.5,
    ) -> None:
        self.vol_target = vol_target
        self.vol_window = vol_window
        self.min_samples = min_samples
        self.per_instrument_cap = per_instrument_cap

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        n_assets = max(df["asset"].n_unique(), 1)
        vol = price_vol(pl.col("close"), self.vol_window, contract.bars_per_year, self.min_samples).over("asset")
        return (
            df.sort("timestamp", "asset")
            .with_columns(vol_ann=vol)
            .with_columns(
                weight=(pl.lit(self.vol_target / n_assets) / pl.col("vol_ann")).clip(
                    -self.per_instrument_cap, self.per_instrument_cap
                )
            )
        )


pipeline = BuyHoldRiskScaledPipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    vol_target: float = 0.16,
    vol_window: int = 1260,
    min_samples: int = 60,
    per_instrument_cap: float = 0.5,
    warmup: int = 60,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Fixed-vol-target long book; no stop loss and no forecast."""
    _ = target_symbols, kwargs
    pipe = BuyHoldRiskScaledPipeline(vol_target, vol_window, min_samples, per_instrument_cap)
    feat = pipe.compute(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    target = value_matrix(feat, "weight", assets)
    target[:warmup] = 0.0
    return {"positions": fill_held(target, observation_mask(feat, assets))}
