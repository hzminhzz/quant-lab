"""MIT-licensed static subset of the trends-research trading book (vendored, not tuned).

Upstream: https://github.com/LucasJoly/trends-research (local mirror
`data/external/trends-research`), `Live/2_Engine/trading_book.py`,
commit `0426baca40b148b9fed760740a7b7ac2e4cb3a70`, file sha256
`f7bfac2916b53eb436fb360fe2a183629cd15d2fe14647df4aeba6f5aff7f0a5`.
Copyright (c) 2026 Lucas Joly, MIT licence - see `trends_trading_book.LICENSE.txt` beside this file.

Why this file exists
--------------------
`strategies/candidates/s183_reference.py` (HYP-2026-127) used to import upstream by path with
`importlib.util.spec_from_file_location`. Warden's AST boundary (#89) bans dynamic imports in
strategies, and the path-loaded file lived in untracked `data/external/`, outside the strategy's
code closure, so the digest recorded on RUN-RU-001/002 did not cover the code that actually ran.
Only the pure, I/O-free functions that strategy calls are copied here, byte-for-byte from the
upstream commit: upstream's data loading, FX/IRX helpers, argparse CLI, caching and dynamic module
loading are deliberately absent and no upstream code is executed by this repository.

Do not edit these bodies for tuning. They are the frozen upstream algebra; behaviour is pinned by
`tests/test_ticket03_static_migration_parity.py` against values computed from the upstream file.
"""

from __future__ import annotations

import numpy as np
import polars as pl

# Upstream constants, verbatim; only those the vendored functions read are copied.
EWMAC_PAIRS = [(16, 64), (32, 128), (64, 256)]
VOL_EWM_SPAN = 32
VOL_EWM_MIN = 32
VOL_ROLL_WINDOW = 2560
VOL_ROLL_MIN = 256
VOL_W_SHORT = 0.70
VOL_W_LONG = 0.30
GVOL_SPAN = 64
GVOL_STEEPNESS = 10.0
GVOL_TRIGGER = 1.0
GVOL_FLOOR = 0.50
VOV_INNER_WINDOW = 21
VOV_OUTER_WINDOW = 64
VOV_AVG_WINDOW = 256
VOV_DIR_LOOKBACK = 64
SIGNAL_PHI = 10.0
SIGNAL_W = 256
SIGNAL_CAP = 20.0
FDM_CORR_SPAN = 512
FDM_MIN_PERIODS = 256
FDM_FLOOR = 1.0
FDM_CAP = 2.0
FDM_VAR_FLOOR = 0.01
XS_LOOKBACK = 256
XS_MIN_INSTS = 3
TRADING_DAYS_YEAR = 256
SKEW_WINDOW = 256
SKEW_MIN = SKEW_WINDOW


def ewmac(close: pl.Series) -> dict:
    out = {}
    for f, s in EWMAC_PAIRS:
        fast = close.ewm_mean(alpha=2.0 / (f + 1), adjust=False)
        slow = close.ewm_mean(alpha=2.0 / (s + 1), adjust=False)
        out[f"{f}-{s}"] = fast - slow

    total = None
    for name in (f"{f}-{s}" for f, s in EWMAC_PAIRS):
        total = out[name] if total is None else total + out[name]
    out["TS_trend"] = total / len(EWMAC_PAIRS)
    return out


def _blended_std(x: pl.Series) -> pl.Series:
    short = x.ewm_std(span=VOL_EWM_SPAN, adjust=True, bias=False,
                      min_samples=VOL_EWM_MIN, ignore_nulls=False)
    long = x.rolling_std(VOL_ROLL_WINDOW, min_samples=VOL_ROLL_MIN, ddof=1)
    return VOL_W_SHORT * short + VOL_W_LONG * long


def daily_vol_abs(cont_close: pl.Series) -> pl.Series:
    return _blended_std(cont_close.diff())


def s_g_vol(cont_close: pl.Series, vol_abs: pl.Series) -> pl.Series:
    fast = cont_close.diff().ewm_std(span=GVOL_SPAN, adjust=True, bias=False,
                                     min_samples=GVOL_SPAN, ignore_nulls=False)
    ratio = fast / vol_abs
    z = GVOL_STEEPNESS * (ratio - GVOL_TRIGGER)
    gate = 1.0 - (1.0 - GVOL_FLOOR) * (1.0 / (1.0 + (-z).exp()))
    return gate.fill_null(1.0)


def skew(daily_ret: pl.Series, window: int = SKEW_WINDOW,
         min_samples: int = SKEW_MIN) -> pl.Series:
    return -daily_ret.rolling_skew(window, bias=False, min_samples=min_samples)


def vov_inner(daily_ret: pl.Series,
              window: int = VOV_INNER_WINDOW) -> pl.Series:
    return daily_ret.rolling_std(window, min_samples=window, ddof=1)


def vov_smooth(inner: pl.Series,
               window: int = VOV_OUTER_WINDOW) -> pl.Series:
    return inner.rolling_std(window, min_samples=window, ddof=1)


def vov_mean_ann(smooth: pl.Series,
            window: int = VOV_AVG_WINDOW) -> pl.Series:
    return smooth.rolling_mean(window, min_samples=window)


