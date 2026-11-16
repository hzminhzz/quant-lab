---
name: vike-polymarket-smart-money
description: Smart-money signals on Polymarket, plus a one-pass market overview. Two modes — flows (markets with largest net cohort inflow) or mispricings (markets where the cohort bought meaningfully below the current mid); the overview combines them with trending markets. Use when the user asks "what is smart money buying on Polymarket", "any prediction-market alpha", "give me a Polymarket overview" or "what's happening in prediction markets today".
allowed-tools: Bash(vike-data:*)
---

# vike-polymarket-smart-money

The "smart-money cohort" is the set of wallets with at least $1,000 of realized PnL, at least 5 resolved markets and at least $5,000 of volume. The tool reports what that cohort is doing **in markets**. It does not return a list of top traders: for one trader use `vike-polymarket-trader`.

## Command

```bash
vike-data polymarket smart-money --mode flows|mispricings [--hours N] [--category C] [--limit N] [--json]
# --hours 1-720 (default 24); --limit default 10, max 20; category names are exact
```

## Mode A — flows (default)

```bash
vike-data polymarket smart-money --mode flows --hours 24 --category all
```

Markets with the largest **net buy** from cohort wallets in the last N hours. `net_inflow = buy_inflow − sell_outflow`, in dollars (not shares). Useful for "where is smart money concentrating today".

Output: `condition_id`, `title`, `slug`, `smart_wallets` (cohort count), `buy_inflow`, `sell_outflow`, `net_inflow`, `trades`, `last_ts`, `yes_mid` (null when the market has no recorded mid: absent data, not zero). Best first by `net_inflow`.

## Mode B — mispricings

```bash
vike-data polymarket smart-money --mode mispricings --hours 24 --category Crypto
```

Markets where cohort wallets recently bought meaningfully **below** the current mid — the market has since moved toward the cohort's entry. A market appears only if the cohort put in at least $500 and the mid is at least 0.03 in their favour.

Output: `condition_id`, `title`, `slug`, `smart_wallets`, `last_buy_ts`, `total_buy_value`, `side`, `avg_buy_price`, `current_mid`, `delta`, `buy_value`. Best first by `delta`.

- `delta = current_mid − avg_buy_price` for the side they bought, as a probability difference (0.05 = 5 cents). It records where the cohort was early; it is not a forecast.
- `side` is `YES` (the first outcome) or `NO` (the second), not literally Yes/No: on a two-team or Over/Under market take the names from the market.

## Market overview (3 parallel calls)

For "give me a Polymarket overview" / "what's happening in prediction markets today":

```bash
vike-data polymarket markets --category all --limit 10 --json &
vike-data polymarket smart-money --mode flows --hours 24 --json &
vike-data polymarket smart-money --mode mispricings --hours 24 --json &
wait
```

> **Polymarket snapshot (last 24h)**
>
> **Top 5 trending markets by volume**
> - `<question>` — Yes mid `<m>` (from `outcomes[]`), `volume_24h` `$X` — ends `<end_date>`
>
> **Top 5 smart-money inflows**
> - `<title>` (`<slug>`) — `<smart_wallets>` cohort wallets, `net_inflow` `$X`, `yes_mid` `<m>`
>
> **Top 5 mispricings**
> - `<title>` (`<side>`) — cohort `avg_buy_price` `<p>`, `current_mid` `<m>`, `delta` `<d>`, `buy_value` `$X`

Trim aggressively if the user asked a narrower question. About 5 credits for the three calls.

## Reading

- **Flows**: high net inflow + low `smart_wallets` count = concentrated bet (few wallets, big size). Higher conviction signal than diffuse inflow across 50 wallets.
- **Mispricings**: a large `delta` is interesting only when paired with a cohort that has a history in that category. Cross-check with `vike-polymarket-trader` on the top wallets.
- **Short-dated markets dominate.** Five-minute "up or down" markets resolve fast and show huge deltas: pass `--category` (or skip those slugs) when you want slower markets. Very short `--hours` windows (an hour or two) are noisy for the mispricing read.
- The category also scopes the cohort. An unknown category spelling returns an empty list, not an error.

## Live

```bash
vike-data stream polymarket --channel pm.positions --filter market=<condition_id> --filter min_usd=1000   # who opens, adds to, reduces or closes in a flagged market
```

`min_usd` is the size of the trade. Each event names the wallet (`subject.wallet`), `data.action`, `shares` and `avg_price`, and carries that wallet's `labels.pnl_tier` and `volume_tier` (its Polymarket tier, not membership of the cohort above). To follow chosen wallets instead, `--filter wallet=0x...` (see `vike-polymarket-trader`). Unfiltered it is 60,000 to 150,000 events an hour (measured 2026-11-02): always filter by `market` or `wallet`. Fields, timing and the one-connection rule: `vike-stream`.

## Anti-patterns

- Don't treat "smart-money cohort" as omniscient. They're filtered for past performance — survivorship bias applies, especially in fast-moving political markets.
- Mispricings can be slow-to-resolve. A 5% mispricing on a market resolving in 3 months might be efficient pricing for tail risk, not edge.
- Don't combine the cohort definition with strict ROI filters — the cohort is built on realized PnL, not Sharpe.
- Don't report all three overview sections if the user asked a narrower question; trending markets that closed earlier today still rank by 24h volume, so check `ended` / `end_date` before recommending a position.

## Pairs well with

- `vike-polymarket-markets` — the trending list without the cohort view
- `vike-polymarket-market-detail` — drill into a flagged market
- `vike-pm-market-diligence` — spread, depth, holders and resolution of a flagged market
- `vike-polymarket-trader` — profile the top wallets active in flagged markets
