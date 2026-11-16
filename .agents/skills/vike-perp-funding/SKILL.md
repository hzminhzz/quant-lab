---
name: vike-perp-funding
description: Perpetual-futures funding rates across CEX venues and Hyperliquid — the top cross-venue funding-arb opportunities, and the per-venue funding of one symbol. Use when the user asks "any funding arb today" / "where to long-short X for funding" / "highest funding rates" / "where to trade X" / "compare BTC perp across exchanges" / "best venue for ETH long".
allowed-tools: Bash(vike-data:*)
---

# vike-perp-funding

Funding rates across the CEX venues and Hyperliquid, from one tool (`perp funding`) in two modes. The rows name the exchange; there is no mark price and no bid/ask in either mode.

## Commands

### A. Arb opportunities (no symbol)

```bash
vike-data perp funding --json
```

Returns **at most 5** pre-ranked rows where the spread between the highest-funding and the lowest-funding venue is ≥ 0.3% per day (spreads of more than ~10% per day are dropped as artifacts). Each row:
- `symbol`
- `long_exchange`, `long_daily_pct` (the venue to be long: lowest funding)
- `short_exchange`, `short_daily_pct` (the venue to be short: highest funding)
- `spread_daily_pct`

**These are pre-ranked and already normalised to daily % — cite the rows directly.** The list is a short top-5, not a full-market scan.

### B. One symbol, every venue

```bash
vike-data perp funding --symbol BTC --json
```

One row per venue: `exchange`, `symbol`, `funding_rate`, `interval_h`, `next_ts`, `vol_24h_usd`.

`funding_rate` is the **raw per-interval rate as a fraction** (0.0001 = 0.01% per interval) and venues use different intervals (`interval_h`: Hyperliquid 1h, most CEXs 8h). **Normalise yourself before comparing venues:**

```
daily_pct = funding_rate × (24 / interval_h) × 100
annualised_pct = daily_pct × 365
```

`vol_24h_usd` is the venue's 24h perp notional: use it as the liquidity check.

## Venue comparison (one symbol)

Run B, normalise, then report:

> **`<SYM>` perp — funding by venue**
>
> | Venue | Funding per interval | Interval | Daily % | 24h volume |
> |---|---|---|---|---|
> | ... | ... | ... | ... | ... |
>
> **Best venue to long**: `<X>` (lowest daily funding, i.e. longs pay least or receive)
> **Best venue to short**: `<Y>` (highest daily funding, i.e. shorts receive most)
> **Funding edge**: long-short = X%/day, gross

- Positive funding → longs pay shorts: a good place to be short. Negative → shorts pay longs: a good place to be long.
- A divergence above 0.5%/day between two venues with similar liquidity is an edge worth quantifying.

## Reading the arb

```
spread_daily_pct = 0.42%   →  ~$420/day per $100k notional per leg, gross
                              (before fees, slippage, margin and transfer costs)
```

A funding arb at 0.4–1% daily is real. Anything much higher on a liquid pair is usually a halted market or a venue artifact: check `vol_24h_usd` in mode B for both venues before calling it a trade.

## Anti-patterns

- Don't quote a daily funding rate as "annualized" without converting (× 365). Be explicit about the unit.
- Funding flips every 1–8 hours depending on the venue. The number is the current rate, not a forecast — don't extrapolate without saying so.
- Spread < 0.3% daily is filtered out at the source (not enough to cover fees + execution). Don't try to lower the filter.
- Don't blindly pick the lowest-funding venue without checking `vol_24h_usd` — Hyperliquid funding looks great on small alts but the book can be thin.
- Don't read `funding_rate` of an 8h venue against a 1h venue without normalising.
- Don't promise a price comparison from this tool: for price gaps across venues use `vike-perp-overview` (`perp spreads`).
- The arb list often leads with thin tokens: run mode B and read `vol_24h_usd` on both legs before calling a row a trade.
- Funding is the cost of carrying a position, not how crowded it is: for open interest and long/short per venue see `vike-perp-overview`, and `vike-perp-crowding` for the combined read.

## Pairs well with

- `vike-perp-overview` for cross-venue price gaps, open interest, long/short and the market snapshot
- `vike-perp-crowding` to put a funding number next to open interest and positioning; `vike-hl-asset-briefing` for one Hyperliquid symbol
- `vike-hl-top-traders` to see how Hyperliquid's best traders are doing
