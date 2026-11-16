---
name: vike
description: "Use this skill whenever the user asks any question about on-chain crypto data, wallet behavior, token transfers, smart money, CEX perpetual futures, Hyperliquid perps, prediction markets (Polymarket), ENS, or on-chain flows — and live data from vike.io would improve the answer. This includes questions like \"who is buying X token\", \"what is wallet 0x... doing\", \"top wallets by PnL\", \"funding rate spread across Binance/Bybit/OKX\", \"largest HL perp traders this week\", \"who is winning on Polymarket\", \"what is the ENS name of this address\", \"label this wallet\", \"trace this transfer\", \"top tokens by smart-money inflow\". It is also the entry point that decides which vike-* skill answers a question (tokens, wallets, labels, perps, Hyperliquid, Polymarket, candles, alerts). Always use this skill proactively when the user's query could benefit from real-time or historical on-chain data — don't wait for the user to explicitly ask to \"use vike\". If the user asks a general on-chain question that requires synthesis across wallets/tokens/venues, route to vike first."
---

# vike — on-chain, perps and prediction-market data

This skill is for anyone who asks an AI agent about crypto data: who is buying a token, what a wallet holds, what Hyperliquid or Polymarket traders are doing right now. It tells the agent which vike tool answers which question and how to read the result, so it does not guess or invent numbers. It reads its data from vike.io and needs a free vike API key.

