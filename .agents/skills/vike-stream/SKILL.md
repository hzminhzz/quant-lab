---
name: vike-stream
description: Live events over WebSocket with `vike-data stream` - Hyperliquid fills, liquidations, positions (with leverage and liquidation price), top-10 positions per coin, closed trades, liquidation risk, order walls, closed candles, TWAPs, ledger, leverage changes and funding; HL cohort flow, indices, cohort bias and cohort leverage; on-chain transfers on eth, bsc, base and hyperevm; Polymarket trades, wallet position changes, resolutions, price moves and new markets - printed as NDJSON, resumed by cursor after any drop. Use when the user wants to "watch", "tail", "follow live", "stream" or "be notified as it happens" rather than query history.
allowed-tools: Bash(vike-data:*)
---

# vike-stream

```bash
vike-data stream <hl|hl-cohorts|onchain|polymarket> --channel <channel...> [--filter key=value ...] [--cursor <cursor>]
```

Connects to `wss://stream.vike.io/v1/stream/<path>` with the API key in the `X-API-KEY` header, subscribes once per
`--channel`, and prints one JSON object per line on stdout: every `event` frame and every `gap` frame, exactly as the
server sent them. Status lines (subscribed, reconnecting, errors) go to stderr. It runs until Ctrl-C.

| Path | Channels |
|---|---|
| `hl` | `hl.fills`, `hl.liquidations`, `hl.positions`, `hl.liq_risk`, `hl.order_walls`, `hl.candles`, `hl.twaps`, `hl.ledger`, `hl.leverage`, `hl.funding`, `hl.top_positions`, `hl.closed_trades` |
| `hl-cohorts` | `hl.cohort_flow`, `hl.indices`, `hl.cohort_bias`, `hl.cohort_leverage` |
| `onchain` | `chain.transfers` |
| `polymarket` | `pm.trades`, `pm.positions`, `pm.resolutions`, `pm.price_moves`, `pm.new_markets` |

The server decides what each path serves: `vike-data stream info` prints the current channels and filter keys, and a
channel the path does not serve is refused with `unknown_channel` and the list. The CLI has no channel list of its own.

`hl.candles` is the Hyperliquid candle stream: closed bars, filtered by `coin`, `interval` (`1m 5m 15m 30m 1h 4h 1d`),
`market` (`perp` or `spot`) and `dex` (`HL` or a HIP-3 dex). The old `wss://data.vike.io/v1/stream` is closed (410).

- `hl.positions`: the fills that change a big position. `data.leverage` and `data.liquidation_px` are the wallet's own
  at the last position capture, i.e. BEFORE that fill (`null` for an `open`; a `liquidation_px` of 0 is "none"),
  `labels.tags` its trader tags. Filters `min_leverage` (a number) and `tag` (a list) exist on this channel only.
- `hl.top_positions`: per coin of the top 50, the position that `entered`, `left` or `moved` within the top 10 (filter
  `event` takes ONE of those, `direction` one of `long` / `short`). The first capture sends all 10 of every coin as `entered`.
- `hl.closed_trades`: a finished round trip (entry and exit price, `realized_pnl`, `duration_seconds`), in batches
  every 30 minutes; unfiltered up to ~32,000 an hour (measured 2026-11-02): filter by `wallet` or `min_usd`.
- `hl.cohort_leverage` (`hl-cohorts`): once an hour per cohort of one `axis`, `perp_bias` and `equity_exposure`
  against account value; no wallet or coin, filters `axis` and `cohort` only.
