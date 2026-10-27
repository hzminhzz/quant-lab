"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 19 (pp. 367-380).

Cross-sectional momentum within asset classes. The relative price of an instrument is
the difference between its normalised price and its class index (p. 368):

    R_i,t = PN_i,t - A_class(i),t

Average outperformance over horizon H is (R_t - R_{t-H}) / H, smoothed with an EWMA whose
span is H / 4 (pp. 369-371), scaled by the Table 80 forecast scalar, capped at +/-20,
averaged equally across the six horizons (5, 10, 20, 40, 80, 160), multiplied by the
Table 84 FDM for six horizons (1.49, p. 374) and capped again. Positions use dynamic cash
volatility sizing with the 10% buffer and no stop.

The book is explicit (p. 371) that this rule does *not* enforce market neutrality: a class
member deemed to be outperforming is more likely to be long, but the book does not balance
longs against shorts. What is exactly removed is the class mean: for a fully observed class
the relative prices sum to zero at every timestamp.

The acceptance draft described this chapter as a 252-day return ranked into long/short
halves. That is not the published rule, so the relative-price form above is implemented;
horizons, scalars, spans and FDM all remain module constants so the mapping is auditable.
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

HORIZONS: tuple[int, ...] = (5, 10, 20, 40, 80, 160)
SMOOTH_SPANS: dict[int, int] = {5: 2, 10: 3, 20: 5, 40: 10, 80: 20, 160: 40}
FORECAST_SCALARS: dict[int, float] = {5: 56.1, 10: 79.0, 20: 108.5, 40: 153.5, 80: 217.1, 160: 296.8}
FDM = 1.49


class XsMomentumPipeline:
    """Relative normalised price -> averaged multi-horizon cross-sectional forecast."""

    def __init__(
        self,
        horizons: tuple[int, ...] = HORIZONS,
        scalars: dict[int, float] | None = None,
        fdm: float = FDM,
        vol_span: int = 35,
        vol_target: float = 0.16,
        per_instrument_cap: float = 0.5,
    ) -> None:
        self.horizons = horizons
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
        )

        per_horizon = [
            (
                ((pl.col("relative") - pl.col("relative").shift(h)) / h)
                .ewm_mean(span=SMOOTH_SPANS[h]) * self.scalars[h]
            ).clip(-20.0, 20.0).over("asset")
            for h in self.horizons
        ]
        total = per_horizon[0]
        for expr in per_horizon[1:]:
            total = total + expr
        return (
            feat.with_columns(
                forecast=(total / len(per_horizon) * self.fdm).clip(-20.0, 20.0).fill_nan(0.0).fill_null(0.0)
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
            .drop("cls")  # the leakage verifier compares every non-key column numerically
        )


pipeline = XsMomentumPipeline()


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
    """Buffered cross-sectional book; no stop loss."""
    _ = target_symbols, kwargs
    pipe = XsMomentumPipeline(fdm=fdm, vol_span=vol_span, vol_target=vol_target,
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