What the platform has: wallet and token analytics on Ethereum, BSC, Base and HyperEVM (each tool's chain list is under "What each tool really returns"), multi-source wallet labels, CEX perpetual funding and cross-venue spreads, Hyperliquid wallets, cohorts, leaderboards, liquidations, markets, builders, vaults and candles, Polymarket markets, traders, tape, order books, settlements and the Parquet archive, transfer, Hyperliquid and Polymarket alerts, and live event streams over WebSocket. 70 tools in one MCP server.

**MCP endpoint for all calls:** `https://data.vike.io/mcp` (JSON-RPC 2.0)
**Or use the CLI:** `npm install -g @vike-io/cli` → `vike-data <command>`

## What it does and who it is for

It is for traders, researchers and developers who use an AI agent to look at crypto data. With this skill the agent knows which vike tool to call for wallets, tokens, smart money, Hyperliquid perps, Polymarket markets and alerts. It also knows how to read each answer and what the tools cannot do, so it reports real numbers instead of guesses. The data comes from vike.io and a free vike API key is needed.

---

## Critical Rules

Hard rules — agents that violate these produce wrong answers. Read once, apply always.

1. **Addresses are always full 0x-hex (42 chars).** Never truncate when querying. Never display truncated when the user asks for the address — they'll copy-paste it.
2. **Perp and Hyperliquid symbols are upper-cased by the server.** Echo back what the user wrote.
3. **Windows are counted in UTC calendar days, not a rolling 24 hours.** On the token screener `24h` spans yesterday and today; on Hyperliquid `30d` is today plus the previous 30 days.
4. **Page sizes are small and differ per tool; these have no paging.** Max 20: `wallet_discover` (`size`), `polymarket_smart_money`. Max 30: `polymarket_markets`. Max 50: `token_holders`, `wallet_counterparties`, `wallet_compare`. Max 100: `token_screener`, `hl_perp_top_traders` (`size`), and 100 addresses for `wallet_batch_labels`. Max 500: `wallet_transactions`, `hl_position_match`. `token_transfers` has no limit: it always returns 10 rows. To go deeper, narrow the filter or the window instead of asking for more rows. The `hl` commands, `polymarket market-trades`, `tape`, `settlements` and `alerts events` DO page: pass the answer's next cursor back with `--cursor`.
5. **Refresh differs per tool; never promise "minutes".** `wallet_discover` and `wallet_summary` are rebuilt nightly, so a wallet active an hour ago may not show. Transfers are synced block by block. `hl_position_match` only matches positions seen within 15 minutes of the newest snapshot batch. For events as they happen use `vike-data stream` (`vike-stream`).
6. **USD amounts are floats in dollars.** `--min-usd 10000` = ten thousand dollars, not micro-USD.
7. **`--json` is always available** on CLI commands and gives stable machine-readable output. The default (human) format may change; `--json` will not.
8. **Resolve a token before asking transfer/holder questions.** `token_search` → take the canonical address → pass it to `token_transfers` / `token_holders`. Symbol-only queries lose precision on tickers that collide across chains (e.g. FUN, BEAM, USDC).
9. **`token_transfers` lists the top 10 NET buyers (`tab=bought`) or net sellers, ranked by net USD**, and a net buyer's `net_usd` is negative. A row can still be a pool, vault or router: `--hide-contracts` leaves out the contracts we know of.

---

## Step 1: Confirm Authentication

Follow this decision tree **before every session**. Do not skip to Step 2 until auth is confirmed.

```
1. Is the CLI installed? (`vike-data --version`)
   ├── YES → continue
   └── NO  → `npm install -g @vike-io/cli`

2. Is $VIKE_API_KEY set OR has the user run `vike-data init` / `vike-data login` already?
   ├── YES → run `vike-data doctor` to verify the key + endpoint reachability. Proceed.
   └── NO  → prompt the user (see Setup below).
```

### Setup

If the user has no API key yet, tell them:

> **To use vike, you'll need a free API key:**
>
> 1. Visit https://vike.io/account?tab=api-keys&utm_source=skill&utm_medium=ai&utm_campaign=cli_skill, sign in, and copy a key (starts with `vk_`).
> 2. Save it: `vike-data login --api-key vk_...` (or `export VIKE_API_KEY=vk_...`).
> 3. Verify: `vike-data doctor`.
>
> One key gets 20,000 requests per rolling hour, shared by the MCP server and the REST API.

If using the MCP server directly (without the CLI), authenticate with the key in this header:
```
X-API-KEY: vk_...
```

---

## Step 2: Route the question

Pick by the question, then read the named skill for the recipe and the traps. If two rows fit, prefer the task skill (a multi-call workflow) over the per-tool skill.

| User is asking about... | CLI / MCP tool | Skill |
|---|---|---|
| Token symbol/name → address | `vike-data token search` / `token_search` | `vike-token-search` |
| Who bought / sold a token (top 10, one side per call) | `vike-data token transfers` / `token_transfers` | `vike-token-transfers` |
| "Research token X" end to end | search → transfers (both tabs) | `vike-token-research` |
| Top holders of a token (ranked by net inflow, not balance) | `vike-data token holders` / `token_holders` | `vike-token-holders` |
| Holder quality: largest balances, share of supply, exchanges / contracts / fresh / flagged wallets | `call token_holders` (`sort: "balance"`) → `wallet batch` | `vike-token-holder-diligence` |
| Tokens trending / with the biggest inflow / smart-money inflow | `vike-data token screener` / `token_screener` | `vike-token-screener` |
| "Is smart money buying X?" / alpha wallets | screener `--smart-money-only` → transfers | `vike-smart-money-discovery` |
| Is smart money entering, holding or exiting X over 24h / 7d / 30d / 90d | screener at four windows, differenced | `vike-token-smart-money-trend` |
| One wallet's volume / inflow / outflow / tx count by window | `vike-data wallet summary` / `wallet_summary` | `vike-wallet-summary` |
| Who is this address, is it an exchange, which exchanges does it use, who funded it | `search` → `labels` → `wallet summary` → `wallet exchange-usage` → `wallet counterparties` | `vike-wallet-who-is` |
| Full picture of one wallet (holdings, flows, counterparties) | several calls | `vike-wallet-portfolio` |
| Where did the money go, did it reach an exchange (exploit, scam) | `call wallet_trace` (`direction: "out"`) → `wallet txs` → `wallet batch`; `stream onchain` | `vike-follow-the-money` |
| Do these wallets share an owner, find a wallet's siblings | `labels` → `wallet first-funder` → `wallet compare` → `wallet trace` | `vike-wallets-related` |
| Top wallets by volume / flow / tx count | `vike-data wallet discover` / `wallet_discover` | `vike-wallet-discover` |
| Individual transfers of a wallet | `vike-data wallet transactions` / `wallet_transactions` | `vike-wallet-transactions` |
| Who a wallet trades with | `vike-data wallet counterparties` / `wallet_counterparties` | `vike-wallet-counterparties` |
| Follow funds N hops out | `vike-data wallet trace` / `wallet_trace` | `vike-wallet-trace` |
| Are wallets A and B related (exactly two) | `vike-data wallet compare` / `wallet_compare` | `vike-wallet-compare` |
| Daily net-flow series (not PnL) | `vike-data wallet pnl-history` / `wallet_pnl_history` | `vike-wallet-pnl-history` |
| What a wallet holds right now | `vike-data wallet balances` / `wallet_token_balances` | `vike-wallet-balances` |
| ENS name of an address (reverse only) | `vike-data wallet ens` / `wallet_ens` | `vike-wallet-ens` |
| Label many addresses (up to 100) | `vike-data wallet batch` / `wallet_batch_labels` | `vike-wallet-batch` |
| Why is this wallet labelled X (sources) | `vike-data labels <address>` / `wallet_label_provenance` | `vike-wallet-label-provenance` |
| Funding rates, funding arb, one symbol across venues | `vike-data perp funding` / `perp_funding` | `vike-perp-funding` |
| Cross-venue price spread, "perp market overview"; CEX open interest or long/short of one symbol | `vike-data perp spreads` + `perp funding` + `perp top-traders`; `call perp_open_interest`, `call perp_long_short` | `vike-perp-overview` |
| Is X crowded: funding, open interest net of price, long/short per venue | `perp funding` + `call perp_open_interest` + `call perp_long_short` + `candles hl` | `vike-perp-crowding` |
| Top Hyperliquid traders; the full leaderboard | `vike-data perp top-traders` / `hl_perp_top_traders`; `vike-data hl leaderboard` | `vike-hl-top-traders` |
| HL share card → which wallet holds it | `vike-data perp find-position` / `hl_position_match` | `vike-hl-position-match` |
| One HL wallet: equity, positions, cohorts, risk, closed trades, funding, ledger, orders, liquidations | `vike-data hl wallet`, `hl wallet-trades`, `hl wallet-activity` | `vike-hl-trader-profile` |
| Are whales / smart money long or short X (HL cohorts) | `vike-data hl cohort-metrics <symbol>`, `hl segments`, `hl market --view indices` | `vike-hl-cohort-positioning` |
| Did cohort positioning lead price (a backtest without look-ahead) | `hl cohort-metrics --label-basis point_in_time` + `candles hl` | `vike-hl-cohort-backtest` |
| Brief on one HL symbol: price, funding, OI, cohorts, liquidations | several `hl` calls + `perp funding` | `vike-hl-asset-briefing` |
| Liquidation risk by symbol, liquidation heatmap, recent or live liquidations | `vike-data hl liquidations`; `stream hl --channel hl.liq_risk hl.liquidations` | `vike-hl-liquidation-radar` |
| Builder codes: fees, users, routed share | `vike-data hl builders` | `vike-hl-builder-intel` |
| HL open interest, vaults | `vike-data hl market`, `hl vaults`, `hl vault` | this skill |
| HL orders and order clusters, TWAPs, the ledger, leverage changes, closed trades, venue stats, HYPE holders, the Parquet archive | `vike-data hl orders`, `hl twaps`, `hl ledger`, `hl leverage-changes`, `hl wallet-trades`, `hl stats`, `hl hype-holders`, `hl archive` | this skill |
| A name, symbol, address, ENS name or polymarket.com link → what it is | `vike-data search <q>` / `search` | this skill |
| Candles: Hyperliquid perp/spot (history, or the newest bar of every symbol), Polymarket outcome token; live HL bars | `vike-data candles hl` (`--view latest`), `candles polymarket`; `stream hl --channel hl.candles` | `vike-candles` |
| Trending Polymarket markets (by 24h volume) | `vike-data polymarket markets` / `polymarket_markets` | `vike-polymarket-markets` |
| One Polymarket market | `vike-data polymarket market <condition_id>` / `polymarket_market_detail` | `vike-polymarket-market-detail` |
| One Polymarket trader | `vike-data polymarket wallet` / `polymarket_wallet` | `vike-polymarket-trader` |
| Polymarket smart-money flows / mispricings / overview | `vike-data polymarket smart-money` / `polymarket_smart_money` | `vike-polymarket-smart-money` |
| Polymarket wallet leaderboard | `vike-data polymarket leaderboard` / `polymarket_leaderboard` | this skill |
| One market's liquidity, trades with wallets, raw tape, order book, tape stats, on-chain resolution | `vike-data polymarket market-trades`, `tape`, `orderbook`, `market-stats`, `settlement` | `vike-pm-market-diligence` |
| Many resolutions; the Parquet L2 archive | `vike-data polymarket settlements`, `archive` | `vike-pm-l2-archive` |
| Who funded a wallet; which exchanges it uses | `vike-data wallet first-funder`, `wallet exchange-usage` | `vike-wallet-who-is` |
| A daily brief across perps, Hyperliquid, Polymarket and on-chain | several calls, cheap first | `vike-daily-brief` |
| Find Polymarket markets by slug, or one by condition_id | `vike-data polymarket markets --view search --query btc-updown` / `--view market --condition-id 0x...` | this skill |
| Alerts: create / list / edit / delete / events (transfer and Hyperliquid) | `vike-data alerts ...` / `alerts_*` | `vike-alerts-setup` |
| Alert delivery to an HTTPS webhook | `vike-data alerts register-webhook`, `test-webhook`, `rotate-secret` | `vike-alerts-webhook` |
| Live events: HL fills, liquidations, positions, top-10 positions, closed trades, candles, TWAPs, ledger, leverage, funding, cohort flow, bias and leverage; on-chain transfers; Polymarket trades, position changes and new markets | `vike-data stream <hl\|hl-cohorts\|onchain\|polymarket> --channel ...`; `stream info`, `check`, `events`, `connections` | `vike-stream` |
| A tool with no command of its own | `vike-data call <tool> --args '{...}'` | this skill |
| Install, auth, errors, conventions | — | `vike-core` |

**When in doubt, start with `vike-data token search` or `vike-data wallet summary`.** Both are cheap and disambiguate what the user actually meant.

### Example query routing
```
"Who bought the most PEPE in the last 24h?"          → token_search, then token_transfers (tab=bought, period=24h; the top 10 net buyers)
"What is 0xd8dA6BF...AbA96 holding?"                  → wallet_token_balances   (+ wallet_summary for flows)
"Funding rate for BTC across all perp venues"         → perp_funding            (symbol=BTC; raw per-interval rates)
"Which venue pays the most funding on ETH?"           → perp_funding            (symbol=ETH, normalise to daily yourself)
"Biggest HL perp PnL last week"                       → hl_perp_top_traders     (window=7d, sort=pnl)
"What are the hottest crypto prediction markets?"     → polymarket_markets      (category=Crypto) → polymarket_market_detail
"Why is 0x28C6... labeled 'Binance hot wallet'?"      → wallet_label_provenance (address=0x28C6...)
"What is the ENS name of 0xd8dA6BF...?"               → wallet_ens              (address=0xd8dA...; name → address is `search`)
"Alert me on WBTC transfers over $1M"                 → alerts_create           (token_symbol=WBTC, min_value_usd=1000000; fires on ANY such transfer)
"Trace funds from 0xHACKER 3 hops out"                → wallet_trace            (address=0xHACKER, depth=3, direction=out)
```

---

## Step 3: Call the tool

### Two equivalent interfaces

**A) Via CLI (recommended for shell-bound agents).** All commands accept `--json` for machine-readable output:
```bash
vike-data wallet summary 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 --json
vike-data perp funding --symbol BTC --json
```

