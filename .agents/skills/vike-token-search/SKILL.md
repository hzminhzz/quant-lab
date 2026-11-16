---
name: vike-token-search
description: Resolve a token symbol or name into the canonical contract address + chain + metadata. Use this BEFORE any other token command — most tools need the address, not the symbol.
allowed-tools: Bash(vike-data:*)
---

# vike-token-search

Symbol/name → address resolver.

## Command

```bash
vike-data token search <query> [--chain ethereum|bsc|base|hyperevm] [--limit N] [--json]
```

## Example

```bash
vike-data token search PEPE --chain ethereum --limit 5
# →  symbol  name    address                                     chain
#    PEPE    Pepe    0x6982508145454ce325ddbe47a25d4ec3d2311933  ethereum
#    PEPE2   ...     0x...                                       ethereum
```

## When to use

- Any time the user mentions a token by symbol — always confirm the address before downstream calls.
- `--chain hyperevm` searches a small registry (about 50 tokens), so no match may only mean the token is not in it. For a name across every chain, plus live price and the vike.io page, `vike-data search <name>` (its `tokens` section).
- The default chain is **ethereum**; if the user asks "what's the address of CAKE", default to **bsc** if the symbol is bsc-native.

## Anti-patterns

- Do not hardcode addresses you "remember" — symbols collide across chains and tokens. Always resolve.
- If `--limit 1` returns the wrong token, increase to 5-10 and pick the highest-volume / canonical match. The first row is not always the canonical one.

## Pairs well with

- `vike-data token transfers <address>` after resolution
- `vike-token-research` for a full multi-call report
