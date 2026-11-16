---
name: vike-smart-money-discovery
description: Smart-money discovery on Ethereum — which tokens the smart-money cohort is flowing into, and who the biggest net buyers of one token are. Use when the user asks "is smart money buying X" / "find smart money" / "any alpha wallets in Z".
allowed-tools: Bash(vike-data:*)
---

# vike-smart-money-discovery

Composite playbook built on `token screener --smart-money-only`. Smart money here means the cohort behind the screener's `smart_inflow` and `--smart-money-only`, which exist for **Ethereum only** (a 400 on bsc, base and hyperevm).

**What this can and cannot say.** It can say which tokens the cohort is flowing into and how much, and who the biggest net buyers of a token are. It cannot list which of those buyers belong to the cohort: no tool returns cohort membership. State that limit in the answer.

## Workflow

### A. Which tokens is the cohort buying?

```bash
vike-data token screener --chain ethereum --window 7d --sort smart_inflow --smart-money-only --limit 100 --json
```

- Skip the row `0x0000000000000000000000000000000000000000`: it is the native coin, not a token you can drill into.
- Rows carry `token_address` (the `symbol` is often blank): identify tokens by address, and resolve names with `vike-data token search`.
- If the user named a token: resolve it (`vike-data token search <symbol> --limit 3 --json`) and look for its `token_address` in the rows. Present means the cohort moved it; absent from the top 100 means no visible cohort inflow in that window.

### B. Who is accumulating it?

```bash
vike-data token transfers <addr> --tab bought --period 7d --hide-contracts --json
```

The list is the **top 10 net buyers by net USD**, not the cohort (a buyer's `net_usd` is negative; `net_tokens` is the net in whole tokens). `--hide-contracts` leaves out the pools and routers we know of; a row can still be an exchange or an unmarked contract.

### C. Strip the intermediaries

```bash
vike-data wallet batch <addr1> <addr2> ... --json
```

Drop rows labelled `cex`, `dex`, router or contract. What is left are the real net buyers of the token.

### D. Profile the survivors (cap at 5)

```bash
vike-data wallet summary <addr> --json
```

Report address, label (if any), the token's `net_tokens` and `net_usd` over the window, and the wallet's 30d volume.

## Verdict template

> **Smart-money take on $TOKEN (7d):** the cohort's `smart_inflow` into $TOKEN is $Y (screener rank #R by `smart_inflow`). Among the top-10 net buyers, N survive the label filter; the largest are `<addr1> (net_tokens X, label?)`. **Read: accumulation / churn / no-signal.** The cohort members themselves are not listed by any tool.

## Anti-patterns

- Don't rank wallets by `wallet discover --sort pnl` and intersect them with a token's buyers: `realized_pnl` is stored wrongly for many wallets and is only an ordering key, and the overlap with a 10-row buyer list is almost always empty.
- Don't accept any wallet labeled as a CEX (binance/coinbase/bybit/etc.) as "smart money": they are aggregators.
- Don't use `--window 24h` for the screen: smart money usually positions before the 24h window.
- Single-token wallets (`tokens_traded < 3` in `wallet summary`) are usually airdrop-flippers, not strategic.

## Cost note

Screener (3 credits) + `token transfers` (2) + `wallet batch` (2) + up to 5 × `wallet summary` (2).

## Pairs well with

- `vike-token-smart-money-trend` for whether the cohort is entering, holding or exiting ONE token over 24h / 7d / 30d / 90d
- `vike-token-screener` for the filters and the column meanings
- `vike-token-research` for the price/flow context
- `vike-perp-funding` if the token has a perp listing (positioning bias)