Any MCP tool, including one with no command of its own, is one `call` away; it prints the tool's raw JSON and exits 1 when the tool refuses the input:
```bash
vike-data call hl_wallet --args '{"address":"0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045","view":"summary"}'
```

**B) Via an MCP connector (recommended when your client has one).** All 70 tools live at `https://data.vike.io/mcp`. Connect the client to that address with the `X-API-KEY` header above, then call a tool by name with its arguments, for example `wallet_summary` with `address` set to a full 0x address.

Run `vike-data schema --pretty` (or call MCP `tools/list`) to see the live arg shape for every tool. Always read the schema before constructing arguments — defaults and enum values change.

---

## What each tool really returns

### Tokens
| Tool | Returns | Not |
|---|---|---|
| `token_search` | symbol, name, address, logo for a query | no market cap |
| `token_transfers` | the top 10 net buyers (`tab=bought`) or net sellers, ranked by net USD, period 1h/24h/7d/30d: `address, bought, sold, net_tokens, usd_spent, usd_received, net_usd, chains`; `hide_contracts` drops known pools and routers | no `--min-usd`; window ends at the token's newest priced row; a net seller has `net_usd > 0` |
| `token_holders` | wallets ranked by cumulative net inflow USD, with entity/type; `sort: "balance"` (via `call`) ranks by on-chain balance and adds `balance`, `value_usd`, `supply_pct` | the balance ranking covers candidate wallets, not every holder, and not every chain |
| `token_screener` | per-token volume, net/in/out USD, `unique_holders` (wallets ACTIVE in the window, not growth), tx count | no age, liquidity or sector filter; no ascending sort; native coin shows as `0x000…000`; `symbol` is often blank |