def vov_signal(smooth: pl.Series, mean_ann: pl.Series,
               cont_close: pl.Series,
               lookback: int = VOV_DIR_LOOKBACK) -> pl.Series:
    ratio = -((smooth / mean_ann) - 1.0)
    prev = cont_close.shift(lookback)
    diff = (cont_close - prev).to_numpy().astype(np.float64)
    scale = np.maximum(np.abs(cont_close.to_numpy().astype(np.float64)),
                       np.abs(prev.to_numpy().astype(np.float64)))
    diff = np.where(np.abs(diff) <= 1e-9 * np.maximum(scale, 1.0), 0.0, diff)
    direction = np.where(np.isnan(diff), np.nan, np.where(diff >= 0, 1.0, -1.0))
    return pl.Series(direction).fill_nan(None) * ratio


def normalise_signal(f: pl.Series, phi: float = SIGNAL_PHI,
                     window: int = SIGNAL_W,
                     cap: float | None = SIGNAL_CAP) -> pl.Series:
    x = f.to_numpy().astype(np.float64)
    fin = np.isfinite(x)
    if not fin.any():
        return pl.Series(np.full(x.size, np.nan)).fill_nan(None)
    filled = x.copy()
    first = int(np.argmax(fin))
    filled[first:] = np.where(fin[first:], filled[first:], 0.0)
    present = np.isfinite(filled)
    num = pl.Series(np.where(present, np.abs(filled), 0.0)).rolling_sum(
        window, min_samples=1).to_numpy()
    den = pl.Series(present.astype(np.float64)).rolling_sum(
        window, min_samples=1).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_abs = np.where(den >= window, num / np.maximum(den, 1.0), np.nan)
        out = filled * (phi / mean_abs)
        if cap is not None:
            out = np.clip(out, -cap, cap)
    return pl.Series(np.where(np.isfinite(out), out, np.nan)).fill_nan(None)


def trend_sign(ts_sign: pl.Series, xs_sign: pl.Series,
               w_ts: float = 0.5, w_xs: float = 0.5) -> pl.Series:
    return (w_ts * ts_sign + w_xs * xs_sign).clip(-SIGNAL_CAP, SIGNAL_CAP)


def _recursive_sum(v: np.ndarray, decay: float) -> np.ndarray:
    from scipy.signal import lfilter
    return lfilter([1.0], [1.0, -decay], np.asarray(v, dtype=np.float64), axis=0)


def ewm_corr_4(X: np.ndarray, span: float, min_periods: int) -> np.ndarray:
    X = np.asarray(X, dtype=np.float64)
    T, K = X.shape
    alpha = 2.0 / (span + 1.0)
    decay = 1.0 - alpha
    mask = np.isfinite(X)
    vals = np.where(mask, X, 0.0)
    mf = mask.astype(np.float64)

    cov = np.full((T, K, K), np.nan)
    for i in range(K):
        for j in range(i, K):
            both = mf[:, i] * mf[:, j]
            xi, xj = vals[:, i] * both, vals[:, j] * both
            sw = _recursive_sum(both, decay)
            sw2 = _recursive_sum(both, decay * decay)
            sx = _recursive_sum(xi, decay)
            sy = _recursive_sum(xj, decay)
            sxy = _recursive_sum(xi * xj / np.where(both > 0, 1.0, 1.0), decay)
            with np.errstate(invalid="ignore", divide="ignore"):
                mx, my = sx / sw, sy / sw
                c = sxy / sw - mx * my
                denom = sw * sw - sw2
                c = np.where(denom > 0, c * (sw * sw) / denom, np.nan)
            n_obs = np.cumsum(both)
            c = np.where(n_obs >= max(min_periods, 1), c, np.nan)
            cov[:, i, j] = c
            cov[:, j, i] = c

    d = np.sqrt(np.clip(np.einsum("tii->ti", cov), 0.0, None))
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = cov / (d[:, :, None] * d[:, None, :])
    corr = np.clip(corr, -1.0, 1.0)
    idx = np.arange(K)
    corr[:, idx, idx] = np.where(np.isfinite(corr[:, idx, idx]), 1.0, np.nan)
    return corr


def xs_return(cont_close: pl.Series, raw_close: pl.Series,
              lookback: int = XS_LOOKBACK) -> pl.Series:
    return (cont_close - cont_close.shift(lookback)) / raw_close.shift(lookback)


def cross_sectional_z(panel: dict[str, pl.Series], dates: pl.Series,
                      min_insts: int = XS_MIN_INSTS) -> dict:
    insts = sorted(panel)
    M = np.column_stack([panel[i].to_numpy().astype(float) for i in insts])
    finite = np.isfinite(M)
    n = finite.sum(axis=1)

    mean = np.full(M.shape[0], np.nan)
    std = np.full(M.shape[0], np.nan)
    usable = n >= max(min_insts, 2)
    if usable.any():
        Mm = np.where(finite, M, np.nan)
        with np.errstate(invalid="ignore"):
            mean[usable] = np.nanmean(Mm[usable], axis=1)
            std[usable] = np.nanstd(Mm[usable], axis=1, ddof=1)
    good = usable & np.isfinite(std) & (std != 0.0)
    z = np.full(M.shape, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        z[good] = (M[good] - mean[good, None]) / std[good, None]

    return ({i: pl.Series(z[:, k]).fill_nan(None) for k, i in enumerate(insts)},
            dates, int(good.sum()), int(M.shape[0]))
