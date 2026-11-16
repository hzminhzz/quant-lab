---
name: vike-token-screener
description: Token discovery filter — rank tokens by volume / net inflow / active-wallet count / smart-money inflow within a window, with min-volume and min-holder filters. Use when the user asks "what tokens are trending today" / "find tokens with smart money accumulating" / "rising tokens this week" / token discovery from scratch.
allowed-tools: Bash(vike-data:*)
---

# vike-token-screener

## Command

```bash
vike-data token screener
  [--chain ethereum|bsc|base|hyperevm]
  [--window 24h|7d|30d|90d]
  [--sort volume|net_inflow|holders|smart_inflow]
  [--limit N]                (default 20, max 100)
  [--min-volume USD]         (drop thin tokens)
  [--min-holders N]          (require N+ distinct wallets active in the window)
  [--smart-money-only]       (restrict to smart-money cohort flows)
  [--json]
```

`--smart-money-only` and `--sort smart_inflow` exist for `--chain ethereum` only; on `bsc`, `base` and `hyperevm` they are a 400.

## Examples

```bash
# Trending today by volume
vike-data token screener --window 24h --sort volume --limit 20

# Tokens the smart-money cohort is flowing into (Ethereum only)
vike-data token screener --window 7d --sort smart_inflow --smart-money-only

# Broad participation: most distinct wallets active in the window
vike-data token screener --window 30d --sort holders --min-volume 100000

# Biggest net inflow among tokens with real activity
vike-data token screener --min-volume 1000000 --min-holders 100 --sort net_inflow
```

## Output columns

`token_address`, `symbol`, `name`, `volume_usd`, `net_flow_usd`, `in_usd`, `out_usd`, `unique_holders`, `tx_count`.

- `symbol` is often blank and `name` null: the screener identifies tokens **by address**. Report the address. Don't invent a symbol, and don't call a blank-symbol row "unknown token" without checking the next point.
- **`0x0000000000000000000000000000000000000000` is the chain's native coin** (ETH on ethereum, BNB on bsc), not a contract. It ranks high on volume and `smart_inflow`. `vike-data token transfers` returns no buyers for it, so skip it before drilling into a screened token.
- `unique_holders` counts wallets **active in the window** (that sent or received the token), not wallets that hold it and not growth.
- `24h` spans yesterday and today (UTC calendar days), not a rolling 24 hours.

## Sort modes

| Sort | What it surfaces |
|---|---|
| `volume` | Most-traded tokens (default): attention, not direction |
| `net_inflow` | Largest `net_flow_usd` first (in > out). There is **no ascending sort**, so the bottom rows are the smallest inflows, not the biggest outflows |
| `holders` | Most distinct wallets active in the window |
| `smart_inflow` | Tokens the smart-money cohort is flowing into (Ethereum only) |

## Reading

- **High volume + `in_usd` ≈ `out_usd`** = churn (DEX MM, arb bots). Low signal. If `in_usd` equals `out_usd` to the cent and `net_flow_usd` is exactly `0.0`, the figure is almost certainly transfer volume counted on both sides, not a quiet token.
- **High volume + strong `net_flow_usd`** = real demand. Worth investigating with `vike-data token transfers`.
- **Many active wallets + flat volume** = small transactions spread widely (airdrop claims also look like this).
- **`smart_inflow` + high values + low overall volume** = cohort positioned before retail. A screen to start from, not a verdict: confirm with `token transfers`.

## Filter tips

| Goal | Filters |
|---|---|
| "Busy memecoins" | `--window 24h --sort holders --min-holders 50 --min-volume 50000` |
| "Mid-cap smart-money inflow" | `--sort smart_inflow --min-volume 500000 --smart-money-only` |
| "What's getting dumped" | **Not answerable from the screener** (no ascending sort, no outflow rank). For one known token use `vike-data token transfers <addr> --tab sold` |

## Anti-patterns

- Don't use `--window 24h --sort smart_inflow` for hot-new-token discovery: smart money usually positions before the 24h window; try `--window 7d`.
- Don't omit `--min-holders` when sorting by `holders`: junk airdrops with 50,000 one-transaction addresses dominate the top otherwise.
- Don't omit `--min-volume` when sorting by `net_inflow`: tiny tokens with $100 net flow can rank high in % terms.
- Don't use `--smart-money-only` on `bsc`, `base` or `hyperevm`: it is a 400, not an empty result.

## Cost note

3 credits per call. Cheaper than `wallet trace` (4) but heavier than per-tool lookups. Use deliberately for discovery, not as a default.

## Pairs well with

- `vike-data token holders <addr>`: who moves the most of a flagged token
- `vike-data token transfers <addr>`: WHO is buying/selling once a token is interesting (read `net_tokens`)
- `vike-smart-money-discovery`: composite playbook starting from this screen
