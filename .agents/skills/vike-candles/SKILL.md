---
name: vike-candles
description: Historical OHLCV candles for a Hyperliquid perp or spot market, or for one Polymarket outcome token. Use when the user asks for a price chart, candles, OHLCV, or price history of a Hyperliquid asset or a Polymarket outcome (its probability over time).
allowed-tools: Bash(vike-data:*)
---

# vike-candles

Two candle sources, both from venues whose data is on-chain. There is **no** candle source for
CEX tickers (BTCUSDT on Binance and the like) or for an arbitrary ERC-20 contract address; say so
rather than guessing a venue.

## Command

Hyperliquid (perp or spot), built from HL's own executed fills:

```bash
vike-data candles hl <symbol>
  [--interval 1m|5m|15m|30m|1h|4h|1d]   # default 1h
  [--market perp|spot]                  # default perp
  [--dex <name>]                        # HIP-3 builder DEX; default HL (the core venue)
  [--start <iso|epoch-ms>] [--end <iso|epoch-ms>]   # half-open [start, end)
  [--limit N]                           # default 500, max 5000
  [--view export]                       # the same, limit defaults to 5000
  [--json]

vike-data candles hl --view latest [--market perp|spot] [--dex <name>]   # the newest 1m bar of EVERY symbol
                                                                         # that traded in the last hour (may still be forming)
```

Live closed bars as they happen: `vike-data stream hl --channel hl.candles --filter coin=BTC,ETH --filter interval=1m`
(also `market=perp|spot`, `dex=HL`; see `vike-stream`).

Polymarket, ONE outcome token (a probability, 0-1):

```bash
vike-data candles polymarket <token_id>
  [--interval 1m|5m|1h|1d]              # default 1h
  [--from <epoch-ms>] [--to <epoch-ms>] # both inclusive
  [--limit N]                           # default 500, max 1000
  [--json]
```

## Common queries

| Question | Command |
|---|---|
| "Hourly BTC perp candles, last week" | `vike-data candles hl BTC --interval 1h --limit 168` |
| "HYPE spot daily candles" | `vike-data candles hl HYPE --market spot --interval 1d` |
| "4h ETH, 200 bars" | `vike-data candles hl ETH --interval 4h --limit 200` |
| "Price history of the YES outcome of market X" | take the leg's `token_id` from `outcomes[]` in `vike-data polymarket markets` (or `clob_token_ids` in `vike-data polymarket market <condition_id>`), then `vike-data candles polymarket <token_id> --interval 1h` |

## Output

HL: `ts` (epoch ms, UTC), `open`, `high`, `low`, `close`, `volume`, `volume_usd`, `n_trades`, ascending by `ts`, plus `source`.
Polymarket: `ts`, `open`, `high`, `low`, `close`, `volume`, `trades`, oldest first.

## Pitfalls

- Polymarket takes an **outcome token id**, not a `condition_id`. A condition id returns nothing useful; read the token ids off `outcomes[].token_id` in `vike-data polymarket markets` or `clob_token_ids` in `vike-data polymarket market`.
- Polymarket has no cursor: a window wider than `--limit` is truncated at the **old** end. Raise `--from` to reach the recent end.
- Polymarket `--from` / `--to` are epoch **milliseconds**; HL `--start` / `--end` also accept ISO-8601.
- HL buckets are left-aligned UTC and the window is half-open.

## Pairs well with

- `vike-data polymarket market <condition_id>` — to find the token ids; `vike-pm-market-diligence` for the order book and tape of the same token
- `vike-data perp top-traders` — who is trading the asset behind the candles; `vike-hl-cohort-positioning` for how cohorts are positioned in it
- `vike-hl-cohort-backtest` — cohort positioning against these candles, without look-ahead
