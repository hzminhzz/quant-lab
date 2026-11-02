"""Daily rebalance of the exact four-hour low-vol Ridge forecast.

Correlated economic revision of HYP-ARXIV-XS-LOWVOL-20261102. It does not
create an independent prediction signal. Original coefficient fitting, feature
timestamps, validation labels, cost and delay semantics are unchanged.
"""

from __future__ import annotations

import importlib.util
from bisect import bisect_left
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract


def _source_module():
    # Warden loads strategies by a direct file path, not as package modules.
    # This avoids importing 'strategies' from the CLI entrypoint.
    filename = Path(__file__).with_name("arxiv_lowvol_xs_1h.py")
    spec = importlib.util.spec_from_file_location("_arxiv_lowvol_frozen_baseline", filename)
    if spec is None or spec.loader is None:
        raise ImportError("Missing the frozen low-vol baseline")
    import sys
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


baseline = _source_module()
pipeline = baseline.pipeline


def _daily_positions(
    baseline_positions: np.ndarray,
    observed_open: np.ndarray,
    *,
    start: int,
    end: int,
    interval: int,
) -> np.ndarray:
    """A held weight cannot silently be restored after a missing market bar."""
    if interval < 1 or start < 0 or end > len(baseline_positions):
        raise ValueError("Invalid frozen daily rebalance window")
    positions = np.zeros_like(baseline_positions, dtype=float)
    state = np.zeros(baseline_positions.shape[1], dtype=float)
    for t in range(start, end):
        if (t - start) % interval == 0:
            state = baseline_positions[t].copy()
        tradable = np.isfinite(observed_open[t]) & (observed_open[t] > 0)
        state = np.where(tradable, state, 0.0)
        positions[t] = state
    return positions


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    *,
    ridge_alpha: float = 100.0,
    rebalance_every: int = 24,
    top_k: int = 10,
    min_liquidity_usd: float = 25000.0,
    **kwargs: Any,
) -> dict[str, Any]:
    _ = kwargs
    if rebalance_every != 24:
        raise ValueError("Only the frozen 24h turnover revision is allowed")
    # Same fold-local fitted forecasts and OOS IC as the original strategy.
    original = baseline.generate_positions(
        panel_df,
        contract,
        ridge_alpha=ridge_alpha,
        rebalance_every=4,
        top_k=top_k,
        min_liquidity_usd=min_liquidity_usd,
    )
    panel = panel_df.sort("timestamp", "asset")
    stamps = sorted(panel["timestamp"].unique().to_list())
    assets = sorted(panel["asset"].unique().to_list())
    if original["positions"].shape != (len(stamps), len(assets)):
        raise ValueError("Source positions are not aligned to observed execution timestamps")
    opens = panel.pivot(index="timestamp", on="asset", values="open").sort("timestamp").select(assets).to_numpy()
    start = bisect_left(stamps, contract.val_start)
    end = bisect_left(stamps, contract.val_end)
    positions = _daily_positions(
        original["positions"], opens,
        start=start, end=end, interval=rebalance_every,
    )
    return {
        "positions": positions,
        "ic_series": original["ic_series"].copy(),
        "source_fold_coefficients": original["oos_fold_ridge_coefficients"],
    }
