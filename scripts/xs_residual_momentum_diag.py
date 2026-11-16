#!/usr/bin/env python3
"""Residual-momentum horizon profile on the broad crypto daily panel (unregistered diagnostics).

Answers what RESULT_binance.md left open: *does the idiosyncratic-trend mechanism exist at any
horizon, factor or universe*, or is 15d/60d/BTC simply the wrong place to look?

Read-only. Writes `data/exploratory/crypto_perps_broad_1d.parquet` (the daily aggregate the SPEC asks
for) and a JSON report. Registers nothing, spends no trial, touches no contract.

Every knob the thesis left implicit is swept, and the sweep reports its own size so the best cell can
be judged against the number of cells searched.

    uv run python scripts/xs_residual_momentum_diag.py --execute
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
D1 = REPO / "data" / "exploratory" / "crypto_perps_broad_1d.parquet"
OUT = REPO / ".workspace" / "plans" / "residual_momentum_hl" / "diag_horizon.json"

START, END = "2021-01-01", "2025-06-30"
MIN_BARS_PER_DAY = 20
MIN_COV = 0.9
REG_WINDOW = 60
VOL_WINDOW = 30
SIG_LAG = 1          # score is known at Sunday's close, entry is Monday's open
FWD = (1, 2, 3, 5, 7, 10, 20, 60)


# ---------------------------------------------------------------- panel


def build_daily() -> pl.DataFrame:
    """Aggregate the 1h store to daily bars; a bar needs most of its 24 hours."""
    return (
        pl.scan_parquet(H1)
        .with_columns(pl.col("timestamp").dt.date().alias("date"))
        .sort(["asset", "timestamp"])
        .group_by(["asset", "date"])
        .agg(pl.col("open").first(), pl.col("close").last(),
             pl.col("quote_volume").sum(), pl.len().alias("n_bars"))
        .filter((pl.col("n_bars") >= MIN_BARS_PER_DAY)
                & (pl.col("date") >= pl.lit(START).str.to_date())
                & (pl.col("date") <= pl.lit(END).str.to_date()))
        .select("asset", "date", "open", "close", "quote_volume")
        .collect()
    )


def wide(df: pl.DataFrame, col: str):
    w = df.pivot(on="asset", index="date", values=col).sort("date")
    dates = w["date"].to_numpy().astype("datetime64[D]")
    assets = [c for c in w.columns if c != "date"]
    return w.select(assets).to_numpy().astype(float), assets, dates


# ---------------------------------------------------------------- rolling maths


def roll_sum(x: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray]:
    """Trailing window sum and finite-count, inclusive of the current row. NaN contributes 0."""
    v = np.where(np.isfinite(x), x, 0.0)
    c = np.isfinite(x).astype(float)
    z = np.zeros((w - 1, x.shape[1]))
    sv = np.vstack([z, v]).cumsum(axis=0)
    sc = np.vstack([z, c]).cumsum(axis=0)
    one = np.zeros((1, x.shape[1]))
    return (sv[w - 1:] - np.vstack([one, sv[:-w]]),
            sc[w - 1:] - np.vstack([one, sc[:-w]]))


def fwd_sum(x: np.ndarray, h: int) -> np.ndarray:
    """Forward sum over rows t+1..t+h, NaN unless all h forward rows are finite."""
    s, n = roll_sum(x[::-1], h + 1)
    return np.where(n[::-1] >= h + 1, s[::-1] - x, np.nan)


def shift(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(a, np.nan, dtype=float)
    if k == 0:
        return a.copy()
    out[k:] = a[:-k]
    return out


def shift_mask(m: np.ndarray, k: int) -> np.ndarray:
    """shift() for a boolean mask: the pad rows must be False, not NaN-promoted-to-True."""
    out = np.zeros_like(m)
    if k:
        out[k:] = m[:-k]
    return out


def ols_beta(r: np.ndarray, f: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray]:
    """Trailing OLS of each column of `r` on the 1-D factor `f` with intercept."""
    F = np.broadcast_to(np.asarray(f, float)[:, None], r.shape)
    ok = np.isfinite(r) & np.isfinite(F)
    rr, ff = np.where(ok, r, np.nan), np.where(ok, F, np.nan)
    n = roll_sum(ff, w)[1]
    sff = roll_sum(ff, w)[0]
    sgg = roll_sum(ff * ff, w)[0]
    syy = roll_sum(rr, w)[0]
    sgy = roll_sum(ff * rr, w)[0]
    with np.errstate(invalid="ignore", divide="ignore"):
        var = sgg - sff ** 2 / n
        beta = (sgy - sff * syy / n) / var
        alpha = syy / n - beta * sff / n
    bad = (n < MIN_COV * w) | ~np.isfinite(beta) | (var <= 0)
    beta[bad] = np.nan
    alpha[bad] = np.nan
    return alpha, beta


def residuals(r: np.ndarray, f: np.ndarray, w: int = REG_WINDOW, beta_lag: int = 0,
              strip_alpha: bool = True) -> np.ndarray:
    """Residual return. `strip_alpha=True` removes the fitted intercept as well as the beta.

    The spec says "strip the beta"; the regression residual also removes alpha. That is not a
    cosmetic choice: summing a residual over L days multiplies alpha by L, so with strip_alpha the
    score is partly a ranking on -alpha (trailing drift), which fights momentum.
    """
    a, b = ols_beta(r, f, w)
    if beta_lag:
        a, b = shift(a, beta_lag), shift(b, beta_lag)
    out = r - b * np.asarray(f, float)[:, None]
    return out - a if strip_alpha else out


def score(res: np.ndarray, L: int, vol_window: int = VOL_WINDOW) -> np.ndarray:
    """sum(residual, L) / std(residual, vol_window), NaN unless both windows are near-complete."""
    num, n_num = roll_sum(res, L)
    s2, n_vol = roll_sum(res ** 2, vol_window)
    with np.errstate(invalid="ignore", divide="ignore"):
        vol = np.sqrt(s2 / n_vol)
        s = num / vol
    ok = (n_num >= MIN_COV * L) & (n_vol >= MIN_COV * vol_window) & (vol > 1e-9)
    return np.where(ok, s, np.nan)


def top_universe(qvol: np.ndarray, n: int, window: int = 30) -> np.ndarray:
    """Point-in-time top-`n` by trailing `window`-day quote volume."""
    roll = roll_sum(qvol, window)[0]
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


# ---------------------------------------------------------------- evaluation


def pearson_ic(x: np.ndarray, y: np.ndarray) -> float:
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 8:
        return np.nan
    a, b = x[m] - x[m].mean(), y[m] - y[m].mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 0 else np.nan


def weekly_pearson(s: np.ndarray, fwd: np.ndarray, dates: np.ndarray) -> np.ndarray:
    day = dates.astype("datetime64[D]").astype(int)
    return np.array([pearson_ic(s[t], fwd[t]) for t in range(len(dates)) if day[t] % 7 == 4])


def quintile_ladder(s: np.ndarray, ret: np.ndarray, dates: np.ndarray, q: int = 5) -> list:
    """Forward return by score quintile (0 = lowest score), as (mean, median) over Mondays."""
    day = dates.astype("datetime64[D]").astype(int)
    buckets: list[list[float]] = [[] for _ in range(q)]
    for t in range(len(dates)):
        if day[t] % 7 != 4:
            continue
        a, b = s[t], ret[t]
        ok = np.isfinite(a) & np.isfinite(b)
        if ok.sum() < q * 2:
            continue
        order = np.argsort(a[ok])
        rr = b[ok]
        for i, chunk in enumerate(np.array_split(order, q)):
            buckets[i].append(float(rr[chunk].mean()))
    return [(round(100 * float(np.mean(x)), 3), round(100 * float(np.median(x)), 3)) if x else None
            for x in buckets]


def rank_ic(x: np.ndarray, y: np.ndarray) -> float:
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 8:
        return np.nan
    rx, ry = stats.rankdata(x[m]), stats.rankdata(y[m])
    rx -= rx.mean()
    ry -= ry.mean()
    d = np.sqrt((rx * rx).sum() * (ry * ry).sum())
    return float((rx * ry).sum() / d) if d > 0 else np.nan


def weekly_ic(s: np.ndarray, fwd: np.ndarray, dates: np.ndarray) -> np.ndarray:
    """Spearman IC on every row that is a Monday (entry day)."""
    day = dates.astype("datetime64[D]").astype(int)
    return np.array([rank_ic(s[t], fwd[t]) for t in range(len(dates)) if day[t] % 7 == 4])


def ic_line(s, fwd, dates, name):
    ics = weekly_ic(s, fwd, dates)
    ics = ics[np.isfinite(ics)]
    if len(ics) < 10:
        return {"ic": None, "t": None, "n": int(len(ics))}
    return {"ic": round(float(ics.mean()), 4), "t": round(float(compute_hac_tstat(ics)), 2),
            "n": int(len(ics)), "ic_ir": round(float(ics.mean() / ics.std()), 3)}


def quintile_book(s: np.ndarray, ret: np.ndarray, dates: np.ndarray, q: int = 5) -> dict:
    day = dates.astype("datetime64[D]").astype(int)
    longs, shorts = [], []
    for t in range(len(dates)):
        if day[t] % 7 != 4:
            continue
        a, b = s[t], ret[t]
        ok = np.isfinite(a) & np.isfinite(b)
        if ok.sum() < q * 2:
            continue
        order = np.argsort(a[ok])
        rr = b[ok]
        k = max(1, ok.sum() // q)
        longs.append(float(rr[order[-k:]].mean()))
        shorts.append(float(rr[order[:k]].mean()))
    L, S = np.array(longs), np.array(shorts)
    if len(L) < 20:
        return {}
    ls = L - S
    return {
        "weeks": int(len(L)),
        "long_mean_pct": round(100 * float(L.mean()), 3),
        "long_median_pct": round(100 * float(np.median(L)), 3),
        "long_hit": round(float((L > 0).mean()), 3),
        "long_sr": round(float(L.mean() / L.std() * np.sqrt(52)), 3),
        "long_trim5_pct": round(100 * float(np.sort(L)[: int(len(L) * 0.95)].mean()), 3),
        "ls_mean_pct": round(100 * float(ls.mean()), 3),
        "ls_sr": round(float(ls.mean() / ls.std() * np.sqrt(52)), 3),
    }


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    if not D1.exists():
        if not args.execute:
            print(f"missing {D1}; re-run with --execute")
            return 2
        d = build_daily()
        d.sort(["date", "asset"]).write_parquet(D1)
        print(f"wrote {D1} ({d.height:,} rows, {d['asset'].n_unique()} assets)")

    d = pl.read_parquet(D1)
    close, assets, dates = wide(d, "close")
    opn, assets_o, dates_o = wide(d, "open")
    qvol, assets_q, dates_q = wide(d, "quote_volume")
    assert assets_o == assets == assets_q, "pivot asset order diverged between fields"
    assert np.array_equal(dates_o, dates) and np.array_equal(dates_q, dates), \
        "pivot date index diverged between fields"
    assert (dates.astype("datetime64[D]").astype(int) % 7 == 4).any(), \
        "no Monday in the date index: the 1970-01-01 Thursday offset changed"

    r = np.full_like(close, np.nan)
    r[1:] = close[1:] / close[:-1] - 1.0
    r = np.clip(r, -0.95, 5.0)

    btc = assets.index("BTCUSDT") if "BTCUSDT" in assets else assets.index("BTC")
    f_btc = r[:, btc]
    with np.errstate(invalid="ignore"):
        f_ew = np.nanmean(r, axis=1)

    fwd = {}
    for h in FWD:
        x = np.full_like(close, np.nan)
        x[:-h] = close[h:] / opn[:-h] - 1.0
        fwd[h] = x

    uni = {n: shift_mask(top_universe(qvol, n), SIG_LAG)
           for n in (10, 25, 50, 100, len(assets))}

    def prep(s, n=25):
        return np.where(uni[n], shift(s, SIG_LAG), np.nan)

    rep = {"panel": {"assets": len(assets), "days": int(len(dates)),
                     "first": str(dates[0]), "last": str(dates[-1])},
           "constants": {"reg_window": REG_WINDOW, "vol_window": VOL_WINDOW,
                         "sig_lag": SIG_LAG, "fwd_days": list(FWD), "min_cov": MIN_COV}}

    # 1. horizon profile: formation length x factor, residual vs raw numerator
    res_btc = residuals(r, f_btc)
    res_ew = residuals(r, f_ew)
    print("\n## Horizon profile — IC vs forward 5d total return, top-25\n")
    print("| formation | residual BTC | t | residual EW | t | raw BTC | t |")
    print("|---|---|---|---|---|---|---|")
    profile = []
    for L in (5, 10, 15, 20, 30, 60, 120, 250):
        row = {"formation": L}
        for tag, res in (("btc", res_btc), ("ew", res_ew)):
            v = ic_line(prep(score(res, L)), fwd[5], dates, f"res{L}{tag}")
            row[f"res_{tag}"] = v
        row["raw_btc"] = ic_line(prep(score(r, L)), fwd[5], dates, f"raw{L}")
        profile.append(row)
        print(f"| {L}d | {row['res_btc']['ic']:+.4f} | {row['res_btc']['t']:+.2f} "
              f"| {row['res_ew']['ic']:+.4f} | {row['res_ew']['t']:+.2f} "
              f"| {row['raw_btc']['ic']:+.4f} | {row['raw_btc']['t']:+.2f} |")
    rep["horizon_profile"] = profile
    rep["n_weeks"] = profile[0]["res_btc"]["n"]

    # 1b. "strip the beta" — with or without the fitted intercept?  Summing a residual over L days
    #     multiplies alpha by L, so this is not cosmetic.
    print("\n## Alpha sensitivity: subtract the fitted intercept or only the beta?\n")
    print("| formation | strip alpha, top25 | t | beta only, top25 | t "
          "| strip alpha, top100 | t | beta only, top100 | t |")
    print("|---|---|---|---|---|---|---|---|")
    alpha_sens = []
    for L in (5, 15, 30, 60, 120):
        row = {"formation": L}
        for tag, strip in (("strip", True), ("beta_only", False)):
            sc = score(residuals(r, f_btc, REG_WINDOW, 0, strip), L)
            for n in (25, 100):
                row[f"{tag}_{n}"] = ic_line(np.where(uni[n], shift(sc, SIG_LAG), np.nan),
                                            fwd[5], dates, f"{tag}{n}{L}")
            if L == 15:
                mk = uni[25]
                row["corr_strip_vs_beta_only"] = round(float(stats.spearmanr(
                    shift(score(res_btc, 15), SIG_LAG)[mk],
                    shift(sc, SIG_LAG)[mk]).statistic), 3)
        alpha_sens.append(row)
        print(f"| {L}d | {row['strip_25']['ic']:+.4f} | {row['strip_25']['t']:+.2f} "
              f"| {row['beta_only_25']['ic']:+.4f} | {row['beta_only_25']['t']:+.2f} "
              f"| {row['strip_100']['ic']:+.4f} | {row['strip_100']['t']:+.2f} "
              f"| {row['beta_only_100']['ic']:+.4f} | {row['beta_only_100']['t']:+.2f} |")
    rep["alpha_sensitivity"] = alpha_sens

    # 2. does the score predict the residual or the total return?
    #    The forward residual uses ONE beta, the one estimable at entry-1, applied to the whole
    #    holding window, and subtracts NO alpha. Both matter: a trailing residual sum overlaps the
    #    score's own formation window, and a per-coin alpha offset shared with the score's own
    #    -L*alpha term manufactures correlation out of nothing but a stale drift estimate.
    print("\n## Base spec (15d/60d/BTC): forward residual vs forward total\n")
    base = score(res_btc, 15)
    a0, b0 = ols_beta(r, f_btc, REG_WINDOW)
    b_entry = shift(b0, 1)
    fwd_res = (fwd_sum(r, 5)
               - b_entry * fwd_sum(np.broadcast_to(f_btc[:, None], r.shape), 5))
    for lag in (0, 15):
        sl = score(residuals(r, f_btc, REG_WINDOW, lag), 15)
        print(f"beta_lag={lag}")
        for name, tgt in (("fwd5_total", fwd[5]), ("fwd5_residual", fwd_res)):
            v = ic_line(prep(sl), tgt, dates, name)
            rep[f"lag{lag}_{name}"] = v
            print(f"   {name:<14} IC {v['ic']:+.4f} t {v['t']:+.2f} IC_IR {v['ic_ir']:+.3f}")
        if lag == 0:
            base = sl

    # 2b. rank vs Pearson, and the quintile ladder: monotone ordering, or only the tails?
    print("\n## Rank vs Pearson IC, and the quintile ladder (base score, top-25)\n")
    sb = prep(base)
    rvp = {"rank": {}, "pearson": {}}
    for h in (1, 5, 7, 10):
        rk = weekly_ic(sb, fwd[h], dates)
        pe = weekly_pearson(sb, fwd[h], dates)
        rk, pe = rk[np.isfinite(rk)], pe[np.isfinite(pe)]
        rvp["rank"][h] = {"ic": round(float(rk.mean()), 4),
                          "t": round(float(compute_hac_tstat(rk)), 2)}
        rvp["pearson"][h] = {"ic": round(float(pe.mean()), 4),
                             "t": round(float(compute_hac_tstat(pe)), 2)}
        print(f"fwd{h:>2}d  rank IC {rvp['rank'][h]['ic']:+.4f} "
              f"(t {rvp['rank'][h]['t']:+.2f})   pearson IC {rvp['pearson'][h]['ic']:+.4f} "
              f"(t {rvp['pearson'][h]['t']:+.2f})")
    ladder = {}
    fwd_btc5 = fwd_sum(np.broadcast_to(f_btc[:, None], r.shape), 5)
    fwd5_excess = fwd[5] - b_entry * fwd_btc5
    for h, tgt in ((5, fwd[5]), (7, fwd[7])):
        q_lad = quintile_ladder(sb, tgt, dates)
        ladder[h] = q_lad
        print(f"fwd{h}d mean   (low->high score): "
              + "  ".join(f"{x[0]:+.2f}%" for x in q_lad))
        print(f"fwd{h}d median (low->high score): "
              + "  ".join(f"{x[1]:+.2f}%" for x in q_lad))
    q_ex = quintile_ladder(sb, fwd5_excess, dates)
    ladder["fwd5_beta_adj"] = q_ex
    print("fwd5d beta-adj mean   (low->high): "
          + "  ".join(f"{x[0]:+.2f}%" for x in q_ex))
    rep["rank_vs_pearson"] = {k: {str(h): v for h, v in d.items()} for k, d in rvp.items()}
    rep["quintile_ladder"] = {str(k): v for k, v in ladder.items()}

    # 3. IC decay: base score against forward horizons 1..60 days
    print("\n## IC decay (base score, top-25)\n")
    decay = {}
    for h in FWD:
        v = ic_line(prep(base), fwd[h], dates, f"fwd{h}")
        decay[h] = v
        print(f"fwd{h:>2}d  IC {v['ic']:+.4f}  t {v['t']:+.2f}  IC_IR {v['ic_ir']:+.3f}")
    rep["ic_decay"] = decay

    # 4. what the score actually ranks on
    print("\n## Score anatomy (beta_lag=0, top-25)\n")
    alpha_60, beta_60 = ols_beta(r, f_btc, REG_WINDOW)
    btc15 = np.broadcast_to(roll_sum(f_btc[:, None], 15)[0], base.shape)
    anatomy = {}
    m = uni[25] & np.isfinite(shift(base, SIG_LAG))
    for name, other in (("raw_15d_return", roll_sum(r, 15)[0]),
                        ("raw_15d_over_vol", score(r, 15)),
                        ("alpha_60", alpha_60),
                        ("beta_60", beta_60),
                        ("btc_15d_sum", btc15)):
        a, b = shift(base, SIG_LAG)[m], shift(other, SIG_LAG)[m]
        ok = np.isfinite(a) & np.isfinite(b)
        anatomy[name] = round(float(stats.spearmanr(a[ok], b[ok]).statistic), 3)
        print(f"corr(score, {name:<17}) = {anatomy[name]:+.3f}  (n={int(ok.sum()):,})")
    rep["score_anatomy"] = anatomy

    # 5. universe size
    print("\n## Universe size (base score)\n")
    per_uni = {}
    for n, mk in uni.items():
        v = ic_line(np.where(mk, shift(base, SIG_LAG), np.nan), fwd[5], dates, f"n{n}")
        per_uni[n] = v
        print(f"top {n:>3}: IC {v['ic']:+.4f} t {v['t']:+.2f}")
    rep["universe"] = {str(k): v for k, v in per_uni.items()}

    # 6. sub-periods
    print("\n## Sub-periods (base score, top-25)\n")
    years = dates.astype("datetime64[Y]").astype(int) + 1970
    sb = shift(base, SIG_LAG)
    per_year = {}
    for y in sorted(set(years.tolist())):
        sel = np.where(years == y, 1.0, np.nan)[:, None]
        v = ic_line(np.where(uni[25], sb, np.nan) * sel, fwd[5], dates, str(y))
        if v["ic"] is None:
            continue
        per_year[y] = v
        print(f"{y}: IC {v['ic']:+.4f} t {v['t']:+.2f} IC_IR {v['ic_ir']:+.3f} ({v['n']} wk)")
    rep["subperiods"] = {str(k): v for k, v in per_year.items()}

    # 7. books at the tradeable weekly horizon (entry Monday open, exit +7d)
    print("\n## Books (fwd7 open-to-open, top-25, no costs)\n")
    books = {}
    variants = {
        "residual 15d": base,
        "residual 15d negated": -base,
        "residual 60d": score(res_btc, 60),
        "residual 250d": score(res_btc, 250),
        "raw 15d": score(r, 15),
    }
    for name, s in variants.items():
        b = quintile_book(np.where(uni[25], shift(s, SIG_LAG), np.nan), fwd[7], dates)
        books[name] = b
        if b:
            print(f"{name:<22} n={b['weeks']:>3} long {b['long_mean_pct']:+.2f}% "
                  f"med {b['long_median_pct']:+.2f}% hit {b['long_hit']:.2f} "
                  f"SR {b['long_sr']:+.2f} trim5 {b['long_trim5_pct']:+.2f}% | "
                  f"L/S {b['ls_mean_pct']:+.2f}% SR {b['ls_sr']:+.2f}")
    rep["books"] = books

    rep["search_cells"] = (len(profile) * 3          # horizon profile: formation x 3 numerators
                           + len(alpha_sens) * 4     # alpha sensitivity: convention x universe
                           + 4                       # forward-residual vs forward-total, 2 lags
                           + len(rvp["rank"]) * 2    # rank vs Pearson
                           + len(ladder)             # quintile ladders
                           + len(decay) + len(anatomy)
                           + len(per_uni) + len(per_year) + len(books))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rep, indent=2) + "\n")
    print(f"\nwrote {args.out}  (search cells: {rep['search_cells']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
