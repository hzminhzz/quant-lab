---
name: vike-token-holders
description: Top holders of an ERC-20 token by cumulative net inflow USD, with known labels. Use when the user asks "top holders of X" / "who holds the most of token Y" / "is whale Z holding token A".
allowed-tools: Bash(vike-data:*)
---

# vike-token-holders

## Command

```bash
vike-data token holders <token_address> [--chain ethereum|bsc|base|hyperevm] [--limit N] [--json]
```

## Examples

```bash
# Resolve symbol first if needed:
vike-data token search PEPE --chain ethereum --limit 1 --json
# Then holders:
vike-data token holders 0x6982508145454ce325ddbe47a25d4ec3d2311933 --limit 20
```

## Output columns

`wallet`, `net_flow_usd` (lifetime), `volume_usd` (lifetime), `tx_count`, `first_seen`, `last_active`, `entity`, `wallet_type`

## Reading

- **Sorted descending by `net_flow_usd`** — the biggest accumulators are at the top
- A row with `entity = "Binance"` is a CEX hot/cold wallet — these aggregate millions of retail holders, not single discretionary positions
- `first_seen` earlier than the token's launch month = wallet was active before this token existed; probably an aggregator
- Two wallets with similar `net_flow_usd` but very different `tx_count`: low-tx = single buy-and-hold; high-tx = active trader

## Anti-patterns

- "Top holders" here means cumulative net inflow over our recorded transfer history — NOT current on-chain balance. A wallet that bought $10M and sold $9M shows `net_flow_usd = $1M`, even if current balance is zero. The top rows are very often intermediaries (exchange wallets, lending protocols, MEV bots) with huge `volume_usd` against a small net: read this as "who moves the most of this token". **Always call this out** when reporting top holders. For the largest current BALANCES use `vike-data call token_holders --args '{"token_address":"0x...","chain":"ethereum","limit":50,"sort":"balance"}'` (adds `balance`, `value_usd`, `supply_pct`; not on every chain), and for what one wallet holds `vike-data wallet balances <addr> --chains <chain>`.
- Don't compute a share of supply from `net_flow_usd`; the balance ranking above has `supply_pct`.
- Don't list `tx_count = 1` whales as "smart money" without context — could be an airdrop receiver who never sold.
- Pass `--chain` explicitly for anything but Ethereum (`bsc`, `base`, `hyperevm`); the default is ethereum.
- **It is slow on busy tokens**: the tool scans the whole chain for the token (tens of seconds). Run it once and don't retry in a loop.
- To classify the top holders (exchange, contract, fresh, risk-flagged) use `vike-token-holder-diligence`.

## Pairs well with

- `vike-data labels <addr>` — drill into any holder's full label cascade
- `vike-data token transfers <addr>` — see who's currently buying/selling in last 24h
- `vike-data wallet summary <addr>` — full activity profile of any holder
