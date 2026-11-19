"""Cross-sectional Hill tail index crash risk factor model with purged walk-forward CV (HYP-2026-030)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from cs_common import cs_positions

from warden.contracts import EvaluationContract


def _rolling_hill_score(arr: np.ndarray, window: int = 336, k_frac: float = 0.10) -> np.ndarray:
    """Non-parametric Hill (1975) tail exponent on negative returns. Returns -alpha (higher = heavier tail)."""
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        w = arr[i - window + 1 : i + 1]
        neg = -w[w < 0]
        n_neg = len(neg)
        k = int(np.floor(k_frac * n_neg))
        if k >= 3 and n_neg > k:
            part = np.partition(neg, -k - 1)[-k - 1 :]
            part.sort()
            x_k1 = part[0]
            if x_k1 > 1e-8:
                top_k = part[1:]
                h = np.mean(np.log(top_k / x_k1))
                if h > 1e-6:
                    out[i] = -(1.0 / h)
    return out


class CSHillTailIndexPipeline:
    """Causal cross-sectional feature pipeline for Pareto power-law tail index (Hill 1975, Kelly & Jiang 2014)."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        delay = contract.execution_delay_bars

        df = df.sort("timestamp", "asset")
        df = df.with_columns(ret_1h=pl.col("close").pct_change().over("asset"))
        neg_ret = pl.when(pl.col("ret_1h") < 0).then(-pl.col("ret_1h")).otherwise(0.0)
        df = df.with_columns(neg_ret=neg_ret)

        def hill_col(w: int):
            q90 = pl.col("neg_ret").rolling_quantile(0.90, window_size=w).over("asset")
            excess = (
                pl.when(pl.col("neg_ret") > q90)
                .then(pl.col("neg_ret") / (q90 + 1e-8))
                .otherwise(1.0)
                .log()
            )
            h = excess.rolling_mean(w).over("asset") / 0.10
            return (-(1.0 / (h + 1e-6))).shift(delay).over("asset")

        neg_semi_vol = pl.col("neg_ret").pow(2).rolling_mean(168).sqrt().shift(delay).over("asset")

        df = df.with_columns(
            hill_720=hill_col(720),
            hill_336=hill_col(336),
            neg_semi_vol=neg_semi_vol,
        )
        return df.with_columns(
            feat_hill_720=(pl.col("hill_720") - pl.col("hill_720").mean().over("timestamp"))
            / (pl.col("hill_720").std().over("timestamp") + 1e-8),
            feat_hill_336=(pl.col("hill_336") - pl.col("hill_336").mean().over("timestamp"))
            / (pl.col("hill_336").std().over("timestamp") + 1e-8),
            feat_neg_semi_vol=(pl.col("neg_semi_vol") - pl.col("neg_semi_vol").mean().over("timestamp"))
            / (pl.col("neg_semi_vol").std().over("timestamp") + 1e-8),
        )


def generate_positions(panel_df: pl.DataFrame, contract: EvaluationContract, alpha: float = 10.0, rebalance_every: int = 72, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
    """Train Ridge across purged walk-forward folds and emit dollar-neutral positions."""
    return cs_positions(panel_df, contract, pipeline=pipeline,
                        feature_cols=['feat_hill_720', 'feat_hill_336', 'feat_neg_semi_vol'], warmup=720,
                        alpha=alpha, rebalance_every=rebalance_every, top_k=top_k, ic_guard=False, predict_rowwise=True, invert_aliases=False, fill_missing_assets=False, min_train=10, exclude_symbol='BTCUSDT', **kwargs)


pipeline = CSHillTailIndexPipeline()
