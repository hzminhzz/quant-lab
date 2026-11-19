"""Rules-based 1-hour volatility compression squeeze breakout strategy (HYP-2026-013)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from ts_common import normalize_asset_column

from warden.contracts import EvaluationContract


class VolCompressionBreakoutPipeline:
    """Causal feature pipeline computing Bollinger Bands, Keltner Channels, squeeze status, and directional momentum."""

    def __init__(
        self,
        bb_window: int = 20,
        bb_k: float = 2.0,
        kc_window: int = 20,
        kc_mult: float = 1.5,
        mom_window: int = 12,
    ) -> None:
        self.bb_window = bb_window
        self.bb_k = bb_k
        self.kc_window = kc_window
        self.kc_mult = kc_mult
        self.mom_window = mom_window

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        prev_close = pl.col("close").shift(1).over("asset")
        tr1 = pl.col("high") - pl.col("low")
        tr2 = (pl.col("high") - prev_close).abs()
        tr3 = (pl.col("low") - prev_close).abs()
        tr = pl.max_horizontal(tr1, tr2, tr3)

        prev_mom_close = pl.col("close").shift(self.mom_window).over("asset")
        mom = (pl.col("close") - prev_mom_close) / (prev_mom_close + 1e-12)

        return (
            df.sort("timestamp", "asset")
            .with_columns(
                tr=tr,
                bb_mid=pl.col("close").rolling_mean(self.bb_window).over("asset"),
                bb_std=pl.col("close").rolling_std(self.bb_window).over("asset"),
                mom=mom,
            )
            .with_columns(
                bb_upper=pl.col("bb_mid") + self.bb_k * pl.col("bb_std"),
                bb_lower=pl.col("bb_mid") - self.bb_k * pl.col("bb_std"),
                bb_width=2.0 * self.bb_k * pl.col("bb_std"),
                kc_atr=pl.col("tr").rolling_mean(self.kc_window).over("asset"),
            )
            .with_columns(
                kc_upper=pl.col("bb_mid") + self.kc_mult * pl.col("kc_atr"),
                kc_lower=pl.col("bb_mid") - self.kc_mult * pl.col("kc_atr"),
                kc_width=2.0 * self.kc_mult * pl.col("kc_atr"),
            )
            .with_columns(
                squeeze=(pl.col("bb_width") < pl.col("kc_width")).fill_null(False),
            )
        )


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    bb_window: int = 20,
    bb_k: float = 2.0,
    kc_window: int = 20,
    kc_mult: float = 1.5,
    mom_window: int = 12,
    holding_bars: int = 36,
    squeeze_lookback: int = 5,
    target_symbols: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute causal squeeze breakout positions: enter when Bollinger Band width expands out of Keltner Channel with directional momentum."""
    _ = kwargs
    panel_df = normalize_asset_column(panel_df)

    pipe = VolCompressionBreakoutPipeline(
        bb_window=bb_window,
        bb_k=bb_k,
        kc_window=kc_window,
        kc_mult=kc_mult,
        mom_window=mom_window,
    )
    feat_df = pipe.compute(panel_df, contract)

    assets = sorted(feat_df["asset"].unique().to_list())
    n_bars = len(feat_df["timestamp"].unique())
    n_assets = len(assets)

    squeeze_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="squeeze")
        .fill_null(False)
        .select(assets)
        .to_numpy()
    )
    mom_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="mom")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    close_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="close")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    bb_upper_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="bb_upper")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )
    bb_lower_mat = (
        feat_df.pivot(index="timestamp", on="asset", values="bb_lower")
        .fill_null(0.0)
        .select(assets)
        .to_numpy()
    )

    if target_symbols is not None:
        active_indices = [i for i, sym in enumerate(assets) if sym in target_symbols]
    else:
        active_indices = list(range(n_assets))

    if not active_indices:
        active_indices = list(range(n_assets))

    positions = np.zeros((n_bars, n_assets), dtype=float)
    warmup = max(bb_window, kc_window, mom_window) + squeeze_lookback + 1
    weight = 1.0 / len(active_indices)

    for a_idx in active_indices:
        current_pos = 0.0
        hold_bars = 0
        for t in range(warmup, n_bars):
            in_squeeze = squeeze_mat[t, a_idx]
            had_recent_squeeze = bool(np.any(squeeze_mat[t - squeeze_lookback : t, a_idx]))
            mom = mom_mat[t, a_idx]
            close = close_mat[t, a_idx]
            upper = bb_upper_mat[t, a_idx]
            lower = bb_lower_mat[t, a_idx]

            if current_pos != 0.0:
                hold_bars += 1
                if hold_bars >= holding_bars or (current_pos > 0 and mom < 0) or (current_pos < 0 and mom > 0):
                    current_pos = 0.0
                    hold_bars = 0
            else:
                if had_recent_squeeze and not in_squeeze:
                    if mom > 0 and close >= upper:
                        current_pos = weight
                        hold_bars = 0
                    elif mom < 0 and close <= lower:
                        current_pos = -weight
                        hold_bars = 0

            positions[t, a_idx] = current_pos

    return {"positions": positions}


# Default pipeline instance for warden verify
pipeline = VolCompressionBreakoutPipeline()
