"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 23 (pp. 411-419).

Acceleration: the rate of change of an EWMAC trend forecast. For each speed N in
{8, 16, 32, 64} the vanilla forecast f_N is computed as in strategies 7-9 (EWMAC(N, 4N)
strength / per-bar volatility * the Table 29 scalar, capped at +/-20), and acceleration is
the change of that forecast over N bars:

    acc_N = clip((f_N,t - f_N,t-N) * acc_scalar_N, +/-20)

with the Table 100 acceleration scalars 1.87 / 1.90 / 1.98 / 2.05 (p. 414). The four are
averaged with equal weights and multiplied by the Table 105 FDM of 1.55 (p. 419), capped
at +/-20. Sizing is dynamic cash volatility with the 10% buffer and no stop.
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

SPEEDS: tuple[int, ...] = (8, 16, 32, 64)
EWMAC_SCALARS: dict[int, float] = {8: 5.95, 16: 4.10, 32: 2.79, 64: 1.91}
ACCELERATION_SCALARS: dict[int, float] = {8: 1.87, 16: 1.90, 32: 1.98, 64: 2.05}
FDM = 1.55


class AccelerationPipeline:
    """Change in each speed's EWMAC forecast over that speed, scaled and combined."""

    def __init__(
        self,
        speeds: tuple[int, ...] = SPEEDS,
        acceleration_scalars: dict[int, float] | None = None,
        fdm: float = FDM,
        vol_span: int = 35,
        vol_target: float = 0.16,
        per_instrument_cap: float = 0.5,
    ) -> None:
        self.speeds = speeds
        self.acceleration_scalars = (
            acceleration_scalars if acceleration_scalars is not None else dict(ACCELERATION_SCALARS)
        )
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
        per_speed = []
        for fast in self.speeds:
            forecast = (
                (pl.col("close").ewm_mean(span=fast) - pl.col("close").ewm_mean(span=4 * fast))
                / pl.col("close") / vol_bar * EWMAC_SCALARS[fast]
            ).clip(-20.0, 20.0)
            per_speed.append(
                ((forecast - forecast.shift(fast)) * self.acceleration_scalars[fast]).clip(-20.0, 20.0)
                .over("asset")
            )
        total = per_speed[0]
        for expr in per_speed[1:]:
            total = total + expr
        return (
            df.sort("timestamp", "asset")
            .with_columns(vol_ann=vol_ann, vol_bar=vol_bar)
            .with_columns(
                forecast=(total / len(per_speed) * self.fdm).clip(-20.0, 20.0).fill_nan(0.0).fill_null(0.0)
            )
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


pipeline = AccelerationPipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    fdm: float = FDM,
    vol_span: int = 35,
    vol_target: float = 0.16,
    per_instrument_cap: float = 0.5,
    buffer_fraction: float = 0.10,
    warmup: int = 1,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Buffered acceleration book with no stop loss."""
    _ = target_symbols, kwargs
    pipe = AccelerationPipeline(fdm=fdm, vol_span=vol_span, vol_target=vol_target,
                                per_instrument_cap=per_instrument_cap)
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
