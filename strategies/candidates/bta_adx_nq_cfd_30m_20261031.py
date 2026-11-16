"""ADX-filtered NASDAQ intraday breakout; predeclared CFD bar-confirmed adaptation.

Original publisher: https://breakouttradingacademy.com/nasdaq-hitter-algo-breakout-strategy-very-profitable/
Published 2024-02-14; archived source SHA256 d1e1880870d996e801f3ffe7865be4e829abc8e1de3e470509ac0ea2679fe6fd.
Original: 30m NQ FUTURES, 24h bars, ADX >17.5, INTRABAR buy stop at
highest close of N preceding bars, 08:00-15:00 Chicago, $1000 futures stop,
end-of-day liquidation. Publisher does NOT disclose N and selected ADX using
multiple prior index strategies. Source results contain selection bias.

THIS RESEARCH ADAPTATION IS NOT ORIGINAL-STRATEGY PERFORMANCE:
- N=8 previous completed 30m bars, ADX(14) using standard Wilder smoothing
  are PREDECLARED researcher choices; no empirical selection used here.
- Current CLOSED bar's high > prior 8-close maximum and ADX > 17.5:
  signal only after bar closes, native Warden executes NEXT 30m bar.
- Signals only 08:00-14:00 Chicago; desired flat at 14:30 Chicago
  implies a NEXT_BAR flatten at 15:00 Chicago; all weekdays only.
- No futures per-contract $1000 stop: incompatible with CFD bar-weight model.
  Hence not a faithful stop-entry or stop-loss execution reproduction.
- One entry per local day; target +1 on NQ only, 0 on other symbols.
- Contract costs 0.5bps fee +1.0bps slippage each side, no price-side quotes.
No proprietary data, protected holdout access or brokerage operations.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

CONTRACT_ID = "AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1"
ASSET = "NQ"
CENTRAL = ZoneInfo("America/Chicago")
ADX_PERIOD = 14
ADX_GATE = 17.5
CLOSE_LOOKBACK = 8
ENTRY_START_MINUTE = 8 * 60
ENTRY_END_MINUTE = 14 * 60
FLAT_SIGNAL_MINUTE = 14 * 60 + 30


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Causal Wilder(14) ADX; no prefix- or future-dependent initialization."""
    n = len(high)
    result = np.full(n, np.nan, dtype=np.float64)
    p = ADX_PERIOD
    if n < 2 * p:
        return result
    tr = np.zeros(n, dtype=float)
    plus = np.zeros(n, dtype=float)
    minus = np.zeros(n, dtype=float)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus[i] = up if up > down and up > 0 else 0.0
        minus[i] = down if down > up and down > 0 else 0.0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]),
                    abs(low[i] - close[i - 1]))
    atr = float(np.sum(tr[1:p + 1]))
    plus_sm = float(np.sum(plus[1:p + 1]))
    minus_sm = float(np.sum(minus[1:p + 1]))
    dx = np.full(n, np.nan, dtype=float)
    for i in range(p, n):
        if i > p:
            atr = atr - atr / p + tr[i]
            plus_sm = plus_sm - plus_sm / p + plus[i]
            minus_sm = minus_sm - minus_sm / p + minus[i]
        if atr <= 0:
            dx[i] = 0.0
        else:
            denominator = plus_sm + minus_sm
            dx[i] = (100.0 * abs(plus_sm - minus_sm) / denominator
                     if denominator > 0 else 0.0)
    result[2 * p - 1] = float(np.mean(dx[p:2 * p]))
    for i in range(2 * p, n):
        result[i] = (result[i - 1] * (p - 1) + dx[i]) / p
    return result


class NQADXBreakoutPipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != CONTRACT_ID:
            raise ValueError("Required governed NQ 30m H24 contract")
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        required = {"asset", "timestamp", "open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            raise ValueError("Missing OHLCV fields")
        df = df.sort("asset", "timestamp")
        chunks: list[pl.DataFrame] = []
        for asset in sorted(df["asset"].unique().to_list()):
            chunk = df.filter(pl.col("asset") == asset).sort("timestamp")
            cols = chunk.select("high", "low", "close")
            h = cols["high"].to_numpy().astype(float)
            l = cols["low"].to_numpy().astype(float)
            c = cols["close"].to_numpy().astype(float)
            if not np.all(np.isfinite(h) & np.isfinite(l) & np.isfinite(c)):
                raise ValueError("Invalid price in source OHLC")
            chunk = chunk.with_columns(
                pl.Series("adx14", [float(v) if np.isfinite(v) else None
                                   for v in _adx(h, l, c)], dtype=pl.Float64),
                pl.col("close").rolling_max(CLOSE_LOOKBACK)
                .shift(1).alias("prior_highest_close"),
            )
            chunks.append(chunk)
        return pl.concat(chunks).sort("timestamp", "asset")


pipeline = NQADXBreakoutPipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError(f"Unreviewed strategy parameters: {sorted(kwargs)}")
    if target_symbols != [ASSET]:
        raise ValueError("This exploratory hypothesis only targets NQ CFD")
    if contract.contract_id != CONTRACT_ID or contract.execution_delay_bars != 1:
        raise ValueError("Wrong contract or mandatory next-bar lag")
    feat = pipeline.compute(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    if ASSET not in assets:
        raise ValueError("Missing target NQ")
    ts = sorted(feat["timestamp"].unique().to_list())
    nq = feat.filter(pl.col("asset") == ASSET).select(
        "timestamp", "high", "low", "close", "volume",
        "adx14", "prior_highest_close"
    )
    by_ts = {row[0]: row[1:] for row in nq.iter_rows()}
    out = np.zeros((len(ts), len(assets)), dtype=np.float64)
    asset_idx = assets.index(ASSET)
    pos = False
    entered = False
    last_date = None
    for i, stamp in enumerate(ts):
        local = stamp.astimezone(CENTRAL)
        if local.date() != last_date:
            pos = False
            entered = False
            last_date = local.date()
        clock = local.hour * 60 + local.minute
        if local.weekday() >= 5 or clock >= FLAT_SIGNAL_MINUTE or clock < ENTRY_START_MINUTE:
            pos = False
        elif not pos and not entered and clock <= ENTRY_END_MINUTE:
            row = by_ts.get(stamp)
            if row is not None:
                high, low, close, volume, adx14, level = row
                if (all(x is not None and np.isfinite(x)
                        for x in (high, low, close, volume, adx14, level))
                        and high > low > 0 and close > 0 and volume > 0
                        and adx14 > ADX_GATE and high > level):
                    pos = True
                    entered = True
        out[i, asset_idx] = 1.0 if pos else 0.0
    if not np.isfinite(out).all() or np.any((out < 0.0) | (out > 1.0)):
        raise ValueError("Invalid portfolio weights")
    return {"positions": out}
