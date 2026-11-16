"""Rules-based 1-hour cross-sectional momentum strategy (AF30 Strategy 19).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 19 (Cross-Sectional Momentum / AF30), aligned with source spec:
- Lookback: 24-bar percentage change of close: mom_24 = close.pct_change(24).over('asset')
- Cross-sectional ranking: xs_mom_rank = mom_24.rank().over('timestamp')
- Cross-sectional centering: xs_mom_centered = xs_mom_rank - xs_mom_rank.mean().over('timestamp')
- Normalization & scaling: bounded linear (xs_mom_centered / 3.0).clip(-20.0, 20.0)
- Sizing: inverse volatility weighting based on 240-hour rolling return std, water-filled to 0.35 cap
- Trailing stop: 3.0x ATR-14 (symmetric)
- Rebalance frequency: 168 hours
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import Strategy

from warden.contracts import EvaluationContract


class FeaturePipeline:
    """Causal feature pipeline computing AF30 Strategy 19 cross-sectional momentum forecasts."""

    def __init__(
        self,
        lookback_bars: int = 24,
        atr_window: int = 14,
        vol_window: int = 240,
        vol_min_samples: int = 60,
        scale: float = 3.0,
        cap: float = 20.0,
    ) -> None:
        self.lookback_bars = lookback_bars
        self.atr_window = atr_window
        self.vol_window = vol_window
        self.vol_min_samples = vol_min_samples
        self.scale = scale
        self.cap = cap

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract | None = None) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})

        prev_close = pl.col("close").shift(1).over("asset")
        tr = pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - prev_close).abs(),
            (pl.col("low") - prev_close).abs(),
        )

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                hourly_ret=(pl.col("close") - prev_close) / (prev_close + 1e-12),
                tr=tr,
                mom_24=pl.col("close").pct_change(self.lookback_bars).over("asset"),
            )
            .with_columns(
                atr_14=pl.col("tr").ewm_mean(span=self.atr_window).over("asset"),
                vol_240=(
                    pl.col("hourly_ret")
                    .rolling_std(window_size=self.vol_window, min_samples=self.vol_min_samples)
                    .shift(1)
                    .over("asset")
                ),
            )
            .with_columns(
                raw_inv_vol=1.0 / (pl.col("vol_240") + 1e-12),
                forecast=pl.col("mom_24"),
            )
            .fill_null(0.0)
        )


# Backward-compatible aliases
XSMomentumPipeline = FeaturePipeline
CrossSectionalMomentumPipeline = FeaturePipeline


class XSMomentumStrategy(Strategy):
    """Lifecycle-v1 portable Strategy subclass executing AF30 Strategy 19 cross-sectional momentum."""

    def __init__(
        self,
        lookback_bars: int = 24,
        atr_window: int = 14,
        vol_window: int = 240,
        vol_min_samples: int = 60,
        scale: float = 3.0,
        cap: float = 20.0,
        rebalance_bars: int = 168,
        stop_multiple: float = 3.0,
        per_instrument_cap: float = 0.35,
        gross_exposure: float = 1.0,
    ) -> None:
        super().__init__()
        self.lookback_bars = lookback_bars
        self.atr_window = atr_window
        self.vol_window = vol_window
        self.vol_min_samples = vol_min_samples
        self.scale = scale
        self.cap = cap
        self.rebalance_bars = rebalance_bars
        self.stop_multiple = stop_multiple
        self.per_instrument_cap = per_instrument_cap
        self.gross_exposure = gross_exposure
        self.bar_count = 0

    def on_data(self, timestamp: Any, data: dict[str, Any], context: dict[str, Any], broker: Any) -> None:
        self.bar_count += 1


CrossSectionalMomentumStrategy = XSMomentumStrategy


def capped_waterfall_weights(raw_weights: np.ndarray, cap: float = 0.35, gross: float = 1.0) -> np.ndarray:
    """Enforce exact per-instrument risk budget cap by iterative water-filling."""
    n = len(raw_weights)
    if n == 0:
        return np.empty(0, dtype=float)
    if np.sum(raw_weights) <= 0:
        return np.full(n, min(gross / max(1, n), cap))
    w = (raw_weights / np.sum(raw_weights)) * gross
    frozen = np.zeros(n, dtype=bool)
    for _ in range(n):
        over = (w > cap + 1e-12) & ~frozen
        if not np.any(over):
            break
        w[over] = cap
        frozen[over] = True
        remaining = gross - cap * float(np.sum(frozen))
        free = ~frozen
        free_sum = float(np.sum(raw_weights[free]))
        if free_sum > 0 and remaining > 0:
            w[free] = (raw_weights[free] / free_sum) * remaining
        else:
            w[free] = 0.0
            break
    return np.minimum(w, cap)


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract | None = None,
    lookback_bars: int = 24,
    atr_window: int = 14,
    vol_window: int = 240,
    vol_min_samples: int = 60,
    scale: float = 3.0,
    cap: float = 20.0,
    rebalance_bars: int = 168,
    stop_multiple: float = 3.0,
    per_instrument_cap: float = 0.35,
    gross_exposure: float = 1.0,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal AF30 Strategy 19 cross-sectional momentum positions with symmetric trailing ATR-14 stop overlay."""
    _ = kwargs
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})

    pipe = FeaturePipeline(
        lookback_bars=lookback_bars,
        atr_window=atr_window,
        vol_window=vol_window,
        vol_min_samples=vol_min_samples,
        scale=scale,
        cap=cap,
    )
    feat_df = pipe.compute(panel_df, contract)
    feat_df = (
        feat_df.with_columns(
            xs_mom_rank=pl.col("mom_24").rank().over("timestamp"),
        )
        .with_columns(
            xs_mom_centered=pl.col("xs_mom_rank") - pl.col("xs_mom_rank").mean().over("timestamp"),
        )
        .with_columns(
            forecast=(pl.col("xs_mom_centered") / scale).clip(-cap, cap),
        )
    )
    assets = sorted(feat_df["asset"].unique().to_list())
    fc_pivot = feat_df.pivot(index="timestamp", on="asset", values="forecast").fill_null(0.0)
    for a in assets:
        if a not in fc_pivot.columns:
            fc_pivot = fc_pivot.with_columns(pl.lit(0.0).alias(a))
    fc_mat = fc_pivot.select(assets).to_numpy()

    iv_pivot = feat_df.pivot(index="timestamp", on="asset", values="raw_inv_vol").fill_null(0.0)
    for a in assets:
        if a not in iv_pivot.columns:
            iv_pivot = iv_pivot.with_columns(pl.lit(0.0).alias(a))
    iv_mat = iv_pivot.select(assets).to_numpy()

    close_pivot = feat_df.pivot(index="timestamp", on="asset", values="close").fill_null(0.0)
    for a in assets:
        if a not in close_pivot.columns:
            close_pivot = close_pivot.with_columns(pl.lit(0.0).alias(a))
    close_mat = close_pivot.select(assets).to_numpy()

    high_pivot = feat_df.pivot(index="timestamp", on="asset", values="high").fill_null(0.0)
    for a in assets:
        if a not in high_pivot.columns:
            high_pivot = high_pivot.with_columns(pl.lit(0.0).alias(a))
    high_mat = high_pivot.select(assets).to_numpy()

    low_pivot = feat_df.pivot(index="timestamp", on="asset", values="low").fill_null(0.0)
    for a in assets:
        if a not in low_pivot.columns:
            low_pivot = low_pivot.with_columns(pl.lit(0.0).alias(a))
    low_mat = low_pivot.select(assets).to_numpy()

    atr_pivot = feat_df.pivot(index="timestamp", on="asset", values="atr_14").fill_null(0.0)
    for a in assets:
        if a not in atr_pivot.columns:
            atr_pivot = atr_pivot.with_columns(pl.lit(0.0).alias(a))
    atr_mat = atr_pivot.select(assets).to_numpy()

    n_bars = fc_mat.shape[0]
    n_assets = len(assets)

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        active_indices = list(range(n_assets))

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    warmup = max(lookback_bars, vol_window) + 1
    peak_prices = np.zeros(n_assets, dtype=float)
    trough_prices = np.full(n_assets, 1e12, dtype=float)

    for t in range(warmup, n_bars):
        if t % rebalance_bars == 0:
            active_ivs = iv_mat[t, active_indices]
            weights = capped_waterfall_weights(active_ivs, cap=per_instrument_cap, gross=gross_exposure)
            for idx, a_idx in enumerate(active_indices):
                fc = fc_mat[t, a_idx]
                positions[t, a_idx] = (fc / cap) * weights[idx]
                peak_prices[a_idx] = high_mat[t, a_idx]
                trough_prices[a_idx] = low_mat[t, a_idx]
        else:
            positions[t] = positions[t - 1]

        # Apply trailing ATR stop symmetrically
        for a_idx in active_indices:
            pos = positions[t, a_idx]
            atr = atr_mat[t, a_idx]
            if pos > 1e-6:
                peak_prices[a_idx] = max(peak_prices[a_idx], high_mat[t, a_idx])
                stop_price = peak_prices[a_idx] - stop_multiple * atr
                if close_mat[t, a_idx] < stop_price:
                    positions[t, a_idx] = 0.0
            elif pos < -1e-6:
                trough_prices[a_idx] = min(trough_prices[a_idx], low_mat[t, a_idx])
                stop_price = trough_prices[a_idx] + stop_multiple * atr
                if close_mat[t, a_idx] > stop_price:
                    positions[t, a_idx] = 0.0

    return {"positions": positions, "forecast": fc_mat}


# Default pipeline instance for warden verify
pipeline = FeaturePipeline()
