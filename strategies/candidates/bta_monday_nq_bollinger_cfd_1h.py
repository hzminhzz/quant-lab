"""BTA Monday NASDAQ 1-hour Bollinger/ATR breakthrough — CFD bar-confirmed adaptation.

Primary original publication:
https://breakouttradingacademy.com/steal-this-monday-nasdaq-strategy-super-simple/
Published 2026-07-16. Original: E-mini NQ 1-hour buy-stop at
lower Bollinger(10, 1 std) + 0.8 * ATR(5), Mondays midnight-23:00
Chicago, exit 23:00 Chicago same day. The author does not publish a
single-stop fill simulator compatible with Warden's NEXT_BAR contract.

EXPLICIT differences:
- Inception NQ CFD proxy, not CME NQ futures; historical spread differs.
- Assume long stop order from the positive breakout threshold. Source page
  does not explicitly list the trade side, so this is an assumption.
- At the close of bar t, observe whether high[t] touched the threshold
  derived entirely from completed bars <=t-1. If true, request long at
  NEXT_BAR[t+1], not an imaginary intrabar stop fill at the threshold.
- Bar timestamps denote START of hour; signal at 22:00 Chicago is zero
  so Warden's 1-bar delayed execution flattens at 23:00 local.
- The entry signal is restricted to local hours 00..21 to allow at least
  one full hour of holding. One entry per Monday; no leverage or stops.
- All 1800+ original-source parameter sweeps and 30 prior contract-family
  selection attempts are acknowledged as selection debt, not fresh OOS.

This is exploratory validation of the *adapted economic hypothesis* only.
No source article text is executed, no protected dataset is read, no orders.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

SOURCE_URL = "https://breakouttradingacademy.com/steal-this-monday-nasdaq-strategy-super-simple/"
CONTRACT_ID = "AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1"
SYMBOL = "NQ"
BOLLINGER_PERIOD = 10
BOLLINGER_STD = 1.0
ATR_PERIOD = 5
ATR_MULTIPLIER = 0.8
EXIT_HOUR_CHICAGO = 23
CHICAGO = ZoneInfo("America/Chicago")


class MondayNQBollingerPipeline:
    """All rollups use completed previous bars, never price[t+1]."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Contract mismatch: 24-hour research split required")
        data = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in data.columns and "asset" not in data.columns:
            data = data.rename({"symbol": "asset"})
        required = {"timestamp", "asset", "open", "high", "low", "close", "volume"}
        if not required.issubset(data.columns):
            raise ValueError(f"Missing verified OHLCV: {sorted(required - set(data.columns))}")
        data = data.sort("asset", "timestamp").with_columns(
            previous_close=pl.col("close").shift(1).over("asset"),
        )
        data = data.with_columns(
            true_range=pl.max_horizontal(
                pl.col("high") - pl.col("low"),
                (pl.col("high") - pl.col("previous_close")).abs(),
                (pl.col("low") - pl.col("previous_close")).abs(),
            ),
        )
        data = data.with_columns(
            lower_band_previous=(
                pl.col("close").rolling_mean(BOLLINGER_PERIOD).shift(1).over("asset")
                - BOLLINGER_STD * pl.col("close").rolling_std(
                    BOLLINGER_PERIOD, ddof=0
                ).shift(1).over("asset")
            ),
            atr_previous=(
                pl.col("true_range").rolling_mean(ATR_PERIOD).shift(1).over("asset")
            ),
        )
        return data.with_columns(
            threshold_previous=(
                pl.col("lower_band_previous")
                + ATR_MULTIPLIER * pl.col("atr_previous")
            ),
        ).sort("timestamp", "asset")


pipeline = MondayNQBollingerPipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError(f"Unreviewed strategy parameters: {sorted(kwargs)}")
    if target_symbols != [SYMBOL]:
        raise ValueError("Source-specific NQ CFD only; other indexes require a new hypothesis")
    if contract.contract_id != CONTRACT_ID or contract.execution_delay_bars != 1:
        raise ValueError("Missing approved next-hour CFD contract")
    features = pipeline.compute(panel_df, contract)
    assets = sorted(features["asset"].unique().to_list())
    if SYMBOL not in assets:
        raise ValueError("NQ missing from the pinned indices panel")
    stamps = sorted(features["timestamp"].unique().to_list())
    index = {stamp: idx for idx, stamp in enumerate(stamps)}
    positions = np.zeros((len(stamps), len(assets)), dtype=np.float64)
    nq_idx = assets.index(SYMBOL)

    nq = features.filter(pl.col("asset") == SYMBOL).select(
        "timestamp", "high", "low", "close", "volume", "threshold_previous"
    )
    # Signal is computed only when a completed NQ bar actually exists.
    # Source claims intrabar stop fills; here high[t] is used AFTER bar t
    # completes and Warden fills on t+1. Never use high[t] as the fill price.
    nq_by_stamp = {row[0]: row[1:] for row in nq.iter_rows()}
    in_position = False
    last_entry_date = None

    for stamp in stamps:
        chicago = stamp.astimezone(CHICAGO)
        idx = index[stamp]
        # An hour timestamp is its bar OPEN. To leave at 23:00 local,
        # flatten desired target at the 22:00 decision bar.
        if chicago.weekday() != 0 or chicago.hour >= EXIT_HOUR_CHICAGO - 1:
            in_position = False
        elif chicago.hour <= EXIT_HOUR_CHICAGO - 2 and not in_position:
            bar = nq_by_stamp.get(stamp)
            if bar is not None and last_entry_date != chicago.date():
                high, low, close, volume, trigger = bar
                if (trigger is not None and np.isfinite(trigger)
                        and np.isfinite(high) and np.isfinite(low)
                        and np.isfinite(close) and np.isfinite(volume)
                        and high > low and volume > 0
                        and low > 0 and close > 0
                        and high >= trigger):
                    in_position = True
                    last_entry_date = chicago.date()
        positions[idx, nq_idx] = 1.0 if in_position else 0.0

    if (not np.isfinite(positions).all()
            or np.any((positions < 0) | (positions > 1))):
        raise ValueError("Invalid source-adaptation target positions")
    return {"positions": positions}
