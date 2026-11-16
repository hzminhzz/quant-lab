"""Source-inspired ES CFD Dual Thrust, frozen research adaptation.

Original author tutorial: https://www.quantconnect.com/learning/articles/investment-strategy-library/dual-thrust-trading-algorithm
VPS captured 2026-10-30T17:38:27.152695Z, SHA256
b5c79f8dc6e44ff9974436cba63d723fde902f0a2a2a5b73834c223909bdefac.

Original: SPY EQUITY hourly intraday, N=4 prior daily range,
K1=K2=0.5; published SPY Sharpe -0.17. This is NOT replication.
Frozen ES CFD adaptation: prior four COMPLETE 08:30-15:00 Chicago
RTH sessions, 30m bar-confirmed close crossing cap/floor, NEXT_BAR fill,
RTH open fixed from first 08:30 30m bar, flat at 14:30 for 15:00
next-bar execution. No intrabar stop fills, no overnight position.
Source-side historical selection and CFD/cash session mismatch remain debt.
Contract costs 0.5 bps fee + 1.0 bps slippage PER SIDE.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

CONTRACT_ID = "AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1"
ASSET = "ES"
CENTRAL = ZoneInfo("America/Chicago")
N_SESSIONS = 4
K_LONG = 0.5
K_SHORT = 0.5
START_MIN = 8 * 60 + 30
FLAT_MIN = 14 * 60 + 30
END_MIN = 15 * 60


@dataclass(frozen=True)
class Session:
    day: date
    observations: tuple[tuple[datetime, float, float, float, float, float], ...]
    first_open: float
    highest_high: float
    lowest_low: float
    highest_close: float
    lowest_close: float
    complete: bool


class DualThrustPipeline:
    def compute(
        self,
        bars_df: pl.DataFrame | pl.LazyFrame,
        contract: EvaluationContract,
    ) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Unapproved Dual Thrust data contract")
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        needed = {"timestamp", "asset", "open", "high", "low", "close", "volume"}
        if not needed.issubset(df.columns):
            raise ValueError("Dual Thrust requires complete OHLCV")
        return df.sort("timestamp", "asset")


pipeline = DualThrustPipeline()


def _complete_sessions(df: pl.DataFrame) -> list[Session]:
    by_day: dict[date, list[tuple[datetime, float, float, float, float, float]]] = {}
    for timestamp, op, hi, lo, cl, vol in df.select(
        "timestamp", "open", "high", "low", "close", "volume"
    ).iter_rows():
        values = (float(op), float(hi), float(lo), float(cl), float(vol))
        if not np.isfinite(values).all() or op <= 0 or lo <= 0 or lo > hi:
            raise ValueError("Non-finite or invalid ES OHLCV")
        local = timestamp.astimezone(CENTRAL)
        clock = local.hour * 60 + local.minute
        if local.weekday() >= 5 or not START_MIN <= clock < END_MIN:
            continue
        by_day.setdefault(local.date(), []).append((timestamp, *values))
    result: list[Session] = []
    for day in sorted(by_day):
        items = sorted(by_day[day], key=lambda r: r[0])
        clock_minutes = [
            v[0].astimezone(CENTRAL).hour * 60
            + v[0].astimezone(CENTRAL).minute
            for v in items
        ]
        complete = (len(items) == 13 and
                    clock_minutes == list(range(START_MIN, END_MIN, 30)))
        result.append(Session(
            day=day,
            observations=tuple(items),
            first_open=items[0][1],
            highest_high=max(x[2] for x in items),
            lowest_low=min(x[3] for x in items),
            highest_close=max(x[4] for x in items),
            lowest_close=min(x[4] for x in items),
            complete=complete,
        ))
    return result


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError(f"Non-predeclared parameters: {sorted(kwargs)}")
    if target_symbols != [ASSET]:
        raise ValueError("Only preregistered ES CFD instrument is allowed")
    if contract.contract_id != CONTRACT_ID or contract.execution_delay_bars != 1:
        raise ValueError("Requires governed 30m next-bar contract")
    frame = pipeline.compute(panel_df, contract)
    ts = sorted(frame["timestamp"].unique().to_list())
    assets = sorted(frame["asset"].unique().to_list())
    if ASSET not in assets:
        raise ValueError("Missing ES CFD bars")
    asset_index = assets.index(ASSET)
    out = np.zeros((len(ts), len(assets)), dtype=float)
    index_by_time = {stamp: i for i, stamp in enumerate(ts)}
    history: list[Session] = []
    es = frame.filter(pl.col("asset") == ASSET).sort("timestamp")

    for session in _complete_sessions(es):
        if len(history) >= N_SESSIONS:
            prior = history[-N_SESSIONS:]
            hh = max(x.highest_high for x in prior)
            lc = min(x.lowest_close for x in prior)
            hc = max(x.highest_close for x in prior)
            ll = min(x.lowest_low for x in prior)
            span = max(hh - lc, hc - ll)
            cap = session.first_open + K_LONG * span
            floor = session.first_open - K_SHORT * span
            target = 0.0
            for stamp, op, hi, lo, close, volume in session.observations:
                clock = stamp.astimezone(CENTRAL).hour * 60 + stamp.astimezone(CENTRAL).minute
                if clock >= FLAT_MIN:
                    target = 0.0
                elif volume > 0 and span > 0:
                    if close > cap:
                        target = 1.0
                    elif close < floor:
                        target = -1.0
                out[index_by_time[stamp], asset_index] = target
        # Never let an incomplete/missing session contaminate the prior-four
        # range; skip it, rather than pretending it was a normal full RTH day.
        if session.complete:
            history.append(session)

    if not np.isfinite(out).all() or np.any(np.abs(out) > 1):
        raise ValueError("Invalid Dual Thrust positions")
    return {"positions": out}
