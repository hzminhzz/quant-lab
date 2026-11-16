---
name: vike-polymarket-markets
description: List trending Polymarket prediction markets ranked by 24h volume, filterable by category (Crypto / Sports / Politics / Pop-Culture / Tech). Use when the user asks "what's hot on Polymarket" / "top crypto prediction markets" / "trending markets today".
allowed-tools: Bash(vike-data:*)
---

# vike-polymarket-markets

## Command

```bash
vike-data polymarket markets [--category C] [--limit N] [--json]     # limit default 10, max 30
```

`C` is one of: `Crypto`, `Sports`, `Politics`, `Pop-Culture`, `Tech`, `all` (default). Names are exact.

## Examples

```bash
vike-data polymarket markets --category Crypto --limit 10
vike-data polymarket markets --category Politics --limit 20
vike-data pm markets --json                    # `pm` alias
```

## Output

One row per market, ranked by **24h volume only** (there is no liquidity or volatility ranking):

`condition_id`, `slug`, `question`, `end_date`, `ended` (true once `end_date` has passed), `volume_24h` (USD), `liquidity`, `latest_ts`, and `outcomes`: a list with one entry per outcome leg, each with `token_id`, `outcome` (Yes / No or the leg's name), `mid`, `bid`, `ask`, `snap_vol_24h`.

The `token_id` of each leg is what `vike-data candles polymarket <token_id>`, `polymarket orderbook`, `market-stats` and `tape --token-id` need, so none of them needs a detail call. Read the leg by its `outcome` label, not by position: rows have listed `No` before `Yes` and `Down` before `Up`. The `condition_id` is the other id (for `polymarket market`, `market-trades` and `settlement`); never pass it where a token id is wanted.

## Reading the data

- `mid` is the midpoint price as a probability (0.0 to 1.0). `0.42` means "the market thinks 42% chance".
- A bid/ask spread > 0.05 (5 cents) = thin liquidity; the price is noisier.
- `end_date` is when the market resolves. Sort priority for action-oriented users: markets ending in the next 7 days. Check `ended`: a market that closed earlier today can still rank by its 24h volume.

## Anti-patterns

- Don't quote `mid` as a percentage without saying so. "Yes is 0.42" is the raw probability; "42% chance" is the user-friendly translation.
- For tiny markets (<$50k 24h volume) the quotes are often stale or one-sided. Skip them unless explicitly asked.
- Don't call a list of markets "what smart money is doing": for that use `polymarket smart-money`.

## Pairs well with

- `vike-polymarket-market-detail` — deep-dive on one market once you have its condition_id
- `vike-pm-market-diligence` — spread, depth, large prints, holders and on-chain resolution of one market
- `vike-polymarket-smart-money` — which markets cohort wallets are buying, and a full market overview
- `vike-candles` — probability history for a leg's `token_id`
