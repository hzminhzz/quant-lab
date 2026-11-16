---
name: vike-wallet-label-provenance
description: Multi-source label cascade for an Ethereum wallet — shows what each upstream label source AND our in-house heuristic classifier says about an address, with consensus entity/type and per-source attribution. Use when the user asks "what do the label sources say about 0x..." / "is this address labeled" / "why is this wallet labeled X" / "do the sources agree on this label".
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-label-provenance

The headline differentiator: we don't just show a label, we show **which sources agree** and how confident the consensus is.

## Command

```bash
vike-data labels <address> [--json]
```

Ethereum wallets only. For another chain, or many addresses, use `vike-data wallet batch --chain ethereum|bsc|base|hyperevm`, which reads each chain's own label table. For "who is this wallet" as a whole (labels, ENS, chain, exchanges, counterparties) use `vike-wallet-who-is`.

## Example

```bash
vike-data labels 0x28C6c06298d514Db089934071355E5743bf21d60     # Binance Hot Wallet 14
```

## Output shape

```json
{
  "address": "0x...",
  "found": true,
  "consensus": {
    "entity": "binance",
    "wallet_type": "cex",
    "cex_product": "retail",
    "user_subtype": "active",
    "risk_flag": null,
    "confidence": "medium"
  },
  "sources": [
    { "source": "provider_a", "label": "Binance: Hot Wallet (0x28C)", "entity": "Binance", "category": "Hot Wallet" },
    { "source": "provider_b", "label": "Binance 14", "entity": "binance", "category": null },
    { "source": "provider_c", "label": "Binance: Hot Wallet", "entity": "binance", "category": "exchange" },
    { "source": "provider_d", "label": "binance", "entity": "binance", "category": null },
    { "source": "heuristic:cold_t1", "label": "binance", "confidence": 0.94 }
  ],
  "source_count": 5
}
```

Sources are anonymised on the wire (`provider_a`, `provider_b`, ...). There are at most 6 raw sources (Arkham, OLI, Blockscout, Dune, the MEW darklist, Etherscan tags), plus the heuristic classifier as a `heuristic:*` source. `source_count` includes the heuristic.

## Reading

- **4+ raw sources agree on the entity** → very high confidence; ship the label without caveats. Six is the ceiling, and most labelled wallets have 1-3.
- **2-3 sources agree on the same entity** → high confidence
- **Only 1 source (± heuristic)** → moderate; mention "labeled by one source"
- **Only the heuristic** → low; mention "predicted by heuristic"
- **`found: true` with no sources** → the wallet is in our label table but no raw source labels it
- **`found: false`** → every source was read and none knows the address, and it is not in our label table (`found` is true when ANY source knows it). It does NOT prove the wallet is clean or new: our sources do not cover every wallet. Say "no label source knows it", not "clean".

Early answers (before 2026-10-19) carried only the Arkham-derived source and the heuristic, so older transcripts undercount sources.

## Anti-patterns

- Don't quote any single provider's category field as "the category" — provider taxonomies vary; the `consensus.wallet_type` is the unified field.
- A `risk_flag` of `scam` / `sanctioned` / `phishing` is high-stakes — always mention it explicitly, never bury it.
- Don't use this for a BSC, Base or HyperEVM address: the consensus reads Ethereum tables only (only source `provider_e`, Polymarket wallets and their funders, answers on any EVM chain).
- `consensus.wallet_type` can be wrong when sources disagree (the entity "vitalik buterin" read `dex` because one source says sushiswap): read the `sources` before trusting the type.

## Pairs well with

- `vike-data wallet summary <addr>` — for the flow metrics of the labeled wallet
- `vike-data wallet batch <addr...>` — label many addresses, or another chain
