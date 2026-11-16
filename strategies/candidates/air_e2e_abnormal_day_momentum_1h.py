"""AIR-E2E-20261029-001: pre-registered abnormal-day continuation, not an optimized signal.

Source motivation: Caporale and Plastun (2020), DOI:10.1007/s11408-020-00357-1.
This is a new, fixed adaptation to 8 Binance USDT perps, NOT a faithful replication.
Uses closes available at the current completed hourly bar; Warden applies NEXT_BAR
execution lag, fees, slippage and funding. No future data or sealed holdout is read.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract


class AbnormalDayPipeline:
    """Trailing 24h returns normalized by prior 720h of overlapping 24h returns."""

    def __init__(self, vol_window: int = 720):
        self.vol_window = vol_window

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        _ = contract  # execution lag belongs to Warden, not the feature pipeline
        bars = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in bars.columns and "asset" not in bars.columns:
            bars = bars.rename({"symbol": "asset"})
        bars = bars.sort("asset", "timestamp")
        return (
            bars
            .with_columns(
                ret_24h=(pl.col("close") / pl.col("close").shift(24) - 1.0).over("asset")
            )
            .with_columns(
                prior_24h_vol=(
                    pl.col("ret_24h").rolling_std(self.vol_window).shift(1).over("asset")
                )
            )
            .with_columns(
                shock_z=pl.when(pl.col("prior_24h_vol") > 1e-9)
                .then(pl.col("ret_24h") / pl.col("prior_24h_vol"))
                .otherwise(None)
            )
            .sort("timestamp", "asset")
        )


pipeline = AbnormalDayPipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    z_threshold: float = 2.0,
    vol_window: int = 720,
    rebalance_utc_hour: int = 0,
    per_instrument_cap: float = 0.25,
    gross_limit: float = 1.0,
    target_symbols: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """Hold next UTC day after a >=2-sigma completed 24h move (same sign).

    Decision uses Close[t], not Open[t+1]. Warden's one-bar NEXT_BAR lag
    sets first fill at Open[t+1]. No strategy-local execution lag is applied.
    """
    if z_threshold != 2.0 or vol_window != 720 or rebalance_utc_hour != 0:
        raise ValueError("This pre-registered E2E test has one frozen parameter configuration.")
    if per_instrument_cap != 0.25 or gross_limit != 1.0:
        raise ValueError("Frozen sizing configuration differs from approved E2E test.")
    df = panel_df
    if "symbol" in df.columns and "asset" not in df.columns:
        df = df.rename({"symbol": "asset"})
    if target_symbols is not None:
        requested = set(target_symbols)
        present = set(df["asset"].unique().to_list())
        if not requested or requested - present:
            raise ValueError("target universe absent from canonical data")
        df = df.filter(pl.col("asset").is_in(target_symbols))
    df = pipeline.compute(df, contract)
    assets = sorted(df["asset"].unique().to_list())
    times = sorted(df["timestamp"].unique().to_list())
    z_pivot = df.pivot(index="timestamp", on="asset", values="shock_z").sort("timestamp")
    z = z_pivot.select(assets).to_numpy()
    if z.shape != (len(times), len(assets)):
        raise ValueError("panel must contain one row per timestamp and asset")
    weights = np.zeros_like(z, dtype=np.float64)
    for t, timestamp in enumerate(times):
        if timestamp.hour != rebalance_utc_hour:
            if t:
                weights[t] = weights[t - 1]
            continue
        signal = np.where(np.isfinite(z[t]) & (np.abs(z[t]) >= z_threshold),
                          np.sign(z[t]), 0.0)
        active = np.count_nonzero(signal)
        if active:
            unit_weight = min(per_instrument_cap, gross_limit / active)
            weights[t] = signal * unit_weight
    return {"positions": weights}
