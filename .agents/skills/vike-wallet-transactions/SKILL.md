---
name: vike-wallet-transactions
description: Raw per-wallet transfer list (individual transactions, not aggregates) with direction, day, USD, and token filters. Use when the user asks "show me recent transfers for X" / "last 100 transactions of Y" / "all USDC moves from this wallet" / any forensic audit query.
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-transactions

## Command

```bash
vike-data wallet transactions <address>     # alias: vike-data wallet txs
  [--chain ethereum|bsc|base|hyperevm]
  [--direction in|out|all]
  [--days N]   (max 365)
  [--limit N]  (max 500)
  [--min-usd N]
  [--token <addr>]
  [--json]
```

Chains: `ethereum`, `bsc`, `base`, `hyperevm`. The same address has a separate history per chain: pass `--chain` for each.

## Examples

```bash
# Recent in+out transfers, last 7 days
vike-data wallet txs 0xd8da6bf26964af9d7eed9e03e53415d37aa96045 --days 7 --limit 20

# Only outgoing > $10k in last 30d
vike-data wallet txs 0xabc... --direction out --min-usd 10000

# Only USDC transfers
vike-data wallet txs 0xabc... --token 0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48
```

## Output columns

`time`, `tx_hash`, `from_address`, `to_address`, `token_address`, `usd`, `direction` (in/out), `block`. The USD key is `usd`, and `usd: 0.0` means the transfer is **unpriced**, not worthless: `--min-usd` therefore also hides unpriced transfers. The native coin shows as `token_address` `0x0000000000000000000000000000000000000000`.

Sorted descending by time (most recent first).

## Reading

- High `tx_count` over short window + low `value_usd` per tx = bot / MM / contract
- Mixed in/out with similar volumes = active trader
- Many `out` to one address = sweep / deposit pattern
- One huge `in` then nothing = airdrop receiver / one-time deposit

## Anti-patterns

- Don't use this for aggregated metrics (volume, PnL) — use `wallet summary` instead, it's faster
- Don't paginate by re-running with smaller windows — limit max 500, just bump `--limit`
- Don't use `--token` on every query if you want full activity; the filter restricts to one token

## Pairs well with

- `vike-data wallet summary <addr>` — aggregate metrics over same window
- `vike-data wallet counterparties <addr>` — who they trade with most
- `vike-data wallet trace <addr>` — graph-traverse outward from this wallet
- `vike-data labels <addr>` — identify the wallet itself
