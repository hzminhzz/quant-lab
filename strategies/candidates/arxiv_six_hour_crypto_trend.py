"""Six-hour crypto trend and risk-weighting ablation (arXiv:2602.11708).

This is a frozen, simplified OHLCV-only adaptation. No missing market-cap
series, rolling-Sharpe search, or unavailable ATR stop fills are invented.
Features use only completed hourly bars; Warden applies the execution lag.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract


class SixHourTrendPipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        df = df.rename({"symbol": "asset"}) if "symbol" in df.columns and "asset" not in df.columns else df
        df = df.sort("timestamp", "asset")
        return df.with_columns(
            momentum_96h=(pl.col("close") / pl.col("close").shift(96).over("asset")).log(),
            realized_vol_168h=pl.col("close").log().diff().rolling_std(168).over("asset"),
            liquidity_720h=(pl.col("close") * pl.col("volume")).rolling_sum(720).over("asset"),
            # Source has only OHLCV and 8h funding. This is not a market-cap rank.
        )


def _piv(df: pl.DataFrame, col: str, assets: list[str]) -> np.ndarray:
    return df.pivot(index="timestamp", on="asset", values=col).sort("timestamp").select(assets).to_numpy()


def _allocation(
    trend: np.ndarray, vol: np.ndarray, liquidity: np.ndarray, observed: np.ndarray,
    *, risk_scaled: bool, universe_size: int, long_budget: float, short_budget: float
) -> np.ndarray:
    """Rank solely on trailing observed liquidity. Unobserved assets stay at zero."""
    n = len(trend)
    result = np.zeros(n, dtype=float)
    eligible = np.isfinite(trend) & np.isfinite(vol) & (vol > 1e-8)
    eligible &= np.isfinite(liquidity) & (liquidity > 0) & observed
    loc = np.flatnonzero(eligible)
    if len(loc) == 0:
        return result
    allowed = loc[np.argsort(liquidity[loc], kind="stable")[-universe_size:]]
    for side, budget in ((1, long_budget), (-1, short_budget)):
        loc_side = allowed[trend[allowed] * side > 0]
        if len(loc_side) == 0:
            continue
        inv = 1.0 / np.clip(vol[loc_side], 1e-5, None) if risk_scaled else np.ones(len(loc_side))
        weights = budget * inv / inv.sum()
        # Unallocated budget is cash, not secretly given to one concentrated name.
        result[loc_side] = side * np.minimum(weights, 0.20)
    return result


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    *,
    risk_scaled: bool = True,
    universe_size: int = 15,
    long_budget: float = 0.70,
    short_budget: float = 0.30,
    **kwargs: Any,
) -> dict[str, Any]:
    """Fixed 96h momentum, 6h decision cadence, 30d PIT turnover ranking."""
    _ = kwargs
    if contract.frequency != "1h" or contract.execution_delay_bars < 1:
        raise ValueError("Six-hour decisions require hourly data and next-bar execution")
    if not (1 <= universe_size <= 23 and 0 < long_budget < 1 and 0 < short_budget < 1):
        raise ValueError("Invalid ex-ante budget or universe parameters")

    df = pipeline.compute(panel_df, contract)
    assets = sorted(df["asset"].unique().to_list())
    stamps = sorted(df["timestamp"].unique().to_list())
    if len(stamps) < 900 or len(assets) < 2:
        raise ValueError("Insufficient panel for PIT ranking")
    mom = _piv(df, "momentum_96h", assets)
    vol = _piv(df, "realized_vol_168h", assets)
    liq = _piv(df, "liquidity_720h", assets)
    open_px = _piv(df, "open", assets)
    observed = np.isfinite(open_px) & (open_px > 0)
    pos = np.zeros((len(stamps), len(assets)), dtype=float)
    current = np.zeros(len(assets), dtype=float)

    for t, stamp in enumerate(stamps):
        if t < 720:
            continue
        if stamp.hour % 6 == 5:  # completed 6h candle available at next UTC boundary
            current = _allocation(
                mom[t], vol[t], liq[t], observed[t], risk_scaled=risk_scaled,
                universe_size=universe_size, long_budget=long_budget, short_budget=short_budget,
            )
        # Missing bars are not forward-filled into an executable position.
        pos[t] = np.where(observed[t], current, 0.0)
    return {"positions": pos}


pipeline = SixHourTrendPipeline()
