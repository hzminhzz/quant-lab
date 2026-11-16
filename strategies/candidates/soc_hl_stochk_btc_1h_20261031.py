"""Pinned H018 stochastic mean-reversion source adaptation: Bybit BTC 1h to Binance BTCUSDT USD-M 1h.

Primary source (untrusted): https://github.com/SeifBoukerdenna/hl/blob/88151f9f9845f20636b43fd3f70d133243d60382/docs/hypotheses/H018.md
Blob SHA1 622bc86b73f5e97ec6c4db018a867ab0e51242da. Source explicitly
reports NOT_SIG / OOS mean Sharpe -0.12 after 45 CPCV paths. This is NOT
freshly discovered profitable alpha and the validation periods overlap.
Source defaults: K14, upward crossing of 20 for LONG; exit K>80 or at
24 hourly bars; target 50% equity. No source stops. Only completed
bars; Warden supplies the one-bar NEXT_BAR lag and approved fees/funding.
No publisher code is imported or executed.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

CONTRACT_ID = "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1"
ASSET = "BTCUSDT"
LOOKBACK = 14
ENTRY_K = 20.0
EXIT_K = 80.0
MAX_HOLD = 24
POSITION_WEIGHT = 0.5


class StochKPipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Wrong governed USD-M hourly contract")
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        required = {"timestamp", "asset", "open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            raise ValueError("Missing source OHLCV")
        target = df.filter(pl.col("asset") == ASSET).sort("timestamp")
        if target.is_empty():
            raise ValueError("BTCUSDT missing from pinned panel")
        if target.select(pl.any_horizontal(
            pl.col("low") <= 0, pl.col("high") < pl.col("low"),
            pl.col("close") < pl.col("low"), pl.col("close") > pl.col("high"),
            pl.col("volume") < 0,
        )).to_series().any():
            raise ValueError("Invalid BTC OHLCV")
        target = target.with_columns(
            pl.col("high").rolling_max(LOOKBACK).alias("high_n"),
            pl.col("low").rolling_min(LOOKBACK).alias("low_n"),
        ).with_columns(
            pl.when((pl.col("high_n") - pl.col("low_n")) > 0)
            .then(100.0 * (pl.col("close") - pl.col("low_n")) /
                  (pl.col("high_n") - pl.col("low_n")))
            .otherwise(None).alias("stoch_k"),
        ).with_columns(pl.col("stoch_k").shift(1).alias("prev_stoch_k"))
        return target


pipeline = StochKPipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError(f"Non-preregistered parameters: {sorted(kwargs)}")
    if target_symbols != [ASSET]:
        raise ValueError("H018 source adaptation is BTCUSDT only")
    if (contract.contract_id != CONTRACT_ID or
            contract.frequency != "1h" or
            contract.execution_mode != "NEXT_BAR" or
            contract.execution_delay_bars != 1 or
            contract.label_horizon_bars < MAX_HOLD):
        raise ValueError("Required 1h, 24-bar, next-bar contract")
    feat = pipeline.compute(panel_df, contract)
    # Warden has filtered to the single source instrument; never silently
    # broadcast a BTC-only position to other crypto assets.
    orig = panel_df.collect() if isinstance(panel_df, pl.LazyFrame) else panel_df
    if "symbol" in orig.columns and "asset" not in orig.columns:
        orig = orig.rename({"symbol": "asset"})
    assets = sorted(orig["asset"].unique().to_list())
    timestamps = sorted(orig["timestamp"].unique().to_list())
    if assets != [ASSET] or len(timestamps) != feat.height:
        raise ValueError("BTC-only complete hourly panel required")
    weights = np.zeros((len(timestamps), 1), dtype=float)
    holding = False
    entry_signal_i = -1
    for i, (stamp, volume, k, prev_k) in enumerate(feat.select(
        "timestamp", "volume", "stoch_k", "prev_stoch_k",
    ).iter_rows()):
        if stamp != timestamps[i]:
            raise ValueError("Unexpected timestamp alignment")
        k_ok = k is not None and np.isfinite(k)
        prev_ok = prev_k is not None and np.isfinite(prev_k)
        if holding:
            if (k_ok and k > EXIT_K) or i - entry_signal_i >= MAX_HOLD:
                holding = False
        elif (volume > 0 and k_ok and prev_ok and
              prev_k < ENTRY_K <= k):
            holding = True
            entry_signal_i = i
        weights[i, 0] = POSITION_WEIGHT if holding else 0.0
    return {"positions": weights}