### Wallets
| Tool | Returns | Not |
|---|---|---|
| `wallet_summary` | a dict keyed `all, 90d, 30d, 7d, 24h`, each with `total_volume, total_inflow, total_outflow, net_flow, tx_count, tokens_traded, first_seen, last_active`, for ONE chain, named in `chain` (the more recently active of ethereum and bsc; never summed) | no PnL, holdings or labels; the command has no `--chain`: `call wallet_summary` with `chain` asks one chain |
| `wallet_discover` | `{wallets, total}` ranked by volume / inflow / outflow / net_flow / tx_count / pnl (`size` ≤ 20) | `realized_pnl` is a ranking key only, not a dollar figure; no labels column; base and hyperevm serve `window=all` reliably |
| `wallet_transactions` | single transfers, newest first | no aggregates |
| `wallet_compare` | shared counterparties + shared tokens of exactly two wallets | not 3+ wallets |
| `wallet_trace` | nodes + edges; direction lives in each edge's `out_usd` / `in_usd`; `direction` `out` / `in` (via `call`) expands by where money went / came from | `from` is the side expanded first, not the sender |
| `wallet_counterparties` | top partners with sent/received USD and labels | |
| `wallet_pnl_history` | daily net inflow USD (a proxy) | not realized PnL |
| `wallet_token_balances` | holdings read from the chain at request time; the native coin is always listed | not a DeFi-position view; on base and hyperevm only mapped and priced tokens unless `min_usd=0` |
| `wallet_ens` | name for an address, or `null` | no name → address here (`vike-data search <name.eth>` searches the address a name points to) |
| `wallet_batch_labels` | entity, type, risk flag + 30-day flow fields per address, in input order, from that chain's tables | the 30-day fields are `null` for an address with no label |
| `wallet_first_funder`, `wallet_exchange_usage` | the earliest inbound coin and token transfers with the senders' labels; the exchanges a wallet sent to and received from | not proof of who funded it or owns it |

