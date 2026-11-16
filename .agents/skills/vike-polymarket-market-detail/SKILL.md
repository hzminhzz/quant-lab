---
name: vike-polymarket-market-detail
description: Read one Polymarket market by condition_id - question, outcomes and their token ids, resolution, an hourly probability series, the top 30 holders of each side, the latest 100 trades with wallets, and trade statistics. Use after vike-polymarket-markets when the user asks about one specific market, its holders or what its token ids are.
allowed-tools: Bash(vike-data:*)
---

# vike-polymarket-market-detail

## Command

```bash
vike-data polymarket market <condition_id> [--json]     # 2 credits
```

`condition_id` is `0x` + 64 hex digits, from `vike-data polymarket markets` (or `vike-data search <words or polymarket.com link>`). It is NOT a token id.

## Example

```bash
vike-data polymarket markets --category Crypto --limit 5 --json
#   → pick a condition_id from the output, then:
vike-data polymarket market 0xbac62ac581be240787167755692fda4e07b3f42fb24f61a5d73c06731f8ed158 --json
```

## What it returns

- Metadata: `question`, `slug`, `category` (can be an empty string), `end_date`, `active`, `closed`, `accepting_orders`, `volume_24h`, total volume, `liquidity`.
- `outcomes` (labels) and `clob_token_ids`, in the same order: these token ids feed `candles polymarket`, `polymarket orderbook`, `market-stats` and `tape`. Match outcomes to ids by position HERE and by label everywhere else: `polymarket markets` can list the outcomes in another order.
- `resolution`: `null` while unresolved. Its `resolved_at` can repeat `end_date`; the on-chain time is `resolved_ts_ms` in `vike-data polymarket settlement <condition_id>`.
- `yes_series` / `no_series`: hourly `mid` for the first and second outcome (named yes/no even on Up/Down or team markets).
- `yes_holders` / `no_holders`: up to 30 wallets per side with `proxy_wallet`, `net_shares`, `cost_basis`, `volume_usd`, `trades`.
- `trades`: the latest 100, WITH `proxy_wallet`, `outcome`, `side`, `size` (shares), `price` (probability), `usd`.
- `stats`: trade and wallet counts, volume, first and last seen.

There is no live quote block (take bid/ask/mid from `polymarket markets` or `polymarket orderbook`), no smart-money block (use `polymarket smart-money`) and no fee rate.

## Anti-patterns

- Don't claim a market is "manipulated" or has "insiders" from holders or trades alone; they show who is on a side, not why.
- `end_date` passed is not resolved: a market can stay `active` with `resolution: null`. Confirm with `polymarket settlement`.
- Only the latest 100 trades are returned: for a window use `polymarket market-trades` (with wallets) or `polymarket tape` (no wallets).

## Pairs well with

- `vike-pm-market-diligence` — the full playbook (spread, depth, large prints, settlement, holders' records)
- `vike-polymarket-trader` — profile any holder's `proxy_wallet`
- `vike-polymarket-smart-money` (`--mode mispricings`) — markets where the cohort bought below the current mid
- `vike-candles` — probability history for a token id
