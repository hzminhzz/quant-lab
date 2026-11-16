"""Original pre-registered five-session FX reversal hypothesis HYP-2026-129.

Source and parameters: .workspace/hypotheses/HYP-2026-129.md.
Causal close-based signal at decision t; Warden owns next-bar execution.
No data access, cost overrides, or lagging of positions occurs in this module.
"""

from __future__ import annotations

import math
from typing import Any

import polars as pl

from warden.contracts import EvaluationContract


class FXFiveDayShockReversalPipeline:
    """Per-symbol five-session shock score and fixed-interval contrarian weights."""

    def __init__(
        self,
        formation_bars: int = 5,
        vol_window: int = 20,
        z_threshold: float = 1.25,
        rebalance_every: int = 5,
        per_symbol_weight: float = 0.125,
    ) -> None:
        if formation_bars < 1 or vol_window < 2 or rebalance_every < 1:
            raise ValueError("Windows and rebalance interval must be positive")
        if z_threshold <= 0 or per_symbol_weight <= 0:
            raise ValueError("Threshold and per-symbol weight must be positive")
        self.formation_bars = formation_bars
        self.vol_window = vol_window
        self.z_threshold = z_threshold
        self.rebalance_every = rebalance_every
        self.per_symbol_weight = per_symbol_weight

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        _ = contract
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        df = df.sort("asset", "timestamp")
        df = df.with_columns(
            ret1=(pl.col("close").log() - pl.col("close").log().shift(1).over("asset")),
            impulse=(pl.col("close").log() - pl.col("close").log().shift(self.formation_bars).over("asset")),
            bar_index=pl.col("close").cum_count().over("asset") - 1,
        )
        df = df.with_columns(
            sigma20=pl.col("ret1").rolling_std(window_size=self.vol_window).over("asset")
        )
        df = df.with_columns(
            z=pl.when(pl.col("sigma20") > 1e-10)
            .then(pl.col("impulse") / (pl.col("sigma20") * math.sqrt(self.formation_bars)))
            .otherwise(None)
        )
        df = df.with_columns(
            candidate=pl.when(pl.col("z") <= -self.z_threshold)
            .then(self.per_symbol_weight)
            .when(pl.col("z") >= self.z_threshold)
            .then(-self.per_symbol_weight)
            .otherwise(0.0)
        )
        df = df.with_columns(
            announced=pl.when(pl.col("bar_index") % self.rebalance_every == 0)
            .then(pl.col("candidate"))
            .otherwise(None)
        )
        df = df.with_columns(
            weight=pl.col("announced").forward_fill().over("asset").fill_null(0.0)
        )
        return df.sort("timestamp", "asset")


pipeline = FXFiveDayShockReversalPipeline()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    formation_bars: int = 5,
    vol_window: int = 20,
    z_threshold: float = 1.25,
    rebalance_every: int = 5,
    per_symbol_weight: float = 0.125,
    **kwargs: Any,
) -> dict[str, Any]:
    """Return timestamp x sorted-asset weights; do not apply an execution lag here."""
    _ = kwargs
    feat = FXFiveDayShockReversalPipeline(
        formation_bars=formation_bars,
        vol_window=vol_window,
        z_threshold=z_threshold,
        rebalance_every=rebalance_every,
        per_symbol_weight=per_symbol_weight,
    ).compute(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    weights = (
        feat.pivot(index="timestamp", on="asset", values="weight")
        .sort("timestamp")
        .select(assets)
        .fill_null(0.0)
        .to_numpy()
    )
    return {"positions": weights}
