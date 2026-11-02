"""Four predeclared 4h-horizon cross-sectional factor studies on pinned Binance USD-M 1h.

Methods are fixed, parameter-free research adaptations; no fitted model, no
unseen data, no funding/quote substitution. Scores only see closed hour t;
execution is handled by Warden at t+1 open. Validation labels are computed
ONLY to measure OOS information coefficient, never as trade features.
These are distinct *search attempts*, not independent scientific discoveries.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

import numpy as np
import polars as pl
from scipy.stats import spearmanr

from warden.contracts import EvaluationContract

Kind = Literal["btc_spillover", "liquid_weekly_momentum", "sign_reversal", "price_volume_confirm"]
CONTRACT_ID = "CRYPTO_PERPS_TOP100_1H_V1"
MIN_CROSS_SECTION = 30
PICK = 5
REBALANCE_HOURS = 4
FEATURE_FIELDS = {"timestamp", "asset", "open", "high", "low", "close", "volume", "funding_rate"}
HYPOTHESES = {
    "btc_spillover": "HYP-XS4H-BTC-SPILLOVER-20261102",
    "liquid_weekly_momentum": "HYP-XS4H-LIQ-MOM-20261102",
    "sign_reversal": "HYP-XS4H-SIGN-REV-20261102",
    "price_volume_confirm": "HYP-XS4H-PRICE-VOLUME-20261102",
}


def contiguous(n: int) -> pl.Expr:
    return (
        pl.col("timestamp") - pl.col("timestamp").shift(n).over("asset")
        == pl.duration(hours=n)
    )


def _rolling(name: str, n: int) -> pl.Expr:
    return pl.col(name).rolling_sum(window_size=n, min_samples=n).over("asset")


def source_features(df: pl.DataFrame | pl.LazyFrame, kind: Kind) -> pl.DataFrame:
    if kind not in HYPOTHESES:
        raise ValueError(f"Unknown preregistered source-complete factor: {kind}")
    frame = df.collect() if isinstance(df, pl.LazyFrame) else df
    if "symbol" in frame.columns and "asset" not in frame.columns:
        frame = frame.rename({"symbol": "asset"})
    missing = FEATURE_FIELDS - set(frame.columns)
    if missing:
        raise ValueError(f"Missing pinned Binance OHLCV/funding inputs: {sorted(missing)}")
    if frame.schema["timestamp"] != pl.Datetime("us", "UTC"):
        raise ValueError("Expected timezone-aware UTC hourly observed timestamps")
    if frame.select(pl.struct(["timestamp", "asset"]).is_duplicated().any()).item():
        raise ValueError("Duplicate asset-hour in contract data")
    if frame.filter(pl.col("timestamp").dt.minute() != 0).height:
        raise ValueError("Non-hourly source observations")
    frame = frame.sort("asset", "timestamp")
    positive = (pl.col("close") > 0) & (pl.col("open") > 0) & (pl.col("volume") > 0)

    if kind == "sign_reversal":
        frame = frame.with_columns(
            pl.when(positive)
            .then(-(pl.col("close") - pl.col("open")).sign())
            .otherwise(None).cast(pl.Float64).alias("score")
        )
    elif kind == "liquid_weekly_momentum":
        frame = frame.with_columns(
            (pl.col("close") / pl.col("close").shift(168).over("asset") - 1).alias("mom_168"),
            contiguous(168).alias("contiguous_168"),
            (pl.col("volume") * pl.col("close")).alias("dollar_volume"),
        )
        frame = frame.with_columns(
            _rolling("dollar_volume", 24).alias("volume_usd_24"),
            contiguous(23).alias("contiguous_24"),
        )
        frame = frame.with_columns(
            pl.col("volume_usd_24").rank(method="min", descending=True)
            .over("timestamp").alias("liquidity_rank")
        )
        frame = frame.with_columns(
            pl.when(positive & pl.col("contiguous_168") & pl.col("contiguous_24")
                    & (pl.col("liquidity_rank") <= 50))
            .then(pl.col("mom_168")).otherwise(None).alias("score")
        )
    elif kind == "price_volume_confirm":
        frame = frame.with_columns(
            (pl.col("close") / pl.col("close").shift(24).over("asset") - 1).alias("mom_24"),
            (pl.col("volume") * pl.col("close")).alias("dollar_volume"),
            contiguous(47).alias("contiguous_48"),
        )
        frame = frame.with_columns(
            _rolling("dollar_volume", 24).alias("vol_usd_24"),
        )
        frame = frame.with_columns(
            pl.col("vol_usd_24").shift(24).over("asset").alias("prior_vol_usd_24")
        )
        frame = frame.with_columns(
            pl.when(positive & pl.col("contiguous_48")
                    & (pl.col("prior_vol_usd_24") > 0)
                    & (pl.col("vol_usd_24") > 0))
            .then(pl.col("mom_24") * (
                (pl.col("vol_usd_24") / pl.col("prior_vol_usd_24")).log()
                .clip(lower_bound=0.0)
            )).otherwise(None).alias("score")
        )
    else:  # BTC 4h return signals systematic high-beta altcoin catch-up
        frame = frame.with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("asset") - 1).alias("ret1"),
            (pl.col("close") / pl.col("close").shift(4).over("asset") - 1).alias("ret4"),
            contiguous(168).alias("contiguous_168"),
        )
        btc = frame.filter(pl.col("asset") == "BTCUSDT").select(
            "timestamp", pl.col("ret1").alias("btc_ret1"),
            pl.col("ret4").alias("btc_ret4"),
        )
        if btc.is_empty():
            raise ValueError("BTCUSDT market-leader series absent")
        frame = frame.join(btc, on="timestamp", how="left")
        frame = frame.with_columns(
            (pl.col("ret1") * pl.col("btc_ret1")).alias("cross_ret"),
            (pl.col("btc_ret1") ** 2).alias("btc_ret_sq"),
        )
        frame = frame.with_columns(
            pl.col("cross_ret").rolling_mean(168).over("asset").alias("mean_cross"),
            pl.col("ret1").rolling_mean(168).over("asset").alias("mean_alt_ret"),
            pl.col("btc_ret1").rolling_mean(168).over("asset").alias("mean_btc_ret"),
            pl.col("btc_ret_sq").rolling_mean(168).over("asset").alias("mean_btc_sq"),
        )
        variance = pl.col("mean_btc_sq") - pl.col("mean_btc_ret") ** 2
        covariance = pl.col("mean_cross") - pl.col("mean_alt_ret") * pl.col("mean_btc_ret")
        frame = frame.with_columns(
            pl.when(positive & pl.col("contiguous_168") & (variance > 1e-12)
                    & pl.col("btc_ret4").is_not_null())
            .then(covariance / variance * pl.col("btc_ret4"))
            .otherwise(None).alias("score")
        )

    return frame.select("timestamp", "asset", "score").sort("timestamp", "asset")


def matrix(frame: pl.DataFrame, col: str, assets: list[str]) -> np.ndarray:
    pivot = frame.select("timestamp", "asset", col).pivot(
        index="timestamp", on="asset", values=col
    ).sort("timestamp")
    if not set(assets).issubset(set(pivot.columns)):
        raise ValueError("Missing assets in observed research panel")
    return pivot.select(assets).to_numpy()


def run_factor(
    panel_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract,
    kind: Kind, *,
    rebalance_every: int = REBALANCE_HOURS, top_k: int = PICK,
) -> dict[str, np.ndarray]:
    if (
        contract.contract_id != CONTRACT_ID
        or contract.execution_mode != "NEXT_BAR"
        or contract.execution_delay_bars != 1
        or contract.label_horizon_bars != 4
        or rebalance_every != REBALANCE_HOURS
        or top_k != PICK
    ):
        raise ValueError("Wrong frozen Warden contract or unapproved parameter change")
    panel = panel_df.collect() if isinstance(panel_df, pl.LazyFrame) else panel_df
    if panel["timestamp"].max() >= contract.val_end:
        # This method intentionally never reads or needs any holdout data.
        raise ValueError("Dataset includes observations beyond exploratory cutoff")
    panel = panel.sort("timestamp", "asset")
    timestamps = sorted(panel["timestamp"].unique().to_list())
    assets = sorted(panel["asset"].unique().to_list())
    if "BTCUSDT" not in assets:
        raise ValueError("Known market leader missing in pinned universe")

    f = source_features(panel, kind)
    scores = matrix(f, "score", assets)
    opens = matrix(panel, "open", assets)
    volume = matrix(panel, "volume", assets)
    n, k = opens.shape
    if scores.shape != (n, k):
        raise ValueError("Feature/price matrix not aligned")
    alt_mask = np.array([a != "BTCUSDT" for a in assets], dtype=bool)
    time_idx = {ts: idx for idx, ts in enumerate(timestamps)}
    positions = np.zeros((n, k), dtype=float)
    valid_ics: list[float] = []
    current = np.zeros(k, dtype=float)
    min_bars = 20
    for idx, ts in enumerate(timestamps):
        if ts < contract.val_start or ts >= contract.val_end:
            continue
        if ts.hour % rebalance_every != 0:
            positions[idx] = current
            continue
        is_observed = (alt_mask & np.isfinite(scores[idx])
                       & np.isfinite(opens[idx]) & (opens[idx] > 0)
                       & np.isfinite(volume[idx]) & (volume[idx] > 0))
        candidate_ids = np.flatnonzero(is_observed)
        # No future prices or labels enter the portfolio decision. If the
        # trial cannot complete before validation cutoff, stay flat.
        end_ts = ts + timedelta(
            hours=contract.execution_delay_bars + contract.label_horizon_bars
        )
        if end_ts >= contract.val_end or len(candidate_ids) < MIN_CROSS_SECTION:
            current = np.zeros(k, dtype=float)
        else:
            current = np.zeros(k, dtype=float)
            if kind == "sign_reversal":
                negatives = candidate_ids[scores[idx, candidate_ids] > 0]
                positives = candidate_ids[scores[idx, candidate_ids] < 0]
                if len(negatives) >= top_k and len(positives) >= top_k:
                    current[negatives] = 0.5 / len(negatives)
                    current[positives] = -0.5 / len(positives)
            else:
                ranked = candidate_ids[np.argsort(scores[idx, candidate_ids], kind="stable")]
                long_ids, short_ids = ranked[-top_k:], ranked[:top_k]
                if len(long_ids) >= top_k and len(short_ids) >= top_k:
                    current[long_ids] = 0.5 / top_k
                    current[short_ids] = -0.5 / top_k
        positions[idx] = current

        entry_i = time_idx.get(ts + timedelta(hours=contract.execution_delay_bars))
        exit_i = time_idx.get(end_ts)
        if entry_i is None or exit_i is None or exit_i >= n:
            continue
        entry, exit_price = opens[entry_i], opens[exit_i]
        # Volume/availability is a LABEL mask only, NOT a decision filter.
        # A future no-trade bar is not an invented execution/fill.
        elig = is_observed & np.isfinite(entry) & (entry > 0)
        elig &= np.isfinite(exit_price) & (exit_price > 0)
        elig &= volume[entry_i] > 0
        elig &= volume[exit_i] > 0
        if int(elig.sum()) < min_bars:
            continue
        y = exit_price[elig] / entry[elig] - 1
        x = scores[idx, elig]
        if float(np.std(x)) < 1e-10 or float(np.std(y)) < 1e-10:
            continue
        ic = float(spearmanr(x, y).statistic)
        if np.isfinite(ic):
            valid_ics.append(ic)
    if not valid_ics:
        raise ValueError(f"No causally evaluable validation IC for {kind}")
    return {
        "positions": positions,
        "ic_series": np.asarray(valid_ics, dtype=float),
        "signal_delay_bars": 0,  # Warden, not this module, applies +1
    }
