---
name: vike-token-research
description: Token research playbook — search → transfers (both sides) → net-flow read → buyer/seller verdict. Use when the user asks "research <token>" or "what's happening with <token>".
allowed-tools: Bash(vike-data:*)
---

# vike-token-research

Multi-call token research workflow. It characterises WHO is moving the token (the top wallets on each side), not the token's total flow, and it has no price chart.

## Steps

1. **Resolve address** (skip if user already gave one)
   ```bash
   vike-data token search <symbol> --chain <chain> --limit 3 --json
   ```
   Pick the canonical match; capture the address.

2. **Recent buyers + sellers** (parallel calls)
   ```bash
   vike-data token transfers <addr> --tab bought --period 24h --chain <chain> --hide-contracts --json
   vike-data token transfers <addr> --tab sold   --period 24h --chain <chain> --hide-contracts --json
   ```
   Each call returns the top 10 net buyers or net sellers, ranked by net USD; `--hide-contracts` leaves out the pools and routers we know of.

3. **Read `net_tokens` and `net_usd`** (a net buyer's `net_usd` is negative). Merge the two lists by `address`, then:
   - label the rest with `vike-data wallet batch <addrs...> --chain <chain>` and drop CEX / router / contract rows if the user wants "real" flow;
   - split what is left into net accumulators (`net_tokens > 0`) and net distributors (`net_tokens < 0`) and add up each side's `|net_usd|`.

4. **Optional context** (Ethereum only): `vike-data token screener --window 7d --sort smart_inflow --smart-money-only --limit 100 --json` and look for the token's address in the rows to see whether the smart-money cohort is flowing into it.

## Verdict heuristics

| Net accumulators' USD vs net distributors' USD (24h) | Verdict |
|---|---|
| Accumulators > 2× distributors and > $1M | **Accumulation among the biggest movers** |
| Distributors > 2× accumulators and > $1M | **Distribution among the biggest movers** |
| Within 1.5× of each other | **Churn / range-bound** |
| Fewer than 3 rows left after dropping round trips, or all CEX / router | **No readable signal** — say the top wallets are intermediaries |

State the limit with the verdict: "top 10 per side", not "the market".

## Anti-patterns

- Don't compare the number of buyers to the number of sellers: both lists are always 10 rows.
- Don't run `transfers --period 30d` first — use 24h to see what's happening *now*; 30d for context only if needed.
- Don't quote the first row of `token search` blindly — if the symbol has cross-chain collisions (`FUN`, `BEAM`, `LDO`), inspect `chain` + `address` for each result before picking.
- Don't promise a price chart: there is no candle source for an ERC-20 contract address (`vike-candles` covers Hyperliquid and Polymarket only).

## Cost note

Step 1: free. Step 2: 2 calls × 2 credits. Step 3 labels: 2 credits. Total ≈ 4 calls.

## Pairs well with

- `vike-smart-money-discovery` to see whether the smart-money cohort is flowing into the token; `vike-token-smart-money-trend` for its direction over time
- `vike-token-holder-diligence` for who sits at the top (exchanges, contracts, fresh wallets, risk flags)
- `vike-perp-funding` if the token has a perp listing (BTC/ETH/SOL/major alts)
