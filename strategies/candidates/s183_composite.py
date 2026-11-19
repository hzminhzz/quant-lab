"""RESTORED, NOT THE ORIGINAL BYTES: HYP-2026-126's module after it was rewritten in place.

`s183_reference.py` (HYP-2026-127) is the port of the repo's own implementation; this file is
the earlier reconstruction, kept only so the record is not silently empty. Its original bytes
were overwritten and are unrecoverable, and no strategy-code hash was recorded for the two
screen attempts (only the returns-stream hashes:
`848aeb44...481e` for ATT-d107ad4f9fcac118-001 and `c8c4a430...369c` for `-002`).
Those attempts therefore cannot be reproduced from this tree. See
`.workspace/investigations/trends_research_20261029/REPRODUCTION.md` for the fidelity table.

This is a reconstruction of trends-research S183 from the flagship file's constants, built
before the repo's own reference implementation (`Live/2_Engine/trading_book.py`) was read.
It differs from the original in the volatility estimator, the EWMAC normalisation, the VoV
nesting, the FDM estimator, and it omits the gvol/gdd gates and IDM entirely. See
`.workspace/investigations/trends_research_20261029/REPRODUCTION.md` for the fidelity table.

Sleeves kept: trend (0.5*TS EWMAC + 0.5*XS z-trend), skew (-252-bar skew), vol-of-vol.
Carry is absent (no futures term structure in the lake).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent / "carver_daily"))  # warden loads by path
from carver_common import apply_buffer, observation_mask, value_matrix
from ml4t.engineer import compute_features

SPEEDS: tuple[tuple[int, int], ...] = ((16, 64), (32, 128), (64, 256))  # S183 TREND_SPEED_PAIRS
SCALARS: dict[int, float] = {16: 4.10, 32: 2.79, 64: 1.91}  # Carver Table 29
XS_LOOKBACK = 256  # S183 XS_LOOKBACK
SKEW_WINDOW = 252  # S183 SKEW_WINDOW (256 on a 256-day year)
VOV_WINDOW = 64  # S183 VOV_WINDOW
VOV_AVG_WINDOW = 256  # S183 VOV_AVG_WINDOW
FDM_SPAN = 512  # S183 FDM_CORR_SPAN


def forecast(raw: pl.Expr, span: int = 256) -> pl.Expr:
    """Carver forecast scalar, per asset: mean|f| = 10 trailing, cap +-20.

    `.over("symbol")` wraps the whole chain (ewm then shift), so the scalar for one asset
    never sees another asset's history. `raw` must be a plain column reference.
    """
    scale = 10.0 / raw.abs().ewm_mean(span=span, min_samples=64).shift(1).over("symbol")
    return (raw * scale).clip(-20.0, 20.0)


def build_signal_frame(panel_df: pl.DataFrame, contract: Any) -> pl.DataFrame:
    """Panel -> per-row sleeve forecasts and final weight. Every window is per asset."""
    bars = int(contract.bars_per_year)
    df = panel_df if "symbol" in panel_df.columns else panel_df.rename({"asset": "symbol"})
    df = df.sort("symbol", "timestamp").with_columns(
        returns=pl.col("close").pct_change().over("symbol")
    )
    # ml4t-engineer features; calendar=None = observation rows on a sparse union panel
    df = compute_features(
        df,
        [
            {"name": "realized_volatility",
             "params": {"period": 35, "annualize": True, "trading_periods": bars}},
            {"name": "higher_moments", "params": {"window": SKEW_WINDOW}},
            {"name": "volatility_of_volatility",
             "params": {"vol_period": VOV_WINDOW, "vov_period": VOV_WINDOW, "annualize": False}},
        ],
        group_col="symbol",
        calendar=None,
    )
    if isinstance(df, pl.LazyFrame):
        df = df.collect()
    vol_bar = pl.col("realized_volatility") / math.sqrt(bars)

    # pass A: raw sleeve values, every windowed term explicitly grouped by asset
    df = df.with_columns(
        [
            (
                (pl.col("close").ewm_mean(span=fast).over("symbol")
                 - pl.col("close").ewm_mean(span=slow).over("symbol"))
                / pl.col("close") / vol_bar * SCALARS[fast]
            ).alias(f"ewmac_raw_{fast}")
            for fast, slow in SPEEDS
        ]
    ).with_columns(
        xs_raw=pl.col("close").pct_change(XS_LOOKBACK).over("symbol")
        / (vol_bar * math.sqrt(XS_LOOKBACK)),
        skew_raw=-pl.col("higher_moments_skewness"),
        vov_raw=-(
            pl.col("volatility_of_volatility")
            / pl.col("volatility_of_volatility").rolling_mean(VOV_AVG_WINDOW).over("symbol")
            - 1.0
        )
        * pl.col("close").pct_change(63).over("symbol").sign(),
    )

    # pass B: forecast scaling (plain column refs, so `forecast` owns the only window)
    df = df.with_columns(
        [forecast(pl.col(f"ewmac_raw_{fast}")).alias(f"ewmac_{fast}") for fast, _ in SPEEDS]
    ).with_columns(
        trend_ts=(pl.col(f"ewmac_{SPEEDS[0][0]}") + pl.col(f"ewmac_{SPEEDS[1][0]}")
                  + pl.col(f"ewmac_{SPEEDS[2][0]}")) / len(SPEEDS),
    )
    df = df.with_columns(
        xs_z=(pl.col("xs_raw") - pl.col("xs_raw").mean().over("timestamp"))
        / pl.col("xs_raw").std().over("timestamp")
    ).with_columns(trend_xs=forecast(pl.col("xs_z") * 10.0))
    df = df.with_columns(
        trend=0.5 * pl.col("trend_ts") + 0.5 * pl.col("trend_xs"),
    ).with_columns(
        f_trend=forecast(pl.col("trend")),
        f_skew=forecast(pl.col("skew_raw")),
        f_vov=forecast(pl.col("vov_raw")),
    )
    return add_composite(df).rename({"symbol": "asset"})


def add_composite(df: pl.DataFrame) -> pl.DataFrame:
    """Equal-weight composite x FDM from the trailing correlation of sleeve forecasts."""
    df = df.with_columns(blend_raw=(pl.col("f_trend") + pl.col("f_skew") + pl.col("f_vov")) / 3.0)
    cols = ["f_trend", "f_skew", "f_vov"]
    n = len(cols)
    daily = (
        df.group_by("timestamp")
        .agg([pl.col(c).mean().alias(f"p_{c}") for c in cols])
        .sort("timestamp")
    )
    pairs = [(a, b) for i, a in enumerate(cols) for b in cols[i + 1 :]]
    for a, b in pairs:
        daily = daily.with_columns(
            (pl.col(f"p_{a}") * pl.col(f"p_{b}")).ewm_mean(span=FDM_SPAN, min_samples=256).alias(f"cov_{a}_{b}"),
            (pl.col(f"p_{a}") ** 2).ewm_mean(span=FDM_SPAN, min_samples=256).alias(f"v_{a}"),
            (pl.col(f"p_{b}") ** 2).ewm_mean(span=FDM_SPAN, min_samples=256).alias(f"v_{b}"),
        )
    corrs = [
        (pl.col(f"cov_{a}_{b}") / (pl.col(f"v_{a}") * pl.col(f"v_{b}")).sqrt()).clip(-1, 1)
        for a, b in pairs
    ]
    rho = sum(corrs[1:], corrs[0]) / len(pairs)
    corr = daily.with_columns(rho_bar=rho.shift(1)).select(
        "timestamp",
        fdm=(n / (1.0 + (n - 1.0) * pl.col("rho_bar"))).sqrt().clip(1.0, math.sqrt(n)),
    )
    return df.join(corr, on="timestamp", how="left").with_columns(
        blend=(pl.col("blend_raw") * pl.col("fdm").fill_null(1.0)).clip(-20.0, 20.0)
    )


def add_weights(df: pl.DataFrame, vol_target: float, cap: float) -> pl.DataFrame:
    n_active = pl.col("blend").is_not_null().sum().over("timestamp")
    return df.with_columns(
        weight=((pl.col("blend") / 10.0) * ((vol_target / n_active) / pl.col("realized_volatility")))
        .clip(-cap, cap)
        .fill_null(0.0)
    )


def buffered(target: np.ndarray, observed: np.ndarray, fraction: float = 0.10) -> np.ndarray:
    """Hold the previous weight unless the target moves more than `fraction` of itself."""
    out = np.zeros_like(target)
    for t in range(target.shape[0]):
        for i in range(target.shape[1]):
            if not observed[t, i]:
                out[t, i] = out[t - 1, i] if t else 0.0
                continue
            held = out[t - 1, i] if t else 0.0
            out[t, i] = apply_buffer(target[t, i], held, fraction, abs(target[t, i]))
    return out


class S183Pipeline:
    """Feature pipeline for warden's prefix-invariance check (time leakage)."""

    def compute(self, bars_df: pl.DataFrame, contract: Any) -> pl.DataFrame:
        return build_signal_frame(bars_df, contract)


class S183PerAssetPipeline:
    """Per-asset sleeves only, for the cross-asset leakage check."""

    def compute(self, bars_df: pl.DataFrame, contract: Any) -> pl.DataFrame:
        return build_signal_frame(bars_df, contract).select(
            "timestamp", "asset", "trend_ts", "f_skew", "f_vov"
        )


pipeline = S183Pipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: Any,
    vol_target: float = 0.13,
    per_instrument_cap: float = 0.5,
    buffer_fraction: float = 0.10,
    warmup: int = 1,
    **kwargs: Any,
) -> dict[str, Any]:
    """Buffered multi-sleeve weights. `vol_target=0.13` == S183's 0.20 after its overlays."""
    _ = kwargs
    feat = add_weights(build_signal_frame(panel_df, contract), vol_target, per_instrument_cap)
    assets = sorted(feat["asset"].unique().to_list())
    target = value_matrix(feat, "weight", assets)
    observed = observation_mask(feat, assets)
    target[:warmup] = 0.0
    return {"positions": buffered(target, observed, buffer_fraction), "assets": assets}
