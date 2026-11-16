---
name: vike-wallet-discover
description: Rank wallets by volume / inflow / outflow / pnl / net_flow / tx_count across a time window. Use when the user asks "top wallets on X" / "best traders by PnL" / "biggest buyers last 7d".
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-discover

## Command

```bash
vike-data wallet discover
  [--window 24h|7d|30d|90d|all]
  [--sort volume|inflow|outflow|net_flow|tx_count|pnl]
  [--order asc|desc]
  [--chain ethereum|bsc|base|hyperevm]
  [--size N]      # default 10, max 20
  [--json]
```

**On `base` and `hyperevm` pass `--window all`**: the command sends `30d` by default, and the shorter windows there answer only while they were rebuilt in the last 2 days (otherwise a 400). `ethereum` and `bsc` take every window.

## When to use

| Question | Flags |
|---|---|
| "Top whales by volume this week" | `--sort volume --window 7d` |
| "Biggest accumulators last 30d" | `--sort net_flow --window 30d --order desc` |
| "Wallets dumping the most this 24h" | `--sort outflow --window 24h` |
| "Most active traders last 7d" | `--sort tx_count --window 7d` |
| "Best PnL" | `--sort pnl --window all`: an ordering only, see below |

## Output

An object `{ "wallets": [...], "total": N }`. `total` is the size of the matching population, not the number of rows.

Each wallet: `address`, `volume`, `inflow`, `outflow`, `net_flow`, `tx_count`, `tokens_traded`, `first_seen`, `last_active`, `realized_pnl`, `tokens_profitable`, `pnl_available`. USD columns are already plain dollars.

There is **no label column** and no `*_usd` suffix. To name the wallets, pass the addresses to `vike-data wallet batch`.

## Reading tips

- **`realized_pnl` is a ranking key, not a dollar figure.** Stored values are wrong for many wallets (a measurement found 233 of 500 sampled Ethereum leaderboard wallets at 10× or more above the rebuilt value, and the top rows can show a profit thousands of times the wallet's own volume). Use `--sort pnl` only to order wallets against each other, never quote `realized_pnl`, and never call `pnl_available: 1` a guarantee. It is computed from on-chain transfers; Hyperliquid PnL is a different dataset (`vike-data perp top-traders`, realized PnL only: open positions are not included).
- `first_seen` here is not a lifetime first-seen (it disagrees with `wallet summary`); read it as "first seen in this dataset".
- Labeled wallets first: after `wallet batch`, `entity = "binance"` / `wallet_type = "cex"` means a CEX hot or cold wallet, not a discretionary trader. Filter those out if the user wants "real traders".
- Top volume wallets are almost always bots, routers and venues. `net_flow` is the more informative sort for "who is accumulating", and even then check the wallet type.

## Anti-patterns

- `--size > 20` is silently clamped to 20, and there is no offset: the tool is a top-of-leaderboard view. Don't promise a deeper list.
- Don't build "smart money" lists on `--sort pnl` (see `vike-smart-money-discovery` for the supported route).
- Don't use this for a single wallet: `wallet summary` is the right call.

## Pairs well with

- `vike-data wallet batch <addr...>` to label the rows
- `vike-data wallet summary <address>` to drill into any interesting row
- `vike-smart-money-discovery` for the smart-money read
