"""Robert Carver, Advanced Futures Trading Strategies (2022) - Strategy 18 (pp. 356-366).

Trend following asset classes: measure one latent trend per asset class instead of one
per instrument. The class index is built from the strategy-17 normalised prices (p. 357):

    R_t = mean over members of (PN_i,t - PN_i,t-1),      A_t = sum of R

then the usual six-speed EWMAC ensemble is run on A with the Table 29 scalars and the
strategy-nine FDM (1.26, pp. 359-360). Every member of a class shares that forecast; each
instrument is then sized on its own cash volatility, buffered at 10% exactly as strategy
nine is. No stop loss.
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

SPEEDS: tuple[tuple[int, int], ...] = ((2, 8), (4, 16), (8, 32), (16, 64), (32, 128), (64, 256))
FORECAST_SCALARS: dict[int, float] = {2: 12.1, 4: 8.53, 8: 5.95, 16: 4.10, 32: 2.79, 64: 1.91}
FDM = 1.26


class TrendAssetClassesPipeline:
    """Asset-class normalised-price index -> shared EWMAC forecast per member."""

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
        instruments = (
            df.sort("timestamp", "asset")
            .with_columns(vol_ann=vol_ann, vol_bar=vol_bar)
            .with_columns(pn=normalised_price(pl.col("close"), pl.col("vol_bar")).over("asset"))
        )

        # class-level forecast, computed once per class on the class index
        index = class_index(instruments).with_columns(
            idx_bar_vol=(
                pl.col("level").diff().pow(2).ewm_mean(span=self.vol_span).sqrt().shift(1)
            ).clip(1e-12, None).over("cls")
        )
        per_speed = [
            (
                (pl.col("level").ewm_mean(span=fast) - pl.col("level").ewm_mean(span=slow))
                / pl.col("idx_bar_vol") * self.scalars[fast]
            ).clip(-20.0, 20.0).over("cls")
            for fast, slow in self.speeds
        ]
        total = per_speed[0]
        for expr in per_speed[1:]:
            total = total + expr
        index = index.with_columns(
            class_forecast=(total / len(per_speed) * self.fdm).clip(-20.0, 20.0).fill_nan(0.0).fill_null(0.0)
        )

        mapping = pl.DataFrame({"asset": list(ASSET_CLASSES), "cls": list(ASSET_CLASSES.values())})
        feat = (
            instruments.join(mapping, on="asset", how="left")
            .join(index.select("timestamp", "cls", "class_forecast"), on=["timestamp", "cls"], how="left")
            .with_columns(forecast=pl.col("class_forecast").fill_null(0.0))
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
        return feat

pipeline = TrendAssetClassesPipeline()


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
    """Buffered asset-class trend book; no stop loss."""
    _ = target_symbols, kwargs
    pipe = TrendAssetClassesPipeline(fdm=fdm, vol_span=vol_span, vol_target=vol_target,
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
