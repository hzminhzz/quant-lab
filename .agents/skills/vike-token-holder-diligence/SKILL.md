---
name: vike-token-holder-diligence
description: Due diligence on who sits at the top of a token - the largest balances with their share of supply, how much of it is exchanges, contracts, labelled users, unlabelled or fresh wallets, which holders carry a risk flag, and whether they are selling now. Use when the user asks "is this token held by real wallets" / "are the top holders of X exchanges or contracts" / "how concentrated is X" / "holder quality of X" / "any fresh or flagged wallets among X holders".
allowed-tools: Bash(vike-data:*)
---

# vike-token-holder-diligence

Classify the top 50 wallets of one token by what they are. About 10 credits.

## Chain

```bash
vike-data token search ENA --chain ethereum --limit 3 --json            # 0  take the canonical address
# 2  top holders by on-chain BALANCE, with supply_pct (the `token holders` command ranks by net inflow only)
vike-data call token_holders --args '{"token_address":"0x57e114b691db790c35207b2e685d4a43181e6061","chain":"ethereum","limit":50,"sort":"balance"}'
vike-data token holders 0x57e114b691db790c35207b2e685d4a43181e6061 --chain ethereum --limit 50 --json   # 2  by net inflow: who moved the most
vike-data wallet batch <the 50 wallets> --chain ethereum --json         # 2  one call (max 100)
vike-data token transfers 0x57e114b691db790c35207b2e685d4a43181e6061 --tab sold --period 7d --chain ethereum --hide-contracts --json   # 2  are they selling now?
```

Chains: `ethereum`, `bsc`, `base`, `hyperevm`. `sort: "balance"` is not served on every chain (the error names it); fall back to the flow ranking and say so. Holder calls scan the whole chain for the token and can take tens of seconds on a busy one: run each once. Add `"exclude_exchanges": true` to the `call` arguments to drop exchange-labelled wallets.

## Reading

- **Balance ranking** adds `balance`, `value_usd`, `supply_pct`, `balance_basis`, `balance_approximate`, `candidate_coverage`. The candidates are the wallets with the largest net inflow and volume, so a holder outside both is not in the list: `candidate_coverage` is the share of total supply the candidates hold. `balance_approximate: true` = a balance could not be read or the supply is unknown. `balance_basis: "unsupported"` = that row is still ranked by flow (as for a chain's native coin).
- **Flow ranking**: `net_flow_usd` is cumulative net inflow over recorded history, not a balance. A row with huge `volume_usd` and a small net is an intermediary.
- Holder rows carry `entity` and `wallet_type`; `wallet batch` adds `confidence` and `risk_flag`.

Group the 50 rows and sum `supply_pct` (or `net_flow_usd` on the flow list) per group:

| Group | Rule | Meaning |
|---|---|---|
| Exchange / custody | `wallet_type = cex` | aggregates many retail holders |
| Contract / system | `dex`, `system` or any other non-`user` type | pools, routers, vaults: not a holder opinion |
| Labelled users | `user` with an `entity` | a named person or fund |
| Unlabelled | empty | unknown, say so |
| Fresh | `first_seen` within 7 days | new money; a launch-day cluster is a flag |
| Risk-flagged | non-empty `risk_flag` | quote the flag verbatim |

Also read: `tx_count` 1-2 with a large balance = an airdrop or OTC receiver that may never have sold; `last_active` months old = dormant. On the `--tab sold` list, a top holder among the net sellers is the signal; `--hide-contracts` leaves out the pools and routers we know of.

## Answer template

> Top 50 by balance hold `X%` of supply (candidates cover `C%`): exchanges `A%`, contracts `B%`, labelled users `D%`, unlabelled `E%`; `N` wallets first seen in the last 7 days hold `F%`; `K` wallets are risk-flagged (`<flags>`). `<M>` of them are net sellers this week. `<Balances approximate / flow ranking only on this chain: say so.>`

For what one wallet holds across its tokens: `vike-data wallet balances <addr> --chains <chain>` (3 credits, cap at 5 wallets).

## Anti-patterns

- Don't present the top-50 sum as the token's full concentration: the list is candidates, not every holder.
- Don't call a token a rug or safe from this alone. It is one signal.
- Don't count the exchange group as "retail" or as one holder: it is custody for many.

## Pairs well with

- `vike-token-holders` (the flow list), `vike-token-transfers` (who is buying or selling now), `vike-wallet-who-is` on any single holder
- `vike-wallets-related` when several unlabelled top holders look like one cluster
