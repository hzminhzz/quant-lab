---
name: vike-perp-overview
description: Perp-market snapshot and the venue-level reads of one symbol — the top funding arbs, the widest perp price gaps between venues, the Hyperliquid leaderboard, open interest per venue and each venue's long/short lean. Use when the user asks "what's happening in perps today" / "perp market overview" / "any perp arb today" / "price differences across exchanges" / "where's X cheapest" / "open interest of X" / "long short ratio of X".
allowed-tools: Bash(vike-data:*)
---

# vike-perp-overview

One-shot market overview for perpetuals, plus the cross-venue price-gap, open-interest and long/short tools. There is **no market-wide volume or per-venue mark-price tool**; say so if asked (Hyperliquid's own open interest is `vike-data hl market --json`).

## Open interest and long/short (one symbol, per venue)

These two tools have no command of their own; `vike-data call` prints their raw JSON (1 credit each):

```bash
vike-data call perp_open_interest --args '{"symbol":"BTC"}'                                # newest print per venue
vike-data call perp_open_interest --args '{"symbol":"BTC","exchange":"binance","hours":48}' # hourly series, 1-168
vike-data call perp_long_short --args '{"symbol":"ETH"}'
vike-data call perp_long_short --args '{"symbol":"ETH","exchange":"okx","hours":24}'
```

Pass the base asset (`BTC`, not `BTCUSDT`); `exchange` is lower case.

- **`oi_usd` is USD notional** (open contracts x mark price when polled): not a contract count, so venues add up, and it moves with price even when no position opens or closes. Venues: aster, binance, bitget, bybit, hyperliquid, kucoin, okx. A venue silent for over an hour is under `stale_venues` and NOT in `venues`: never add it into a total.
- **`long_share` / `short_share` are fractions of 1**; `long_short_ratio` = long / short, above 1 = long-heavy. **Each venue counts something different, named in the row's `basis`**: binance top traders by positions; okx and gate top traders by accounts; bybit all accounts. Never average across venues; compare a venue with its own history.
- About 40 major symbols per venue: any other symbol returns empty lists and a `note`, never zeros.
- Whether a symbol is crowded: `vike-perp-crowding`.

## Price spreads

```bash
vike-data perp spreads --json                 # up to 10 symbols with a cross-venue price gap >= 0.5%
vike-data perp spreads --symbol BTC --json    # the same list, filtered to one symbol
```

Rows: `symbol`, `buy_exchange`, `buy_price`, `sell_exchange`, `sell_price`, `spread_pct`. Pre-ranked: cite the rows directly.

- **"Spread" is perp vs perp across venues, not spot vs perp.** `buy_exchange` is the cheapest venue and `sell_exchange` the dearest for that symbol.
- **`--symbol` only filters the global top 10.** An empty answer means the symbol is not among today's widest gaps, not that its venues agree. Don't answer "where's X cheapest" from an empty result; say the tool only lists the 10 widest gaps.
- Outliers (> 400 bps off the cross-venue median) are filtered upstream — typically unit-mismatch listings like `1000SHIB` vs `SHIB`, or stale prices.
- It reads the latest ticker snapshots: a row is a current snapshot, not a forecast.

`spread_pct = 0.7%` on a liquid pair is an opportunity **before** taker fees, slippage and the time to move inventory between venues. For the funding side of the same trade use `vike-perp-funding`.

## Market snapshot (3 parallel calls)

```bash
vike-data perp funding --json &              # top funding-arb opportunities (max 5)
vike-data perp spreads --json &              # widest cross-venue price gaps (max 10)
vike-data perp top-traders --window 1d --sort pnl --size 10 --json &   # HL leaderboard
wait
```

### Report template

> **Perp market snapshot (HL leaderboard: today and yesterday, UTC)**
>
> **Top funding arbs (>0.3% daily spread)**
> - `BTC`: long Bybit @ -0.5%/d, short Hyperliquid @ +0.8%/d → spread 1.3%/d
> - ...
>
> **Widest cross-venue price gaps (>0.5%)**
> - `SOL`: buy Aster @ 138.20, sell OKX @ 139.05 → 0.61%
> - ...
>
> **Top HL traders, last 1d (by realized PnL)**
> - `0xabc...` +$X (`total_realized_pnl`), ROI Y%, rank #1 — ...
> - ...

## When to skip the 3-call snapshot

- User asked about one symbol's funding or venues → `vike-perp-funding`.
- User asked about one HL trader → `vike-hl-trader-profile` (or `vike-hl-top-traders` for a ranking).
- User only wants price gaps → run `perp spreads` alone.

## Anti-patterns

- Don't quote funding rates in 8h chunks when the tool returns daily % — be consistent (daily %).
- Don't present a price gap as "free money" without fees / transfer time.
- Trading 1000-token-suffix pairs without normalizing — `1000PEPE` on Binance ≠ `PEPE` on Bybit; if the spread looks insane it's probably this.
- "Top by PnL today" is noise for swing-trader narratives; only useful for "who's hot right now". Use 7d or 30d for skill claims, and remember `1d` covers today plus the previous UTC day, not a rolling 24 hours.
- Don't present the top-traders list as "the best traders": the leaderboard rewards survivors.

## Pairs well with

- `vike-perp-funding` for the funding deep dive and one-symbol venue comparison
- `vike-perp-crowding` for funding + open interest net of price + long/short on one symbol
- `vike-hl-top-traders` for the leaderboard filters and how to read its columns
- `vike-daily-brief` for the perp section inside a cross-market brief
