#!/usr/bin/env python3
"""Residual reversal at 1h on the broad crypto panel (unregistered diagnostics).

LESSONS.md records a 16-cell "BTC-Residual Momentum" grid on CRYPTO_PERPS_TOP100_1H_V1 over
2024-07..2025-07 with Rank IC uniformly negative (mean -0.0316..-0.0549, HAC t -10.09..-14.57).
This re-runs the same construction on 2021-01..2024-06 — entirely outside that window — and
separately replicates 2024-07..2025-06 to check it was not a single-regime artefact.

Hard boundary: nothing after 2025-06-30 is read. `holdout_start` for the crypto contracts is
2025-07-01, and the exploratory 1h parquet physically contains rows past it.

Read-only. Registers nothing, spends no trial.

    uv run python scripts/xs_residual_reversal_1h_diag.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl
from scipy import stats

from warden.gates import compute_hac_tstat

REPO = Path(__file__).resolve().parents[1]
H1 = REPO / "data" / "exploratory" / "crypto_perps_broad_1h.parquet"
OUT = REPO / ".workspace" / "plans" / "residual_momentum_hl" / "diag_1h.json"

START, END = "2021-01-01", "2025-06-30"      # END is the val_end of the crypto contracts
UNIVERSE_N = 100
UV_WINDOW = 168                              # trailing 7d dollar volume, as xs_panel_diagnostics
VOL_WINDOW = 720                             # 30 days of hourly residuals
REG_WINDOW = 1440                            # 60 days of hourly observations
MIN_COV = 0.9
FORMATIONS = (24, 72, 168, 336)              # the LESSONS grid
HORIZONS = (4, 12, 24, 72)
COST_BPS_SIDE = 6.0                          # contract: 4bps taker + 2bps slippage


def roll_sum(x: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray]:
    v = np.where(np.isfinite(x), x, 0.0)
    c = np.isfinite(x).astype(float)
    z = np.zeros((w - 1, x.shape[1]))
    sv, sc = np.vstack([z, v]).cumsum(axis=0), np.vstack([z, c]).cumsum(axis=0)
    one = np.zeros((1, x.shape[1]))
    return (sv[w - 1:] - np.vstack([one, sv[:-w]]),
            sc[w - 1:] - np.vstack([one, sc[:-w]]))


def ols_beta(r: np.ndarray, f: np.ndarray, w: int, strip_alpha: bool) -> np.ndarray:
    """Trailing OLS on `f`; returns the residual for the current row, beta-lagged zero."""
    F = np.broadcast_to(np.asarray(f, float)[:, None], r.shape)
    ok = np.isfinite(r) & np.isfinite(F)
    rr, ff = np.where(ok, r, np.nan), np.where(ok, F, np.nan)
    n = roll_sum(ff, w)[1]
    sff, sgg = roll_sum(ff, w)[0], roll_sum(ff * ff, w)[0]
    syy, sgy = roll_sum(rr, w)[0], roll_sum(ff * rr, w)[0]
    with np.errstate(invalid="ignore", divide="ignore"):
        var = sgg - sff ** 2 / n
        beta = (sgy - sff * syy / n) / var
        alpha = syy / n - beta * sff / n
    bad = (n < MIN_COV * w) | ~np.isfinite(beta) | (var <= 0)
    beta[bad] = np.nan
    alpha[bad] = np.nan
    resid = r - beta * np.asarray(f, float)[:, None]
    return resid - alpha if strip_alpha else resid


def score(res: np.ndarray, L: int) -> np.ndarray:
    num, n_num = roll_sum(res, L)
    s2, n_vol = roll_sum(res ** 2, VOL_WINDOW)
    with np.errstate(invalid="ignore", divide="ignore"):
        vol = np.sqrt(s2 / n_vol)
        s = num / vol
    ok = (n_num >= MIN_COV * L) & (n_vol >= MIN_COV * VOL_WINDOW) & (vol > 1e-9)
    return np.where(ok, s, np.nan)


def top_universe(qvol: np.ndarray, n: int) -> np.ndarray:
    roll = roll_sum(qvol, UV_WINDOW)[0]
    mask = np.zeros(qvol.shape, dtype=bool)
    for t in range(roll.shape[0]):
        row = roll[t]
        ok = np.isfinite(row) & (row > 0)
        k = int(ok.sum())
        if k == 0:
            continue
        if k <= n:
            mask[t] = ok
        else:
            mask[t, np.argsort(-row)[:n]] = True
    return mask


def ic_at(x: np.ndarray, y: np.ndarray) -> float:
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 10:
        return np.nan
    rx, ry = stats.rankdata(x[m]), stats.rankdata(y[m])
    rx -= rx.mean()
    ry -= ry.mean()
    d = np.sqrt((rx * rx).sum() * (ry * ry).sum())
    return float((rx * ry).sum() / d) if d > 0 else np.nan


def ic_series(s: np.ndarray, tgt: np.ndarray, step: int) -> np.ndarray:
    return np.array([ic_at(s[t], tgt[t]) for t in range(0, s.shape[0], step)])


def line(ics: np.ndarray, independent: float) -> dict:
    ics = ics[np.isfinite(ics)]
    if len(ics) < 20:
        return {"ic": None, "t": None, "n": int(len(ics))}
    return {"ic": round(float(ics.mean()), 4),
            "t": round(float(compute_hac_tstat(ics, max_lags=min(24, len(ics) // 4))), 2),
            "ic_ir": round(float(ics.mean() / ics.std()), 3),
            "n": int(len(ics)), "n_independent": round(independent, 1)}


def ladder(s: np.ndarray, ret: np.ndarray, step: int, q: int = 5) -> list:
    """Forward return by score quintile at each rebalance, as (mean, median) over rebalances."""
    buckets: list[list[float]] = [[] for _ in range(q)]
    for t in range(0, s.shape[0] - step, step):
        a, b = s[t], ret[t]
        ok = np.isfinite(a) & np.isfinite(b)
        if ok.sum() < q * 3:
            continue
        order = np.argsort(a[ok])
        rr = b[ok]
        for i, chunk in enumerate(np.array_split(order, q)):
            buckets[i].append(float(rr[chunk].mean()))
    return [(round(100 * float(np.mean(x)), 4), round(100 * float(np.median(x)), 4)) if x else None
            for x in buckets]


def reversal_book(s: np.ndarray, ret: np.ndarray, step: int, q: int = 5,
                  cost_bps_side: float = COST_BPS_SIDE) -> dict:
    """Long the LOW-score fifth, short the HIGH-score fifth, rebalanced every `step` rows."""
    gross, prev = [], {}
    for t in range(0, s.shape[0] - 1, step):
        a, b = s[t], ret[t]
        ok = np.isfinite(a) & np.isfinite(b)
        if ok.sum() < q * 3:
            prev = {}
            continue
        idx = np.flatnonzero(ok)
        order = np.argsort(a[ok])
        rr, k = b[ok], max(1, int(ok.sum()) // q)
        w = {int(i): 1.0 / (2 * k) for i in order[:k]}          # compressed positions
        w.update({int(i): -1.0 / (2 * k) for i in order[-k:]})
        w_orig = {int(idx[i]): v for i, v in w.items()}         # absolute asset positions
        turnover = sum(abs(w_orig.get(j, 0.0) - prev.get(j, 0.0))
                       for j in set(w_orig) | set(prev))
        gross.append(float(sum(v * rr[i] for i, v in w.items()))
                     - cost_bps_side / 1e4 * turnover)
        prev = w_orig
    g = np.array(gross)
    if len(g) < 30:
        return {}
    per_year = 365 * 24 / step
    return {"rebalances": int(len(g)),
            "mean_pct": round(100 * float(g.mean()), 4),
            "median_pct": round(100 * float(np.median(g)), 4),
            "hit": round(float((g > 0).mean()), 3),
            "sr_annual": round(float(g.mean() / g.std() * np.sqrt(per_year)), 3),
            "trim5_pct": round(100 * float(np.sort(g)[: int(len(g) * 0.95)].mean()), 4)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    lo, hi = pl.lit(START).str.to_datetime(time_zone="UTC"), pl.lit(END).str.to_datetime(time_zone="UTC")
    wide = (pl.scan_parquet(H1)
            .filter((pl.col("timestamp") >= lo) & (pl.col("timestamp") <= hi))
            .select("timestamp", "asset", "close", "quote_volume")
            .collect()
            .pivot(on="asset", index="timestamp", values=["close", "quote_volume"])
            .sort("timestamp"))
    ts = wide["timestamp"].to_numpy()
    ccols = [c for c in wide.columns if c.startswith("close_")]
    qcols = [c for c in wide.columns if c.startswith("quote_volume_")]
    close = wide.select(ccols).to_numpy().astype(float)
    qvol = wide.select(qcols).to_numpy().astype(float)
    assert [c[6:] for c in ccols] == [c[13:] for c in qcols], "close/quote_volume asset order diverged"
    assets = [c[6:] for c in ccols]

    r = np.full_like(close, np.nan)
    r[1:] = close[1:] / close[:-1] - 1.0
    r = np.clip(r, -0.5, 2.0)
    btc = assets.index("BTCUSDT") if "BTCUSDT" in assets else assets.index("BTC")
    f_btc = r[:, btc]

    fwd = {}
    for h in HORIZONS:
        x = np.full_like(close, np.nan)
        x[:-h] = close[h:] / close[:-h] - 1.0
        fwd[h] = x

    uni = top_universe(qvol, UNIVERSE_N)

    def s_shift(a: np.ndarray) -> np.ndarray:      # signal known at t-1; position set at t
        return np.vstack([np.full((1, a.shape[1]), np.nan), a[:-1]])

    rep = {"panel": {"assets": len(assets), "hours": int(len(ts)),
                     "first": str(ts[0])[:16], "last": str(ts[-1])[:16],
                     "boundary": f"{END} — nothing at or after 2025-07-01 is read"},
           "constants": {"universe_n": UNIVERSE_N, "uv_window": UV_WINDOW,
                         "vol_window": VOL_WINDOW, "reg_window": REG_WINDOW,
                         "cost_bps_side": COST_BPS_SIDE}}

    eras = {"2021-01..2024-06 (out of the LESSONS window)": (START, "2024-06-30T23:59:59Z"),
            "2024-07..2025-06 (replication)": ("2024-07-01T00:00:00Z", END)}
    rep["ic"] = {}

    for strip_alpha in (True, False):
        res = ols_beta(r, f_btc, REG_WINDOW, strip_alpha)
        label = "strip alpha" if strip_alpha else "beta only"
        for L in FORMATIONS:
            s = score(res, L)
            for era, (e0, e1) in eras.items():
                sel = ((ts >= np.datetime64(e0.replace("T", " ")[:19])) &
                       (ts <= np.datetime64(e1.replace("T", " ")[:19])))
                key = f"{label}|L{L}|{era[:7]}"
                for h in HORIZONS:
                    s_m = np.where(uni & sel[:, None], s_shift(s), np.nan)
                    ics = ic_series(s_m, fwd[h], step=h)   # non-overlapping rebalance marks
                    v = line(ics, ics.size)
                    rep["ic"][f"{key}|fwd{h}"] = v
                    if h == 24:
                        print(f"{label:<12} L={L:>3}h fwd24h {era:<38} "
                              f"IC {v['ic']:+.4f}  HAC t {v['t']:+.2f}  "
                              f"IC_IR {v['ic_ir']:+.3f}")

    # books: the reversal direction, cheap-turnover configurations only
    print("\n## Books (long low-score fifth, short high-score fifth)\n")
    res_beta_only = ols_beta(r, f_btc, REG_WINDOW, False)
    books = {}
    for L, step, h in ((336, 72, 72), (168, 72, 72), (168, 24, 24), (72, 24, 24)):
        s = score(res_beta_only, L)
        for mult, cost in (("1x", COST_BPS_SIDE), ("2x", 2 * COST_BPS_SIDE)):
            b = reversal_book(np.where(uni, s_shift(s), np.nan), fwd[h], step, 5, cost)
            books[f"L{L}_hold{h}_step{step}_{mult}"] = b
            if b:
                print(f"L={L:>3}h hold={h:>2}h step={step:>2}h cost {mult}  "
                      f"n={b['rebalances']:>5} mean {b['mean_pct']:+.4f}% "
                      f"med {b['median_pct']:+.4f}% hit {b['hit']:.3f} "
                      f"SR {b['sr_annual']:+.2f} trim5 {b['trim5_pct']:+.4f}%")
    rep["books"] = books
    rep["search_cells"] = (2 * len(FORMATIONS) * len(eras) * len(HORIZONS) + len(books))

    print("\n## Quintile ladder at 72h hold (low score -> high score)\n")
    s_lad = np.where(uni, s_shift(score(res_beta_only, 168)), np.nan)
    lad = ladder(s_lad, fwd[72], 72)
    print("mean   : " + "  ".join(f"{x[0]:+.4f}%" for x in lad))
    print("median : " + "  ".join(f"{x[1]:+.4f}%" for x in lad))
    rep["quintile_ladder_168_72"] = lad

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rep, indent=2) + "\n")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
