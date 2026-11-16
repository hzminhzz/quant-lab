---
name: vike-token-transfers
description: Top buyers or top sellers of a specific token over a window, with net token and USD flow per wallet. Use when the user asks "who bought X" / "who sold X" / "is whale Y buying Z".
allowed-tools: Bash(vike-data:*)
---

# vike-token-transfers

## Command

```bash
vike-data token transfers <token_address>
  [--tab bought|sold]            # default sold; ONE side per call
  [--period 1h|24h|7d|30d]       # default 24h
  [--chain ethereum|bsc|base|hyperevm|all]
  [--hide-contracts]             # leave out the pools, routers and other contracts we know of
  [--json]
```

There is no `--limit`: every call returns the top 10 wallets for that side. For both sides, call twice.

## When to use

- "Who's accumulating X?" → `--tab bought --period 24h --hide-contracts`
- "Who's dumping X?" → `--tab sold --period 24h --hide-contracts`
- "Smart money on X this week" → `--tab bought --period 7d`, label the addresses with `vike-data wallet batch`

## Output columns

Each row is one wallet: `address`, `bought`, `sold`, `net_tokens`, `usd_spent`, `usd_received`, `net_usd`, `chains`. Amounts are whole tokens and plain USD. The remaining fields (`addr_short`, the `*_fmt` strings, `net_usd_raw`, `net_bar_pct`) are pre-rendered for a UI: ignore them.

There is no `qty`, `usd_value`, `label` or `tx_count` column. To label an address use `vike-data wallet batch`.

## Reading tips

- **The rows are the top 10 NET buyers (`--tab bought`) or net sellers, ranked by net USD.** Wallets that only pass the token through (bought ≈ sold) are not listed, but a row can still be a pool, vault or settlement contract: `--hide-contracts` leaves out the ones we know of, and `wallet batch` labels the rest. `net_tokens` (bought − sold) is the net in whole tokens.
- **Signs differ by convention.** `net_usd = usd_received − usd_spent`, so a net buyer has `net_tokens > 0` and `net_usd < 0`, and a net seller has `net_usd > 0`. Do not read a positive `net_usd` as "bought".
- The two sides of the same period are separate top-10 lists. The same wallet in both = market maker / churn, not a signal.
- The window ends at the token's newest PRICED row, not at the current time. For an illiquid token it can be hours behind "now".
- Many small transfers from labeled exchanges = retail flow via that CEX.
- The native coin appears as `0x0000000000000000000000000000000000000000` in the screener and has no buyers here. Pass a contract address.

## Anti-patterns

- Don't call a contract row a buyer: use `--hide-contracts`, then label what is left.
- Don't request `--chain all` unless you really need cross-chain: it maps the address to the token's address on each chain and is slower.
- 1h period is sparse for low-liquidity tokens. Use 24h as the default starting window.
- Don't treat the 10 rows as the token's whole market: they are the top 10 net movers on one side.

## Pairs well with

- `vike-data wallet batch <addr...>` to label the rows and drop CEX / router rows
- `vike-data wallet summary <addr>` to profile any interesting buyer/seller
- `vike-token-smart-money-trend` to put the buyers next to the smart-money cohort's flow over time
