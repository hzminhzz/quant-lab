#!/usr/bin/env python3
"""Reusable cross-sectional panel diagnostics for quant-lab (unregistered: writes no registry row).

Built from the HYP-2026-125 post-mortem. Encodes the checks that hypothesis needed and did not have:
per-formation IC with HAC t, book Sharpe at 1x/2x/+1-bar, the top-5%-of-bars trim, DSR at a *stated*
selection count, and the pump share of the long leg.

Read-only. Nothing here registers a trial; use `warden screen/backtest` for that.

    uv run python scripts/xs_panel_diagnostics.py --panel data/exploratory/crypto_perps_broad_1h.parquet \
        --contract contracts/crypto_perps_top100_1h_v1.yaml --formations 168,672 --skip 0,168

Panels must carry: timestamp, asset, open, close, quote_volume (volume*close), funding_rate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl
from scipy import stats

from warden.contracts import load_contract
from warden.gates import compute_hac_tstat
from warden.registry import compute_selection_report
from warden.runner import (calculate_metrics_from_returns, execution_returns, simulate_positions,
                           _aligned_matrix, _funding_settlement_mask)

QUOTE_VOL_WINDOW = 168      # the universe ranks on a trailing 7-day dollar volume
MIN_WINDOW_COVERAGE = 0.8


def _lag(m: np.ndarray, k: int) -> np.ndarray:
    if k == 0:
        return m.copy()
    out = np.full_like(m, np.nan)
    out[k:] = m[:-k]
    return out


def _rolling_sum(m: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray]:
    filled = np.where(np.isfinite(m), m, 0.0)
    present = np.isfinite(m).astype(float)
    cs, cp = np.cumsum(filled, axis=0), np.cumsum(present, axis=0)
    total = np.full_like(m, np.nan)
    count = np.full_like(m, np.nan)
    total[w:] = cs[w:] - cs[:-w]
    count[w:] = cp[w:] - cp[:-w]
    return total, count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panel", required=True)
    ap.add_argument("--contract", required=True, help="used for costs, delay, bars_per_year, val window")
    ap.add_argument("--formations", default="168,336,672", help="formation windows in bars")
    ap.add_argument("--rebalance", type=int, default=168,
                    help="holding period and rebalance cadence in bars (independent of the formation window)")
    ap.add_argument("--skip", default="0", help="skip windows in bars, applied to every formation")
    ap.add_argument("--universe", type=int, default=25, help="point-in-time universe size by dollar volume")
    ap.add_argument("--legs", type=int, default=5, help="names per side of the long/short book")
    ap.add_argument("--cap", type=float, default=None, help="exclude candidates whose formation return exceeds this")
    ap.add_argument("--attempts", type=int, default=30, help="honest selection count for the DSR line")
    ap.add_argument("--families-noise", type=float, default=1.4456301171375547, help="inherited noise floor")
    ap.add_argument("--families-keff", type=float, default=7.801830292711028, help="inherited k floor")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    contract = load_contract(args.contract)
    df = pl.read_parquet(args.panel).sort("timestamp", "asset")
    ts = sorted(df["timestamp"].unique().to_list())
    assets = sorted(df["asset"].unique().to_list())
    n_bars, n_a = len(ts), len(assets)
    idx = {t: i for i, t in enumerate(ts)}

    def matrix(col: str) -> np.ndarray:
        p = df.pivot(index="timestamp", on="asset", values=col).sort("timestamp")
        for a in assets:
            if a not in p.columns:
                p = p.with_columns(pl.lit(None, dtype=pl.Float64).alias(a))
        out = np.full((n_bars, n_a), np.nan)
        out[np.array([idx[t] for t in p["timestamp"].to_list()], dtype=int), :] = (
            p.select(assets).to_numpy().astype(float))
        return out

    close, open_ = matrix("close"), matrix("open")
    qvol = matrix("quote_volume") if "quote_volume" in df.columns else matrix("volume") * close
    dvol, cover = _rolling_sum(qvol, QUOTE_VOL_WINDOW)
    dvol = np.where(cover >= MIN_WINDOW_COVERAGE * QUOTE_VOL_WINDOW, dvol, np.nan)

    finite = np.isfinite(close)
    last_obs = np.where(finite.any(axis=0), n_bars - 1 - np.argmax(finite[::-1, :], axis=0), -1)

    horizon = args.rebalance                             # hold != formation: a 4w signal can be held 1w
    fwd = np.full((n_bars, n_a), np.nan)
    for t in range(n_bars - contract.execution_delay_bars - horizon - 1):
        entry = open_[t + contract.execution_delay_bars]
        exit_ = open_[t + contract.execution_delay_bars + horizon]
        fwd[t] = np.where(entry > 0, exit_ / entry - 1.0, np.nan)

    v0 = idx.get(contract.val_start, 0)
    v1 = idx.get(contract.val_end, n_bars)
    grid = [t for t in range(0, n_bars - horizon - 2) if t % horizon == 0 and v0 <= t < v1]
    ret_p = execution_returns(df, contract, assets)
    fund_p = _aligned_matrix(df, "funding_rate", assets)
    fmask = _funding_settlement_mask(ts, contract)
    print(f"panel {Path(args.panel).name}: {n_bars:,} bars x {n_a} assets | validation rebalances: {len(grid)}")

    rows = []
    for L in [int(x) for x in args.formations.split(",")]:
        for sk in [int(x) for x in args.skip.split(",")]:
            base, start = _lag(close, L + sk), _lag(close, sk)
            ret = np.where(np.isfinite(base) & (base > 0) & np.isfinite(start), start / base - 1.0, np.nan)
            ics_p, ics_r, weekly, pumped, fwd_pumped, fwd_clean = [], [], [], [], [], []
            weights: dict[int, np.ndarray] = {}
            for t in grid:
                el = np.where(np.isfinite(ret[t]) & np.isfinite(dvol[t]) & (dvol[t] > 0))[0]
                if el.size < args.universe:
                    continue
                u = el[np.argsort(-dvol[t][el])[: args.universe]]
                x = ret[t][u]
                keep = np.isfinite(x)
                if args.cap is not None:
                    keep &= x <= args.cap
                if keep.sum() < 2 * args.legs:
                    continue
                uu, xx = u[keep], x[keep]
                ok = np.isfinite(fwd[t][uu])
                if ok.sum() >= 8 and np.std(xx[ok]) > 1e-12:
                    ics_p.append(float(np.corrcoef(xx[ok], fwd[t][uu][ok])[0, 1]))
                    ics_r.append(float(stats.spearmanr(xx[ok], fwd[t][uu][ok]).statistic))
                order = uu[np.argsort(xx)]
                long_, short_ = order[-args.legs :], order[: args.legs]
                w = np.zeros(n_a)
                w[long_] = 0.5 / args.legs
                w[short_] = -0.5 / args.legs
                weights[t] = w
                v = np.where(np.isfinite(fwd[t][order]), fwd[t][order], 0.0)
                weekly.append(float(np.mean(v[-args.legs:]) - np.mean(v[: args.legs])))
                is_pump = ret[t][long_] > 0.5
                pumped.append(float(np.mean(is_pump)))
                if is_pump.any():
                    fwd_pumped.append(float(np.nanmean(np.where(np.isfinite(fwd[t][long_][is_pump]), fwd[t][long_][is_pump], 0.0))))
                if (~is_pump).any():
                    fwd_clean.append(float(np.nanmean(np.where(np.isfinite(fwd[t][long_][~is_pump]), fwd[t][long_][~is_pump], 0.0))))

            pos = np.zeros((n_bars, n_a))
            last = np.zeros(n_a)
            for t in range(n_bars):
                if t in weights:
                    last = weights[t]
                pos[t] = last if v0 <= t < v1 else 0.0
            for j, lb in enumerate(last_obs):
                if lb >= 0:
                    pos[max(0, int(lb)):, j] = 0.0

            sl = slice(v0, v1)
            sims = {}
            for mult, doff, tag in ((1.0, 0, "1x"), (2.0, 0, "2x"), (1.0, 1, "+1bar")):
                s = simulate_positions(pos, ret_p["long"], fund_p, contract, sl, cost_multiplier=mult,
                                       delay_offset=doff, funding_mask=fmask, quote_returns=ret_p)
                sims[tag] = calculate_metrics_from_returns(s["gross_returns"], s["net_returns"], s["turnover"],
                                                           contract.bars_per_year)
                if tag == "1x":
                    net = s["net_returns"]
            trim = np.delete(net, np.argsort(net)[-max(1, int(0.05 * len(net))):])
            trim_sr = float(np.mean(trim) / (np.std(trim, ddof=1) + 1e-12) * np.sqrt(contract.bars_per_year))
            dsr1 = compute_selection_report(net, [], contract.bars_per_year, raw_attempt_count=1,
                                            prior_max_keff=1.0, prior_max_noise=0.0)
            dsrn = compute_selection_report(net, [], contract.bars_per_year, raw_attempt_count=args.attempts,
                                            prior_max_keff=max(args.attempts, args.families_keff),
                                            prior_max_noise=args.families_noise)
            row = {"formation": L, "skip": sk, "n_ic": len(ics_p),
                   "pearson_ic": float(np.mean(ics_p)) if ics_p else None,
                   "pearson_t": float(compute_hac_tstat(ics_p)) if ics_p else None,
                   "rank_ic": float(np.mean(ics_r)) if ics_r else None,
                   "rank_t": float(compute_hac_tstat(ics_r)) if ics_r else None,
                   "median_week": float(np.median(weekly)) if weekly else None,
                   "hit": float(np.mean(np.array(weekly) > 0)) if weekly else None,
                   "net_sr": sims["1x"]["net_sharpe"], "net_sr_2x": sims["2x"]["net_sharpe"],
                   "net_sr_delay": sims["+1bar"]["net_sharpe"], "max_dd": sims["1x"]["max_drawdown_pct"],
                   "turnover": sims["1x"]["annualized_turnover"], "trim5_sr": trim_sr,
                   "dsr_k1": dsr1["probability"], "dsr_honest": dsrn["probability"],
                   "pump_share": float(np.mean(pumped)) if pumped else None,
                   "fwd_long_pumped": float(np.mean(fwd_pumped)) if fwd_pumped else None,
                   "fwd_long_clean": float(np.mean(fwd_clean)) if fwd_clean else None}
            rows.append(row)
            print(f"  {L:5d}b skip {sk:4d} | IC {row['pearson_ic']:+.4f} (t {row['pearson_t']:+.2f}) "
                  f"rank {row['rank_ic']:+.4f} (t {row['rank_t']:+.2f}) | netSR {row['net_sr']:+.3f} "
                  f"2x {row['net_sr_2x']:+.3f} +1bar {row['net_sr_delay']:+.3f} | DD {row['max_dd']:5.1f}% "
                  f"| median wk {row['median_week']:+.2%} hit {row['hit']:.2f} | trim5 {trim_sr:+7.2f} "
                  f"| DSR {row['dsr_k1']:.3f}/{row['dsr_honest']:.3f} | pumps {row['pump_share']:.0%}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"panel": args.panel, "contract": args.contract,
                                                   "universe": args.universe, "cap": args.cap, "rows": rows}, indent=2))
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
