"""Pure session-aware closing-window feature and label construction.

The XNYS clock is an explicit cash-session proxy, not an exchange-futures
settlement calendar. No market data is loaded by this module.
"""
from __future__ import annotations

from datetime import date

import exchange_calendars as xc
import polars as pl

from case_studies.phase2.volatility import MINUTE, validate_minute_bars

BASE_FEATURES = ("rod",)
CANDIDATE_FEATURES = ("rod", "session_vol", "rod_x_vol")


def cash_sessions(start: str, end: str) -> pl.DataFrame:
    """UTC boundaries from the actual historical cash-exchange calendar."""
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("inverted session range")
    cal = xc.get_calendar("XNYS")
    schedule = cal.schedule.loc[start:end]
    if schedule.empty:
        raise ValueError("no sessions")
    return pl.DataFrame({
        "session_open": schedule["open"].dt.to_pydatetime(),
        "session_close": schedule["close"].dt.to_pydatetime(),
    }).with_columns(
        pl.col("session_open", "session_close").cast(pl.Datetime("us", "UTC"))
    ).sort("session_open")


def _clock(sessions: pl.DataFrame) -> pl.DataFrame:
    clock = sessions.select(
        pl.col("session_open", "session_close").cast(pl.Datetime("us", "UTC"))
    ).sort("session_open")
    if clock.is_empty() or clock["session_open"].n_unique() != clock.height:
        raise ValueError("empty or duplicate sessions")
    if clock.select(
        (
            (pl.col("session_close") <= pl.col("session_open") + pl.duration(minutes=30))
            | (pl.col("session_open") <= pl.col("session_close").shift(1))
            | (pl.col("session_open").dt.epoch("us") % 60_000_000 != 0)
            | (pl.col("session_close").dt.epoch("us") % 60_000_000 != 0)
        ).fill_null(False).any()
    ).item():
        raise ValueError("invalid or overlapping sessions")
    return clock.with_columns(
        (pl.col("session_close") - pl.duration(minutes=30)).alias("decision_timestamp"),
        (pl.col("session_close") - pl.duration(minutes=29)).alias("entry_timestamp"),
        pl.col("session_close").alias("exit_timestamp"),
        (pl.col("session_close").shift(1) - MINUTE).alias("previous_close_bar"),
    )


def _assign(bars: pl.DataFrame, clock: pl.DataFrame) -> pl.DataFrame:
    return validate_minute_bars(bars).join_asof(
        clock, left_on="timestamp", right_on="session_open", strategy="backward"
    ).filter(pl.col("timestamp") <= pl.col("session_close"))


def closing_features(bars: pl.DataFrame, sessions: pl.DataFrame) -> pl.DataFrame:
    """ROD and intraday variance using only completed, available prefix bars."""
    clock = _clock(sessions)
    prefix = _assign(bars, clock).filter(
        pl.col("timestamp") < pl.col("decision_timestamp")
    ).with_columns(
        pl.when(pl.col("timestamp") == pl.col("session_open"))
        .then((pl.col("close") / pl.col("open")).log())
        .otherwise(
            (pl.col("close") / pl.col("close").shift(1).over("session_open")).log()
        ).pow(2).alias("_r2")
    )
    grouped = prefix.group_by("session_open").agg(
        pl.len().alias("_count"),
        pl.col("timestamp").min().alias("_first"),
        pl.col("timestamp").max().alias("_last"),
        pl.col("available_at").max().alias("_known"),
        pl.col("close").last().alias("_price"),
        pl.col("_r2").sum().sqrt().alias("session_vol"),
    )
    prior = validate_minute_bars(bars).select(
        pl.col("timestamp").alias("previous_close_bar"),
        pl.col("close").alias("_previous_close"),
        pl.col("available_at").alias("_prior_known"),
    )
    return clock.join(grouped, on="session_open", how="inner").join(
        prior, on="previous_close_bar", how="inner", validate="m:1"
    ).filter(
        (pl.col("_first") == pl.col("session_open"))
        & (pl.col("_last") == pl.col("decision_timestamp") - MINUTE)
        & (pl.col("_count") ==
           (pl.col("decision_timestamp") - pl.col("session_open")).dt.total_minutes())
        & (pl.col("_known") <= pl.col("decision_timestamp"))
        & (pl.col("_prior_known") <= pl.col("decision_timestamp"))
    ).with_columns(
        (pl.col("_price") / pl.col("_previous_close")).log().alias("rod"),
        pl.max_horizontal("_known", "_prior_known").alias("feature_available_at"),
    ).with_columns(
        (pl.col("rod") * pl.col("session_vol")).alias("rod_x_vol")
    ).select(
        "session_open", "session_close", "decision_timestamp",
        "entry_timestamp", "exit_timestamp", "feature_available_at",
        *CANDIDATE_FEATURES,
    ).sort("decision_timestamp")


def closing_labels(bars: pl.DataFrame, sessions: pl.DataFrame) -> pl.DataFrame:
    """Exact entry/exit minute opens, refusing incomplete holding windows."""
    clock = _clock(sessions)
    holding = _assign(bars, clock).filter(
        pl.col("timestamp") >= pl.col("entry_timestamp")
    ).group_by("session_open").agg(
        pl.len().alias("_count"),
        pl.col("timestamp").min().alias("_first"),
        pl.col("timestamp").max().alias("_last"),
        pl.col("open").first().alias("_entry"),
        pl.col("open").last().alias("_exit"),
        pl.col("available_at").max().alias("label_available_at"),
    )
    return clock.join(holding, on="session_open", how="inner").filter(
        (pl.col("_first") == pl.col("entry_timestamp"))
        & (pl.col("_last") == pl.col("exit_timestamp"))
        & (pl.col("_count") ==
           (pl.col("exit_timestamp") - pl.col("entry_timestamp")).dt.total_minutes() + 1)
    ).select(
        "decision_timestamp", "entry_timestamp", "exit_timestamp", "label_available_at",
        (pl.col("_exit") / pl.col("_entry")).log().alias("target_return"),
    ).sort("decision_timestamp")
