---
name: vike-wallet-batch
description: Bulk label + 30d summary lookup for up to 100 addresses in one call, per chain. Use when the user pastes a CSV of addresses, asks "enrich this list with labels", or wants to quickly screen many wallets for entity / risk / activity.
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-batch

## Command

```bash
vike-data wallet batch <addr1> <addr2> <addr3> ...
  [--chain ethereum|bsc|base|hyperevm]     # default ethereum; labels and flows are read from THAT chain's tables
  [--json]
```

Caps at 100 addresses per call. For larger lists, split into chunks.

## Example

```bash
vike-data wallet batch 0xd8da... 0xabc... 0xdef... --json
```

## Output

One row per INPUT address (preserves input order — useful when joining back to a source CSV). Each row echoes its own `wallet`, lower-cased: compare case-insensitively.

```json
[
  { "wallet": "0xd8da...", "entity": "vitalik buterin", "wallet_type": "dex", "confidence": "medium", "risk_flag": "", "net_30d_usd": -70023.25, "volume_30d_usd": 70046.75, "tx_30d": 454, "tokens_30d_count": 157, "found": true },
  { "wallet": "0xabc...",  "entity": "binance", "wallet_type": "cex", "confidence": "high", "risk_flag": "", "net_30d_usd": -5e9, "volume_30d_usd": 9e9, "tx_30d": 2000, "tokens_30d_count": 80, "found": true },
  { "wallet": "0xdef...",  "entity": null, "wallet_type": null, "confidence": null, "risk_flag": null, "net_30d_usd": null, "volume_30d_usd": null, "tx_30d": null, "tokens_30d_count": null, "found": false }
]
```

## Chain behaviour

- `--chain` picks the label table and the 30-day flow table. Before 2026-10-19 it was accepted and ignored (`bsc` and `base` returned Ethereum labels), so earlier results for those chains are Ethereum's.
- **The four 30-day fields (`net_30d_usd`, `volume_30d_usd`, `tx_30d`, `tokens_30d_count`) are `null`, not 0,** for an address we have no label for (`found: false`). Don't report null as zero activity.
- `found: false` = the address is not in that chain's label table. It is not proof the wallet is new or clean. Labels are sparse on `hyperevm`, so most rows there will be `found: false`.

## Reading

- Filter to `wallet_type = "user"` to find real-people addresses worth investigating further
- Any `risk_flag` that is non-empty → flag prominently in any user-facing report
- `entity = "binance"` + `wallet_type = "cex"` rows in a "smart money" list = false positives (CEX deposits aren't traders)
- Use it to label the output of `token transfers`, `wallet counterparties`, `wallet discover` or `token holders` in one call

## Anti-patterns

- Don't run individual `vike-data wallet summary` in a loop for >5 addresses — this batch is far faster for labels and 30d activity
- Don't pass a mix of chains in one call: pick the chain the addresses were seen on
- Don't expect lifetime data — this is a 30d window. For lifetime use `wallet summary`

## Pairs well with

- `vike-data labels <addr>` — drill into any Ethereum address with high-confidence labels (source attribution)
- `vike-data wallet compare <a> <b>` — pick two interesting batch results and diff them
