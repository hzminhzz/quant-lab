"""Causal, fail-closed Binance order-flow factors for 72-hour research.

The original HYP-2026-050 and HYP-2026-054 research modules used
fabricated fallbacks (volume*close/one trade or candle-direction flow).
This module NEVER substitutes proxies for observed Binance trade data.
A 1h bar's inputs are usable only after its close; Warden next-bar
execution is NOT pre-lagged here. No futures, labels, or target data in
feature construction. Source-specific research, not venue qualification.
"""

from __future__ import annotations

from typing import Literal

import polars as pl

Factor = Literal["ats", "ntr"]

REQUIRED = frozenset({
    "timestamp", "asset", "open", "high", "low", "close", "volume",
    "quote_volume", "trade_count", "taker_buy_base_volume",
    "taker_buy_quote_volume", "flow_valid",
})
HOURS = (24, 72, 168, 720)
FACTOR_FIELDS = {
    "ats": ("feat_ats_ratio_168", "feat_ats_ratio_72", "feat_ats_rank"),
    "ntr": ("feat_ntr_168", "feat_ntr_72", "feat_ntr_24"),
}


def _sum(name: str, window: int) -> pl.Expr:
    return pl.col(name).rolling_sum(window_size=window, min_samples=window).over("asset")


def _continuous(window: int) -> pl.Expr:
    # Missing observed hours must not be interpreted as a full lookback.
    return (pl.col("timestamp") - pl.col("timestamp").shift(window - 1).over("asset")
            == pl.duration(hours=window - 1))


def build_features(df: pl.DataFrame | pl.LazyFrame, factor: Factor) -> pl.DataFrame:
    """Use exclusively observed and contiguous hourly bars, including real zero trades."""
    if factor not in FACTOR_FIELDS:
        raise ValueError(f"Unknown order-flow family: {factor}")
    frame = df.collect() if isinstance(df, pl.LazyFrame) else df
    if "symbol" in frame.columns and "asset" not in frame.columns:
        frame = frame.rename({"symbol": "asset"})
    missing = REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"Missing observed Binance order-flow fields: {sorted(missing)}")
    if frame.schema["timestamp"] != pl.Datetime("us", "UTC"):
        raise ValueError("Expected UTC microsecond hourly timestamps")
    if frame.schema["flow_valid"] != pl.Boolean:
        raise ValueError("flow_valid must be a boolean source-validity mask")
    if frame.select(pl.struct(["timestamp", "asset"]).is_duplicated().any()).item():
        raise ValueError("Duplicate timestamp/asset rows")
    if frame.filter(pl.col("timestamp").dt.minute() != 0).height:
        raise ValueError("Non-hourly Binance observation")
    no_trade = (
        (pl.col("volume") == 0)
        & (pl.col("quote_volume") == 0)
        & (pl.col("trade_count") == 0)
        & (pl.col("taker_buy_base_volume") == 0)
        & (pl.col("taker_buy_quote_volume") == 0)
        & (pl.col("open") == pl.col("high"))
        & (pl.col("open") == pl.col("low"))
        & (pl.col("open") == pl.col("close"))
    )
    invalid_flag = frame.filter(
        (pl.col("flow_valid") & (
            (pl.col("quote_volume") <= 0)
            | (pl.col("trade_count") <= 0)
            | (pl.col("taker_buy_quote_volume") < 0)
            | (pl.col("taker_buy_quote_volume") > pl.col("quote_volume") * 1.0000001)
        )) | (~pl.col("flow_valid") & ~no_trade)
    )
    if invalid_flag.height:
        raise ValueError("flow_valid mask conflicts with observed trade records")

    frame = frame.sort("asset", "timestamp")
    windows = (72, 168, 720) if factor == "ats" else (24, 72, 168)
    sums: list[pl.Expr] = []
    for n in windows:
        sums += [
            _sum("quote_volume", n).alias(f"q_{n}"),
            _sum("trade_count", n).alias(f"count_{n}"),
            _continuous(n).alias(f"contiguous_{n}"),
        ]
        if factor == "ntr":
            sums.append(_sum("taker_buy_quote_volume", n).alias(f"buy_{n}"))
    frame = frame.with_columns(sums)

    signals: list[pl.Expr] = []
    if factor == "ats":
        for n in windows:
            good = pl.col(f"contiguous_{n}") & (pl.col(f"count_{n}") > 0)
            signals.append(
                pl.when(good)
                .then(pl.col(f"q_{n}") / pl.col(f"count_{n}"))
                .otherwise(None).alias(f"ats_{n}")
            )
        frame = frame.with_columns(signals)
        signals = [
            pl.when((pl.col("ats_720") > 0) & pl.col("flow_valid"))
            .then(pl.col("ats_168") / pl.col("ats_720"))
            .otherwise(None).alias("raw_ats_ratio_168"),
            pl.when((pl.col("ats_720") > 0) & pl.col("flow_valid"))
            .then(pl.col("ats_72") / pl.col("ats_720"))
            .otherwise(None).alias("raw_ats_ratio_72"),
        ]
    else:
        for n in windows:
            valid = pl.col(f"contiguous_{n}") & (pl.col(f"q_{n}") > 0) & pl.col("flow_valid")
            signals.append(
                pl.when(valid)
                .then((2 * pl.col(f"buy_{n}") - pl.col(f"q_{n}")) / pl.col(f"q_{n}"))
                .otherwise(None).alias(f"raw_ntr_{n}")
            )
    frame = frame.with_columns(signals)
    if factor == "ats":
        signals = [
            _zscore("raw_ats_ratio_168", "feat_ats_ratio_168"),
            _zscore("raw_ats_ratio_72", "feat_ats_ratio_72"),
            pl.when(pl.col("raw_ats_ratio_168").is_not_null())
            .then(pl.col("raw_ats_ratio_168").rank().over("timestamp")
                  / pl.col("raw_ats_ratio_168").count().over("timestamp"))
            .otherwise(None).alias("feat_ats_rank"),
        ]
    else:
        signals = [_zscore(f"raw_ntr_{n}", f"feat_ntr_{n}") for n in (168, 72, 24)]
    return frame.with_columns(signals).sort("timestamp", "asset")


def _zscore(src: str, dest: str) -> pl.Expr:
    std = pl.col(src).std().over("timestamp")
    return pl.when(std > 1e-12).then(
        (pl.col(src) - pl.col(src).mean().over("timestamp")) / std
    ).otherwise(None).alias(dest)
