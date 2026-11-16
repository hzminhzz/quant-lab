"""STOIC XSMOM source-based exploratory adaptation, fixed before Stage 1.

Original: https://stoicresearch.substack.com/p/14-cross-sectional-momentum
Research adaptation: 19/20 of the original source's listed crypto tickers,
because PAXGUSDT is absent from the ex-ante 2023-01-01 Binance snapshot.
Source rule kept: prior 10-day return, five longs/five shorts, dollar neutral,
21-day rebalance, no stops, next-bar lag and funding/costs injected by Warden.

The source post was published in 2026 after the 2024-25 validation window.
A favorable 2025 score is only retrospective evidence, never independent OOS.
No parameter sweep, target optimization, source code execution, or holdout use.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

ORIGINAL_AUTHOR = "Stoic Research & Technology"
ORIGINAL_SOURCE = "https://stoicresearch.substack.com/p/14-cross-sectional-momentum"
CONTRACT_ID = "CRYPTO_PERPS_TOP100_1H_V1"
SOURCE_TICKERS = (
    "BTC", "ETH", "XRP", "BNB", "SOL", "TRX", "DOGE", "ADA", "BCH", "ZEC",
    "LINK", "XLM", "LTC", "AVAX", "HBAR", "PAXG", "UNI", "DOT", "NEAR", "ETC",
)
# This subset is frozen to the coins available on the *first* exploratory day,
# not selected using future performance or listing survival.
SOURCE_ADAPTATION_SYMBOLS = tuple(sorted(
    ticker + "USDT" for ticker in SOURCE_TICKERS if ticker != "PAXG"
))
LOOKBACK_HOURS = 10 * 24
REBALANCE_HOURS = 21 * 24
LONG_NAMES = 5
SHORT_NAMES = 5
LEG_GROSS = 0.5


class SourceFeaturePipeline:
    """Exact causal prior 10-day return used by the screened signal."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Unexpected Warden contract in source feature pipeline")
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        df = df.sort("asset", "timestamp")
        return df.with_columns(
            score_10d=(
                pl.col("close").shift(1).over("asset") /
                pl.col("close").shift(1 + LOOKBACK_HOURS).over("asset")
            ).log()
        ).sort("timestamp", "asset")


pipeline = SourceFeaturePipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs,
) -> dict:
    if kwargs:
        raise ValueError(f"Unapproved strategy parameters: {sorted(kwargs)}")
    if contract.contract_id != CONTRACT_ID:
        raise ValueError("Source adaptation requires exact pinned broad-perps contract")
    if target_symbols is None or tuple(sorted(target_symbols)) != SOURCE_ADAPTATION_SYMBOLS:
        raise ValueError("Ex-ante source universe does not equal the frozen 19-asset cohort")
    panel = panel_df.collect() if isinstance(panel_df, pl.LazyFrame) else panel_df
    panel = panel.rename({"symbol": "asset"}) if "symbol" in panel.columns and "asset" not in panel.columns else panel
    assets = sorted(panel["asset"].unique().to_list())
    if tuple(assets) != SOURCE_ADAPTATION_SYMBOLS:
        raise ValueError("Warden panel does not contain precisely the pinned author cohort")
    panel = pipeline.compute(panel, contract)
    matrix = panel.pivot(index="timestamp", on="asset", values="close").sort("timestamp")
    score_panel = panel.pivot(index="timestamp", on="asset", values="score_10d").sort("timestamp")
    if matrix.height * len(assets) != panel.height:
        raise ValueError("Sparse symbol-time matrix; do not forward-fill or synthesize prices")
    if any(asset not in matrix.columns for asset in assets):
        raise ValueError("Source symbol missing in price matrix")
    close = matrix.select(assets).to_numpy()
    scores_by_time = score_panel.select(assets).to_numpy()
    if not np.isfinite(close).all() or np.any(close <= 0):
        raise ValueError("Nonfinite or nonpositive source data")
    timestamps = matrix["timestamp"].to_list()
    weights = np.zeros_like(close, dtype=float)
    target = np.zeros(len(assets), dtype=float)
    next_rebalance = None

    for index, ts in enumerate(timestamps):
        # One decision per UTC day, only after 240 hours of completed history.
        # Warden applies its required one-bar execution lag to this target.
        if ts.hour == 0 and index > LOOKBACK_HOURS:
            if next_rebalance is None or ts >= next_rebalance:
                scores = scores_by_time[index]
                if np.isfinite(scores).all():
                    order = np.argsort(scores, kind="stable")
                    target[:] = 0.0
                    target[order[:SHORT_NAMES]] = -LEG_GROSS / SHORT_NAMES
                    target[order[-LONG_NAMES:]] = +LEG_GROSS / LONG_NAMES
                    next_rebalance = ts + __import__("datetime").timedelta(hours=REBALANCE_HOURS)
        weights[index, :] = target
    return {"positions": weights}
