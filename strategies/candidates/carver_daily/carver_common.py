"""Causal primitives shared by the Carver AF30 daily strategy suite.

Strategy modules load this as a sibling: `sys.path.insert(0, str(Path(__file__).parent))`
then `from carver_common import ...`. Neither `warden verify` (in-process) nor pytest's
rootdir puts the repo root on `sys.path`, so a `strategies.*` import fails there.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl

FORECAST_AVG = 10.0
FORECAST_CAP = 20.0

# Instrument -> asset class, per .workspace/af30_campaign/multi_asset_coverage_audit.md.
ASSET_CLASSES: dict[str, str] = {
    "ES": "equity_us",
    "NQ": "equity_us",
    "DAX": "equity_eu",
    "EUSIDXEUR": "equity_eu",
    "HK33": "equity_asia",
    "NIKKEI225": "equity_asia",
    "AUDUSD": "fx",
    "CADJPY": "fx",
    "EURUSD": "fx",
    "GBPJPY": "fx",
    "GBPUSD": "fx",
    "USDCAD": "fx",
    "USDCHF": "fx",
    "USDJPY": "fx",
    "XAGUSD": "metals",
    "XAUUSD": "metals",
    "CL": "energy",
}


def ewmac(close: pl.Expr, fast: int, slow: int) -> pl.Expr:
    """Carver EWMAC trend: (EMA_fast - EMA_slow) / price."""
    return (close.ewm_mean(span=fast) - close.ewm_mean(span=slow)) / close


def ann_vol(close: pl.Expr, window: int, bars_per_year: int, min_samples: int = 2) -> pl.Expr:
    """RAW statistic: annualised close-to-close volatility of the window ending NOW.

    Not lagged, and not for position sizing -- use `price_vol` or `ewma_price_vol` there.
    The one-bar lag those add is a timing convention rather than a leak fix: the contract
    already delays execution by one bar, so a signal computed from bar t trades bar t+1.
    """
    returns = close.pct_change()
    return returns.rolling_std(window, min_samples=min_samples) * math.sqrt(bars_per_year)


def price_vol(close: pl.Expr, window: int, bars_per_year: int, min_samples: int = 2) -> pl.Expr:
    """ann_vol lagged one bar, so sizing never uses the bar it trades."""
    return ann_vol(close, window, bars_per_year, min_samples).shift(1)


def ewma_price_vol(close: pl.Expr, span: int, bars_per_year: int) -> pl.Expr:
    """Carver p. 206 volatility: EWMA span-35 std of returns, annualised, lagged one bar."""
    return (close.pct_change().pow(2).ewm_mean(span=span).sqrt() * math.sqrt(bars_per_year)).shift(1)


def forecast_from_trend(
    trend: pl.Expr, price_vol: pl.Expr, scalar: float, cap: float = FORECAST_CAP
) -> pl.Expr:
    """Carver forecast scaling: trend strength / price volatility * scalar, clipped."""
    return (trend / price_vol * scalar).clip(-cap, cap)


def cash_vol_positions(
    forecast: np.ndarray | float,
    vol: np.ndarray | float,
    weights: np.ndarray | float,
    vol_target: float,
    cap: float = 0.5,
) -> np.ndarray:
    """(forecast / 10) * (vol_target * weight / vol), clipped to the per-instrument cap."""
    safe_vol = np.maximum(np.asarray(vol, dtype=float), 1e-8)
    raw = (np.asarray(forecast, dtype=float) / FORECAST_AVG) * (vol_target * np.asarray(weights, dtype=float) / safe_vol)
    return np.clip(raw, -cap, cap)


def apply_buffer(target: float, held: float, buffer_fraction: float, weight: float) -> float:
    """Carver pp. 193-195: keep the held weight until the target drifts past the deadband."""
    return float(held) if abs(float(target) - float(held)) <= buffer_fraction * weight else float(target)


def value_matrix(df: pl.DataFrame, col: str, assets: list[str]) -> np.ndarray:
    """Timestamp x asset matrix; a bar an instrument did not trade is 0."""
    frame = df.pivot(index="timestamp", on="asset", values=col).sort("timestamp")
    missing = [a for a in assets if a not in frame.columns]
    if missing:
        frame = frame.with_columns([pl.lit(0.0).alias(a) for a in missing])
    return frame.select(assets).fill_null(0.0).to_numpy().copy()  # callers mask the warmup


def observation_mask(df: pl.DataFrame, assets: list[str]) -> np.ndarray:
    """True where the instrument printed a bar at that timestamp (sparse session union)."""
    frame = df.with_columns(_obs=pl.lit(True)).pivot(index="timestamp", on="asset", values="_obs").sort("timestamp")
    missing = [a for a in assets if a not in frame.columns]
    if missing:
        frame = frame.with_columns([pl.lit(False).alias(a) for a in missing])
    return frame.select(assets).fill_null(False).to_numpy().astype(bool)


def fill_held(target: np.ndarray, observed: np.ndarray) -> np.ndarray:
    """Carry the last weight across closed bars; a closed bar is not a trade."""
    held = np.where(observed, target, np.nan)
    frame = pl.DataFrame(held, schema=[f"a{i}" for i in range(held.shape[1])], nan_to_null=True)
    return frame.select(pl.all().forward_fill().fill_null(0.0)).to_numpy()

def normalised_step(close: pl.Expr, vol_bar: pl.Expr, scale: float = 100.0) -> pl.Expr:
    """Carver S17 increment: 100 risk-adjusted returns; call with .over("asset")."""
    return pl.when(vol_bar > 0).then(scale * close.pct_change() / vol_bar).otherwise(0.0)


def normalised_price(close: pl.Expr, vol_bar: pl.Expr, scale: float = 100.0) -> pl.Expr:
    """Cumulative normalised price PN; call with .over("asset")."""
    return normalised_step(close, vol_bar, scale).cum_sum()


def class_index(df: pl.DataFrame, classes: dict[str, str] | None = None) -> pl.DataFrame:
    """Carver S18 asset-class index: equal-weighted mean of member normalised-price
    increments, cumulated per class. Returns timestamp, cls, level."""
    mapping = pl.DataFrame(
        {"asset": list((classes or ASSET_CLASSES).keys()), "cls": list((classes or ASSET_CLASSES).values())}
    )
    long = df.join(mapping, on="asset", how="inner").with_columns(dp=pl.col("pn").diff().over("asset"))
    return (
        long.group_by(["timestamp", "cls"])
        .agg(pl.col("dp").mean().alias("r"))
        .sort("timestamp")
        .with_columns(level=pl.col("r").cum_sum().over("cls"))
    )
