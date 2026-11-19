"""Shared rules_ts sizing primitive: iterative water-filling to a per-instrument cap.

Copied verbatim across the AF30 1h trend modules; owned here once. Loaded as a
sibling (warden's strategy_loader puts the module directory on sys.path).
"""

from __future__ import annotations

import numpy as np


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
