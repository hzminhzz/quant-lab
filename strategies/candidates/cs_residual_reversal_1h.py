"""Residual reversal: short the idiosyncratic winners, long the idiosyncratic losers.

Thesis under test: the top-performing coins *reverse*. Blitz-Huij-Martens residual momentum claims the
opposite (idiosyncratic trend persists); the diagnostics in
`.workspace/plans/residual_momentum_hl/RESULT_binance.md` measured the sign and found it negative at
every formation from 5 days to 250 days on daily bars, and at 24h..336h on hourly bars with HAC |t| up
to 18.8. This module trades that measured sign.

Score, all from bars <= t:

    r_i      = close/close[-1] - 1
    beta_i   = OLS(r_i ~ r_BTC) over the trailing 1440 bars (60 days), pairwise-complete
    resid_i  = r_i - beta_i * r_BTC                    (beta only; the intercept is NOT removed)
    score_i  = sum(resid_i, 168) / std(resid_i, 720)   (7-day formation, 30-day vol)

Disclosed deviations from the original spec, each measured before registration:

  * sign      -- the spec longs the top fifth; the measurement says the top fifth underperforms.
  * intercept -- "strip the beta" is taken literally: the fitted intercept is not subtracted. Summing
                 a residual over L bars enters the intercept L times, and removing it weakens the
                 measured effect at every formation.
  * formation -- 15 days becomes 168 bars (7 days), the strongest stable hourly cell.
  * venue     -- Binance UM top-100 instead of Hyperliquid, which has no history in the lake.

A symbol whose data ends is closed at its own last tradable bar. Causality: features use bars <= t and
positions are NOT pre-lagged; warden applies execution_delay_bars at simulation.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
from scipy import stats

from warden.contracts import EvaluationContract
from warden.leakage import compute_open_forward_label

FORMATION_BARS = 168        # 7 days
REG_WINDOW = 1440           # 60 days for the beta regression
VOL_WINDOW = 720            # 30 days for the score denominator
UV_WINDOW = 168             # 7 days of dollar volume for the universe
MIN_COVERAGE = 0.9


class ResidualReversalPipeline:
    """Causal features: quote volume per bar, which the liquidity universe ranks on."""

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        return df.sort("timestamp", "asset").with_columns(
            quote_vol=pl.col("volume") * pl.col("close"),
        )


def _matrix(df: pl.DataFrame, value: str, assets: list[str], n_bars: int, ts_index: dict) -> np.ndarray:
    """Pivot one column onto the full regular timestamp grid; absent cells stay NaN."""
    p = df.pivot(index="timestamp", on="asset", values=value).sort("timestamp")
    for a in assets:
        if a not in p.columns:
            p = p.with_columns(pl.lit(None, dtype=pl.Float64).alias(a))
    out = np.full((n_bars, len(assets)), np.nan, dtype=float)
    rows = np.array([ts_index[t] for t in p["timestamp"].to_list()], dtype=int)
    out[rows, :] = p.select(assets).to_numpy().astype(float)
    return out


def _roll(mat: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Trailing sum and present-count over `window` rows, inclusive of the current row."""
    filled = np.where(np.isfinite(mat), mat, 0.0)
    present = np.isfinite(mat).astype(float)
    pad = np.zeros((window - 1, mat.shape[1]))
    one = np.zeros((1, mat.shape[1]))
    out = []
    for series in (filled, present):
        cs = np.vstack([pad, series]).cumsum(axis=0)
        out.append(cs[window - 1:] - np.vstack([one, cs[:-window]]))
    return out[0], out[1]