Chains: every token and wallet tool takes `chain` = `ethereum` | `bsc` | `base` | `hyperevm` (default ethereum; `token_transfers` also `all`), and `wallet_token_balances` scans all four by default. Exceptions: `token_screener` smart-money mode and `smart_inflow` sort are `ethereum` only (a 400 elsewhere); `wallet_label_provenance` reads the Ethereum label table; labels are sparse on hyperevm; alerts and the stream spell Ethereum `eth`. Arbitrum, Bitcoin, Solana, Tron and other non-EVM chains are not served: say so.

### Labels
`wallet_label_provenance` returns the raw source labels (at most 6 sources plus our heuristic classifier) and a consensus entity/type. When an answer hinges on "is this really a CEX hot wallet?", call it rather than trusting one source. Every source is read on every call, and `found` is true when ANY source knows the address; `found: false` means none does and the wallet is not in our label table. It is not proof the wallet is clean.

### CEX perps
| Tool | Returns |
|---|---|
| `perp_funding` | with `symbol`: raw per-venue `funding_rate` + `interval_h` (daily % = rate × 24 / interval_h × 100); without: up to 5 pre-ranked arb rows (`long_exchange`, `short_exchange`, `spread_daily_pct`) |
| `perp_spreads` | up to 10 cross-venue price-gap rows (≥ 0.5%); `symbol` only filters that global top 10, so it is often empty |
| `perp_open_interest` (`call`) | `oi_usd` per venue (USD notional, moves with price), newest print or an hourly series (`hours` 1-168); `stale_venues` are never in a total |
| `perp_long_short` (`call`) | `long_share`, `short_share` (fractions), `long_short_ratio` per venue, each with its `basis` (what the venue counts); about 40 major symbols |

