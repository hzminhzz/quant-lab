"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 9 (pp. 202-225).

Multiple trend following rules: the six EWMAC speeds (2,8), (4,16), (8,32), (16,64),
(32,128) and (64,256) are each converted to a capped forecast with their own Table 29
scalar (p. 205), averaged with equal forecast weights of 1/6, multiplied by the Table 36
forecast diversification multiplier for six trend filters (FDM 1.26, p. 223), capped at
+/-20, sized on cash volatility and buffered at 10% (pp. 194-195). No stop loss.

FDM provenance: Table 36 gives 1.26 for the six-filter EWMAC set, and the book text
warns the FDM must exceed 1 only because the six forecasts are imperfectly correlated.
An earlier spec draft carried 1.41; that number is the ratio between consecutive
forecast scalars (p. 205 note 127), not a six-filter FDM, so 1.26 is the default and
`fdm` stays a parameter for the screen YAML.
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

SPEEDS: tuple[tuple[int, int], ...] = ((2, 8), (4, 16), (8, 32), (16, 64), (32, 128), (64, 256))
FORECAST_SCALARS: dict[int, float] = {2: 12.1, 4: 8.53, 8: 5.95, 16: 4.10, 32: 2.79, 64: 1.91}
FDM = 1.26


class EwmacMultiPipeline:
    """Six-speed EWMAC ensemble: equal forecast weights, FDM, cash-vol sizing."""

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
            ((pl.col("close").ewm_mean(span=fast) - pl.col("close").ewm_mean(span=slow))
             / pl.col("close") / vol_bar * self.scalars[fast]).clip(-20.0, 20.0).over("asset")
            for fast, slow in self.speeds
        ]
        total = per_speed[0]
        for expr in per_speed[1:]:
            total = total + expr
        combined = (total / len(per_speed) * self.fdm).clip(-20.0, 20.0)
        return (
            df.sort("timestamp", "asset")
            .with_columns(vol_ann=vol_ann, vol_bar=vol_bar)
            .with_columns(forecast=combined)
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


pipeline = EwmacMultiPipeline()


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
    """Buffered six-speed ensemble; no stop loss."""
    _ = target_symbols, kwargs
    pipe = EwmacMultiPipeline(fdm=fdm, vol_span=vol_span, vol_target=vol_target,
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
