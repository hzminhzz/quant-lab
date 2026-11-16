---
name: vike-pm-market-diligence
description: Full due diligence on one Polymarket market - finding it, spread and depth from the order book, recorded activity, large recent prints, who traded and who holds each side, on-chain resolution status and smart-money context, then a live feed of its trades, position changes and price moves. Handles the condition id versus token id split. Use when the user asks "is this Polymarket market liquid" / "who is on each side of this market" / "has this market resolved on-chain" / "check this prediction market before I trade" / "diligence on this Polymarket market".
allowed-tools: Bash(vike-data:*)
---

# vike-pm-market-diligence

About 18 credits for every step with two outcomes and three holders. Run only the sections the question needs.

**Two ids, never interchangeable.** A market has one `condition_id` (`0x` + 64 hex) and one OUTCOME TOKEN id per outcome (a long decimal number). `polymarket market`, `market-trades` and `settlement` take the condition id; `orderbook`, `market-stats`, `tape` and `candles polymarket` take a token id and answer nothing useful for a condition id. Token ids come from `clob_token_ids` in `polymarket market` (same order as its `outcomes`) or `outcomes[].token_id` in `polymarket markets`. **Map outcomes to ids by their label** everywhere else: `polymarket markets` has listed `No` before `Yes` and `Down` before `Up`.

## Chain

```bash
vike-data search "fed rate cut december" --json                        # 1  `markets` section; a polymarket.com link works too
vike-data polymarket markets --view search --query fed-rate-cut --json # 1  condition_id, token_ids, neg_risk, active/closed by slug
CID=0xbac62ac581be240787167755692fda4e07b3f42fb24f61a5d73c06731f8ed158
vike-data polymarket market $CID --json                                # 2  detail: outcomes, holders, last 100 trades, stats
vike-data polymarket orderbook <token_id> --depth 5 --json             # 1 per outcome: spread and depth
vike-data polymarket market-stats <token_id> --json                    # 1  what is recorded for this token
vike-data polymarket tape --token-id <token_id> --from-ms <now-1h> --to-ms <now> --event-type trade --limit 1000 --json   # 1  large prints
vike-data polymarket market-trades $CID --from-ms <now-24h> --limit 500 --json   # 1  trades WITH wallets, newest first
vike-data polymarket settlement $CID --json                            # 1  on-chain resolution
vike-data polymarket wallet <proxy_wallet> --json                      # 2 each: the top 3 holders
vike-data polymarket smart-money --mode flows --hours 24 --json        # 2  is this market in the cohort's flows
```

## Reading

- **`market`**: `question`, `end_date`, `active`, `closed`, `accepting_orders`, `resolution`, `volume_24h`, `liquidity`, `outcomes`, `clob_token_ids`, hourly `yes_series` / `no_series` (first and second outcome, named so even on Up/Down markets), `yes_holders` / `no_holders` (30 each: `proxy_wallet`, `net_shares`, `cost_basis`, `volume_usd`, `trades`), the latest 100 `trades` with wallets, and `stats`. No live quote, no cohort block, no fee rate.
- **Resolved or not**: an `end_date` in the past only means the end has passed: a market can stay `active` with `resolution: null` and `settlement` = `[]` for weeks. `settlement` returns one row (`winning_index`, `winning_outcome`, `winning_token_id`, `payouts`, `resolved_block`, `resolved_ts_ms`) once the chain resolved it; `[]` means not resolved on chain, not that the market is open (a voided market has none). Take the resolution time from `resolved_ts_ms`, not `resolution.resolved_at`.
- **`orderbook`** rows: `as_of_ts` (ms, the snapshot actually used: can be days old for a quiet or finished market), `side`, `level` (1 = best), `price` (a probability), `size` (shares), asks before bids. Keep one row per (`side`, `level`), take level 1 on each side for the spread, sum sizes within 5 cents for depth. A side can be missing, so no spread exists.
- **`market-stats`**: `trade_volume` is in SHARES, not dollars. `total_events: 0` means never recorded: re-check the token id before calling a market dead.
- **`tape`**: `ts` ms, `price` a probability, `size` shares, `side`. The window is at most 24 hours and `--to-ms` is exclusive; page while `has_more` with `--cursor`. It holds NO wallets.
- **`market-trades`**: wallets per fill. One row is one ORDER's fill, not one match: a match is the resting `maker` rows plus one `taker` row of their summed size, so never add `size * price` over both roles. `proxy_wallet` is Polymarket's proxy, not necessarily the person's own address.
- **Holders**: `polymarket wallet <proxy_wallet>` gives `realized_pnl_usd`, `resolved_markets`, volume-weighted `hit_rate` (a fraction) and `hit_rate_ci`, plus `correlated` wallets. Quote `hit_rate` only with 10 or more `resolved_markets`. A holder's `cost_basis` against the current mid says whether they are in profit on paper.
- **Smart money** (wallets with $1k+ realized PnL on 5+ resolved markets): look for the `condition_id` in the `flows` / `mispricings` lists. Absence from a top list is not evidence.

## Watch it live

```bash
vike-data stream polymarket --channel pm.trades pm.positions pm.price_moves pm.resolutions --filter market=$CID
```

`--filter` applies to every `--channel` of the command, and `market` is the only key all four take: a `role` filter here is refused (`bad_subscription`, exit 1). To count each match once, keep the taker rows of `pm.trades` with jq: `... | jq -c 'select(.type=="event" and .event.source=="pm.trades" and .event.data.role=="taker")'`. Don't start a second `stream polymarket` for it: one key holds one connection per path, and the second replaces the first (`4410`). `pm.trades` carries the trader's wallet; `pm.positions` is each of those trades again as what it did to the wallet's position (`data.action` `open`, `increase`, `reduce`, `close` or `flip`, `shares_before`, `shares`, `avg_price`: who is entering or leaving this market), so drop `pm.trades` from the list if you only want position changes; `pm.price_moves` is a move of 0.05 or more within five minutes; `pm.resolutions` the on-chain resolution. `shares` count trades only: shares a wallet got from a split, merge or transfer are not on the tape, so a wallet that sells them shows a negative `shares`.

## Answer template

> `<question>` ends `<end_date>`; `<open | past end, unresolved | resolved on-chain to X at T>`. `<Yes/Up>` mid `<m>`; spread `<s>`, depth to 5c `<n>` shares each side (book as of `<as_of_ts>`); 24h volume `$V`, liquidity `$L`. Largest prints last hour: `<size @ price>`. Top holders: `<n>` wallets hold `<outcome>`, cost basis `<c>`; `<k>` have 10+ resolved markets (hit rates `<...>`). Cohort: `<in flows | not in the top list>`. Quotes are probabilities, not dollars.

## Anti-patterns

- Don't pass a condition id to `orderbook`, `market-stats` or `tape`, and don't call a market "dead" on zero stats.
- Don't say "insider" or "manipulated" from holders and flows: they show who is on a side, not why.
- Don't read the first row of `outcomes[]` as Yes.

## Pairs well with

- `vike-polymarket-market-detail`, `vike-polymarket-trader`, `vike-polymarket-smart-money`, `vike-candles` (probability history), `vike-pm-l2-archive` (bulk history)
