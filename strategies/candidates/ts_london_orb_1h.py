"""Rules-based 1-hour London Opening Range Breakout (ORB) strategy (HYP-2026-008)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class LondonORBPipeline:
    """Causal feature pipeline tracking Asian session range (00:00-08:00 UTC) and volume baseline."""

    def __init__(
        self,
        asian_start_hour: int = 0,
        asian_end_hour: int = 8,
        vol_baseline_bars: int = 24,
    ) -> None:
        self.asian_start_hour = asian_start_hour
        self.asian_end_hour = asian_end_hour
        self.vol_baseline_bars = vol_baseline_bars

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        # Extract UTC hour and date for session boundaries
        return (
            df.sort("timestamp", "asset")
            .with_columns(
                hour=pl.col("timestamp").dt.hour(),
                vol_baseline=pl.col("volume").rolling_mean(self.vol_baseline_bars, min_samples=1).over("asset"),
            )
            .with_columns(
                vol_ratio=pl.col("volume") / (pl.col("vol_baseline") + 1e-12),
            )
        )


class LondonORBStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing London Opening Range Breakout."""

    def __init__(
        self,
        asian_start_hour: int = 0,
        asian_end_hour: int = 8,
        entry_hour: int = 8,
        exit_hour: int = 16,
        volume_mult: float = 1.2,
    ) -> None:
        super().__init__()
        self.asian_start_hour = asian_start_hour
        self.asian_end_hour = asian_end_hour
        self.entry_hour = entry_hour
        self.exit_hour = exit_hour
        self.volume_mult = volume_mult
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        self.bar_count += 1


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    asian_start_hour: int = 0,
    asian_end_hour: int = 8,
    entry_hour: int = 8,
    exit_hour: int = 16,
    volume_mult: float = 1.2,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Causal London ORB: Asian range (00:00-08:00 UTC), enter on breakout with expanding volume, exit at 16:00 UTC."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = LondonORBPipeline(
        asian_start_hour=asian_start_hour,
        asian_end_hour=asian_end_hour,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    timestamps = sorted(feat_df["timestamp"].unique().to_list())
    n_bars = len(timestamps)
    n_assets = len(assets)

    high_mat = feat_df.pivot(index="timestamp", on="asset", values="high").fill_null(0.0).select(assets).to_numpy()
    low_mat = feat_df.pivot(index="timestamp", on="asset", values="low").fill_null(0.0).select(assets).to_numpy()
    close_mat = feat_df.pivot(index="timestamp", on="asset", values="close").fill_null(0.0).select(assets).to_numpy()
    vol_ratio_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="vol_ratio").fill_null(0.0).select(assets).to_numpy()
    )

    hours = np.array([ts.hour for ts in timestamps], dtype=int)

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        # Default to BTC and ETH if present, else all assets
        majors = [i for i, sym in enumerate(assets) if "BTC" in sym or "ETH" in sym]
        active_indices = majors if majors else list(range(n_assets))

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    weight = 1.0 / len(active_indices)

    if active_indices and n_bars:
        act = np.asarray(active_indices, dtype=int)
        emit_exit_hour = exit_hour - contract.execution_delay_bars

        hi_a = high_mat[:, act]
        lo_a = low_mat[:, act]
        cl_a = close_mat[:, act]
        vr_a = vol_ratio_mat[:, act]

        # Session state machine: flat outside the entry-to-exit window, entry only on the
        # entry hours while flat, otherwise the held position persists.
        flat = (hours >= emit_exit_hour) | (hours < asian_end_hour)
        entry_rows = np.flatnonzero(((hours == entry_hour) | (hours == entry_hour + 1)) & ~flat)

        if hi_a.dtype.kind not in "fiu" or lo_a.dtype.kind not in "fiu" or cl_a.dtype.kind not in "fiu":
            # Non-numeric OHLC: fail closed with the scalar loop's comparison error
            # (same numpy UFuncTypeError type and message).
            np.less(np.float64(-1e9), cl_a.flat[0])

        if entry_rows.size:
            # Asian range state: reset on the asian_start_hour bar, otherwise the running
            # extreme carried across bars per asset (identical to the sequential scan).
            a_rows = np.flatnonzero((hours >= asian_start_hour) & (hours < asian_end_hour))
            if a_rows.size:
                reset = hours[a_rows] == asian_start_hour
                bounds = np.flatnonzero(reset)
                if bounds.size == 0 or bounds[0] != 0:
                    bounds = np.concatenate(([0], bounds))
                bounds = np.append(bounds, a_rows.size)
                gh_a, gl_a = hi_a[a_rows], lo_a[a_rows]
                state_hi = np.empty_like(gh_a)
                state_lo = np.empty_like(gl_a)
                for k in range(bounds.size - 1):
                    s, e = int(bounds[k]), int(bounds[k + 1])
                    state_hi[s:e] = np.maximum.accumulate(gh_a[s:e], axis=0)
                    state_lo[s:e] = np.minimum.accumulate(gl_a[s:e], axis=0)
                # Range in force at each entry bar: the last asian bar at or before it.
                last_a = np.searchsorted(a_rows, entry_rows, side="right") - 1
                have = last_a >= 0
                asian_hi = np.where(have[:, None], state_hi[np.maximum(last_a, 0)], -1e9)
                asian_lo = np.where(have[:, None], state_lo[np.maximum(last_a, 0)], 1e9)
            else:
                asian_hi = np.full((entry_rows.size, act.size), -1e9)
                asian_lo = np.full((entry_rows.size, act.size), 1e9)

            vol_ok = vr_a[entry_rows] >= volume_mult
            long_break = vol_ok & (asian_hi < cl_a[entry_rows]) & (asian_hi > 0)
            short_break = vol_ok & (cl_a[entry_rows] < asian_lo) & (asian_hi > 0)
            cand = long_break | short_break
            cand_value = np.where(long_break, weight, -weight)

            # Only the first breakout signal of each non-flat run can open a position.
            run_id = np.cumsum(flat)[entry_rows]
            new_run = np.ones(entry_rows.size, dtype=bool)
            new_run[1:] = run_id[1:] != run_id[:-1]
            run_start = np.maximum.accumulate(np.where(new_run, np.arange(entry_rows.size), 0))
            seen = np.cumsum(cand, axis=0)
            base = np.where(run_start[:, None] > 0, seen[np.maximum(run_start - 1, 0)], 0)
            first_cand = cand & ((seen - base) == 1)

            flat_rows = np.flatnonzero(flat)
            for r, j in np.argwhere(first_cand):
                row = int(entry_rows[r])
                k = int(np.searchsorted(flat_rows, row))
                end = int(flat_rows[k]) if k < flat_rows.size else n_bars
                positions[row:end, act[j]] = cand_value[r, j]

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = LondonORBPipeline()