These two have no command: `vike-data call perp_open_interest --args '{"symbol":"BTC","hours":24}'`. None returns mark prices or bid/ask per venue. "Spread" here is perp vs perp across venues, not spot vs perp.

### Hyperliquid
| Tool | Returns |
|---|---|
| `hl_perp_top_traders` | up to 100 wallets (`size`, default 20): `total_realized_pnl`, `roi_pct`, `win_rate_pct`, `max_drawdown_pct` (all percents; 12.5 = 12.5%), `trade_count`, `total_volume`. ROI = window realized PnL / CURRENT account value; drawdown is account-level and all-time |
| `hl_position_match` | wallets whose open position matches a share card (`symbol`, side, leverage, entry) |
| `hl_ohlcv` | perp or spot candles |
| `hl_wallet` (`vike-data hl wallet`) | one wallet: equity, open positions, cohort labels, risk metrics, lifetime flow; `--view trades_summary` for closed-trade stats |
| `hl_wallet_activity` (`hl wallet-activity`) | one wallet's fills, funding, ledger or builder routing, newest first, paged with `--cursor` |
| `hl_cohort_metrics` (`hl cohort-metrics <symbol>`) | hourly long/short notional per cohort of one symbol; `bias` is a fraction -1..+1. For a backtest pass `--label-basis point_in_time` |
| `hl_segments` (`hl segments`) | the cohort labels (`--view cohorts`), one cohort now (`summary`), the symbol x cohort board (`heatmap`), one cohort hour by hour (`history`) |
| `hl_leaderboard` (`hl leaderboard`) | traders on 20 metrics; `--min-win-rate` is a FRACTION here (0.6), a percent in `perp top-traders` |
| `hl_liquidations`, `hl_market`, `hl_builders`, `hl_vaults`, `hl_vault` | liquidations / risk / heatmap; open interest, symbol series, positions, indices, large fills, an hourly panel of up to 64 symbols; builder codes; vaults |
| `hl_orders`, `hl_twaps`, `hl_ledger`, `hl_leverage_changes` | resting orders at one instant, one wallet's orders, order clusters; the cross-wallet feeds (follow one with `--order asc` and each `nextCursor`, stop on an empty page) |
| `hl_wallet_trades`, `hl_stats`, `hl_hype_holders`, `hl_archive` | one wallet's closed trades; hourly venue equity / open interest / volume; HYPE holders; the daily fills and positions Parquet files (`--view url` = a 7-day link) |

Every `hl` command takes `--view` (the first view is the default) and only the flags that view uses: a flag the view does not take is refused by name, not ignored.

