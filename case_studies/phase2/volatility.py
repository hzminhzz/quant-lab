"""Pure causal minute-clock transformations. No data loading or scoring."""
from __future__ import annotations

import polars as pl

MINUTE = pl.duration(minutes=1)
HORIZONS = (5, 60, 1440)
HOUR_FEATURES = tuple(f"hour_{i:02d}" for i in range(1, 24))
BASE_FEATURES = ("rv5", "rv60", "rv1440", *HOUR_FEATURES)
CANDIDATE_FEATURES = (*BASE_FEATURES, "boundary_count")
VARIANCE_FLOOR = 1e-12


def validate_minute_bars(bars: pl.DataFrame) -> pl.DataFrame:
    """One instrument, explicit source availability, UTC minute-open timestamps.

    No filling, resampling, sorting ambiguity, or implicit publication time.
    Missing minutes are allowed but invalidate affected windows.
    """
    required = {"timestamp", "available_at", "open", "close"}
    if not required.issubset(bars.columns):
        raise ValueError(f"missing columns: {sorted(required - set(bars.columns))}")
    for name in ("timestamp", "available_at"):
        dtype = bars.schema[name]
        if not isinstance(dtype, pl.Datetime) or dtype.time_zone != "UTC":
            raise ValueError(f"{name} must be UTC datetime")
    if bars.is_empty():
        raise ValueError("empty input")
    if "symbol" in bars.columns and (
        bars["symbol"].null_count() or bars["symbol"].n_unique() != 1
    ):
        raise ValueError("one instrument only")
    out = bars.select(
        pl.col("timestamp", "available_at").cast(pl.Datetime("us", "UTC")),
        pl.col("open", "close").cast(pl.Float64),
    ).sort("timestamp")
    if out.null_count().sum_horizontal().item() != 0:
        raise ValueError("null input")
    if out["timestamp"].n_unique() != out.height:
        raise ValueError("duplicate minute")
    invalid = out.select(
        (
            (pl.col("timestamp").dt.epoch("us") % 60_000_000 != 0)
            | (pl.col("available_at") < pl.col("timestamp") + MINUTE)
            | ~pl.col("open").is_finite()
            | ~pl.col("close").is_finite()
            | (pl.col("open") <= 0)
            | (pl.col("close") <= 0)
        ).any()
    ).item()
    if invalid:
        raise ValueError("invalid price, minute clock, or premature availability")
    return out


def _squared_returns(price: str) -> pl.Expr:
    return (
        pl.when(pl.col("timestamp").diff() == MINUTE)
        .then((pl.col(price).log() - pl.col(price).log().shift(1)).pow(2))
        .otherwise(None)
    )


def volatility_features(bars: pl.DataFrame) -> pl.DataFrame:
    """Causal features at decision t, on completed bars through t-1 minute."""
    df = validate_minute_bars(bars).with_columns(
        _squared_returns("close").alias("_r2"),
        pl.col("available_at").dt.epoch("us").alias("_available_us"),
        (pl.col("timestamp") + MINUTE).alias("decision_timestamp"),
    )
    df = df.with_columns(
        *[pl.col("_r2").rolling_sum(k, min_samples=k).alias(f"rv{k}") for k in HORIZONS],
        pl.col("_available_us").rolling_max(1441, min_samples=1441).alias("_known_us"),
    )
    df = df.filter(
        (pl.col("decision_timestamp").dt.minute() % 5 == 0)
        & pl.col("rv1440").is_not_null()
        & (pl.col("_known_us") <= pl.col("decision_timestamp").dt.epoch("us"))
    ).with_columns(
        pl.from_epoch("_known_us", time_unit="us").dt.replace_time_zone("UTC")
        .alias("feature_available_at"),
        (pl.col("decision_timestamp") + MINUTE).alias("entry_timestamp"),
        (pl.col("decision_timestamp") + pl.duration(minutes=6)).alias("exit_timestamp"),
        *[
            (pl.col("decision_timestamp").dt.hour() == i).cast(pl.Float64)
            .alias(f"hour_{i:02d}")
            for i in range(1, 24)
        ],
    )
    # Integer UTC minutes. Count grid points b with entry <= b < exit.
    start = pl.col("entry_timestamp").dt.epoch("us") // 60_000_000
    end = pl.col("exit_timestamp").dt.epoch("us") // 60_000_000
    return df.with_columns(
        (((end - 1) // 15) - ((start - 1) // 15)).cast(pl.Float64)
        .alias("boundary_count")
    ).select(
        "decision_timestamp", "feature_available_at", "entry_timestamp", "exit_timestamp",
        *CANDIDATE_FEATURES,
    )


def variance_labels(bars: pl.DataFrame) -> pl.DataFrame:
    """Target at execution opens t+1,...,t+6; labels never enter feature code."""
    df = validate_minute_bars(bars).with_columns(
        _squared_returns("open").alias("_r2"),
        pl.col("available_at").dt.epoch("us").alias("_available_us"),
    ).with_columns(
        pl.col("_r2").rolling_sum(5, min_samples=5).alias("target_variance"),
        pl.col("_available_us").rolling_max(6, min_samples=6).alias("_known_us"),
    )
    return df.filter(pl.col("target_variance").is_not_null()).select(
        (pl.col("timestamp") - pl.duration(minutes=6)).alias("decision_timestamp"),
        (pl.col("timestamp") - pl.duration(minutes=5)).alias("entry_timestamp"),
        pl.col("timestamp").alias("exit_timestamp"),
        pl.from_epoch("_known_us", time_unit="us").dt.replace_time_zone("UTC")
        .alias("label_available_at"),
        "target_variance",
    )