def _reversal_score(ret: np.ndarray, factor: np.ndarray, formation: int) -> tuple[np.ndarray, np.ndarray]:
    """sum(resid, formation) / std(resid, vol_window), plus the residual vol itself.

    The vol is returned because the score already divides by it: the *ranking* is vol-normalised
    while an equal-weight book is not, so the denominator is also the natural inverse-vol weight.
    """
    F = np.broadcast_to(np.asarray(factor, float)[:, None], ret.shape)
    ok = np.isfinite(ret) & np.isfinite(F)
    r = np.where(ok, ret, np.nan)
    n = _roll(np.where(ok, 1.0, np.nan), REG_WINDOW)[0]
    sff = _roll(np.where(ok, F, np.nan), REG_WINDOW)[0]
    sgg = _roll(np.where(ok, F * F, np.nan), REG_WINDOW)[0]
    syy = _roll(r, REG_WINDOW)[0]
    sxy = _roll(np.where(ok, F * r, np.nan), REG_WINDOW)[0]
    with np.errstate(divide="ignore", invalid="ignore"):
        cov = sxy - sff * syy / n
        var = sgg - sff * sff / n
        beta = cov / var
    beta[(n < MIN_COVERAGE * REG_WINDOW) | ~np.isfinite(beta) | (var <= 0)] = np.nan

    resid = ret - beta * np.asarray(factor, float)[:, None]
    num, n_num = _roll(resid, formation)
    sq, n_sq = _roll(resid * resid, VOL_WINDOW)
    with np.errstate(divide="ignore", invalid="ignore"):
        vol = np.sqrt(sq / n_sq)
        score = num / vol
    bad = (n_num < MIN_COVERAGE * formation) | (n_sq < MIN_COVERAGE * VOL_WINDOW) | ~(vol > 1e-9)
    score[bad] = np.nan
    vol[bad] = np.nan
    return score, vol


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    formation_bars: int = FORMATION_BARS,
    rebalance_every: int = 24,
    universe_size: int = 100,
    quintile_fraction: float = 0.2,
    weighting: str = "equal",
    **kwargs: Any,
) -> dict[str, Any]:
    """Long the low-score fifth (recent idiosyncratic losers), short the high-score fifth.

    `weighting`: "equal" (0.5/n per name) or "inv_vol" (1/residual_vol within each leg, clipped to
    0.5x..2x the equal weight and renormalised). The score already divides by residual vol, so
    `inv_vol` is what makes the book consistent with its own ranking; `equal` over-weights the
    highest-vol names, which is where the pump tail lives.
    """
    df = ResidualReversalPipeline().compute(panel_df, contract)
    timestamps = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    n_bars, n_assets = len(timestamps), len(assets)
    if n_bars <= REG_WINDOW + formation_bars + 2:
        raise ValueError(
            f"panel too short for a {REG_WINDOW}+{formation_bars}-bar warm-up ({n_bars} bars)"
        )
    ts_index = {t: i for i, t in enumerate(timestamps)}

    close = _matrix(df, "close", assets, n_bars, ts_index)
    open_ = _matrix(df, "open", assets, n_bars, ts_index)
    qvol = _matrix(df, "quote_vol", assets, n_bars, ts_index)

    prev_close = np.full_like(close, np.nan)
    prev_close[1:] = close[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.where(np.isfinite(prev_close) & (prev_close > 0), close / prev_close - 1.0, np.nan)
    ret = np.clip(ret, -0.5, 2.0)

    btc = assets.index("BTCUSDT") if "BTCUSDT" in assets else 0
    if not np.isfinite(ret[:, btc]).any():
        raise ValueError("panel has no BTC factor column; the residual construction needs one")
    score, rvol = _reversal_score(ret, ret[:, btc], formation_bars)

    dvol, dcount = _roll(qvol, UV_WINDOW)
    dvol = np.where(dcount >= 0.8 * UV_WINDOW, dvol, np.nan)

    val_start_idx = ts_index.get(contract.val_start, int(n_bars * 0.25))
    val_end_idx = ts_index.get(contract.val_end, n_bars)
    first = max(val_start_idx, REG_WINDOW + formation_bars)
    n_leg = max(1, int(round(universe_size * quintile_fraction)))
    w_each = 0.5 / n_leg                     # gross 1.0, dollar-neutral

    finite_close = np.isfinite(close)
    has_data = finite_close.any(axis=0)
    last_obs = np.where(has_data, n_bars - 1 - np.argmax(finite_close[::-1, :], axis=0), -1)

    positions = np.zeros((n_bars, n_assets), dtype=float)
    universes: dict[int, np.ndarray] = {}
    for t in range(first, val_end_idx):
        if t % rebalance_every != 0 and t != val_start_idx:
            positions[t] = positions[t - 1]
            continue
        eligible = np.where(np.isfinite(score[t]) & np.isfinite(dvol[t]) & (dvol[t] > 0))[0]
        if eligible.size < 20:
            positions[t] = positions[t - 1]
            continue
        keep = min(universe_size, eligible.size)
        universe = eligible[np.argsort(-dvol[t][eligible])[:keep]]
        order = universe[np.argsort(score[t][universe])]     # ascending score
        legs = max(1, min(n_leg, universe.size // 2))
        losers, winners = order[:legs], order[-legs:]
        weights = np.zeros(n_assets, dtype=float)
        if weighting == "inv_vol":
            eq = 0.5 / legs
            for leg, sign in ((losers, 1.0), (winners, -1.0)):
                v = rvol[t][leg]
                inv = np.where(np.isfinite(v) & (v > 0), 1.0 / np.where(v > 0, v, 1.0), np.nan)
                if not np.isfinite(inv).any():
                    inv = np.ones(leg.size)
                inv = np.where(np.isfinite(inv), inv, np.nanmedian(inv))
                inv = np.clip(inv / inv.sum() * 0.5, 0.5 * eq, 2.0 * eq)
                weights[leg] = sign * inv / inv.sum() * 0.5
        else:
            weights[losers] = w_each                      # losers: long
            weights[winners] = -w_each                    # winners: short
        positions[t] = weights
        universes[t] = universe

    for j, last in enumerate(last_obs):
        if last >= 0:
            positions[max(0, int(last)):, j] = 0.0

    # Rank IC of the TRADED direction (-score) against the traded horizon. Rank, not Pearson: the
    # cross-section has fat tails and the skill mandates Spearman for cross-sectional signals. The
    # Pearson value is recorded alongside so the choice is auditable, not selective.
    forward = compute_open_forward_label(open_, contract.execution_delay_bars, rebalance_every)
    ic_list: list[float] = []
    pearsons: list[float] = []
    for t in range(val_start_idx, min(val_end_idx, n_bars - rebalance_every - contract.execution_delay_bars)):
        universe = universes.get(t)
        if universe is None:
            continue
        x = -score[t][universe]
        y = forward[t][universe]
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 5 or np.std(x[ok]) < 1e-12:
            continue
        ic_list.append(float(stats.spearmanr(x[ok], y[ok])[0]))
        pearsons.append(float(np.corrcoef(x[ok], y[ok])[0, 1]))
    ic_arr = np.array(ic_list, dtype=float)
    return {
        "positions": positions,
        "ic_series": ic_arr,
        "mean_ic": float(np.mean(ic_arr)) if ic_arr.size else None,
        "mean_pearson": float(np.mean(pearsons)) if pearsons else None,
        "rebalances": len(universes),
    }


pipeline = ResidualReversalPipeline()
