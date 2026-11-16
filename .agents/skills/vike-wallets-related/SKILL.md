---
name: vike-wallets-related
description: Gather evidence on whether two wallets, or a wallet and its possible siblings, belong to the same entity - labels, first funders, shared counterparties and tokens with exchanges and routers removed, direct links, a depth-2 trace - with confidence tiers. Use when the user asks "are these wallets the same person" / "find wallets related to 0x..." / "is this a sybil cluster" / "does B belong to A".
allowed-tools: Bash(vike-data:*)
---

# vike-wallets-related

Evidence, never proof: no tool exposes a deposit-address owner or a cluster id. Cap the cluster at 10 wallets and ask before widening. About 20-30 credits.

## Chain

```bash
vike-data labels $A --json                                           # 1  A and B: same entity already?
vike-data labels $B --json
vike-data wallet first-funder $A --json                              # 2  earliest inbound coin and token, sender labelled
vike-data wallet first-funder $B --json
vike-data wallet compare $A $B --window 90d --limit 50 --json        # 3, up to ~20 s on busy wallets
vike-data wallet batch <shared counterparties> --json                # 2  label them, drop exchanges and routers
vike-data wallet trace $A --depth 2 --width 10 --window 90d --min-usd 1000 --json   # 4  is B in A's neighbourhood?
```

Add `--chain bsc|base|hyperevm` to every wallet command when the wallets live there (default ethereum); `labels` reads the Ethereum label table, so on other chains use `wallet batch --chain`.

## Siblings mode (only A is known)

`wallet counterparties $A --window 90d --limit 50`, label them with `wallet batch`, drop `cex`, `dex`, `market_maker` and contract rows, keep the highest-volume unlabelled or `user` wallets, then `wallet compare $A <candidate>` for the top 3-5, `wallet first-funder` on each, and `wallet trace` once. Stop at 10 wallets.

## Reading

- `compare` returns `shared_counterparties` (`wallet`, `a_volume_usd`, `b_volume_usd`, `entity`, `wallet_type`) and `shared_tokens`. The big shared rows are almost always an exchange, a router or USDT/USDC/the native coin: in a live market-maker pair the top shared counterparties were Binance, another wallet of the same firm and Coinbase, the shared tokens ETH, USDT and USDC. **Remove CEX, router and stablecoin overlap before counting anything.**
- **Direct links**: B appearing as a node of A's trace (an edge with `out_usd` or `in_usd` > 0 between them) is stronger than any overlap.
- **First funders**: the same non-exchange sender in `first_native_in` or `first_token_in` of both is a link; the same exchange hot wallet is not (everyone withdrawing from it shares it). Native rows hold top-level sends only and history starts per chain (`coverage.history_from`): no match is not evidence against.

| Tier | Evidence |
|---|---|
| High | the same entity in `labels` / `batch` for both; or a direct transfer between them AND a shared non-CEX counterparty |
| Medium | several shared non-exchange counterparties AND shared niche tokens (not stablecoins/ETH); one direct link of size; or the same non-exchange first funder |
| Low / none | overlap only through exchanges, routers, stablecoins; matching ENS style; funding from the same CEX |

State the tier and the list of what was removed.

## Anti-patterns

- Don't claim "same person" from a shared CEX hot wallet: everyone who uses that exchange shares it.
- Don't trace through exchanges or past 10 wallets; don't trace at `--min-usd 0`.
- Don't call two wallets unrelated from one chain: run the commands on the chain where both are active (`wallet balances` shows where the value is).

## Pairs well with

- `vike-wallet-who-is`, `vike-wallet-compare`, `vike-wallet-counterparties`, `vike-wallet-trace`, `vike-wallet-batch`
- `vike-polymarket-trader`: for Polymarket wallets, `polymarket wallet` returns `correlated`, the wallets trading the same markets
