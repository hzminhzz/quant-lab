"""Reviewed causal templates for autonomous Stage 1 intake (no model-produced Python).

Two fixed, predeclared hypotheses:
- SHOCK_REVERSAL: fade 2.5-sigma 1h move with >1.5x volume for 4h.
- VOLUME_BREAKOUT: follow 72h range break on >1.5x volume for 12h.
Each candidate is a distinct strategy-selection attempt; neither is tuned here.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract


class FixedFeaturePipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        return (
            df.sort("asset", "timestamp")
            .with_columns(
                ret_1h=(pl.col("close") / pl.col("close").shift(1) - 1.0).over("asset"),
                prior_mean_volume=pl.col("volume").rolling_mean(168).shift(1).over("asset"),
                prior_high=pl.col("high").rolling_max(72).shift(1).over("asset"),
                prior_low=pl.col("low").rolling_min(72).shift(1).over("asset"),
            )
            .with_columns(
                prior_vol_1h=pl.col("ret_1h").rolling_std(168).shift(1).over("asset"),
                vol_ratio=pl.col("volume") / (pl.col("prior_mean_volume") + 1e-9),
            )
            .sort("timestamp", "asset")
        )


pipeline = FixedFeaturePipeline()


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract,
                       mode: str = "shock_reversal", **kwargs) -> dict:
    """Warden applies the mandatory NEXT_BAR execution lag; do not double lag."""
    if kwargs:
        raise ValueError(f"Unapproved parameters: {sorted(kwargs)}")
    if mode not in ("shock_reversal", "volume_breakout"):
        raise ValueError(f"Unregistered template mode: {mode}")
    df = pipeline.compute(panel_df, contract)
    times = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    if len(assets) != 8:
        raise ValueError("Template is frozen to the existing eight-symbol contract")
    def pivot(col: str) -> np.ndarray:
        p = df.pivot(index="timestamp", on="asset", values=col).sort("timestamp")
        if p.height != len(times) or any(a not in p.columns for a in assets):
            raise ValueError("Nonrectangular hourly panel")
        return p.select(assets).to_numpy()
    ret, vol, ratio = pivot("ret_1h"), pivot("prior_vol_1h"), pivot("vol_ratio")
    close, high, low = pivot("close"), pivot("prior_high"), pivot("prior_low")
    if mode == "shock_reversal":
        trigger = np.where(
            (np.abs(ret) >= 2.5 * vol) & (vol > 1e-10) & (ratio >= 1.5),
            -np.sign(ret), 0.0,
        )
        horizon = 4
    else:
        trigger = np.where(
            (close > high) & (ratio >= 1.5), 1.0,
            np.where((close < low) & (ratio >= 1.5), -1.0, 0.0),
        )
        horizon = 12
    pos = np.zeros_like(ret, dtype=float)
    active_until = np.zeros(len(assets), dtype=int)
    target = np.zeros(len(assets), dtype=float)
    for t in range(len(times)):
        for j in range(len(assets)):
            if t >= active_until[j]:
                signal = trigger[t, j] if np.isfinite(trigger[t, j]) else 0.0
                if t >= 169 and signal != 0:
                    target[j], active_until[j] = 0.20 * signal, t + horizon
                else:
                    target[j], active_until[j] = 0.0, t
        gross = float(np.sum(np.abs(target)))
        pos[t] = target / max(1.0, gross)
    return {"positions": pos}
