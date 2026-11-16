"""DAX 1h oversold/volatility gate; explicit delayed-stop research adaptation.

Original: https://www.algomatictrading.com/post/oversold-intraday-on-dax
Raw HTML SHA256 (2026-10-30 capture):
09749d54e9967710e5c7993fceb928f3442ea562749bf6d96039779e5e815a7a.
Source: IG DAX CET 1h, IBS<=.05 RSI2<50 BBW50<.06, exit
low>prior close plus an INTRABAR 0.5% stop with no fees.
Adaptation: Wilder RSI2, Bollinger width 4*population STD50/SMA50,
completed-bar LOW below 0.995*SIGNAL close => NEXT_BAR stop proxy,
NOT source intrabar stop-fill or actual entry fill. Contract 1.5bps/side.
"""
from __future__ import annotations

import numpy as np
import polars as pl
from warden.contracts import EvaluationContract

CONTRACT_ID = "AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1"
ASSET = "DAX"
IBS_MAX = 0.05
RSI_MAX = 50.0
BBW_MAX = 0.06
STOP_FRAC = 0.005


def _rsi2(values: np.ndarray) -> np.ndarray:
    n = len(values)
    out = np.full(n, np.nan, dtype=float)
    if n <= 2:
        return out
    moves = np.diff(values)
    ups = np.maximum(moves, 0.0)
    downs = np.maximum(-moves, 0.0)
    up = float(np.mean(ups[:2]))
    down = float(np.mean(downs[:2]))
    for i in range(2, n):
        if i > 2:
            up = 0.5 * (up + ups[i - 1])
            down = 0.5 * (down + downs[i - 1])
        out[i] = (50.0 if up == down == 0.0
                  else 100.0 if down == 0.0
                  else 100.0 - 100.0 / (1.0 + up / down))
    return out


class OversoldDaxPipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Wrong governed DAX 1h contract")
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        needed = {"timestamp", "asset", "open", "high", "low", "close", "volume"}
        if not needed.issubset(df.columns):
            raise ValueError("Missing OHLCV fields")
        dax = df.filter(pl.col("asset") == ASSET).sort("timestamp")
        if dax.is_empty():
            raise ValueError("Missing DAX")
        prices = dax["close"].to_numpy().astype(float)
        opens = dax["open"].to_numpy().astype(float)
        highs = dax["high"].to_numpy().astype(float)
        lows = dax["low"].to_numpy().astype(float)
        vol = dax["volume"].to_numpy().astype(float)
        if (not np.isfinite(prices).all() or not np.isfinite(opens).all()
                or not np.isfinite(highs).all() or not np.isfinite(lows).all()
                or not np.isfinite(vol).all() or np.any(lows <= 0)
                or np.any(opens <= 0) or np.any(vol < 0)
                or np.any(highs < np.maximum(opens, prices))
                or np.any(lows > np.minimum(opens, prices))):
            raise ValueError("Invalid price/source OHLCV")
        return dax.with_columns(
            pl.Series("rsi2", [float(x) if np.isfinite(x) else None
                                 for x in _rsi2(prices)], dtype=pl.Float64),
            ((pl.col("close") - pl.col("low")) /
             (pl.col("high") - pl.col("low")).clip(lower_bound=0.001)).alias("ibs"),
            (4.0 * pl.col("close").rolling_std(50, ddof=0) /
             pl.col("close").rolling_mean(50)).alias("bbw"),
            (pl.col("low") > pl.col("close").shift(1)).alias("exit_recovery"),
        )


pipeline = OversoldDaxPipeline()


def generate_positions(panel_df: pl.DataFrame | pl.LazyFrame,
                       contract: EvaluationContract,
                       target_symbols: list[str] | None = None,
                       **kwargs: object) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError(f"Non-predeclared parameters: {sorted(kwargs)}")
    if target_symbols != [ASSET]:
        raise ValueError("DAX only")
    if contract.contract_id != CONTRACT_ID or contract.execution_delay_bars != 1:
        raise ValueError("Required 1h NEXT_BAR contract")
    feat = pipeline.compute(panel_df, contract)
    original = panel_df.collect() if isinstance(panel_df, pl.LazyFrame) else panel_df
    if "symbol" in original.columns and "asset" not in original.columns:
        original = original.rename({"symbol": "asset"})
    ts = sorted(original["timestamp"].unique().to_list())
    assets = sorted(original["asset"].unique().to_list())
    out = np.zeros((len(ts), len(assets)), dtype=float)
    by_stamp = {t: i for i, t in enumerate(ts)}
    column = assets.index(ASSET)
    pos = False
    entry_ref = 0.0
    for stamp, close, low, volume, ibs, rsi, bbw, exit_signal in feat.select(
        "timestamp", "close", "low", "volume", "ibs", "rsi2",
        "bbw", "exit_recovery",
    ).iter_rows():
        if pos:
            # Delayed stop event proxy, never claim original intrabar fill.
            if bool(exit_signal) or low <= entry_ref * (1.0 - STOP_FRAC):
                pos = False
        elif (all(x is not None and np.isfinite(x) for x in (ibs, rsi, bbw))
              and volume > 0 and ibs <= IBS_MAX and rsi < RSI_MAX
              and bbw < BBW_MAX):
            pos = True
            entry_ref = float(close)
        out[by_stamp[stamp], column] = 1.0 if pos else 0.0
    if not np.isfinite(out).all() or np.any((out < 0) | (out > 1)):
        raise ValueError("Invalid DAX weights")
    return {"positions": out}
