"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 12 (pp. 280-300).

Adjusted trend: a variation on any EWMAC strategy in which the forecast mappings are
reshaped for the speeds where the book finds the linear forecast/return relationship
breaks down (pp. 291-292):

- EWMAC2 uses the 'double V' mapping, which replaces the plain cap: forecasts double
  below |F| = 10, then fall linearly to zero at |F| = 20 (anchors 10 -> 20 and 20 -> 0,
  so the average absolute forecast stays near its target of 10).
- EWMAC4 and EWMAC64 use 'scale and cap': F * 1.25 capped at +/-15, which caps earlier
  while preserving the scaling.
- EWMAC8, EWMAC16 and EWMAC32 are unchanged at +/-20.

The six adjusted forecasts are averaged with equal weights, multiplied by the Table 36
FDM (1.26, p. 223) and capped at +/-20. Positions use dynamic cash volatility sizing and
no stop loss.

Fidelity note: the printed mapping formulae are images in the source PDF, so the double V
is reconstructed from the prose and its two stated anchors; `test_s12_double_v_preserves_
average_forecast_scale` pins the scaling that the prose says must be preserved. The
acceptance draft described this chapter as volatility-regime scaling with "Table 45"
scalars, but Table 45 is the carry table (p. 223+) and the volatility-regime strategy is
thirteen, which needs a carry series and is out of scope for this work unit.
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

SPEEDS: tuple[tuple[int, int], ...] = ((2, 8), (4, 16), (8, 32), (16, 64), (32, 128), (64, 256))
FORECAST_SCALARS: dict[int, float] = {2: 12.1, 4: 8.53, 8: 5.95, 16: 4.10, 32: 2.79, 64: 1.91}
FDM = 1.26
SCALE_AND_CAP: dict[int, tuple[float, float]] = {4: (1.25, 15.0), 64: (1.25, 15.0)}


def double_v(forecast: pl.Expr, knee: float = 10.0, cap: float = 20.0) -> pl.Expr:
    """Double modest forecasts, then taper linearly to zero at the cap (p. 291)."""
    size = forecast.abs()
    return (
        pl.when(size <= knee)
        .then(2.0 * forecast)
        .when(size < cap)
        .then(forecast.sign() * 2.0 * (cap - size))
        .otherwise(0.0)
    )


def adjust_speed(fast: int, forecast: pl.Expr) -> pl.Expr:
    """Apply the per-speed mapping the book prescribes for that EWMAC variation."""
    if fast == 2:
        return double_v(forecast)
    if fast in SCALE_AND_CAP:
        scale, cap = SCALE_AND_CAP[fast]
        return (forecast * scale).clip(-cap, cap)
    return forecast.clip(-20.0, 20.0)


class AdjustedTrendPipeline:
    """Six-speed EWMAC ensemble with reshaped forecast mappings."""

    def __init__(
        self,
        speeds: tuple[tuple[int, int], ...] = SPEEDS,
        scalars: dict[int, float] | None = None,
        fdm: float = FDM,
        vol_span: int = 35,
        vol_target: float = 0.16,
        per_instrument_cap: float = 0.5,
    ) -> None:
        self.speeds = speeds
        self.scalars = scalars if scalars is not None else dict(FORECAST_SCALARS)
        self.fdm = fdm
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
        per_speed = [
            adjust_speed(
                fast,
                ((pl.col("close").ewm_mean(span=fast) - pl.col("close").ewm_mean(span=slow))
                 / pl.col("close") / vol_bar * self.scalars[fast]).over("asset"),
            )
            for fast, slow in self.speeds
        ]
        total = per_speed[0]
        for expr in per_speed[1:]:
            total = total + expr
        combined = (total / len(per_speed) * self.fdm).clip(-20.0, 20.0)
        return (
            df.sort("timestamp", "asset")
            .with_columns(vol_ann=vol_ann, vol_bar=vol_bar)
            .with_columns(forecast=combined.fill_nan(0.0).fill_null(0.0))
            .with_columns(
                weight=((pl.col("forecast") / 10.0) * (self.vol_target / n_assets) / pl.col("vol_ann")).clip(
                    -self.per_instrument_cap, self.per_instrument_cap
                ).fill_nan(0.0).fill_null(0.0)
            )
        )


pipeline = AdjustedTrendPipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    fdm: float = FDM,
    vol_span: int = 35,
    vol_target: float = 0.16,
    per_instrument_cap: float = 0.5,
    warmup: int = 1,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Adjusted-mapping trend book; no buffer and no stop."""
    _ = target_symbols, kwargs
    pipe = AdjustedTrendPipeline(fdm=fdm, vol_span=vol_span, vol_target=vol_target,
                                 per_instrument_cap=per_instrument_cap)
    feat = pipe.compute(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    target = value_matrix(feat, "weight", assets)
    target[:warmup] = 0.0
    return {"positions": fill_held(target, observation_mask(feat, assets))}
