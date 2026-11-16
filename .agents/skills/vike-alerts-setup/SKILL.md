---
name: vike-alerts-setup
description: Create, list, edit, and delete alerts - on-chain transfer alerts (a token, one wallet, USD thresholds; eth, bsc, base, hyperevm) Hyperliquid alerts (a wallet's fills, cohort fills, liquidations, cohort bias, liquidation proximity) and Polymarket alerts (a wallet's trades, big trades in a market, price moves, a market resolving) - and read the events they produced. Delivered by email or an HTTPS webhook. Use when the user asks "alert me when X" / "watch for big transfers" / "notify on whale moves" / "tell me when this wallet trades".
allowed-tools: Bash(vike-data:*)
---

# vike-alerts-setup

## Pre-check: what channels does the user have set up?

```bash
vike-data alerts channels --json
```

Returns enabled email + webhook channels. **A channel kind the user has no enabled channel of is dropped from a transfer alert, and a transfer alert left with no channel e-mails the account address.** Whatever the channels, every match is recorded and read with `vike-data alerts events` (kept 15 days). A Hyperliquid or Polymarket alert is delivered by `--channel email` (one mail per pass listing its events; after 10 events in a UTC day no more mail, except `hl_wallet`), by a registered webhook (`vike-alerts-webhook`; asking for one the user has not registered is an error), or only read with `alerts events`.

## CRUD

### List

```bash
vike-data alerts list --json                    # active alerts
vike-data alerts list --include-inactive --json # also paused/deleted
```

### Create

```bash
vike-data alerts create \
  --label "Big USDC moves" \
  --token USDC \
  --chain eth \
  --min-usd 1000000 \
  --channel email \
  --cooldown 60
```

Required: `--label`. Transfer alerts: `--token`, `--chain eth|bsc|base|hyperevm` (default eth), `--min-usd`, `--max-usd`, `--address 0x...` with `--address-role any|from|to` to watch ONE wallet, `--channel email webhook`.

```bash
# One wallet: every transfer it receives on base (no cooldown, no daily cap)
vike-data alerts create --label "Treasury in" --chain base \
  --address 0x1111111111111111111111111111111111111111 --address-role to --channel email
```

Hyperliquid alerts (`--type`; leave out `--chain`, `--address`, `--max-usd`; delivered by `--channel email` and/or `webhook`, or with no channel read them with `vike-data alerts events`):

| `--type` | Fires on | Flags |
|---|---|---|
| `hl_wallet` | every fill of one wallet | `--wallet` (required), `--actions open close flip increase reduce`, `--token`, `--min-usd` |
| `hl_cohort_fill` | fills by wallets in cohorts | `--pnl-cohorts "3x smart" ...` and/or `--size-cohorts "4x whale" ...` (one required), `--min-usd` >= 10000 (required) |
| `hl_liquidation` | liquidations | optional `--wallet`, cohorts, `--token`, `--min-usd` |
| `hl_cohort_bias` | a cohort index crossing a level | `--token` and `--index smi\|wsi\|smart_bias\|whale_bias\|smi_wsi_gap` (required), `--threshold`, `--band`, `--direction up\|down\|both` |
| `hl_liq_proximity` | a wallet getting close to liquidation | `--wallet` (required), `--threshold` (0-1, default 0.8), `--band` |

```bash
vike-data alerts create --label "BTC smart money flips" --type hl_cohort_bias \
  --token BTC --index smi --threshold 0 --band 0.1 --channel webhook
```

Polymarket alerts (`--type`; leave out `--chain`, `--address`, `--max-usd`; the same channels). `--wallet` is the proxy wallet the site shows at `vike.io/polymarket/wallet/<address>`; `--condition-id` is the market (`0x` + 64 hex: the id in `vike.io/polymarket/market/<id>`, or find it with `vike-data polymarket markets --view search --query <slug>`):

| `--type` | Fires on | Flags |
|---|---|---|
| `pm_wallet_trade` | every trade of one wallet | `--wallet` (required), optional `--condition-id` (one market), `--min-usd` |
| `pm_market_trade` | big trades in one market | `--condition-id` and `--min-usd` >= 1000 (both required) |
| `pm_price_move` | the market's price moving sharply | `--condition-id` (required), `--direction up\|down\|both` |
| `pm_resolution` | the market resolving | `--condition-id` (required) |

```bash
vike-data alerts create --label "Whale bets on this market" --type pm_market_trade \
  --condition-id 0x... --min-usd 5000 --channel email
```

### Edit (partial update — only fields you pass are changed)

```bash
vike-data alerts edit 42 --min-usd 5000000          # raise threshold
vike-data alerts edit 42 --active false             # pause
vike-data alerts edit 42 --channel email webhook    # both channels
vike-data alerts edit 42 --address ''               # drop the wallet condition
```

### Events (what fired, and each delivery's status, kept 15 days)

```bash
vike-data alerts events --limit 50 --json           # page on with --cursor <next_cursor>
vike-data alerts events --rule-id 42 --json
vike-data alerts resend <delivery_id>               # send one delivery again
```

### Delete (only when the user explicitly asks)

```bash
vike-data alerts delete 42                          # soft-delete (pauses, keeps history)
vike-data alerts delete 42 --hard                   # permanently remove: only on an explicit "delete it for good"
```

Never delete or hard-delete an alert on your own initiative, to "clean up" or to replace it: pause it with `edit --active false` if the user only wants it quiet.

## Notes + gotchas

- Transfer chains: **eth**, **bsc**, **base**, **hyperevm**, one per alert. For several, create several alerts.
- **A transfer alert watches a token, one wallet, or both.** Without `--address` it fires on ANY transfer of that token on that chain whose USD value crosses the thresholds. With `--address` it fires on every transfer that wallet sent (`from`), received (`to`) or either (`any`); without `--min-usd` that includes dust and unpriced tokens. "Alert me when whale 0xABCD sells" is `--address 0xABCD --address-role from`; on Hyperliquid it is `--type hl_wallet --wallet 0xABCD --actions close reduce`. There are no token price or funding alerts (a Polymarket market's price: `pm_price_move`), and delivery is email or an HTTPS webhook (Discord is off; no Telegram).
- Thresholds are in **USD value** of the transfer at fire time, not token quantity.
- **At most 200 active alerts per account**, any mix of on-chain, Hyperliquid and Polymarket; the 201st is refused at create or resume. Pausing frees a slot.
- `--cooldown` is the minimum gap between notifications of a token-wide transfer alert (default 60 min). Address alerts, Hyperliquid and Polymarket alerts notify on every event and ignore it.
- Tokens are matched by symbol (UPPERCASE). For cross-chain symbols like `USDC` that exist on multiple chains, be explicit with `--chain`.
- A transfer alert whose channels are not set up is not silent: it falls back to e-mailing the account address, and every match is in `alerts events`. Run `vike-data alerts channels` first so the user gets the delivery they expect.

## Confirmation flow with the user

Before creating an alert, summarize back what will be created:

> Creating alert "**Big USDC moves**": will fire when a USDC transfer on **eth** exceeds **$1M**, delivered via **email**, with a 60-minute cooldown. OK to proceed?

Wait for confirmation, then run `vike-data alerts create`.

## Anti-patterns

- Don't create more than 5 alerts in one batch without confirmation — users almost always over-set on the first try.
- Don't quote thresholds as token amounts ("alert when 1000 ETH moves"). The system thresholds on USD only.
- If `min-usd` is < 10000, warn the user — they'll get spammed.