- `pm.positions`: each `pm.trades` fill again as its effect on the wallet's position, `data.action` (`open`,
  `increase`, `reduce`, `close`, `flip`), `shares_before`, `shares`, `avg_price`. Filters `wallet`, `market`, `min_usd`
  (the trade's size), `action`; unfiltered 60,000 to 150,000 an hour (measured 2026-11-02): filter by `wallet` or `market`.

## Examples

```bash
# Hyperliquid BTC and ETH fills of $100k or more, by smart wallets
vike-data stream hl --channel hl.fills --filter coin=BTC,ETH --filter min_usd=100000 --filter "pnl_cohort=3x smart,2x smart"

# Live 1-minute and 1-hour BTC and ETH perp candles (closed bars)
vike-data stream hl --channel hl.candles --filter coin=BTC,ETH --filter interval=1m,1h --filter market=perp

# Every liquidation and every fill of one wallet, on one connection
vike-data stream hl --channel hl.liquidations hl.fills --filter wallet=0x1111111111111111111111111111111111111111

# USDC transfers on base of 1M USDC or more (min_amount is in tokens; min_usd would drop the unpriced events)
vike-data stream onchain --channel chain.transfers --filter chain=base --filter token=0x833589fcd6edb6e08f4c7c32d4f71b54bda02913 --filter min_amount=1000000

# Position changes at 20x or more, BTC and ETH (the wallet's leverage and liquidation price before the fill are in data)
vike-data stream hl --channel hl.positions --filter coin=BTC,ETH --filter min_leverage=20

# Every round trip one wallet closes (a batch every 30 minutes)
vike-data stream hl --channel hl.closed_trades --filter wallet=0x1111111111111111111111111111111111111111

# One Polymarket wallet opening or closing a position
vike-data stream polymarket --channel pm.positions --filter wallet=0x1111111111111111111111111111111111111111 --filter action=open,close

# Keep only the events, with jq
vike-data stream polymarket --channel pm.trades --filter min_usd=10000 | jq -c 'select(.type == "event") | .event'
```

`--filter` applies to every `--channel` of the command; a key the channel does not take is refused by the server
with the key's name. List keys (`wallet`, `coin`, `token`, `market`, `chain`, cohorts, ...) take comma-separated
values and repeat; `side`, `direction`, `event`, `axis`, `move`, `role` take one value; `true` / `false` is sent as a yes/no (`contract=false`); `min_usd`, `min_amount`, `min_move`,
`min_leverage` take a number. Channels in one command must all take every key you pass (`min_leverage` and `tag` are `hl.positions` only). Channel fields and filter keys: https://vike.io/docs/data/reference/stream-channels

## Without a socket

These are MCP calls: they open no connection, so they never replace one.

```bash
vike-data stream info --json                                    # every path, channel, filter key, limit and close code
vike-data stream check hl.fills --filter coin=BTC --filter min_usd=50000   # the server's own validator; exit 1 if refused
vike-data stream events hl.liquidations --filter coin=BTC --since-ms 1793178870000 --json   # stored events (3 days), paged by --cursor
vike-data stream connections --hours 24 --json                  # your recent connections: who closed whom (4410), events sent
```

Run `stream check` before a long-running `stream`: it shows the filter exactly as the server will apply it (with
the `hl.fills` default `min_usd` of 1000 added) or the server's error naming the bad key.

## Reading the output

- Keep the **frame's** `cursor` (the top-level one, not `event.cursor`) of the last line you handled. `--cursor <it>`
  on the next run resumes from there, up to 3 days back; older is `cursor_expired` and the stream continues from now.
- The CLI resumes by itself after any drop (restart, network, slow reader) from the last cursor of each subscription.
  Delivery is at least once: drop a repeated `event.event_id`.
- A `gap` frame (`reason: channel_idle`) means the channel was not running between `from` and `to` (nobody was
  subscribed). It does not mean nothing happened; read that window from the REST endpoints or the `hl` / `polymarket`
  commands. For what a socket missed while the channel was running, `vike-data stream events <channel> --cursor <c>`.
- `hl.fills` without `min_usd` gets a $1,000 floor; `min_usd=0` is every fill (hundreds per second).
  `chain.transfers` unfiltered is a firehose; transfers are priced after they are streamed, so `min_usd` on it drops almost every event: filter by `wallet`, `token` (with `min_amount`), `entity`, `wallet_type` or `contract=false`.

## One connection per key

On each path, one API key holds one connection; an account holds at most 3. A second program on the same key and
path takes over, and the first exits with `replaced (4410)` and does not reconnect (two programs reconnecting would
take the connection from each other forever). Give each program its own key (`VIKE_API_KEY=... vike-data stream ...`).
`vike-data stream connections` shows which of your connections closed which.
Exit 1 also on `HTTP 401` / `4401` (the key) and on a refused subscription (`bad_subscription`, `unknown_channel`).

## Anti-patterns

- Don't run two `vike-data stream` processes on the same key and path: they replace each other.
- Don't treat silence as "nothing happened" in the first minutes: an idle channel starts on demand (up to ~12 minutes
  for `hl.indices`, `hl.liq_risk` and `hl.top_positions`, up to 30 for `hl.closed_trades`; `pm.positions` takes one to
  three minutes like `pm.trades`; `hl.cohort_leverage` sends its newest hour at once, then one an hour).
- Don't use the stream for history: it keeps 3 days. Use the query commands.

## Pairs well with

- `vike-alerts-setup`: a stored rule that notifies by webhook without a running process.
- `vike-hl-top-traders`: pick the wallets to put in a `wallet` filter.
- Playbooks with a live section: `vike-hl-liquidation-radar` (`hl.liq_risk`, `hl.liquidations`, `hl.positions`), `vike-follow-the-money` (`chain.transfers`), `vike-hl-cohort-positioning` (`hl.cohort_bias`, `hl.indices`, `hl.cohort_leverage`, `hl.top_positions`), `vike-hl-trader-profile` (`hl.closed_trades`), `vike-pm-market-diligence` and `vike-polymarket-trader` (`pm.positions`).
