---
name: vike-pm-l2-archive
description: Find and fetch the free Polymarket L2 archive - daily Parquet files of the full order-book events, the trade tape and top-of-book quotes, whole or cut per market family such as BTC 5-minute up/down - with their sizes, a 7-day download link, and the on-chain outcomes to join them to. Says when the API tape is enough instead. Use when the user asks "get me Polymarket order book history" / "BTC 5m up/down L2 data for last week" / "download the Polymarket trades for a day" / "data to backtest a Polymarket strategy".
allowed-tools: Bash(vike-data:*)
---

# vike-pm-l2-archive

The archive is one Parquet file per UTC day and stream, recorded since 2026-07-25, free under CC BY 4.0 (credit "Polymarket L2 archive (c) Vike (vike.io/data), CC BY 4.0"). `vike-data` lists files and resolves links; **it never downloads.** 1 credit per call.

## Chain

```bash
vike-data polymarket archive --view datasets --asset btc --tenor 5m --stream trades --date-from 2026-10-15 --date-to 2026-10-17 --json
vike-data polymarket archive --view url --stream trades --date 2026-10-16 --json                         # multi-asset file, one day
vike-data polymarket archive --view url --stream trades --asset btc --tenor 5m --date 2026-10-16 --json  # family file
vike-data polymarket settlements --slug-prefix btc-updown-5m- --from-ms <start> --to-ms <end> --limit 1000 --json   # how each market resolved
```

Streams: `book_events` (full L2: snapshots, `price_change` deltas, trades, tick-size and status markers), `trades` (taker tape), `l1_quotes` (derived top of book). A row carries `date`, `stream`, `layout` (`flat` = every market, `family` = one asset and tenor), `bytes`, `rows`, `url`. `datasets` pages with `--limit` (max 1000) and `--offset` against `total`.

## Pick the right file

- `--asset` and `--tenor` go together or not at all, and either one hides every flat file. Without them `datasets` mixes both layouts: filter on `layout`, or the same day appears twice.
- **Check `bytes` first.** Measured 2026-10-19: one day of `trades` about 12 MB; a BTC 5m family `trades` day 3-5 MB; one day of `book_events` **8.5 GB (532 million rows)**. Tell the user before any `book_events` download, and prefer the family file for one asset.
- A few hours or one token: use the API, not a bulk file: `vike-data polymarket tape --token-id <id> --from-ms ... --to-ms ... --event-type trade` (at most 24 h a call, no wallets), `vike-data polymarket market-trades <condition_id>` (with wallets) or `vike-data candles polymarket <token_id>`.

## Fetch (outside this skill's tool grant; hand the user the command)

```bash
curl -C - -o trades-2026-10-16.parquet "<url from the row>"
```

The link needs no API key and is valid for 7 days (`expires_at`): treat it as a secret, don't paste it anywhere shared. **Download the file whole and read the local copy** with DuckDB, polars or pyarrow: pointing a reader at the URL turns one file into thousands of range requests. The manifest and a small sample need no key at all: `https://data.vike.io/archive/manifest.json`, `https://data.vike.io/archive/samples/`.

## Reading the files

- The L2 tape carries no wallet addresses, only size and time. Prices are probabilities, sizes are shares.
- On the API tape a `0` in `best_bid`, `best_ask`, `price` or `size` on a row type that does not populate it means absent, not zero; check the schema in the manifest before assuming the same of a Parquet column.
- Join across files on the outcome token id; `settlements` rows carry `token_ids` and `winning_token_id` for the join to outcomes.

## Anti-patterns

- Don't promise a download from the CLI, and don't fetch `book_events` days to answer a question the API tape can.
- Don't redistribute without the attribution above.

## Pairs well with

- `vike-pm-market-diligence` (ids and one market live), `vike-candles` (probability bars), `vike-polymarket-markets`
- The Hyperliquid twin: `vike-data hl archive --view url --dataset fills --date 2026-10-16` (daily fills and positions files)
