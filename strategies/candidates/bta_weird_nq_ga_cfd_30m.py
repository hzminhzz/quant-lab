"""BTA weird NQ genetic-algorithm breakout — explicit CFD NEXT_BAR adaptation.

Source: https://breakouttradingacademy.com/weird-nasdaq-algo-strategy/
Source date 2024-05-22. Original uses NQ FUTURES 30-minute OHLC,
long stop trigger = last high + abs(typical price - highest open 35 bars),
07:00-18:00 America/New_York and closes same session with a $1,000
per-NQ-futures-contract stop. Formula was selected by genetic algorithm
through thousands of prior candidates: substantial source selection debt.

THIS IS A DIFFERENT EXECUTION STRATEGY, not a reproduction:
- NQ CFD proxy (not CME NQ contract), 30-min data, no futures $/point.
- "Typical price" is explicitly previous COMPLETE bar HLC3; source's
  exact original typical-price sampling instant not fully specified.
- After bar t COMPLETES, a high[t] touch confirms a signal. Native
  Warden executes on NEXT_BAR[t+1], not intrabar stop entry.
- Source's $1,000 futures stop cannot be executed price-faithfully by
  a portfolio-weighted next-bar engine. This research does NOT model it.
  Intrabar $-stop PnL cannot be inferred from OHLC with quote timestamps.
- Conservative last entry target 17:00 ET, desired flat at 17:30 ET
  for native t+1 flatten at 18:00; no overnight by intention, but
  unexpected early market closures remain an execution-data risk.
- Author's optimized results cannot be treated as independent OOS.
No protected holdout reads or broker orders.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

CONTRACT_ID = "AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1"
SOURCE = "https://breakouttradingacademy.com/weird-nasdaq-algo-strategy/"
EASTERN = ZoneInfo("America/New_York")
ASSET = "NQ"
OPEN_LOOKBACK = 35
EXIT_HOUR = 17
EXIT_MINUTE = 30


class NQWeirdBreakoutPipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Expected governed 24 half-hour index CFD contract")
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        need = {"timestamp", "asset", "open", "high", "low", "close", "volume"}
        if not need.issubset(df.columns):
            raise ValueError("Missing OHLCV fields")
        return (
            df.sort("asset", "timestamp")
            .with_columns(
                prior_high=pl.col("high").shift(1).over("asset"),
                prior_typical=(
                    (pl.col("high") + pl.col("low") + pl.col("close")) / 3
                ).shift(1).over("asset"),
                prior_35_highest_open=(
                    pl.col("open").rolling_max(OPEN_LOOKBACK)
                    .shift(1).over("asset")
                ),
            )
            .with_columns(
                breakout_level=pl.col("prior_high") + (
                    pl.col("prior_typical") - pl.col("prior_35_highest_open")
                ).abs()
            )
            .sort("timestamp", "asset")
        )


pipeline = NQWeirdBreakoutPipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError(f"Unreviewed parameters {sorted(kwargs)}")
    if target_symbols != [ASSET]:
        raise ValueError("Original NQ futures breakout adapted ONLY to NQ CFD")
    if contract.contract_id != CONTRACT_ID or contract.execution_delay_bars != 1:
        raise ValueError("Must use approved 30-minute next-bar execution")
    feat = pipeline.compute(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    if ASSET not in assets:
        raise ValueError("No NQ data")
    stamps = sorted(feat["timestamp"].unique().to_list())
    nq = feat.filter(pl.col("asset") == ASSET).select(
        "timestamp", "high", "low", "close", "volume", "breakout_level"
    )
    nqrows = {row[0]: row[1:] for row in nq.iter_rows()}
    output = np.zeros((len(stamps), len(assets)), dtype=np.float64)
    nq_index = assets.index(ASSET)
    in_pos = False
    prior_date = None
    entered = False

    for idx, ts in enumerate(stamps):
        local = ts.astimezone(EASTERN)
        if local.date() != prior_date:
            in_pos = False
            entered = False
            prior_date = local.date()
        minute_of_day = local.hour * 60 + local.minute
        exit_minute = EXIT_HOUR * 60 + EXIT_MINUTE
        if minute_of_day >= exit_minute or minute_of_day < 7 * 60:
            in_pos = False
        elif not in_pos and not entered:
            current = nqrows.get(ts)
            # Source is a CME futures high-touched stop; CFD current bar
            # high is only evidence AFTER COMPLETION, never the fill price.
            if current is not None:
                high, low, close, volume, trigger = current
                if (trigger is not None and np.isfinite(trigger)
                    and all(np.isfinite(x) for x in (high, low, close, volume))
                    and high > low > 0 and close > 0 and volume > 0
                    and high > trigger):
                    in_pos = True
                    entered = True
        output[idx, nq_index] = float(in_pos)
    if not np.isfinite(output).all() or np.any(output < 0) or np.any(output > 1):
        raise ValueError("Invalid NQ position matrix")
    return {"positions": output}
