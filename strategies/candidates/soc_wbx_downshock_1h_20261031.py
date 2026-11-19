"""2022 Wen/Bouri/Xu/Zhao crypto jump-reversal research adaptation.

This code is NOT a reproduction of the published article: all numerical
thresholds and four-hour exit are independent, prespecified choices.
A downside jump with unusually high completed-bar volume is treated as a
proxy for temporary forced-flow price pressure, NOT observed liquidations.

Frozen source/economic specification:
  AI-research-loop/examples/STRATEGY_RESEARCH_20261031_WBX_DOWNSHOCK.md
RePEc HTML sha256:
  c3cf332fb56ee98889343524f3842e6e0d0e8a38fd1c225518ccd3acd0550076

No training, performance-selected parameters, hidden features or external I/O.
Warden supplies the single one-bar next-open execution delay and all costs.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

CONTRACT_ID = "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1"
ASSETS = ("BTCUSDT", "ETHUSDT", "LTCUSDT", "XRPUSDT")
PIT_HOURS = 72
DOWN_VOL_MULTIPLIER = 1.5
VOLUME_MULTIPLIER = 1.5
CLOSE_LOCATION_MAX = 0.35
HOLD_BARS = 4
WEIGHT_PER_ASSET = 0.25

_OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
_SORTED_ASSETS = tuple(sorted(ASSETS))
_N_ASSETS = len(ASSETS)
_ONE_HOUR = np.timedelta64(1, "h")


def _validate(panel: pl.DataFrame, contract: EvaluationContract, target_symbols: list[str] | None) -> tuple[pl.DataFrame, list]:
    if (
        contract.contract_id != CONTRACT_ID
        or contract.frequency != "1h"
        or contract.execution_mode != "NEXT_BAR"
        or contract.execution_delay_bars != 1
        or contract.label_horizon_bars < HOLD_BARS
    ):
        raise ValueError("Original approved USD-M hourly contract required")
    if target_symbols is None or tuple(sorted(target_symbols)) != tuple(sorted(ASSETS)):
        raise ValueError("Frozen four-name source universe required; no subset or asset optimization")
    columns = {"timestamp", "asset", "open", "high", "low", "close", "volume"}
    if not columns <= set(panel.columns):
        raise ValueError(f"Missing required OHLCV columns: {sorted(columns - set(panel.columns))}")
    ordered = panel.sort("timestamp", "asset")
    unique_assets = tuple(sorted(ordered["asset"].unique().to_list()))
    if unique_assets != _SORTED_ASSETS:
        raise ValueError("Only four prespecified sources; no dynamic universe")
    timeline = ordered["timestamp"].unique().sort()
    n_timestamps = timeline.len()
    if n_timestamps < PIT_HOURS + HOLD_BARS + 1:
        raise ValueError("Insufficient source bars for 72h lagged features and 4h exit")
    # Vectorised contiguous-hour check: one diff over the distinct timeline
    # instead of a Python pairwise scan over every bar.
    timeline_np = timeline.to_numpy()
    if np.any(np.diff(timeline_np) != _ONE_HOUR):
        raise ValueError("Missing hourly timestamps; do not fabricate candles")
    if ordered.height != n_timestamps * _N_ASSETS:
        raise ValueError("Unaligned or duplicated instrument timestamp records")
    # (timestamp, asset) sorted rows mean a complete panel is a regular grid:
    # each asset's own timestamp sequence is one boolean-mask selection over
    # the grid, so alignment is proven with numpy comparisons instead of
    # per-asset list materialisation.
    ts_grid = ordered["timestamp"].to_numpy().reshape(n_timestamps, _N_ASSETS)
    asset_grid = ordered["asset"].to_numpy().reshape(n_timestamps, _N_ASSETS)
    for asset in ASSETS:
        own_ts = ts_grid[asset_grid == asset]
        if own_ts.size != n_timestamps or not np.array_equal(own_ts, timeline_np):
            raise ValueError(f"Missing/misaligned hourly panel for {asset}")
    values = {
        name: ordered[name].to_numpy().astype(float, copy=False).reshape(n_timestamps, _N_ASSETS)
        for name in _OHLCV_COLUMNS
    }
    for index, asset in enumerate(ASSETS):
        for name in _OHLCV_COLUMNS:
            if not np.isfinite(values[name][:, index]).all():
                raise ValueError(f"Invalid nonfinite {name} for {asset}")
        open_, high, low, close, volume = (values[name][:, index] for name in _OHLCV_COLUMNS)
        if np.any(
            (open_ <= 0) | (high < low)
            | (low <= 0)
            | (close < low)
            | (close > high)
            | (volume < 0)
        ):
            raise ValueError(f"Invalid OHLCV bounds for {asset}")
    return ordered, timeline.to_list()


class ShockVolumeFeaturePipeline:
    """Causal, per-asset completed-bar diagnostic features for native verify."""

    def compute(
        self, bars_df: pl.DataFrame | pl.LazyFrame,
        contract: EvaluationContract,
    ) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        ordered, _ = _validate(df, contract, list(ASSETS))
        frames = []
        for asset in sorted(ASSETS):
            sub = ordered.filter(pl.col("asset") == asset).sort("timestamp")
            close = sub["close"].to_numpy().astype(float)
            low = sub["low"].to_numpy().astype(float)
            high = sub["high"].to_numpy().astype(float)
            volume = sub["volume"].to_numpy().astype(float)
            returns = np.full(len(close), np.nan)
            returns[1:] = np.log(close[1:] / close[:-1])
            # Both estimators use ONLY the prior 72 completed hours.
            prior_sigma = pl.Series(returns).shift(1).rolling_std(
                window_size=PIT_HOURS, min_samples=PIT_HOURS
            ).to_numpy()
            prior_volume = pl.Series(volume).shift(1).rolling_median(
                window_size=PIT_HOURS, min_samples=PIT_HOURS
            ).to_numpy()
            width = high - low
            location = np.divide(
                close - low, width, out=np.full(len(close), np.nan),
                where=width > 0,
            )
            event = (
                np.isfinite(prior_sigma)
                & (prior_sigma > 0)
                & np.isfinite(prior_volume)
                & (prior_volume > 0)
                & (returns <= -DOWN_VOL_MULTIPLIER * prior_sigma)
                & (volume >= VOLUME_MULTIPLIER * prior_volume)
                & (location <= CLOSE_LOCATION_MAX)
            )
            # Never emit floating NaN as a valid observable feature.
            frames.append(pl.DataFrame({
                "timestamp": sub["timestamp"],
                "asset": sub["asset"],
                "log_ret": pl.Series(returns).fill_nan(None),
                "prior_sigma": pl.Series(prior_sigma).fill_nan(None),
                "prior_volume": pl.Series(prior_volume).fill_nan(None),
                "close_location": pl.Series(location).fill_nan(None),
                "shock_event": pl.Series(event),
            }))
        return pl.concat(frames).sort("timestamp", "asset")


pipeline = ShockVolumeFeaturePipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    target_symbols: list[str] | None = None,
    **extra: object,
) -> dict[str, np.ndarray]:
    """Return target weights after completed-bar information only.

    Signals at bar i -> target weights i..i+3, exit target 0 at i+4.
    Warden moves the position one bar later for causal next-open execution.
    No stop, reset, same-bar reentry, performance-tuned hold or exposure >1.
    """
    if extra:
        raise ValueError(f"Unregistered research parameters: {sorted(extra)}")
    panel = panel_df.collect() if isinstance(panel_df, pl.LazyFrame) else panel_df
    ordered, timestamps = _validate(panel, contract, target_symbols)
    features = pipeline.compute(ordered, contract)
    n_bars = len(timestamps)
    # Same alignment proof as before, read off the sorted (timestamp, asset)
    # grid instead of four per-asset timestamp list comparisons.
    if (
        features.height != n_bars * _N_ASSETS
        or not np.array_equal(features["timestamp"].to_numpy(), ordered["timestamp"].to_numpy())
    ):
        raise ValueError("Feature timestamps lost original alignment")
    feature_assets = features["asset"].to_numpy().reshape(n_bars, _N_ASSETS)
    for index, asset in enumerate(ASSETS):
        if not (feature_assets[:, index] == asset).all():
            raise ValueError("Feature timestamps lost original alignment")
    events = features["shock_event"].to_numpy().reshape(n_bars, _N_ASSETS)
    weights = np.zeros((n_bars, _N_ASSETS), dtype=np.float64)
    for column, _asset in enumerate(sorted(ASSETS)):
        # Only event bars matter: walk them once per asset and fill each
        # holding window with slice assignments instead of a per-bar loop.
        # An event on a bar inside a holding window, including the exit bar
        # itself, is ignored; the next eligible entry bar is start+5.
        starts = np.flatnonzero(events[:, column])
        cursor = 0
        while cursor < starts.size:
            start = int(starts[cursor])
            stop = start + HOLD_BARS
            weights[start:min(stop, n_bars), column] = WEIGHT_PER_ASSET
            if stop < n_bars:
                # Exit target stays flat on the exit bar even if another
                # jump occurs there. No same-bar reentry.
                weights[stop, column] = 0.0
            cursor = int(np.searchsorted(starts, start + HOLD_BARS + 1, side="left"))

    if np.max(np.abs(weights).sum(axis=1)) > 1.0 + 1e-12:
        raise ValueError("Prespecified 1x maximum gross exposure exceeded")
    return {"positions": weights}
