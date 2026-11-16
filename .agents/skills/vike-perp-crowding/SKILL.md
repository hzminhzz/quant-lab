---
name: vike-perp-crowding
description: Is a perpetual-futures symbol crowded one way - funding paid, open interest net of the price move and the long/short lean on each venue, compared with the symbol's own last week, plus the Hyperliquid funding history and wallet-cohort lean. Use when the user asks "is BTC long crowded" / "is ETH positioning stretched across exchanges" / "is open interest rising with price" / "are traders over-leveraged long on SOL".
allowed-tools: Bash(vike-data:*)
---

# vike-perp-crowding

Three lenses on one symbol, about 6 credits. No lens has a validated threshold: judge each against the symbol's OWN last week and report what agrees, never a score. Base asset only (`BTC`, not `BTCUSDT`). Open interest and long/short have no command of their own: they are `vike-data call`.

## Chain

```bash
S=BTC
vike-data perp funding --symbol $S --json                                   # funding on every venue now
vike-data hl market --view hourly --symbols $S --days 7 --json              # Hyperliquid funding, open interest and close per hour
vike-data call perp_open_interest --args '{"symbol":"BTC","hours":168}'     # hourly oi_usd per venue
vike-data call perp_long_short --args '{"symbol":"BTC","hours":168}'        # hourly long_short_ratio per venue, with `basis`
vike-data candles hl $S --interval 1h --limit 169 --json                    # price over the same hours, to net it out of OI
vike-data hl cohort-metrics $S --axis size --hours 1 --json                 # optional: whales on Hyperliquid
```

Without `hours` both `call`s return the newest print per venue; `"exchange":"binance"` narrows to one venue.

## Lens 1: funding

Raw `funding_rate` is per interval and `interval_h` differs (Hyperliquid 1h, most CEXs 8h): `daily_pct = funding_rate x 24 / interval_h x 100`. Positive = longs pay. `perp funding` is a snapshot, so for the CEXs say "above/below the other venues"; Hyperliquid's own funding history is in the hourly panel, so judge it against its week.

## Lens 2: open interest net of price

`oi_usd` is USD notional = contracts x mark price, so it rises with price on its own. Over the same hours: `contracts_change% ~ oi_change% - price_change%`. OI rising faster than price = new positions; falling while price rises = positions closing. Venues in `stale_venues` are not in `venues` and never in a total. The series is `[hour, oi_usd]` pairs, `hours + 1` points, the first hour partial.

## Lens 3: who leans

`long_share` / `short_share` are fractions of 1; `long_short_ratio` = long / short, above 1 long-heavy. **Each venue counts something different** and the row says so in `basis`: binance = top traders by positions, okx and gate = top traders by accounts, bybit = all accounts. Never average across venues; compare each venue with its own 7-day range. About 40 major symbols per venue; other symbols return empty lists and a `note`, not zeros. Hyperliquid cohorts (`bias` -1..+1 of notional, sum rungs by notional) are wallets, not accounts counted by a venue.

## Verdict

State which lenses point the same way and which do not. Illustrative wording, not live data: "funding positive on most venues, contracts rising on binance but falling on hyperliquid, long ratio at its 7-day high on binance only: mixed, not crowded". Name "crowded long" only when funding, net OI and at least two bases agree and sit at the top of their own week.

## Anti-patterns

- Don't read `oi_usd` growth as new longs, or a long/short ratio as dollars.
- Don't average ratios across venues or compare bases; don't forecast a squeeze.
- Don't leave stale venues in the comparison.

## Pairs well with

- `vike-perp-funding`, `vike-perp-overview`, `vike-hl-cohort-positioning`, `vike-hl-asset-briefing`, `vike-candles`
