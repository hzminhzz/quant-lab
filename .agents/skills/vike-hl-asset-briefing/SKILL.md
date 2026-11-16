---
name: vike-hl-asset-briefing
description: One-page briefing on a single Hyperliquid symbol - price, Hyperliquid funding and open interest by the hour, funding and open interest on the other venues, how the size and PnL cohorts are positioned, where liquidations cluster, large fills and where resting orders stack. Use when the user asks "brief me on ETH on Hyperliquid" / "what is going on with SOL perps" / "positioning, funding and liquidations for BTC" / "is HYPE crowded".
allowed-tools: Bash(vike-data:*)
---

# vike-hl-asset-briefing

About eight calls for ONE symbol, 1 credit each. Every section is skippable if the user asked something narrower. Symbols are upper case; a HIP-3 market is `dex:TICKER`.

## Chain

```bash
S=ETH
vike-data candles hl $S --interval 1h --limit 24 --json            # price: 24 hourly bars
vike-data hl market --view hourly --symbols $S --days 7 --json     # Hyperliquid close, open interest, funding rate, premium per hour
vike-data perp funding --symbol $S --json                          # funding on every venue now (normalise it yourself)
vike-data call perp_open_interest --args '{"symbol":"ETH"}'        # open interest per venue, USD notional (no command of its own)
vike-data hl cohort-metrics $S --axis size --hours 1 --json        # whales' long/short
vike-data hl cohort-metrics $S --axis pnl  --hours 1 --json        # profitable vs losing wallets
vike-data hl liquidations --view heatmap --symbol $S --bins 20 --json   # where liquidations would cluster
vike-data hl market --view large_fills --symbol $S --json               # the newest fills of $50k+ (at most 200)
```

Optional: `hl orders --view clusters --symbol $S` (standing orders bucketed around the mid, `--range-pct`, `--step-bps`), `hl market --view size_breakdown --symbol $S` (position-size tiers with `value_close_to_liquidation`), `hl liquidations --view fills --symbol $S --limit 20` (the latest liquidations).

## Reading each section

- **Price**: candles of `ts` (epoch ms), `open..close`, `volume_usd`. The hourly panel gives the same closes beside funding and open interest, so a funding or OI change can be read against the price move of the same hour.
- **Funding**: raw `funding_rate` per interval, and `interval_h` differs (Hyperliquid 1h, most CEXs 8h): `daily_pct = funding_rate x (24 / interval_h) x 100`. Positive = longs pay shorts. Halted or silent venues are left out of the per-symbol list.
- **Open interest**: `oi_usd` is USD notional (contracts x mark price), so it moves with price even when nobody opens or closes. Venues in `stale_venues` are not in `venues` and never in a total.
- **Cohorts**: `bias` is a fraction -1..+1 of notional; group rungs by summing notional (see `vike-hl-cohort-positioning`); check `position_count` before calling a rung's lean meaningful. Labels are today's.
- **Liquidation heatmap**: bins are equal-width over +-50% of `mark_price`; `liquidation_value_usd` is open notional whose liquidation price falls in the bin, with `long_value_usd` / `short_value_usd` and `most_impacted_cohort`. `coverage.in_window_fraction_of_open` is the share of open notional the map covers: positions outside the window or without a liquidation price are dropped, so clusters are a floor. Long liquidations sit BELOW the mark, short ones above.
- **Large fills**: the newest 200 fills of $50k+ the feed holds for the symbol, newest first; on a busy symbol they may reach back only minutes. Quote `covered_from` (how far back the answer reaches) and `truncated` (true = older fills may have been cut), never "today". `--min-usd` raises the floor.

## Answer template

> `<S>` on Hyperliquid: price `<p>` (`<24h change>`). Funding `<daily %>` on Hyperliquid (`<7d range>`) vs `<range>` on `<n>` other venues. OI `$X` on Hyperliquid, `$Y` across `<k>` venues. Whales `<bias_g>`, profitable wallets `<bias>`, losing wallets `<bias>` (labels as of today). Nearest liquidation cluster `<side>` `$V` around `<price range>` (`<coverage>` of open notional mapped). Largest fill since `<covered_from>`: `<side> $F` by `<wallet>`.

## Anti-patterns

- Don't call a funding spread "free money" and don't average funding across venues with different intervals.
- Don't read an OI change in USD as new positions without taking out the price move (see `vike-perp-crowding`).
- Don't run `hl market --view positions` here without `--symbol`.

## Pairs well with

- `vike-hl-cohort-positioning`, `vike-hl-liquidation-radar`, `vike-perp-funding`, `vike-perp-crowding`, `vike-candles`
