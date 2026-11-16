---
name: vike-wallet-portfolio
description: Holdings-and-activity snapshot of one wallet — ENS, labels, token balances across Ethereum, BSC, Base and HyperEVM, recent flow and top counterparties in one report. Use when the user gives a wallet address and asks for a complete picture ("full report on X" / "everything about this wallet" / "wallet snapshot" / "what does this wallet hold and do").
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-portfolio

Multi-call playbook for a complete wallet snapshot. It reports what the tools return: identity, token balances, flows and counterparties. There is **no DeFi-position data** in any tool, so never report protocol positions. For identity alone ("who is this", "is it an exchange") use `vike-wallet-who-is`.

## Workflow (run in parallel where possible)

```bash
ADDR=0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045

vike-data wallet ens     $ADDR --json &     # 1 credit
vike-data labels         $ADDR --json &     # 1 credit
vike-data wallet summary $ADDR --json &     # 2 credits - names its `chain`
vike-data wallet balances $ADDR --json &    # 3 credits - all four chains; add --chains to narrow (faster)
wait
vike-data wallet counterparties $ADDR --chain <summary.chain> --window 30d --limit 10 --json   # 2 credits
```

Then label the counterparties with an empty `entity` in one call: `vike-data wallet batch <cp1> <cp2> ... --chain <summary.chain> --json` (2 credits). A top counterparty that is a CEX tells you which exchange the wallet uses; an unlabelled high-volume counterparty is worth a `wallet compare` or `wallet trace`.

## Report template

> **`<ENS or 0x...>` — wallet snapshot**
> - **Identity:** `<ens.eth>` · `<entity (from labels)>` · `<wallet_type>` (confidence: `<H/M/L>`). If `labels` says `found: false`, say "not in our label table".
> - **Priced token holdings:** `$X` (floor: unpriced tokens are excluded; on Base and HyperEVM only mapped and priced tokens show) across `<chains_scanned>`. Top 3 by USD: `<symbol1 $X on chain>`, ... Native balance: `<n ETH/BNB/HYPE>`.
> - **30d flow on `<chain>`:** `+/-$Z` net (`summary["30d"].net_flow`), one chain.
> - **Activity:** `<tx_count>` transfers in 30d, `<tokens_traded>` tokens traded all-time.
> - **Top counterparties:** `<entity or address>` · `$volume`, ...
> - **Vibe check:** `<saver / trader / farmer / institutional / inactive / fresh>`

## Vibe-check rubric

| Pattern | Label |
|---|---|
| `wallet_type = "cex"` or `"market_maker"` | **Institutional** |
| 80%+ of priced holdings in stablecoins, low tx count | **Saver / dry powder** |
| `tokens_traded` 50+, low average holding USD | **Airdrop farmer** |
| High tx count, balanced inflow / outflow | **Active trader** |
| Big priced holdings, low tx count | **HODLer** |
| Zero `tx_count` in `30d` | **Inactive** |
| `first_seen` (from `all`) < 30d ago | **Fresh wallet** |

## Cost

6 calls, about 11 credits. Use sparingly — only when the user wants the FULL picture, not a single dimension.

## When to use a focused skill instead

- "Who is X" / "is it an exchange" → `vike-wallet-who-is`
- "Just labels for X" → `vike-wallet-label-provenance`
- "What does X hold" → `vike-wallet-balances`
- "Top counterparties of X" → `vike-wallet-counterparties`
- "X's Hyperliquid account" → `vike-hl-trader-profile`

## Pairs well with

- `vike-wallet-trace` to follow the money out of the wallet
- `vike-smart-money-discovery` to put a token the wallet trades in context
