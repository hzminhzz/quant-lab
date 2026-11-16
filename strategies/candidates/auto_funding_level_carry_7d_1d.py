"""Delphic Alpha funding-level carry — explicitly *Binance-only* adaptation.

Original rules (not exact replicated universe): https://delphicalpha.substack.com/p/strategy-crypto-funding-carry-makes
Published 2026-10-26; original source uses 32 Binance+Hyperliquid dual-listed
coins. This first executable version uses only the 88 Binance USDT perpetuals
available on 2023-01-01. It is an instrument-universe adaptation and its
2024-2025 validation is retrospective, NOT independently unseen OOS.

Frozen signal: average funding_rate for last 7*24 COMPLETE hourly bars,
ranking 1/3 cheapest LONG, 1/3 most expensive SHORT, every day 00:00 UTC.
Ex-ante liquidity: >=60 prior daily observations and median of previous
60 daily dollar volume >=5 million. Position weights inverse 30-day
daily-close log-return volatility, gross 0.5 each side. No optimization.
Warden owns execution delay, fees/slippage and actual funding PnL.
This module never executes raw publisher code or uses protected data.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

SOURCE = "https://delphicalpha.substack.com/p/strategy-crypto-funding-carry-makes"
CONTRACT_ID = "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1"
ORIGINAL_UNIVERSE = "32 dual-listed Binance/Hyperliquid perpetuals"
ADAPTATION_UNIVERSE = "Binance-only inception cohort of 88 perpetuals"
FUNDING_LOOKBACK_HOURS = 168
VOL_LOOKBACK_DAYS = 30
LIQUIDITY_LOOKBACK_DAYS = 60
MIN_MEDIAN_DAILY_DOLLARS = 5_000_000
LEG_GROSS = 0.5
MIN_ELIGIBLE = 12
REBALANCE_HOURS = 24


class FundingFeaturePipeline:
    """Causal features that operate strictly on earlier, completed hourly bars."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Wrong Warden contract; new research contract required")
        panel = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in panel.columns and "asset" not in panel.columns:
            panel = panel.rename({"symbol": "asset"})
        required = {"timestamp", "asset", "close", "volume", "funding_rate"}
        if not required.issubset(panel.columns):
            raise ValueError(f"Missing funding/price data: {sorted(required - set(panel.columns))}")
        return (
            panel.sort("asset", "timestamp")
            .with_columns(
                funding_signal_7d=(
                    pl.col("funding_rate")
                    .rolling_mean(FUNDING_LOOKBACK_HOURS)
                    .shift(1).over("asset")
                ),
                quote_volume=pl.col("close") * pl.col("volume"),
            )
            .sort("timestamp", "asset")
        )


pipeline = FundingFeaturePipeline()


def generate_positions(panel_df: pl.DataFrame | pl.LazyFrame,
                       contract: EvaluationContract,
                       target_symbols: list[str] | None = None, **kwargs) -> dict:
    """Source-parameter-frozen daily ranking on an ex-ante cohort.

    Warden applies a separate 1h NEXT_BAR lag; we never pre-lag execution.
    """
    if kwargs:
        raise ValueError(f"Unreviewed strategy parameters: {sorted(kwargs)}")
    if target_symbols is None or len(target_symbols) != len(set(target_symbols)):
        raise ValueError("Exact ex-ante source-adaptation cohort is required")
    if contract.contract_id != CONTRACT_ID:
        raise ValueError("Warden contract identity mismatch")
    df = pipeline.compute(panel_df, contract)
    assets = sorted(df["asset"].unique().to_list())
    if sorted(target_symbols) != assets or len(assets) != 88:
        raise ValueError("Refuse future-selected, missing or changed broad research universe")
    stamps = sorted(df["timestamp"].unique().to_list())
    if len(stamps) * len(assets) != df.height:
        raise ValueError("Missing timestamp-symbol observations; sparse panel requires new data contract")
    if len(stamps) < (LIQUIDITY_LOOKBACK_DAYS + 3) * 24:
        raise ValueError("Insufficient 60-day history for causal liquidity and volatility")

    def matrix(column: str) -> np.ndarray:
        wide = df.pivot(index="timestamp", on="asset", values=column).sort("timestamp")
        if wide.height != len(stamps) or any(name not in wide.columns for name in assets):
            raise ValueError(f"Nonrectangular {column} panel")
        return wide.select(assets).to_numpy().astype(float)

    close = matrix("close")
    volume = matrix("volume")
    funding_signal = matrix("funding_signal_7d")
    if np.any(~np.isfinite(close)) or np.any(close <= 0):
        raise ValueError("Invalid causal closing prices")
    if np.any(~np.isfinite(volume)) or np.any(volume < 0):
        raise ValueError("Invalid quoted market volume")

    notional = close * volume
    cum_notional = np.vstack([
        np.zeros((1, len(assets)), dtype=float),
        np.cumsum(notional, axis=0, dtype=float),
    ])
    positions = np.zeros((len(stamps), len(assets)), dtype=float)
    target = np.zeros(len(assets), dtype=float)
    warmup_hours = LIQUIDITY_LOOKBACK_DAYS * 24 + 1

    for t, dt in enumerate(stamps):
        if t >= warmup_hours and dt.hour == 0:
            # Complete prior 60 UTC days; never uses current signal bar volume.
            daily_volumes = np.stack([
                cum_notional[t - 24 * k] - cum_notional[t - 24 * (k + 1)]
                for k in range(LIQUIDITY_LOOKBACK_DAYS)
            ])
            med_dollar_volume = np.median(daily_volumes, axis=0)
            # Previous 31 daily closes supply exactly 30 complete daily returns.
            history_indices = t - 1 - np.arange(VOL_LOOKBACK_DAYS + 1) * 24
            daily_log_close = np.log(close[history_indices])
            daily_rets = np.diff(daily_log_close, axis=0)
            volatility = np.std(daily_rets, axis=0, ddof=1)
            funding = funding_signal[t]
            eligible = np.flatnonzero(
                np.isfinite(funding)
                & np.isfinite(volatility)
                & (volatility > 1e-8)
                & (med_dollar_volume >= MIN_MEDIAN_DAILY_DOLLARS)
            )
            target[:] = 0
            if len(eligible) >= MIN_ELIGIBLE:
                order = eligible[np.argsort(funding[eligible], kind="stable")]
                n_third = len(eligible) // 3
                long = order[:n_third]
                short = order[-n_third:]
                long_inverse_vol = 1.0 / volatility[long]
                short_inverse_vol = 1.0 / volatility[short]
                target[long] = LEG_GROSS * long_inverse_vol / long_inverse_vol.sum()
                target[short] = -LEG_GROSS * short_inverse_vol / short_inverse_vol.sum()
        positions[t] = target
    if not np.isfinite(positions).all() or np.any(np.abs(positions).sum(axis=1) > 1.0000001):
        raise ValueError("Invalid gross-weight output")
    return {"positions": positions}
