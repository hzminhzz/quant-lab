"""ES 15m RTH down-session rebound: new researcher adaptation of BTA previous-day tendency.

Source: https://breakouttradingacademy.com/big-index-trading-secret-to-get-real-results/
Author publishes a down-day tendency, NOT an entry/exit/sizing system.
This study fixes the RTH red-session test and an eight-bar early-next-session
holding rule before screening. Source continuous-roll and fills unverified.

A 15m timestamp is the interval START. A target at t uses the bar closed
at t+15m, then Warden delays entry to the next observed bar's open.
Never hold through the CME overnight session. No holdout access.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

CONTRACT_ID = "CME_ES_15M_SYNCSTER_V1"
NY = ZoneInfo("America/New_York")
RTH_FIRST = 570  # 09:30 New York, handles DST
RTH_LAST = 945  # 15:45, last completed regular-session bar
BAR_MINUTES = 15
EARLY_SLOTS = 8  # eight 15m decision bars, no optimization


class ESDownDayPipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID or contract.execution_delay_bars != 1:
            raise ValueError("Requires frozen CME ES 15m next-bar contract")
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        needed = {"timestamp", "asset", "open", "high", "low", "close", "observed_minutes"}
        if not needed.issubset(df.columns) or set(df["asset"].unique().to_list()) != {"ES"}:
            raise ValueError("Expected ES 15m observed CME bars")
        df = df.sort("timestamp", "asset")
        positions = np.zeros(df.height, dtype=np.float64)
        latest_rth_date: date | None = None
        last_closed_date: date | None = None
        prior_was_red: bool | None = None
        rth_count = 0
        rth_first_open = 0.0
        rth_full = False
        prior_slot = -1
        early_allowed = False

        for i, (ts, op, cl, minutes) in enumerate(
            df.select("timestamp", "open", "close", "observed_minutes").iter_rows()
        ):
            ny = ts.astimezone(NY)
            local_date = ny.date()
            minute = ny.hour * 60 + ny.minute
            if not (RTH_FIRST <= minute <= RTH_LAST) or (minute - RTH_FIRST) % BAR_MINUTES:
                continue
            slot = (minute - RTH_FIRST) // BAR_MINUTES
            full = minutes == 15
            if local_date != latest_rth_date:
                # A previous RTH session without the final completed 15m
                # bar is not a valid prior red day. Never carry stale signals.
                if latest_rth_date is not None and last_closed_date != latest_rth_date:
                    prior_was_red = None
                latest_rth_date = local_date
                rth_count, prior_slot = 0, -1
                rth_first_open, rth_full = 0.0, False
                early_allowed = bool(
                    prior_was_red and last_closed_date is not None
                    and 0 < (local_date - last_closed_date).days <= 5
                    and slot == 0 and full
                )
            if slot == 0:
                rth_first_open = float(op)
                rth_full = full
            else:
                if slot != prior_slot + 1:
                    early_allowed = False
                rth_full = rth_full and full and slot == prior_slot + 1
            rth_count += 1
            prior_slot = slot
            if slot < EARLY_SLOTS and full and early_allowed:
                positions[i] = 1.0
            elif slot < EARLY_SLOTS:
                early_allowed = False
            if slot == 25:
                last_closed_date = local_date
                prior_was_red = bool(
                    rth_full and rth_count == 26 and float(cl) < rth_first_open
                )
        return df.with_columns(pl.Series("desired_position", positions))


pipeline = ESDownDayPipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs or target_symbols != ["ES"]:
        raise ValueError("Unapproved ES universe or parameter override")
    frame = pipeline.compute(panel_df, contract)
    return {"positions": frame["desired_position"].to_numpy().reshape(-1, 1)}
