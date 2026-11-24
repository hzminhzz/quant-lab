"""S183 ("IG VoV Quad Sharpened") ported from the repo's own reference implementation.

Registered as HYP-2026-127. `s183_composite.py` is the earlier reconstruction (HYP-2026-126)
and stays frozen: two screen attempts are bound to it.

Source of truth, not re-derived: every sleeve and both portfolio gates call the functions in
`strategies/candidates/trends_trading_book.py` — a vendored, byte-identical static subset of
`data/external/trends-research/Live/2_Engine/trading_book.py` — and the sizing loop mirrors
`Live/3_Portfolio/portfolio.py:simulate`. Constants are theirs.

Order of operations, exactly as `trading_book` assembles it:

  TS_trend   = mean of the three EWMAC price differences (16,64) (32,128) (64,256)
  XS_trend   = cross-sectional z of r256 (256-bar price change over the raw close)
  Trend_sign = 0.5*normalise(TS, cap=None) + 0.5*normalise(XS, cap=None), clipped +-20
  Skew_sign  = normalise(-rolling_skew(256) of daily returns)
  VoV_sign   = normalise(VoV), VoV = -((smooth/mean)-1) * sign(64-bar price change)
  Sign_raw   = 0.25*Trend + 0.25*Carry + 0.25*Skew + 0.25*VoV
  fdm        = clip(1/sqrt(w'Cw), 1, 2) over the sleeve means, w = 1/N
  SIGNAL     = normalise(Sign_raw * fdm).ewm_mean(alpha=0.5), clipped +-20

Sizing, as `portfolio.simulate`:

  weight = (SIGNAL/10) * tau * (1/n_active) * idm * gvol * gdd / rel_vol
  idm    = min(4, 1/sqrt(w'Cw)) from the EWM correlation of daily returns
  gvol   = s_g_vol (sigmoid vol gate, floor 0.5)
  gdd    = sigmoid drawdown gate on the 64-bar cumulative P&L, threshold -0.10, floor 0.5
  buffer = trade only when |target - held| > 0.10*|held|

Declared deviations:
  * Carry is absent (no futures term structure). Its 1/4 slot stays in `Sign_raw` at zero —
    the faithful analogue of a missing sleeve, and conservative: renormalising to 1/3 would
    inflate the tested book by 33%. The FDM quadratic form uses the three sleeves that exist.
  * `1/rel_vol` with `rel_vol = vol_abs/close/sqrt(256)` replaces `pointsize*FX*price/sigma`;
    identical algebra when FX = 1, which holds because each instrument is single-currency.
  * No Panama adjustment (our panel is already one continuous series), no roll cost, and
    integer contract truncation becomes fractional weights.

Migration (issue #89, static strategy imports): this module used to load the upstream book at
import time with `importlib.util.spec_from_file_location`. It now imports the vendored subset
statically and declares it in `CODE_DEPENDENCIES`, so `code_closure` covers every line that runs.
Nothing about the signal, the sizing or the declared deviations changed. Historical identity:
original file sha256 `ef0dd455567df35908ba918d959e44daeb8db097b49dd10930081db535a63f0d`,
original closure digest `e8997c9e96ce0c9bdca32cf81a430a55afefffbc1b4f1e51ef9bc144eac81582`
(the value recorded on RUN-RU-001/002). Editing the source necessarily changes that digest, so
those two trials stay historical and are no longer qualification-bound; no trial, budget, ledger
row or qualification state was reset. See `docs/migrations/issue-89-static-strategy-imports.md`.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl
import trends_trading_book as tb
from scipy.signal import lfilter

# The vendored upstream book is imported statically (the loader puts this directory on sys.path);
# declare it so the strategy's code closure hashes it too.
CODE_DEPENDENCIES = ("strategies/candidates/trends_trading_book.py",)
TRADING_DAYS_YEAR = 256  # theirs
TAU = 0.20  # theirs: nominal vol target, before the gates
IDM_CAP, IDM_SPAN, IDM_MIN, IDM_VAR_FLOOR = 4.0, 512, 256, 0.01
FDM_CAP, FDM_VAR_FLOOR = 2.0, 0.01
GDD_LOOKBACK, GDD_THRESHOLD, GDD_STEEPNESS, GDD_FLOOR = 64, -0.10, 10.0, 0.50
BUFFER = 0.10


def _tb() -> Any:
    """The vendored upstream book. Kept as a function so the investigation scripts still import it."""
    return tb


def _per_instrument(close: pl.Series, book: Any) -> dict[str, pl.Series]:
    ret = (close - close.shift(1)) / close.shift(1)
    smooth = book.vov_smooth(book.vov_inner(ret))
    vol_abs = book.daily_vol_abs(close)
    return {
        "TS_trend": book.ewmac(close)["TS_trend"],
        "r256": book.xs_return(close, close),
        "Skew": book.skew(ret),
        "VoV": book.vov_signal(smooth, book.vov_mean_ann(smooth), close),
        "gvol": book.s_g_vol(close, vol_abs),
        "vol_abs": vol_abs,
        "ret": ret,
        "close": close,
    }


def _aligned(frame: pl.DataFrame, grid: pl.Series, col: str) -> np.ndarray:
    return (
        pl.DataFrame({"timestamp": grid})
        .join(frame.select("timestamp", col), on="timestamp", how="left")[col]
        .to_numpy()
        .astype(float)
    )


def _pooled_fdm(means: np.ndarray, book: Any) -> np.ndarray:
    """Their formula, generalised to the N sleeves that actually exist (carry is absent)."""
    n = means.shape[1]
    corr = book.ewm_corr_4(means, book.FDM_CORR_SPAN, book.FDM_MIN_PERIODS)
    w = np.full(n, 1.0 / n)
    var_blend = np.einsum("i,tij,j->t", w, np.clip(corr, -1.0, 1.0), w)
    ok = ~np.isnan(corr).any(axis=(1, 2)) & (var_blend > FDM_VAR_FLOOR)
    fdm = np.ones(means.shape[0])
    fdm[ok] = np.clip(1.0 / np.sqrt(var_blend[ok]), 1.0, FDM_CAP)
    return fdm


def build_signal_frame(panel_df: pl.DataFrame, contract: Any) -> pl.DataFrame:
    """Panel -> per-row sleeves, composite and SIGNAL. Every window is per instrument."""
    _ = contract
    book = _tb()
    df = panel_df if "symbol" in panel_df.columns else panel_df.rename({"asset": "symbol"})
    df = df.sort("symbol", "timestamp")

    frames: dict[str, pl.DataFrame] = {}
    for (sym,), g in df.group_by(["symbol"], maintain_order=True):
        g = g.sort("timestamp")
        frames[sym] = pl.DataFrame(
            {"timestamp": g["timestamp"], "symbol": sym} | _per_instrument(g["close"], book)
        )

    grid = pl.Series(
        "timestamp", sorted(set().union(*(set(f["timestamp"]) for f in frames.values())))
    )
    z, _, _, _ = book.cross_sectional_z(
        {sym: pl.Series(_aligned(f, grid, "r256")) for sym, f in frames.items()}, grid
    )

    signed: dict[str, pl.DataFrame] = {}
    for sym, f in frames.items():
        f = f.join(
            pl.DataFrame({"timestamp": grid, "XS_trend": z[sym]}), on="timestamp", how="left"
        )
        ts_sign = book.normalise_signal(f["TS_trend"], cap=None)
        xs_sign = book.normalise_signal(f["XS_trend"], cap=None)
        signed[sym] = f.with_columns(
            Trend_sign=book.trend_sign(ts_sign, xs_sign),
            Skew_sign=book.normalise_signal(f["Skew"]),
            VoV_sign=book.normalise_signal(f["VoV"]),
        )

    sleeve_cols = ("Trend_sign", "Skew_sign", "VoV_sign")
    means = np.column_stack(
        [
            np.nanmean(
                np.column_stack([_aligned(f, grid, c) for f in signed.values()]), axis=1
            )
            for c in sleeve_cols
        ]
    )
    fdm_f = pl.DataFrame({"timestamp": grid, "fdm": _pooled_fdm(means, book)})

    out = []
    for f in signed.values():
        f = f.join(fdm_f, on="timestamp", how="left").with_columns(
            # carry slot held at zero: see the module docstring
            Sign_raw=0.25 * pl.col("Trend_sign")
            + 0.25 * pl.col("Skew_sign")
            + 0.25 * pl.col("VoV_sign")
        )
        f = f.with_columns(fdm_norm=pl.col("Sign_raw") * pl.col("fdm"))
        f = f.with_columns(FDM_MASTER=book.normalise_signal(f["fdm_norm"]))
        out.append(
            f.with_columns(
                SIGNAL=pl.col("FDM_MASTER")
                .ewm_mean(alpha=0.5, adjust=False, min_samples=1, ignore_nulls=False)
                .clip(-20.0, 20.0)
            )
        )
    return pl.concat(out).rename({"symbol": "asset"}).sort("timestamp", "asset")


def _idm(returns: np.ndarray, active: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """1/sqrt(w'Cw) over the active book, EWM covariance as in portfolio.ewm_corr_sum."""
    T, K = returns.shape
    decay = 1.0 - 2.0 / (IDM_SPAN + 1.0)
    mask = np.isfinite(returns)
    vals = np.where(mask, returns, 0.0)
    mf = mask.astype(float)

    def acc(a: np.ndarray) -> np.ndarray:
        return lfilter([1.0], [1.0, -decay], a, axis=0)

    sw, sw2 = acc(mf), acc(mf * mf)
    sx, sxx = acc(vals * mf), acc(vals * vals * mf)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = sx / sw
        var = sxx / sw - mean * mean
        den = sw * sw - sw2
        var = np.where(den > 0, var * (sw * sw) / den, np.nan)
    var = np.where(np.cumsum(mf, axis=0) >= IDM_MIN, var, np.nan)
    sd = np.sqrt(np.clip(var, 0.0, None))
    act = active & np.isfinite(sd) & (sd > 0)
    n_act = act.sum(axis=1).astype(float)

    iu, ju = np.triu_indices(K, k=1)
    both = mf[:, iu] * mf[:, ju]
    b_sw, b_sw2 = acc(both), acc(both * both)
    b_sx, b_sy = acc(vals[:, iu] * both), acc(vals[:, ju] * both)
    b_sxy = acc(vals[:, iu] * vals[:, ju] * both)
    with np.errstate(invalid="ignore", divide="ignore"):
        mx, my = b_sx / b_sw, b_sy / b_sw
        cov = b_sxy / b_sw - mx * my
        d = b_sw * b_sw - b_sw2
        corr = np.where(d > 0, cov * (b_sw * b_sw) / d, np.nan) / (sd[:, iu] * sd[:, ju])
    corr = np.clip(corr, -1.0, 1.0)
    corr = np.where(np.cumsum(both, axis=0) >= IDM_MIN, corr, np.nan)
    live = act[:, iu] & act[:, ju]
    pair_sum = np.where(live, np.where(np.isfinite(corr), corr, 1.0), 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        wcw = (n_act + 2.0 * pair_sum) / (n_act * n_act)
    wcw = np.where(n_act > 0, wcw, np.nan)
    idm = np.ones(T)
    good = np.isfinite(wcw) & (wcw > IDM_VAR_FLOOR)
    idm[good] = np.minimum(IDM_CAP, 1.0 / np.sqrt(wcw[good]))
    return idm, n_act


def generate_positions(
    panel_df: pl.DataFrame,
    contract: Any,
    vol_target: float = TAU,
    buffer_fraction: float = BUFFER,
    warmup: int = 1,
    **kwargs: Any,
) -> dict[str, Any]:
    """SIGNAL -> weight -> vol gate -> buffer. The drawdown gate is deliberately absent.

    `gdd` in the reference is driven by the account's own cumulative P&L, which only an
    execution engine can supply. Warden's runner simulates positions itself and returns no
    equity path, so applying the gate here would require a hand-rolled P&L loop — the thing
    AGENTS.md forbids. It is implemented where the equity exists, in the engine path
    (`reproduce_s183.py`, a `ml4t.backtest.Strategy` reading `broker.get_account_value()`),
    and this screen therefore measures the book with the vol gate and IDM only.
    """
    _ = kwargs
    feat = build_signal_frame(panel_df, contract)
    assets = sorted(feat["asset"].unique().to_list())
    wide = {c: feat.pivot(on="asset", index="timestamp", values=c).sort("timestamp") for c in
            ("SIGNAL", "gvol", "vol_abs", "ret", "close")}
    mat = {
        c: np.column_stack([wide[c][a].to_numpy() for a in assets]).astype(float) for c in wide
    }

    active = np.isfinite(mat["SIGNAL"]) & np.isfinite(mat["close"]) & (mat["close"] > 0)
    idm, n_act = _idm(mat["ret"], active)
    with np.errstate(invalid="ignore", divide="ignore"):
        # annualised relative vol: vol_abs is a std of price differences, so it must be
        # scaled UP by sqrt(256) before dividing by price (the reference divides by sigma,
        # an annualised USD vol, and multiplies by price — same quantity).
        rel_vol = mat["vol_abs"] * math.sqrt(TRADING_DAYS_YEAR) / mat["close"]
        base = np.where(
            active & (rel_vol > 0),
            (np.nan_to_num(mat["SIGNAL"]) / 10.0)
            * vol_target
            * np.where(n_act > 0, 1.0 / n_act, 0.0)[:, None]
            * idm[:, None]
            * np.where(np.isfinite(mat["gvol"]), mat["gvol"], 1.0)
            / rel_vol,
            0.0,
        )

    T, K = base.shape
    held = np.zeros((T, K))
    for t in range(T):
        prev = held[t - 1] if t else np.zeros(K)
        want = np.where(np.abs(base[t] - prev) > buffer_fraction * np.abs(prev), base[t], prev)
        held[t] = np.where(active[t], want, prev)
    held[:warmup] = 0.0
    return {"positions": held, "assets": assets}


class S183Pipeline:
    """Feature pipeline for warden's prefix-invariance check (time leakage)."""

    def compute(self, bars_df: pl.DataFrame, contract: Any) -> pl.DataFrame:
        return build_signal_frame(bars_df, contract)


class S183PerAssetPipeline:
    """Per-instrument sleeves only, for the cross-asset leakage check.

    XS_trend and the pooled FDM are same-date cross-sectional quantities by construction, so
    shocking one asset is supposed to move the others; prefix invariance covers their timing.
    """

    def compute(self, bars_df: pl.DataFrame, contract: Any) -> pl.DataFrame:
        cols = ["timestamp", "asset", "TS_trend", "Skew", "VoV", "gvol", "vol_abs"]
        return build_signal_frame(bars_df, contract).select(cols)


pipeline = S183Pipeline()
