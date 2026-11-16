"""BTC 21-23 UTC seasonality; frozen source-inspired Binance-perp adaptation.

Original publisher: EdgeTools, TradingView, 2024-10-30,
https://www.tradingview.com/script/IzFZxayj/
Source page default: buy at 21:00 UTC, exit at 23:00 UTC, 2 hours.
Captured original HTML 2026-10-30 UTC, SHA256
a4d4ee62841aaad48207547cd7e7a39ab14130197e4943f9a6bf75017eac0c0f.

Source original instrument venue unspecified. THIS is not an original
spot/TradingView fill reproduction: Binance BTCUSDT USD-M perp 1h OHLCV,
native Warden NEXT_BAR at bar-open timestamp +1 hour. Signal at 20:00
for 21:00 open and flatten signal at 22:00 for 23:00 open.
Fixed 2-bar hold. Native taker 4.5+slippage 2bps per side, funding.
Source-selected favorable hours require multiple-comparison skepticism.
No stop, leverage, intrabar fills, holdout reads, or trading orders.
"""
from __future__ import annotations

from datetime import timedelta
from itertools import pairwise

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

CONTRACT_ID = "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1"
SYMBOL = "BTCUSDT"


class BtcUtc2123Pipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Contract mismatch")
        frame = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in frame.columns and "asset" not in frame.columns:
            frame = frame.rename({"symbol": "asset"})
        required = {"timestamp", "asset", "open", "high", "low", "close", "volume"}
        if not required.issubset(frame.columns):
            raise ValueError("Missing OHLCV field(s)")
        return frame.sort(["timestamp", "asset"])


pipeline = BtcUtc2123Pipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError(f"Parameters not preregistered: {sorted(kwargs)}")
    if target_symbols != [SYMBOL]:
        raise ValueError("BTCUSDT-only source adaptation")
    if (contract.contract_id != CONTRACT_ID
            or contract.frequency != "1h"
            or contract.execution_mode != "NEXT_BAR"
            or contract.execution_delay_bars != 1
            or contract.label_horizon_bars < 2):
        raise ValueError("Require approved 1h NEXT_BAR contract and 2h horizon")
    frame = pipeline.compute(panel_df, contract)
    times = sorted(frame["timestamp"].unique().to_list())
    assets = sorted(frame["asset"].unique().to_list())
    if SYMBOL not in assets:
        raise ValueError("Missing BTCUSDT")
    btc = frame.filter(pl.col("asset") == SYMBOL).sort("timestamp")
    stamps = btc["timestamp"].to_list()
    if len(stamps) != len(times) or stamps != times:
        raise ValueError("BTCUSDT has missing or duplicated panel timestamps")
    for prior, later in pairwise(stamps):
        if prior.tzinfo is None or later - prior != timedelta(hours=1):
            raise ValueError("BTC hourly timestamps must be continuous and timezone-aware")
    if stamps and stamps[-1].tzinfo is None:
        raise ValueError("Timestamps must be timezone-aware")
    pos = np.zeros((len(times), len(assets)), dtype=float)
    ix = assets.index(SYMBOL)
    index_by_time = {stamp: i for i, stamp in enumerate(times)}
    for t, vol in btc.select("timestamp", "volume").iter_rows():
        if not np.isfinite(vol) or vol < 0:
            raise ValueError("Invalid volume")
        # Under native NEXT_BAR signal at timestamp t becomes position
        # at next bar open t+1h. Hold 21:00->23:00 UTC.
        pos[index_by_time[t], ix] = 1.0 if t.hour in (20, 21) else 0.0
    return {"positions": pos}
