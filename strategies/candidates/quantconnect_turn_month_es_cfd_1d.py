"""QuantConnect turn-of-month equity flow — explicitly delayed ES CFD adaptation.

Original publicly documented idea:
https://www.quantconnect.com/research/15377/turn-of-the-month-in-equity-indexes/
Original SPY equity ETF trades after the NYSE market opens on the final
trading session of each month, then exits after ~3 trading days.

This adaptation uses *ES CFD* daily bars from the human-owned Warden
INDICES_1D_V1 dataset. We CANNOT execute original SPY opening orders:
a NYSE last-session close signal becomes NEXT_BAR (often a partial Sunday
CFD session) and exits following the third NYSE session of next month.
Positions therefore differ materially from the author's implementation.

NYSE calendar is a deterministic public timetable, known independently
of future price/return bars. Do not infer month-end from observed future
market values or a survivor-selected ETF membership panel.

The 2015..2025-06 pinned ES exploratory panel has 125 complete monthly
windows, with maximum signal holding span 5 dataset bars (source 3 trading
sessions + occasional Sunday). The 2025-06 incomplete final window is
not opened. No parameter search, optimized holding duration or stops.
"""
from __future__ import annotations

from datetime import date, timedelta

import exchange_calendars as xc
import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

SOURCE_URL = "https://www.quantconnect.com/research/15377/turn-of-the-month-in-equity-indexes/"
CONTRACT_ID = "INDICES_1D_V1"
SYMBOL = "ES"
SESSIONS_HOLD = 3
DATASET_LAST = date(2025, 6, 30)
CALENDAR = "XNYS"


def _calendar_windows(first: date, last: date) -> tuple[set[date], set[date]]:
    """Known NYSE trading calendar, independent of price history and returns.

    Emission: last NYSE trading day at *bar close*, desired long 1.
    Emission: third NYSE trading day of next month at *bar close*, flat.
    Warden NEXT_BAR inserts the sole actual execution lag.
    """
    calendar = xc.get_calendar(CALENDAR)
    sessions = calendar.sessions_in_range(
        (first - timedelta(days=35)).isoformat(),
        (last + timedelta(days=42)).isoformat(),
    )
    bymonth: dict[tuple[int, int], list[date]] = {}
    for session in sessions:
        day = session.date()
        bymonth.setdefault((day.year, day.month), []).append(day)
    starts: set[date] = set()
    ends: set[date] = set()
    for (y, m), dates in bymonth.items():
        if not dates:
            continue
        next_month = (y + int(m == 12), m % 12 + 1)
        following = bymonth.get(next_month)
        if not following or len(following) < SESSIONS_HOLD:
            continue
        entry = dates[-1]
        exit_day = following[SESSIONS_HOLD - 1]
        # Neither historical backtest boundary nor a missing 2025-07 price
        # is permitted to create unresolved post-validation positions.
        # The publicly known future exchange calendar may define the exit
        # even when a prefix stops before that date. Filtering by the prefix
        # endpoint would introduce non-causal prefix-dependent positions.
        if first <= entry and exit_day <= DATASET_LAST:
            starts.add(entry)
            ends.add(exit_day)
    return starts, ends


class TurnMonthESPipeline:
    def compute(
        self, bars_df: pl.DataFrame | pl.LazyFrame,
        contract: EvaluationContract,
    ) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Only the approved ES index daily contract")
        data = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in data.columns and "asset" not in data.columns:
            data = data.rename({"symbol": "asset"})
        if not {"timestamp", "asset", "close", "volume"}.issubset(data.columns):
            raise ValueError("Verified daily OHLCV incomplete")
        if data.is_empty():
            raise ValueError("No market data")
        return data.sort("timestamp", "asset")


pipeline = TurnMonthESPipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError("Unreviewed calendar parameters")
    if target_symbols != [SYMBOL]:
        raise ValueError("No ETF/stock/other index substitution authorized")
    if (contract.contract_id != CONTRACT_ID
            or contract.execution_delay_bars != 1
            or contract.label_horizon_bars < 5):
        raise ValueError("Missing governed next-day market execution")
    data = pipeline.compute(panel_df, contract)
    stamps = sorted(data["timestamp"].unique().to_list())
    assets = sorted(data["asset"].unique().to_list())
    if SYMBOL not in assets:
        raise ValueError("ES absent from verified market panel")
    if stamps[-1].date() > DATASET_LAST:
        raise ValueError("No new market snapshot / protected data supported")
    es_index = assets.index(SYMBOL)
    es = data.filter(pl.col("asset") == SYMBOL).select(
        "timestamp", "close", "volume"
    )
    valid = {
        stamp.date(): bool(np.isfinite(close) and close > 0
                           and np.isfinite(volume) and volume > 0)
        for stamp, close, volume in es.iter_rows()
    }
    starts, ends = _calendar_windows(stamps[0].date(), stamps[-1].date())
    output = np.zeros((len(stamps), len(assets)), dtype=float)
    active = False
    # A position is only created on a *completed* month-end ES daily bar;
    # third party open orders are NOT replicated. An extra next-day shift
    # occurs only in native Warden, not in our generated position series.
    for i, stamp in enumerate(stamps):
        day = stamp.date()
        if day in ends:
            active = False
        if day in starts and valid.get(day, False):
            active = True
        output[i, es_index] = float(active)
    if not np.isfinite(output).all() or not (0 <= output).all():
        raise ValueError("Invalid monthly position weights")
    return {"positions": output}
