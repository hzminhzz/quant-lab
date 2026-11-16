"""Pure calendar NQ CFD Monday long window (explicit next-bar adaptation).

Original: https://breakouttradingacademy.com/i-tested-300-nasdaq-time-strategies-i-found-the-1-outlier/
Published 2026-06-17; source SHA256
481214f3307b889043db45d61ba54380d02deab2acb7068ea4ec0afb8a249700.
Original NQ futures tested 276 hourly entry/exit combinations and selected
Monday 10:00-22:00. Author source does NOT explicitly state session timezone.
This adapts to NQ CFD hourly with AMERICA/CHICAGO clock, native NEXT_BAR.
It is NOT independent OOS: publisher selected through 276 time windows.
- To ENTER at 10:00 Chicago next bar, emit long signal at 09:00;
- To EXIT at 22:00 Chicago next bar, emit flat at 21:00.
- Only Mondays; no stop, fixed long weight +1, no extra filters.
- Source did not prescribe contract spread/fee; governed 1.5bps one-way.
- No holdout reads, source-selected windows carry major multiplicity debt.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

CONTRACT_ID = "AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1"
ASSET = "NQ"
CHI = ZoneInfo("America/Chicago")
SIGNAL_START = 9
SIGNAL_END = 21


class NQMondayTimePipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Wrong governed hourly index contract")
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        required = {"timestamp", "asset", "open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            raise ValueError("Incomplete source bars")
        df = df.sort("timestamp", "asset")
        flags = []
        for stamp in df["timestamp"].to_list():
            local = stamp.astimezone(CHI)
            flags.append(int(local.weekday() == 0 and
                             SIGNAL_START <= local.hour < SIGNAL_END))
        return df.with_columns(pl.Series("monday_window", flags, dtype=pl.Int8))


pipeline = NQMondayTimePipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError(f"Unreviewed parameters: {sorted(kwargs)}")
    if contract.contract_id != CONTRACT_ID or contract.execution_delay_bars != 1:
        raise ValueError("Must use governed hourly next-bar CFD contract")
    if target_symbols != [ASSET]:
        raise ValueError("Original NQ futures source adapted only to NQ CFD")
    features = pipeline.compute(panel_df, contract)
    assets = sorted(features["asset"].unique().to_list())
    stamps = sorted(features["timestamp"].unique().to_list())
    if ASSET not in assets:
        raise ValueError("NQ not available")
    flags = dict(features.filter(pl.col("asset") == ASSET)
                 .select("timestamp", "monday_window").iter_rows())
    weights = np.zeros((len(stamps), len(assets)), dtype=float)
    for i, stamp in enumerate(stamps):
        weights[i, assets.index(ASSET)] = float(flags.get(stamp, 0))
    return {"positions": weights}
