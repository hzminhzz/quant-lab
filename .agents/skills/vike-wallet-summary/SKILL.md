---
name: vike-wallet-summary
description: Aggregate metrics for a single wallet — volume, inflow, outflow, net flow, tx count, tokens traded across 24h / 7d / 30d / 90d / all windows. Use when the user gives an address and asks "what's this wallet doing".
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-summary

## Command

```bash
vike-data wallet summary <address> [--json]
```

No chain flag: one call returns every window, for one chain. To ask a given chain use the tool directly: `vike-data call wallet_summary --args '{"address":"0x...","chain":"base"}'` (`ethereum`, `bsc`, `base` or `hyperevm`).

## Output

A JSON object with a `chain` field and one block per window: `all`, `90d`, `30d`, `7d`, `24h`. Each window holds:

`total_volume`, `total_inflow`, `total_outflow`, `net_flow` (USD), `tx_count`, `tokens_traded`, `first_seen`, `last_active`.

- **One chain only, named in `chain`.** Without a chain the figures are for the more recently active of Ethereum and BSC, never summed; Base or HyperEVM answer only for a wallet with no priced Ethereum or BSC volume. Always say which chain the numbers are for; `wallet balances` shows which chain holds the value.
- **`tokens_traded` is per window** (distinct tokens moved in it). **`first_seen` and `last_active` are all-time** and repeat in every window: read them from `all`.
- A window with no activity is present and zeroed (`24h` often is), not omitted.
- Refreshed nightly: a wallet active in the last hours may not show yet.
- It carries no PnL, no holdings, no labels and no Hyperliquid data. For those: `wallet balances`, `labels` / `wallet batch`, and `hl wallet`.

## Reading tips

- **`net_flow` >> 0** = accumulator. Inflow heavy → still buying.
- **`net_flow` << 0** = distributor. Outflow heavy → still selling.
- **Volume high, `net_flow` ≈ 0** = trader/MM (churn). Look at counterparty patterns separately.
- **`tokens_traded` high (>50)** = farmer / spray-and-pray. Lower signal per token.
- **`tokens_traded` low (<5), high volume** = concentrated conviction.
- Lifetime `all` against a quiet `30d` (e.g. 900k lifetime transactions, 18 in 30d) = a long-lived wallet that is quiet now. Don't size a wallet up on `all` alone.

## Anti-patterns

- Don't claim a wallet is "smart money" based on summary alone: it has no PnL. Check labels with `vike-data labels <addr>` or `vike-data wallet batch`.
- A single big inflow can dominate `net_flow` for a window — eyeball `tx_count` to gauge whether it's one trade or many.
- Don't describe the result as "across ETH/BSC/Base/HyperEVM": it is one chain.

## Pairs well with

- `vike-data labels <addr>` / `vike-data wallet batch` to say who the wallet is
- `vike-data wallet balances <addr>` for current holdings
- `vike-data token transfers` after identifying which tokens the wallet trades
