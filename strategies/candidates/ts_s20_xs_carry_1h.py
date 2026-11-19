"""Rules-based 1-hour cross-sectional carry strategy (AF30 Strategy 20).

Direct native translation of Robert Carver, Advanced Futures Trading Strategies,
Strategy 20 (Cross-Sectional Carry / AF30), aligned with source spec:
- Carry accumulation: carry_24 = carry_rate.rolling_sum(24).shift(1).over('asset')
- Carry data detection: reads carry_rate or funding_rate if present, else fallback to 0.0
- Cross-sectional ranking: xs_carry_rank = carry_24.rank().over('timestamp')
- Cross-sectional centering: xs_carry_centered = xs_carry_rank - xs_carry_rank.mean().over('timestamp')
- Normalization & scaling: bounded linear (xs_carry_centered / 3.0).clip(-20.0, 20.0)
- Sizing: inverse volatility weighting based on 240-hour rolling return std, water-filled to 0.35 cap
- Trailing stop: 3.0x ATR-14 (symmetric)
- Rebalance frequency: 168 hours
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ts_common import capped_waterfall_weights_clamp as capped_waterfall_weights
from ts_common import normalize_asset_column, trailing_atr_stop_symmetric

from warden.contracts import EvaluationContract


class FeaturePipeline:
    """Causal feature pipeline computing AF30 Strategy 20 cross-sectional carry forecasts."""

    def __init__(
        self,
        carry_window: int = 24,
        carry_min_samples: int = 24,
        atr_window: int = 14,
        vol_window: int = 240,
        vol_min_samples: int = 60,
        scale: float = 3.0,
        cap: float = 20.0,
    ) -> None:
        self.carry_window = carry_window
        self.carry_min_samples = carry_min_samples
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

        actual_carry_col = "carry_rate" if "carry_rate" in df.columns else ("funding_rate" if "funding_rate" in df.columns else None)
        if actual_carry_col is None:
            raise ValueError(
                "Missing required carry data: panel contains neither 'carry_rate' nor 'funding_rate' column"
            )
        carry_std = float(df[actual_carry_col].drop_nulls().std() or 0.0)
        if carry_std == 0.0:
            raise ValueError(
                f"Carry column '{actual_carry_col}' has zero variance (all zero values). "
                "Carry strategies require active financing data (e.g. CRYPTO_MAJORS_1H_V1 or futures term structure)."
            )
        carry_col = pl.col(actual_carry_col)

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
                carry_24=(
                    carry_col.rolling_sum(window_size=self.carry_window, min_samples=self.carry_min_samples)
                    .shift(1)
                    .over("asset")
                ),
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
                forecast=pl.col("carry_24"),
            )
            .fill_null(0.0)
        )


# Backward-compatible aliases
XSCarryPipeline = FeaturePipeline
CrossSectionalCarryPipeline = FeaturePipeline


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract | None = None,
    carry_window: int = 24,
    carry_min_samples: int = 24,
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
    """Compute causal AF30 Strategy 20 cross-sectional carry positions with symmetric trailing ATR-14 stop overlay."""
    _ = kwargs
    panel_df = normalize_asset_column(panel_df)

    pipe = FeaturePipeline(
        carry_window=carry_window,
        carry_min_samples=carry_min_samples,
        atr_window=atr_window,
        vol_window=vol_window,
        vol_min_samples=vol_min_samples,
        scale=scale,
        cap=cap,
    )
    feat_df = pipe.compute(panel_df, contract)
    feat_df = (
        feat_df.with_columns(
            xs_carry_rank=pl.col("carry_24").rank().over("timestamp"),
        )
        .with_columns(
            xs_carry_centered=pl.col("xs_carry_rank") - pl.col("xs_carry_rank").mean().over("timestamp"),
        )
        .with_columns(
            forecast=(pl.col("xs_carry_centered") / scale).clip(-cap, cap),
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
    warmup = max(carry_window, vol_window) + 1
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

        trailing_atr_stop_symmetric(active_indices, positions, atr_mat, t, peak_prices, high_mat, stop_multiple, close_mat, trough_prices, low_mat)

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = FeaturePipeline()
