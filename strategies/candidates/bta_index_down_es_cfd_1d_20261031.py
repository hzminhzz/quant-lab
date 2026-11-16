"""ES daily down-day reversal; independent bar-confirmed CFD hypothesis.

Original practitioner source: https://breakouttradingacademy.com/big-index-trading-secret-to-get-real-results/
Source was published 2022-12-01, captured 2026-10-30 SHA256
a5c0671d0136297977aebd4e3b81bc708fdd46cb56a063e263827ec20d205b5a.
Publisher states a completed down index day tends to be followed by an
up day. It does NOT publish an executable entry/exit/fee protocol.

FROZEN RESEARCHER ADAPTATION, NOT SOURCE REPLICATION:
- ES CFD daily bars only, red day defined Close[t] < Open[t] using
  a COMPLETED bar; desired +1 long at t, Warden fills NEXT_BAR at t+1.
- Desired flat after a non-red completed bar; Warden executes next day.
  Consecutive red days mean continuous exposure instead of churn.
- No intrabar stop, no shorting, no overnight funding estimate.
- This is an open-to-open CFD proxy, NOT cash-market intraday
  close-to-close SPY/S&P 500; Sunday session and settlement differences
  require further examination before downstream use.
- Native contract INDICES_1D_V1: 0.5bps fee+0.5bps slippage EACH side.
No holdout access or broker orders.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

CONTRACT_ID = "INDICES_1D_V1"
ASSET = "ES"


class DownDayReversalPipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("This requires governed INDICES_1D_V1")
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        needed = {"timestamp", "asset", "open", "high", "low", "close", "volume"}
        if not needed.issubset(df.columns):
            raise ValueError("Missing OHLCV")
        if df.select(
            (pl.col("open") <= 0).any() |
            (pl.col("high") < pl.col("low")).any() |
            (pl.col("volume") < 0).any()
        ).item():
            raise ValueError("Invalid source market data")
        return (
            df.sort("asset", "timestamp")
            .with_columns(
                day_red=(pl.col("close") < pl.col("open")).cast(pl.Int8)
            )
            .sort("timestamp", "asset")
        )


pipeline = DownDayReversalPipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError(f"Unreviewed parameters {sorted(kwargs)}")
    if target_symbols != [ASSET]:
        raise ValueError("This research is restricted to ES CFD")
    if contract.contract_id != CONTRACT_ID or contract.execution_delay_bars != 1:
        raise ValueError("Missing governed next-bar execution")
    frame = pipeline.compute(panel_df, contract)
    ts = sorted(frame["timestamp"].unique().to_list())
    assets = sorted(frame["asset"].unique().to_list())
    if ASSET not in assets:
        raise ValueError("Missing ES market")
    out = np.zeros((len(ts), len(assets)), dtype=float)
    index = assets.index(ASSET)
    rows = dict(frame.filter(pl.col("asset") == ASSET)
                .select("timestamp", "day_red").iter_rows())
    for i,t in enumerate(ts):
        out[i,index] = float(rows.get(t, 0))
    return {"positions": out}