### Candles
`hl_ohlcv` (Hyperliquid perp or spot, 1m–1d, built from HL's own fills, also HIP-3 builder DEXs) and `polymarket_ohlcv` (ONE outcome token, price = probability 0–1, needs a `token_id`, not a condition_id). `candles hl --view latest` is the newest 1-minute bar of every symbol; live closed bars come from `vike-data stream hl --channel hl.candles --filter coin=BTC --filter interval=1m`. There is no candle source for CEX tickers or an arbitrary ERC-20.

### Polymarket
| Tool | Returns |
|---|---|
| `polymarket_markets` | markets ranked by 24h volume only (`volume_24h`, `liquidity`, `ended`, `outcomes[]` with `token_id`, `mid`, `bid`, `ask`) |
| `polymarket_market_detail` | one market: outcomes with `clob_token_ids`, volume, liquidity, an hourly series per outcome, the top 30 holders per side, the last 100 trades with wallets, tape stats, resolution status; no live quote and no smart-money block |
| `polymarket_wallet` | one trader: realized PnL, volume-weighted `hit_rate`, `resolved_markets`, positions |
| `polymarket_smart_money` | MARKETS where the profitable-wallet cohort is flowing (`flows`) or bought below the mid (`mispricings`); not a list of top traders |

### Alerts
Create + manage on-chain transfer alerts, Hyperliquid alerts and Polymarket alerts, delivered by email or an HTTPS webhook.

| Tool | Does |
|---|---|
| `alerts_list`, `alerts_list_channels` | list rules / enabled channels |
| `alerts_create`, `alerts_edit`, `alerts_delete` | manage a rule: transfer (token and/or one `address`, chain `eth`/`bsc`/`base`/`hyperevm`, USD thresholds) or Hyperliquid (`--type hl_wallet`, `hl_cohort_fill`, `hl_liquidation`, `hl_cohort_bias`, `hl_liq_proximity`) or Polymarket (`--type pm_wallet_trade`, `pm_market_trade`, `pm_price_move`, `pm_resolution`) |
| `alerts_events`, `alerts_resend_delivery` | what fired and each delivery's status (15 days); send one again |
| `alerts_register_webhook`, `alerts_test_webhook`, `alerts_rotate_webhook_secret` | an HTTPS webhook (HMAC-signed deliveries): register, test, rotate its secret |

A token-wide transfer alert fires on ANY transfer of the token above the USD threshold on that chain; an address alert on every transfer of that wallet. Every match is recorded in `alerts events` (15 days) whatever the channels; a transfer alert left with no enabled channel e-mails the account address. Hyperliquid and Polymarket alerts go to email (one mail per pass, at most 10 a day except `hl_wallet`), a webhook, or are read with `alerts events`. An account has at most 200 active alerts in all (any mix of on-chain, Hyperliquid, Polymarket); pausing frees a slot. There is no token price or funding alert (a Polymarket market's price moves: `pm_price_move`), no Discord (off) and no Telegram delivery. For a live feed without a rule, use `vike-data stream`.

---

## Cost & rate limits

Credits are an accounting figure only; they never block a call. The one limit is 20,000 requests per rolling hour per key, shared by MCP and REST.

- **0 credits:** `token_search`, `alerts_list`, `alerts_list_channels`.
- **1:** `wallet_ens`, `wallet_label_provenance`, `perp_funding`, `perp_spreads`, `perp_open_interest`, `perp_long_short`, `search`, every `hl` command, `hl_ohlcv`, `polymarket_markets`, `polymarket_ohlcv`, the Polymarket tape, order book, stats, trades, settlement and archive calls.
- **2:** most others, `wallet_summary` among them.
- **3:** `token_screener`, `wallet_compare`, `wallet_token_balances`, `alerts_create`.
- **4:** `wallet_trace`.

## What vike does NOT do

- **No NFT data** — out of scope by design.
- **No on-chain trading / signing** — read-only platform, no execution.
- **No ENS name → address in `wallet ens`** (`vike-data search <name.eth>` does it), no candles for CEX tickers or arbitrary ERC-20 contracts.
- **No non-EVM chains** (Bitcoin, Solana, Tron, Monero, Zcash, Dash) and no Arbitrum.

If the user asks for any of the above, say so directly rather than proposing a workaround.
