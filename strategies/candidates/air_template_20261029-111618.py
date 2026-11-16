"""Frozen, manually reviewed causal Stage-1 research templates.

These are independent, predeclared adaptations, not hyperparameter sweeps or
faithful reproductions of online creators. An LLM may SELECT a template only.
Warden owns the mandatory NEXT_BAR execution delay and transaction costs.
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
        df = df.sort("asset", "timestamp")
        return (
            df.with_columns(
                ret_1h=(pl.col("close") / pl.col("close").shift(1) - 1).over("asset"),
                ret_168h=(pl.col("close") / pl.col("close").shift(168) - 1).over("asset"),
                prior_mean_volume=pl.col("volume").rolling_mean(168).shift(1).over("asset"),
                prior_high=pl.col("high").rolling_max(72).shift(1).over("asset"),
                prior_low=pl.col("low").rolling_min(72).shift(1).over("asset"),
                prior_high_24=pl.col("high").rolling_max(24).shift(1).over("asset"),
                prior_low_24=pl.col("low").rolling_min(24).shift(1).over("asset"),
                prior_high_168=pl.col("high").rolling_max(168).shift(1).over("asset"),
                prior_high_240=pl.col("high").rolling_max(240).shift(1).over("asset"),
                prior_low_240=pl.col("low").rolling_min(240).shift(1).over("asset"),
                ema_48=pl.col("close").ewm_mean(span=48, adjust=False).over("asset"),
                ema_200=pl.col("close").ewm_mean(span=200, adjust=False).over("asset"),
            ).with_columns(
                prior_vol_1h=pl.col("ret_1h").rolling_std(168).shift(1).over("asset"),
                prior_vol_24=pl.col("ret_1h").rolling_std(24).shift(1).over("asset"),
                vol_ratio=pl.col("volume") / (pl.col("prior_mean_volume") + 1e-9),
                gain=pl.col("ret_1h").clip(lower_bound=0),
                loss=(-pl.col("ret_1h")).clip(lower_bound=0),
            ).with_columns(
                rsi_2=100 - 100 / (
                    1 + pl.col("gain").rolling_mean(2).over("asset") /
                    (pl.col("loss").rolling_mean(2).over("asset") + 1e-12)
                )
            ).sort("timestamp", "asset")
        )


pipeline = FixedFeaturePipeline()

ALLOWED_MODES = (
    "shock_reversal",
    "volume_breakout",
    "dip_7d",
    "rsi2_trend_pullback",
    "donchian_trend",
    "vol_compression_breakout",
    "cross_section_momentum_7d",
)


def generate_positions(
    panel_df: pl.DataFrame, contract: EvaluationContract,
    mode: str = "shock_reversal", **kwargs,
) -> dict:
    """Predeclared long/short weights, one bar of execution lag owned by Warden."""
    if kwargs:
        raise ValueError(f"Unapproved parameters: {sorted(kwargs)}")
    if mode not in ALLOWED_MODES:
        raise ValueError(f"Unregistered template mode: {mode}")
    df = pipeline.compute(panel_df, contract)
    times = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    if len(assets) != 8:
        raise ValueError("Templates frozen to the eight-symbol contract")
    if len(times) < 300:
        raise ValueError("Insufficient warmup for frozen strategy")

    def pivot(column: str) -> np.ndarray:
        p = df.pivot(index="timestamp", on="asset", values=column).sort("timestamp")
        if p.height != len(times) or any(a not in p.columns for a in assets):
            raise ValueError("Non-rectangular timestamp/asset panel")
        return p.select(assets).to_numpy()

    ret = pivot("ret_1h")
    ratio = pivot("vol_ratio")
    close = pivot("close")
    hi = pivot("prior_high")
    lo = pivot("prior_low")
    vol = pivot("prior_vol_1h")
    trigger = np.zeros_like(ret, dtype=float)
    horizon = 12

    if mode == "shock_reversal":
        trigger = np.where(
            (np.abs(ret) >= 2.5 * vol) & (vol > 1e-10) & (ratio >= 1.5),
            -np.sign(ret), 0.0,
        )
        horizon = 4
    elif mode == "volume_breakout":
        trigger = np.where(
            (close > hi) & (ratio >= 1.5), 1.0,
            np.where((close < lo) & (ratio >= 1.5), -1.0, 0.0),
        )
        horizon = 12
    elif mode == "dip_7d":
        # Independent fixed interpretation of an online 7-day dip buying idea.
        high = pivot("prior_high_168")
        trigger = np.where(
            (high > 0) & (close <= 0.88 * high), 1.0, 0.0,
        )
        horizon = 4
    elif mode == "rsi2_trend_pullback":
        rsi = pivot("rsi_2")
        fast, slow = pivot("ema_48"), pivot("ema_200")
        trigger = np.where(
            (fast > slow) & (rsi < 10), 1.0,
            np.where((fast < slow) & (rsi > 90), -1.0, 0.0),
        )
        horizon = 12
    elif mode == "donchian_trend":
        high, low = pivot("prior_high_240"), pivot("prior_low_240")
        trigger = np.where(close > high, 1.0, np.where(close < low, -1.0, 0.0))
        horizon = 24
    elif mode == "vol_compression_breakout":
        slow_vol, fast_vol = vol, pivot("prior_vol_24")
        squeeze = (slow_vol > 1e-12) & (fast_vol < 0.55 * slow_vol)
        high, low = pivot("prior_high_24"), pivot("prior_low_24")
        trigger = np.where(
            squeeze & (close > high), 1.0,
            np.where(squeeze & (close < low), -1.0, 0.0),
        )
        horizon = 8
    elif mode == "cross_section_momentum_7d":
        returns = pivot("ret_168h")
        target = np.zeros(len(assets), dtype=float)
        weights = np.zeros_like(ret, dtype=float)
        for t, timestamp in enumerate(times):
            if timestamp.hour == 0 and t >= 240:
                row = returns[t]
                target.fill(0)
                if np.isfinite(row).all():
                    rank = np.argsort(row, kind="stable")
                    target[rank[:2]] = -0.2
                    target[rank[-2:]] = 0.2
            weights[t] = target
        return {"positions": weights}
    else:
        raise AssertionError("unreachable strategy mode")

    pos = np.zeros_like(ret, dtype=float)
    active_until = np.zeros(len(assets), dtype=int)
    target = np.zeros(len(assets), dtype=float)
    for t in range(len(times)):
        for j in range(len(assets)):
            if t >= active_until[j]:
                signal = trigger[t, j] if np.isfinite(trigger[t, j]) else 0.0
                if t >= 242 and signal != 0:
                    target[j], active_until[j] = 0.20 * signal, t + horizon
                else:
                    target[j], active_until[j] = 0.0, t
        gross = float(np.sum(np.abs(target)))
        pos[t] = target / max(1.0, gross)
    return {"positions": pos}
