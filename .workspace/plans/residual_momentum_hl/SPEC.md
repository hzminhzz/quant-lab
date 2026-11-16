# Thesis draft — residual momentum, top-25 perps (2026-10-29)

Principal's spec, transcribed. **Not registered.** No contract, no panel, no trial.

## As stated

- Universe: the top 25 Hyperliquid perps.
- Every Monday: for each coin, regress its daily returns on BTC over the last 60 days, strip the beta,
  and sum the residual over the last 15 days; divide by 30-day vol.
- Long the top fifth only (5 of 25). No short leg.
- The rank reads only daily closes from before the entry.

Mechanism this encodes (Blitz–Huij–Martens residual momentum): idiosyncratic trend persists because
information diffuses slowly across a cross-section, while market beta — which mean-reverts and dominates
raw momentum's crashes — is removed from the score.

## Data reality, checked today

| source | coverage | usable? |
|---|---|---|
| lake HL second / L2 | 9 symbols, 2026-01-15 → 2026-09-11 | no — 8 months, 9 names |
| lake HL tick | 6 symbols, 2026-01-01 → 2026-02-28 | no |
| lake HL 1m OHLCV | 1 store (`XYZ_PERP`, a synthetic index) | no |
| paid HL minute (0xArchive) | from 2025-03-22 | ≤82 weekly rebalances, window overlaps the sealed crypto holdout |
| lake Binance UM 1h | 863 symbols, 2020-01 → 2026-10 | **yes** — aggregate to daily |
| lake daily store | none exists (`grep ohlcv_1d` = 0 stores) | must be built from the 1h store |

So the thesis cannot be run on Hyperliquid today. The only executable version is a Binance proxy:
aggregate the 1h store to daily, keep 863 symbols, 2021-01 → 2025-06, and rank a point-in-time top-25.
That gives 208 Monday rebalances — the same power ceiling the last thesis hit.

## Ambiguities that change the answer (need the principal's call)

1. **"Top 25" by what, over what window?** Proposal: trailing 30-day quote volume, point-in-time,
   re-ranked every Monday. A static list reintroduces the survivorship problem.
2. **Divide by which vol?** Residual vol (consistent with "strip out the beta") or total vol? Proposal:
   30-day standard deviation of the *residuals*, since the numerator is a residual sum.
3. **Long-only means the portfolio is not beta-neutral.** Stripping beta cleans the *score*; it does not
   remove the book's market exposure. Five equal-weight longs at gross 1.0 carry roughly full alt beta,
   so drawdown and Sharpe will be dominated by market direction, and the six-check audit's cost/latency
   check will be measuring a directional bet. Options: (a) accept it and report beta-adjusted metrics,
   (b) add a BTC hedge to make it market-neutral, (c) keep long-only but size by residual vol. This is
   the single biggest design decision in the thesis.
4. **Entry timing.** Signal from closes through Sunday; on a daily contract the position is set on
   Sunday's bar and warden fills at Monday's open with `execution_delay_bars: 1`. Confirm that is the
   intended "Monday entry".
5. **60d / 15d / 30d windows are calendar-days or trading-days?** Crypto trades 24/7, so they coincide,
   but state it: 60, 15 and 30 daily bars.

## What the next session needs

1. A daily panel: `data/exploratory/crypto_perps_broad_1d.parquet` (aggregate the 1h store; 863 symbols,
   2021-01 → 2025-06; funding joined at 8h buckets as in `build_crypto_perps_broad_1h.py`).
2. A contract: `CRYPTO_PERPS_BROAD_1D_V1` (daily frequency, `label_horizon_bars: 5` for a weekly hold,
   `embargo_bars: 5`, costs as siblings). Principal-only.
3. The Discord code, for three specific things: the universe definition, the vol denominator, and whether
   the book carries a hedge.

## Expected shape, from what this repo has already measured

- 208 weekly bets is the ceiling; the previous thesis showed that a Sharpe near 1.4 at 208 bets scores
  DSR ≈ 0.48 at an honest selection count and cannot reach 0.90.
- A long-only alt book will show a much larger drawdown than the long-short version (the long-short book
  already hit 33-45%). Budget for that in the falsification criteria, not after.
- Check the top-5%-of-bars trim before Stage 3 — `scripts/xs_panel_diagnostics.py` does it in one line.
