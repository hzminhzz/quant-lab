"""Weekly cross-sectional momentum: long the top fifth, short the bottom fifth.

Faithful to the claim under test (0xArchive write-up, forwarded by the principal 2026-10-28):
rank perps by trailing 7-day return, go long the top fifth, short the bottom fifth, repeat next
week. Two adaptations are pre-registered and disclosed in .workspace/hypotheses/HYP-2026-125.md:

  * venue   -- Binance UM top-100 panel instead of Hyperliquid;
  * universe -- point-in-time top-25 by trailing 7-day *quote* volume (volume x close), so a
    name can only be traded after it is liquid. Raw volume is not used: 1000SHIB/1000LUNC style
    symbols would otherwise rank on contract-unit inflation, not traded value.

A symbol whose data ends is closed at its own last tradable bar. The weekly cadence means the
surviving leg then runs one-sided (net exposure, half gross) until the next rebalance -- that is
what a weekly-only book actually experiences, so it is measured rather than smoothed away.

Causality: features use bars <= t only. Warden applies execution_delay_bars at simulation, so
positions must NOT be pre-lagged here (that raises DoubleLatencyError).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label

LOOKBACK_BARS = 168          # 7 days of 1h bars
MIN_WINDOW_COVERAGE = 0.8    # share of the 168-bar window that must be present


class XSWeeklyMomentumPipeline:
    """Causal features: quote volume per bar (the ranking and universe inputs are built from it)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        return df.sort("timestamp", "asset").with_columns(
            quote_vol=pl.col("volume") * pl.col("close"),
        )


def _matrix(df: pl.DataFrame, value: str, assets: list[str], n_bars: int, ts_index: dict) -> np.ndarray:
    """Pivot one column onto the full regular timestamp grid; absent cells stay NaN."""
    p = df.pivot(index="timestamp", on="asset", values=value).sort("timestamp")
    for a in assets:
        if a not in p.columns:
            p = p.with_columns(pl.lit(None, dtype=pl.Float64).alias(a))
    out = np.full((n_bars, len(assets)), np.nan, dtype=float)
    rows = np.array([ts_index[t] for t in p["timestamp"].to_list()], dtype=int)
    out[rows, :] = p.select(assets).to_numpy().astype(float)
    return out


def _lagged_close(close: np.ndarray, lag: int) -> np.ndarray:
    prev = np.full_like(close, np.nan)
    prev[lag:] = close[:-lag]
    return prev


def _rolling_sum(mat: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Trailing sum and the number of present observations in the same window."""
    filled = np.where(np.isfinite(mat), mat, 0.0)
    present = np.isfinite(mat).astype(float)
    cs, cc = np.cumsum(filled, axis=0), np.cumsum(present, axis=0)
    total = np.full_like(mat, np.nan)
    count = np.full_like(mat, np.nan)
    total[window:] = cs[window:] - cs[:-window]
    count[window:] = cc[window:] - cc[:-window]
    return total, count


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    lookback_bars: int = LOOKBACK_BARS,
    rebalance_every: int = LOOKBACK_BARS,
    universe_size: int = 25,
    quintile_fraction: float = 0.2,
    **kwargs: Any,
) -> dict[str, Any]:
    """Long the top fifth / short the bottom fifth of the point-in-time liquid universe."""
    df = XSWeeklyMomentumPipeline().compute(panel_df, contract)
    timestamps = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    n_bars, n_assets = len(timestamps), len(assets)
    if n_bars <= lookback_bars + 2:
        raise ValueError(f"panel too short for a {lookback_bars}-bar lookback ({n_bars} bars)")
    ts_index = {t: i for i, t in enumerate(timestamps)}

    close = _matrix(df, "close", assets, n_bars, ts_index)
    open_ = _matrix(df, "open", assets, n_bars, ts_index)
    qvol = _matrix(df, "quote_vol", assets, n_bars, ts_index)

    prev_close = _lagged_close(close, lookback_bars)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.where(
            np.isfinite(prev_close) & (prev_close > 0) & np.isfinite(close),
            close / prev_close - 1.0,
            np.nan,
        )
    dvol, cover = _rolling_sum(qvol, lookback_bars)
    dvol = np.where(cover >= MIN_WINDOW_COVERAGE * lookback_bars, dvol, np.nan)

    n_leg = max(1, int(round(universe_size * quintile_fraction)))
    w_each = 0.5 / n_leg                     # gross 1.0, dollar-neutral
    val_start_idx = ts_index.get(contract.val_start, int(n_bars * 0.25))
    val_end_idx = ts_index.get(contract.val_end, n_bars)
    first = max(val_start_idx, lookback_bars)

    # Last bar each asset actually trades. A symbol whose data ends is delisted or halted: it must
    # be closed at its own last tradable bar, never carried at a stale price (see HYP-2026-125).
    finite_close = np.isfinite(close)
    has_data = finite_close.any(axis=0)
    last_obs = np.where(has_data, n_bars - 1 - np.argmax(finite_close[::-1, :], axis=0), -1)

    positions = np.zeros((n_bars, n_assets), dtype=float)
    universes: dict[int, np.ndarray] = {}
    for t in range(first, val_end_idx):
        # The weekly grid is anchored on the panel origin (Sunday 00:00 UTC). The window's first
        # bar is also a rebalance so the book is not flat until the next grid point.
        if t % rebalance_every != 0 and t != val_start_idx:
            positions[t] = positions[t - 1]
            continue
        eligible = np.where(np.isfinite(ret[t]) & np.isfinite(dvol[t]) & (dvol[t] > 0))[0]
        if eligible.size < universe_size:
            positions[t] = positions[t - 1]
            continue
        universe = eligible[np.argsort(-dvol[t][eligible])[:universe_size]]
        order = universe[np.argsort(ret[t][universe])]      # ascending momentum
        weights = np.zeros(n_assets, dtype=float)
        weights[order[-n_leg:]] = w_each                    # winners: long
        weights[order[:n_leg]] = -w_each                    # losers: short
        positions[t] = weights
        universes[t] = universe

    # Force-flat any asset from its own last tradable bar onward. Without this the carried weight
    # would keep a dead symbol open at a stale price for up to one week, and the short leg would
    # silently collect a survivorship credit.
    for j, last in enumerate(last_obs):
        if last >= 0:
            positions[max(0, int(last)):, j] = 0.0

    # IC against the traded horizon (one week), not the contract's 4-bar label: the score is
    # meant to predict the next week's cross-section, and any shorter label measures a different
    # question. Disclosed in the hypothesis file.
    forward = compute_open_forward_label(open_, contract.execution_delay_bars, rebalance_every)
    ic_list: list[float] = []
    for t in range(val_start_idx, min(val_end_idx, n_bars - rebalance_every - contract.execution_delay_bars)):
        universe = universes.get(t)
        if universe is None:
            continue
        x, y = ret[t][universe], forward[t][universe]
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 5 or np.std(x[ok]) < 1e-12:
            continue
        ic_list.append(float(np.corrcoef(x[ok], y[ok])[0, 1]))
    ic_arr = np.array(ic_list, dtype=float)
    return {
        "positions": positions,
        "ic_series": ic_arr,
        "mean_ic": float(np.mean(ic_arr)) if ic_arr.size else None,
        "rebalances": len(universes),
    }


pipeline = XSWeeklyMomentumPipeline()
