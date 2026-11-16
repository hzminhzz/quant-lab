---
name: vike-hl-builder-intel
description: Hyperliquid builder codes - which front-ends and bots earn the most fees, how concentrated their users are, their symbols and fee series, and which builders one wallet routes through. Use when the user asks "which Hyperliquid builder codes earn the most" / "who runs builder X and how big is it" / "builder fee revenue" / "how much of Hyperliquid volume is routed through builders".
allowed-tools: Bash(vike-data:*)
---

# vike-hl-builder-intel

A builder code lets a front-end or bot attach its address to an order and keep a slice of the fee. About 5 calls, 1 credit each.

## Chain

```bash
vike-data hl builders --view leaderboard --window 30d --sort fee --limit 10 --json     # who earns
vike-data hl builders --view stats --window 30d --json                                  # venue totals, routed share of volume
B=0x5af1b5f44207784dcb850bbb4143c5dcd1885f71
vike-data hl builders --view profile --builder $B --window 30d --json                   # by symbol, by dex, user deciles, lifetime
vike-data hl builders --view users --builder $B --window 30d --limit 10 --json
vike-data hl builders --view series --builder $B --bucket 1d --limit 30 --json
```

`search <name>` finds a builder by name (its `builders` section). Other views: `fills` (the raw routed fills, `--symbol`, `--side`). One wallet's builders: `vike-data hl wallet-activity <wallet> --view builders --window 30d`. `--window` is `1d|7d|30d|all`; page with `nextCursor` -> `--cursor`. `profile`, `users`, `fills` and `series` require `--builder`; a flag a view does not take is refused, not ignored.

## Reading

- `leaderboard` rows: `rank`, `builder`, `fee_usd`, `volume_usd`, `fills`, `users`, `revenue_per_user`, `fee_rate_bps`, `taker_pct`, `market_share_pct`, `active_days`. `--sort fee` ranks the builder's own cut, not what traders paid; the order differs from `--sort volume`.
- **Three fee columns are not interchangeable**: `fee_usd` is the builder's revenue, `trader_fee_usd` what traders paid in total, `builder_routed_deployer_fee_usd` the deployer's cut on builder-routed flow only. On `fills`, `fee` is in its own `fee_token`.
- `stats.totals.builder_routed_share_pct` is the share of all fill notional carrying a builder code.
- `profile` adds `by_symbol`, `by_dex`, `user_volume_distribution` (users split into volume deciles: separates "19k users" from "one whale plus a tail") and `lifetime`. In `users`, `closed_pnl_usd` (and `--sort pnl`) is the TRADERS' realized PnL, not the builder's.
- `series` `users` is a distinct count per bucket and not additive across days; take a period total from `profile`. Builder history starts at different dates: compare `active_days` and the resolved `window` first.

## Anti-patterns

- Don't call a builder profitable or its users winners from `closed_pnl_usd`.
- Don't add daily `users` together, or compare two windows of unequal `active_days`.

## Pairs well with

- `vike-hl-top-traders`, `vike-hl-trader-profile` (one wallet's routing)
