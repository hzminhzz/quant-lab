---
name: vike-wallet-compare
description: Diff two wallets — find their shared counterparties (both transacted with these addresses) and shared tokens (both touched these tokens) within a window. Use when the user asks "are these two wallets related" / "what do X and Y have in common" / "find connections between wallet A and B".
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-compare

## Command

```bash
vike-data wallet compare <address_a> <address_b>
  [--chain ethereum|bsc|base|hyperevm]
  [--window 24h|7d|30d|90d|180d|all]
  [--limit N]   (max 50)
  [--json]
```

Compares exactly two wallets on one chain (`ethereum`, `bsc`, `base` or `hyperevm`; shared-counterparty labels come from that chain's own label table). Busy wallets are slow (about 20 s for a pair of market makers). For the full verdict with exchanges and routers removed, use `vike-wallets-related`.

## Output

```json
{
  "address_a": "0x...",
  "address_b": "0x...",
  "window": "30d",
  "shared_counterparties": [
    { "wallet": "0x...", "a_volume_usd": 50000, "b_volume_usd": 75000, "a_tx_count": 3, "b_tx_count": 5, "entity": "Binance", "wallet_type": "cex" },
    ...
  ],
  "shared_tokens": [
    { "token_address": "0x...", "a_volume_usd": 12000, "b_volume_usd": 8000, "a_tx_count": 2, "b_tx_count": 4 },
    ...
  ],
  "summary": { "shared_counterparty_count": 7, "shared_token_count": 12 }
}
```

## Reading

- Many shared counterparties + same entity types = likely operated by same person/group
- Same exchange (Binance hot wallet) as counterparty for both = trivial overlap, both users of that CEX
- Shared NICHE tokens (long-tail memes, low-vol) with similar tx counts = strong signal of coordination
- Shared infra contracts (Uniswap, 1inch) = trivial overlap, ignore
- Zero shared = wallets genuinely independent

## Anti-patterns

- **Don't infer common ownership from CEX overlap.** Millions of users deposit to the same Binance hot wallets. Shared CEX counterparty = nothing.
- **Don't claim coordination from common DEX router overlap** (Uniswap, 1inch routers). Almost every DEX user shares those.
- Filter mentally for `wallet_type IN ('user', 'unknown')` overlap if you want a meaningful relatedness signal.

## Pairs well with

- `vike-data labels <addr>` — drill into any interesting shared counterparty
- `vike-data wallet trace <addr>` — follow money outward from either wallet
- `vike-data wallet counterparties <addr>` — top partners per wallet individually
