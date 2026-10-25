"""Synthetic benchmark only: market equations ported from run_vertical_slice.py."""

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

ASSETS = ["BTC-PERP", "ETH-PERP", "SOL-PERP", "BNB-PERP", "XRP-PERP", "ADA-PERP", "AVAX-PERP", "DOGE-PERP", "LINK-PERP", "DOT-PERP", "LTC-PERP", "UNI-PERP"]


def generate_canonical_market_panel(parquet_path: Path | None = None, seed: int = 42):
    rng = np.random.default_rng(seed)
    n_bars, n_assets = 2400, len(ASSETS)
    returns, funding = np.zeros((n_bars, n_assets)), np.zeros((n_bars, n_assets))
    market = rng.normal(0.0, 0.005, size=n_bars)
    for t in range(1, n_bars):
        funding[t] = 0.88 * funding[t - 1] + rng.normal(0.0, 0.00025, size=n_assets)
        signal = np.zeros(n_assets)
        if t >= 24:
            past = np.sum(returns[t - 12:t], axis=0)
            vol = np.std(returns[t - 24:t], axis=0) + 1e-6
            signal = np.where(np.abs(past) > 2.1 * vol * math.sqrt(12), np.sign(past), 0.0)
        cs_alpha = -0.32 * (funding[t - 1] - np.mean(funding[t - 1]))
        returns[t] = 0.7 * market[t] + cs_alpha + 0.00075 * signal + rng.normal(0.0, 0.0085, size=n_assets)
    close = 100.0 * np.exp(np.cumsum(returns, axis=0))
    opening = np.vstack([close[0], close[:-1]])
    timestamps = [datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=t) for t in range(n_bars)]
    panel = pl.DataFrame({
        "timestamp": np.repeat(timestamps, n_assets).tolist(), "asset": ASSETS * n_bars,
        "open": opening.ravel(), "high": (np.maximum(opening, close) * 1.001).ravel(),
        "low": (np.minimum(opening, close) * 0.999).ravel(), "close": close.ravel(),
        "volume": np.full(n_bars * n_assets, 1000.0), "funding_rate": funding.ravel(),
    }).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC"))).sort("timestamp", "asset")
    if parquet_path is not None:
        panel.write_parquet(parquet_path)
    return panel
