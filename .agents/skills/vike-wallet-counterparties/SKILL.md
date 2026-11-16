---
name: vike-wallet-counterparties
description: Find the top trading partners of a wallet — who it sent to and received from, ranked by combined USD volume. Use when the user asks "who does this wallet trade with" / "main counterparties of X" / "find connections between wallets".
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-counterparties

## Command

```bash
vike-data wallet counterparties <address>
  [--chain ethereum|bsc|base|hyperevm]
  [--window 24h|7d|30d|90d|180d|all]
  [--limit N]   (max 50)
  [--json]
```

Chains: `ethereum`, `bsc`, `base`, `hyperevm`. `entity` / `wallet_type` come from THAT chain's label table (empty string when unlabelled). `wallet summary` names the chain it answered for; run this per chain for the others.

## Example

```bash
vike-data wallet counterparties 0xd8da6bf26964af9d7eed9e03e53415d37aa96045 --window 30d
```

## Output columns

`wallet`, `total_volume_usd`, `sent_usd`, `received_usd`, `tx_count`, `first_seen`, `last_seen`, `entity`, `wallet_type`

## Reading

- `sent_usd` / `received_usd` are from the QUERIED wallet's side: `sent_usd` is what it sent to that counterparty.
- `first_seen` is the first contact inside the window, not the first ever (widening the window moves it).
- **Sent + Received roughly equal** = active trading partner (DEX router, MM, regular counterparty)
- **Sent >> Received** = wallet pays this counterparty (deposit address, contract call)
- **Received >> Sent** = wallet receives from this counterparty (funding source, employer, airdrop)
- `entity = "Binance"` (CEX) on the top row = the wallet deposits to / withdraws from that exchange
- `wallet_type = "dex"` = router contracts (Uniswap, 1inch, Curve routers) — typical for active traders
- Unlabeled top counterparty with high volume = potentially related wallet worth investigating with `vike-data labels`

## Anti-patterns

- Don't claim two wallets are "controlled by the same person" from counterparties alone — many people deposit to the same Binance address. There is no clustering tool; use `vike-data wallet compare` (shared counterparties and tokens) and `vike-data wallet trace` as evidence, and say it is only evidence.
- Window matters: a 24h view captures recent activity but misses long-standing relationships. Default to 30d, expand to `all` for full history.
- For tokens with high churn (memecoins), top counterparties will be dominated by Uniswap routers; filter mentally for non-DEX rows for human counterparties.

## Pairs well with

- `vike-data labels <addr>` — drill into any interesting counterparty's full label cascade
- `vike-data wallet summary <addr>` — total activity of any counterparty
- `vike-data wallet balances <addr>` — see what tokens that counterparty currently holds
