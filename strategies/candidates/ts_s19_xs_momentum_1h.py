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
from ts_common import capped_waterfall_weights_clamp as capped_waterfall_weights
from ts_common import normalize_asset_column

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
    panel_df = normalize_asset_column(panel_df)

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

    if active_indices:
        act = np.asarray(active_indices, dtype=int)
        eps = 1e-6
        step = abs(rebalance_bars)
        # First rebalance bar at or after warmup (same modulo semantics as the original loop).
        start = warmup + (-warmup) % rebalance_bars
        for seg_start in range(start, n_bars, step):
            seg_end = min(seg_start + step, n_bars)
            length = seg_end - seg_start

            active_ivs = iv_mat[seg_start, act]
            weights = capped_waterfall_weights(active_ivs, cap=per_instrument_cap, gross=gross_exposure)
            entry = (fc_mat[seg_start, act] / cap) * weights

            hi = high_mat[seg_start:seg_end, act]
            lo = low_mat[seg_start:seg_end, act]
            cl = close_mat[seg_start:seg_end, act]
            atr_path = atr_mat[seg_start:seg_end, act]

            # Trail from the rebalance reseed: peak/trough at bar t are exactly the running
            # extremes of the held window, so the path-dependent recursion is a cumulative op.
            peak_path = np.maximum.accumulate(hi, axis=0)
            trough_path = np.minimum.accumulate(lo, axis=0)
            hit = (entry > eps) & (cl < peak_path - stop_multiple * atr_path)
            hit = hit | ((entry < -eps) & (cl > trough_path + stop_multiple * atr_path))

            seg_pos = np.broadcast_to(entry, (length, entry.shape[0])).copy()
            if hit.any():
                first_hit = np.argmax(hit, axis=0)
                rows = np.arange(length)[:, None]
                seg_pos = np.where((rows >= first_hit[None, :]) & hit.any(axis=0)[None, :], 0.0, seg_pos)
            positions[seg_start:seg_end, act] = seg_pos
            peak_prices[act] = peak_path[-1]
            trough_prices[act] = trough_path[-1]

    return {"positions": positions, "forecast": fc_mat}


# Default pipeline instance for warden verify
pipeline = FeaturePipeline()
