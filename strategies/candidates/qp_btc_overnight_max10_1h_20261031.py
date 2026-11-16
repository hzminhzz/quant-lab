"""Quantpedia BTC MAX(10) overnight: predeclared weekday-only Binance-perp adaptation.

Original primary publisher: https://quantpedia.com/how-to-profitably-trade-bitcoins-overnight-sessions/
Quantpedia, Cyril Dujava, 2024-11-12, original Gemini BTC session study:
10-NYSE-day-high entry at regular NYSE 16:00 close, exit at 10:00 NY next open;
its final strategy also trades FRIDAY-to-MONDAY, which exceeds 24h Warden label.

THIS IS NOT A REPLICATION. Frozen adaptation BEFORE any economic screen:
- Exclude Friday-to-Monday and retain only Monday and Tuesday 16:00 NY entries,
  flatten 10:00 NY next calendar day; never shorten an actual held position.
- Use Binance BTCUSDT USD-M perpetual, not source's Gemini spot price,
  and Warden's 4.5bps taker + 2bps slippage/side + funding (native cost).
- The 10-day maximum is the highest of the latest ten *completed regular
  NYSE-session 16:00 BTC hourly closes including the current close*.
  No prior-day window is chosen based on performance.
- Warden 1h timestamps are bar OPEN, its NEXT_BAR engine executes one
  hour AFTER the signal bar opens: 15:00 NY signal -> 16:00 entry,
  09:00 NY desired-flat -> 10:00 next-bar exit.
- XNYS exchange calendar excludes holidays/early-closing sessions.
- The source's 5/10/20/30/40/50-day sweep and session variants count
  as author-side multiple testing, NOT fresh independent OOS evidence.
- No leverage, stop, price prediction, holdout access or broker orders.
"""
from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

CONTRACT_ID = "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1"
ASSET = "BTCUSDT"
NEW_YORK = ZoneInfo("America/New_York")
LOOKBACK_SESSIONS = 10
ENTRY_WEEKDAYS = frozenset({0, 1})  # Mon/Tue only, not Friday 66h hold


class BitcoinOvernightPipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Wrong governed crypto-perpetual contract")
        frame = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in frame.columns and "asset" not in frame.columns:
            frame = frame.rename({"symbol": "asset"})
        required = {"timestamp", "asset", "open", "high", "low", "close", "volume"}
        if not required.issubset(frame.columns):
            raise ValueError("Missing BTC OHLCV bars")
        return frame.sort("timestamp", "asset")


pipeline = BitcoinOvernightPipeline()


def _regular_nyse_sessions(first: date, last: date) -> set[date]:
    """Daily XNYS close must be exactly 16:00 ET, not an early-close day."""
    calendar = xcals.get_calendar("XNYS")
    schedule = calendar.schedule.loc[str(first):str(last)]
    return {
        day.date() for day, close in schedule["close"].items()
        if close.tz_convert(NEW_YORK).hour == 16
        and close.tz_convert(NEW_YORK).minute == 0
    }


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError(f"Non-preregistered parameters: {sorted(kwargs)}")
    if target_symbols != [ASSET]:
        raise ValueError("Source-inspired study is BTCUSDT only")
    if (contract.contract_id != CONTRACT_ID
            or contract.execution_delay_bars != 1
            or contract.label_horizon_bars < 24
            or contract.execution_mode != "NEXT_BAR"):
        raise ValueError("Must use governed 1h/24h NEXT_BAR contract")
    frame = pipeline.compute(panel_df, contract)
    all_times = sorted(frame["timestamp"].unique().to_list())
    assets = sorted(frame["asset"].unique().to_list())
    if ASSET not in assets:
        raise ValueError("BTCUSDT source missing")
    target = frame.filter(pl.col("asset") == ASSET).sort("timestamp")
    stamps = target["timestamp"].to_list()
    if not stamps:
        raise ValueError("No BTC source observations")
    regular = _regular_nyse_sessions(
        stamps[0].astimezone(NEW_YORK).date(),
        stamps[-1].astimezone(NEW_YORK).date(),
    )
    pos = np.zeros((len(all_times), len(assets)), dtype=float)
    index_by_time = {stamp: i for i, stamp in enumerate(all_times)}
    close_history: list[float] = []
    entry_date: date | None = None
    target_ix = assets.index(ASSET)
    for stamp, close, volume in target.select(
        "timestamp", "close", "volume"
    ).iter_rows():
        if not np.isfinite(close) or close <= 0 or not np.isfinite(volume) or volume < 0:
            raise ValueError("Invalid source OHLCV")
        local = stamp.astimezone(NEW_YORK)
        day = local.date()
        if entry_date is not None and day > entry_date and local.hour >= 9:
            entry_date = None  # desired flat at 09:00, NEXT_BAR exit at 10:00
        if local.hour == 15 and day in regular and volume > 0:
            close_history.append(float(close))
            if (len(close_history) >= LOOKBACK_SESSIONS
                    and local.weekday() in ENTRY_WEEKDAYS
                    and close >= max(close_history[-LOOKBACK_SESSIONS:])):
                entry_date = day
        pos[index_by_time[stamp], target_ix] = float(entry_date is not None)
    if np.any(~np.isfinite(pos)):
        raise ValueError("Non-finite positions")
    return {"positions": pos}
