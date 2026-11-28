"""NQ 15m RTH one-hour opening-range breakout, long-only researcher adaptation.

Primary mechanism: https://breakouttradingacademy.com/big-index-trading-secret-to-get-real-results/
BTA describes index breakout follow-through but DOES NOT publish this
opening-range entry, eight-bar expiry or sizing; all are pre-registered
researcher decisions. Continuous-roll and target-venue fills unverified.

Bar t closes at timestamp[t]+15m; Warden executes at next observed open.
Only complete 15m bars and a contiguous 09:30-10:15 NY opening range count.
No parameter optimization, overnight positions, or protected holdout.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

CONTRACT_ID = "CME_NQ_15M_SYNCSTER_V1"
NY = ZoneInfo("America/New_York")
FIRST = 570
LAST = 945
BAR_MINUTES = 15
OPENING_RANGE_BARS = 4
MAX_ENTRY_SLOT = 16  # 13:30, eight bars can exit before RTH close
HOLD_BARS = 8


class NQOpeningRangePipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID or contract.execution_delay_bars != 1:
            raise ValueError("Requires frozen CME NQ 15m next-bar contract")
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        needed = {"timestamp", "asset", "high", "low", "close", "observed_minutes"}
        if not needed.issubset(df.columns) or set(df["asset"].unique().to_list()) != {"NQ"}:
            raise ValueError("Expected NQ 15m observed CME bars")
        df = df.sort("timestamp", "asset")
        target = np.zeros(df.height, dtype=np.float64)
        current_day: date | None = None
        first_range_high, first_range_low = float("-inf"), float("inf")
        range_count, prior_slot = 0, -1
        usable = False
        used_today = False
        last_signal_slot = -1
        for i, (ts, hi, lo, cl, minutes) in enumerate(
            df.select("timestamp", "high", "low", "close", "observed_minutes").iter_rows()
        ):
            ny = ts.astimezone(NY)
            day, minute = ny.date(), ny.hour * 60 + ny.minute
            if not (FIRST <= minute <= LAST) or (minute - FIRST) % BAR_MINUTES:
                continue
            slot = (minute - FIRST) // BAR_MINUTES
            full = minutes == 15
            if day != current_day:
                current_day, range_count, prior_slot = day, 0, -1
                first_range_high, first_range_low = float("-inf"), float("inf")
                usable, used_today, last_signal_slot = True, False, -1
            if not full or (slot != 0 and prior_slot != slot - 1):
                usable = False
            prior_slot = slot
            if slot < OPENING_RANGE_BARS:
                if not usable or slot != range_count:
                    usable = False
                if usable:
                    range_count += 1
                    first_range_high = max(first_range_high, float(hi))
                    first_range_low = min(first_range_low, float(lo))
                continue
            if not usable or range_count != OPENING_RANGE_BARS:
                continue
            if last_signal_slot >= 0:
                if slot - last_signal_slot < HOLD_BARS:
                    target[i] = 1.0
                continue
            if not used_today and slot <= MAX_ENTRY_SLOT and float(cl) > first_range_high:
                target[i] = 1.0
                used_today = True
                last_signal_slot = slot
        return df.with_columns(pl.Series("desired_position", target))


pipeline = NQOpeningRangePipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs or target_symbols != ["NQ"]:
        raise ValueError("Unapproved NQ universe or parameter override")
    frame = pipeline.compute(panel_df, contract)
    return {"positions": frame["desired_position"].to_numpy().reshape(-1, 1)}
