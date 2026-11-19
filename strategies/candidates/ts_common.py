"""Shared rules_ts primitives: iterative water-filling to a per-instrument cap,
trailing-ATR-stop overlays, and legacy asset-column normalization.

Copied verbatim across the AF30 1h trend modules; owned here once. Loaded as a
sibling (warden's strategy_loader puts the module directory on sys.path).
"""

from __future__ import annotations

import numpy as np
import polars as pl


def capped_waterfall_weights(raw_weights: np.ndarray, cap: float = 0.5, gross: float = 1.0) -> np.ndarray:
    """Enforce exact per-instrument risk budget cap by iterative water-filling."""
    n = len(raw_weights)
    if n == 0 or np.sum(raw_weights) <= 0:
        return np.full(n, gross / max(1, n))
    w = (raw_weights / np.sum(raw_weights)) * gross
    frozen = np.zeros(n, dtype=bool)
    for _ in range(n):
        over = (w > cap + 1e-12) & ~frozen
        if not np.any(over):
            break
        w[over] = cap
        frozen[over] = True
        remaining = gross - cap * float(np.sum(frozen))
        free = ~frozen
        free_sum = float(np.sum(raw_weights[free]))
        if free_sum > 0 and remaining > 0:
            w[free] = (raw_weights[free] / free_sum) * remaining
        else:
            w[free] = 0.0
            break
    return w


def capped_waterfall_weights_clamp(raw_weights: np.ndarray, cap: float = 0.35, gross: float = 1.0) -> np.ndarray:
    """Enforce exact per-instrument risk budget cap by iterative water-filling."""
    n = len(raw_weights)
    if n == 0:
        return np.empty(0, dtype=float)
    if np.sum(raw_weights) <= 0:
        return np.full(n, min(gross / max(1, n), cap))
    w = (raw_weights / np.sum(raw_weights)) * gross
    frozen = np.zeros(n, dtype=bool)
    for _ in range(n):
        over = (w > cap + 1e-12) & ~frozen
        if not np.any(over):
            break
        w[over] = cap
        frozen[over] = True
        remaining = gross - cap * float(np.sum(frozen))
        free = ~frozen
        free_sum = float(np.sum(raw_weights[free]))
        if free_sum > 0 and remaining > 0:
            w[free] = (raw_weights[free] / free_sum) * remaining
        else:
            w[free] = 0.0
            break
    return np.minimum(w, cap)


def capped_waterfall_weights_feasibility(raw_weights: np.ndarray, cap: float = 0.5, gross: float = 1.0) -> np.ndarray:
    """Enforce exact per-instrument risk budget cap by iterative water-filling.

    Feasibility guard: If universe size n * cap < gross, clamps total gross exposure to n * cap
    so no individual instrument ever exceeds the declared per_instrument_cap.
    """
    n = len(raw_weights)
    if n == 0:
        return np.zeros(0, dtype=float)
    target_gross = min(gross, n * cap)
    if np.sum(raw_weights) <= 0:
        return np.full(n, min(target_gross / n, cap))
    w = (raw_weights / np.sum(raw_weights)) * target_gross
    frozen = np.zeros(n, dtype=bool)
    for _ in range(n):
        over = (w > cap + 1e-12) & ~frozen
        if not np.any(over):
            break
        w[over] = cap
        frozen[over] = True
        remaining = target_gross - cap * float(np.sum(frozen))
        free = ~frozen
        free_sum = float(np.sum(raw_weights[free]))
        if free_sum > 0 and remaining > 0:
            w[free] = (raw_weights[free] / free_sum) * remaining
        else:
            w[free] = 0.0
            break
    return np.clip(w, 0.0, cap)


def trailing_atr_stop_long_only(active_indices, positions, obs_mat, t, peak_prices, high_mat, stop_multiple, close_mat, atr_mat):
    for a_idx in active_indices:
        if not obs_mat[t, a_idx]:
            continue
        pos = positions[t, a_idx]
        if pos > 1e-06:
            peak_prices[a_idx] = max(peak_prices[a_idx], high_mat[t, a_idx])
            stop_price = peak_prices[a_idx] - stop_multiple * atr_mat[t, a_idx]
            if close_mat[t, a_idx] < stop_price:
                positions[t, a_idx] = 0.0



def trailing_atr_stop_symmetric(active_indices, positions, atr_mat, t, peak_prices, high_mat, stop_multiple, close_mat, trough_prices, low_mat):
    for a_idx in active_indices:
        pos = positions[t, a_idx]
        atr = atr_mat[t, a_idx]
        if pos > 1e-06:
            peak_prices[a_idx] = max(peak_prices[a_idx], high_mat[t, a_idx])
            stop_price = peak_prices[a_idx] - stop_multiple * atr
            if close_mat[t, a_idx] < stop_price:
                positions[t, a_idx] = 0.0
        elif pos < -1e-06:
            trough_prices[a_idx] = min(trough_prices[a_idx], low_mat[t, a_idx])
            stop_price = trough_prices[a_idx] + stop_multiple * atr
            if close_mat[t, a_idx] > stop_price:
                positions[t, a_idx] = 0.0



def trailing_atr_stop_symmetric_obs(active_indices, positions, atr_mat, obs_mat, t, peak_prices, high_mat, stop_multiple, close_mat, trough_prices, low_mat):
    for a_idx in active_indices:
        if not obs_mat[t, a_idx]:
            continue
        pos = positions[t, a_idx]
        atr = atr_mat[t, a_idx]
        if pos > 1e-06:
            peak_prices[a_idx] = max(peak_prices[a_idx], high_mat[t, a_idx])
            stop_price = peak_prices[a_idx] - stop_multiple * atr
            if close_mat[t, a_idx] < stop_price:
                positions[t, a_idx] = 0.0
        elif pos < -1e-06:
            trough_prices[a_idx] = min(trough_prices[a_idx], low_mat[t, a_idx])
            stop_price = trough_prices[a_idx] + stop_multiple * atr
            if close_mat[t, a_idx] > stop_price:
                positions[t, a_idx] = 0.0


def normalize_asset_column(panel_df: pl.DataFrame) -> pl.DataFrame:
    """Rename a legacy ``symbol`` column to ``asset`` when needed (idempotent)."""
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        return panel_df.rename({"symbol": "asset"})
    return panel_df

