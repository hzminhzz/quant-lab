---
name: vike-hl-cohort-positioning
description: Are whales, profitable wallets or losing wallets net long or short a Hyperliquid symbol right now - long/short notional per wallet cohort on the size and pnl ladders, the smart-money and whale indices, and the biggest individual positions, plus live feeds of the cohort lean, cohort leverage and the top-10 positions per coin. Use when the user asks "are whales long or short BTC" / "what are profitable Hyperliquid wallets doing on ETH" / "who is long SOL on Hyperliquid" / "Hyperliquid positioning" / "net long or short by wallet size".
allowed-tools: Bash(vike-data:*)
---

# vike-hl-cohort-positioning

A current read of how each wallet cohort is positioned in ONE symbol. 1 credit per call, about 6. For history and anything backtest-like use `vike-hl-cohort-backtest`: this skill's `pnl` labels are today's.

## Chain

```bash
vike-data hl segments --view cohorts --json                       # the exact cohort labels per ladder
vike-data hl cohort-metrics BTC --axis size --hours 1 --json      # whales: one row per size rung
vike-data hl cohort-metrics BTC --axis pnl  --hours 1 --json      # profitable vs losing wallets (today's grading)
vike-data hl market --view indices --symbol BTC --hours 24 --json # series: smi, wsi per hour; latest: also smart_bias, whale_bias
vike-data hl segments --view summary --axis pnl --cohort smart --json     # that cohort across ALL symbols
vike-data hl market --view positions --symbol BTC --sort size --limit 20 --json   # the biggest open positions, largest first
```

`--hours 1..24` is a current read; the newest SETTLED hour comes back (the hour in progress never does). Symbols are upper case; a HIP-3 market is `dex:TICKER`. Read `freshness`: `stale: true` means do not call it "now" (`behind_hours` says how far behind).

## Units and the ladders

- `bias` = (long - short) / (long + short) of NOTIONAL, a **fraction** from -1 (all short) to +1 (all long). Long share = (1 + bias) / 2. `total_position_value` and `total_position_value_long` are USD; `position_count` counts positions.
- **Several rungs as one group: sum the notional, never average the biases.** `bias_group = (2 * sum(total_position_value_long) - sum(total_position_value)) / sum(total_position_value)`.
- `size` ladder = account value: `shrimp` from $0, `fish` $10k, `dolphin` $50k, `shark` $250k, `whale` $1M, `2x whale` $2.5M, `3x whale` $5M, `4x whale` $10M and up. `pnl` ladder = percentile of cumulative realized PnL with at least 10 closed trades, from `3x smart` (top) through `winner` / `loser` to `3x rekt`; `unknown` = too few trades. `hl segments --view segments` gives each rung's bounds.
- The indices are ratios from -1 to +1: `smi` = long share of the smart cohorts minus that of the rekt cohorts, `wsi` = long share of whales minus shrimp, `smart_bias` / `whale_bias` = (long - short) / (long + short) of those groups. `hl market --view indices` returns `series` (settled hours, `smi` and `wsi` only, pnl rungs graded as of their hour) and `latest` (the newest snapshot, today's grading, with `smart_bias` and `whale_bias` too). `smi_wsi_gap` exists only on the `hl.indices` stream and in `hl_cohort_bias` alerts.

## Reading

- Check `position_count` and the rung's notional before saying a cohort "is short": 20 wallets, or one wallet, can set a rung's bias. Compare rungs, not just totals: whales net short while `smart` is net long is a disagreement worth stating as such.
- `--opened-within 24h|7d|30d` (on `cohort-metrics` and `segments summary`) restricts to recently opened positions: how much of the lean is new.
- `hl market --view positions --sort size` needs `--symbol` and at most `--limit 500`, and takes no cursor; each row has `size_usd`, `leverage`, `liquidation_price`, `unrealized_pnl`, `opened_at` and the wallet's `account_value`. Without `--sort size` rows come in key order, not by size. Never run `positions` without `--symbol` or `--wallet`.

## Live

```bash
vike-data stream hl-cohorts --channel hl.cohort_bias --filter coin=BTC --filter axis=pnl     # every cohort's long/short, each position capture (~12 min)
vike-data stream hl-cohorts --channel hl.indices --filter coin=BTC,ETH --filter index=smi,wsi   # only when an index crosses -0.10 / +0.10
```

`hl.cohort_bias` covers the 50 coins with the most open value; a `cohort` belongs to one axis (`3x smart` with `axis=size` matches nothing).

```bash
vike-data stream hl-cohorts --channel hl.cohort_leverage --filter axis=size --filter cohort=whale,shark   # leverage per cohort, once an hour
vike-data stream hl --channel hl.top_positions --filter coin=BTC --filter event=entered                  # who enters the BTC top 10
```

- **`hl.cohort_leverage`** is the hourly leverage of each cohort on its own money: `perp_bias` = (long - short) / account value, `equity_exposure` = (long + short) / account value (`null` when no wallet of the cohort has a positive account value), with `equity_covered_share` saying how much of the cohort's notional the counted wallets hold. Main-dex positions of native perp accounts, no coin and no wallet; the newest hour arrives at once, then one an hour (hour 08:00 at about 09:03). The same numbers as history: `hl segments --view history --axis size --cohort whale --include leverage`.
- **`hl.top_positions`**: per coin of the top 50, a position that `entered`, `left` or `moved` within the top 10, at each capture (about 12 minutes), with `rank`, `prev_rank`, `direction`, `size_usd`, `leverage`, `liquidation_price`. The first capture sends the whole top 10 of every coin as `entered` (with a `coin` filter: 10 events). The ranking is that of `hl market --view positions --sort size`, live.
- The three `stream hl-cohorts` lines above are alternatives: one key holds one connection per path, so a second `stream hl-cohorts` replaces the first (`4410`). Put channels in one command only when each takes every `--filter` key (`hl.cohort_leverage` has no `coin`), or give each its own key.

For a stored rule: `vike-data alerts create --type hl_cohort_bias --index smi ...` (see `vike-alerts-setup`).

## Answer template

> BTC on Hyperliquid, hour `<ts>` UTC (`freshness.stale: false`): whales (`4x`..`whale`) are `<bias_group>` (`$L` long / `$S` short across `N` positions); `smart` wallets are `<bias>`; `rekt` wallets `<bias>`; `smi` `<v>`. Positioning is notional-weighted, today's cohort labels, Hyperliquid only: a position can be a hedge held elsewhere.

## Anti-patterns

- Don't turn a lean into a price forecast, and don't say "smart money is short" when the group is one rung of 20 positions.
- Don't use these default labels on history: the `pnl` ladder is graded today (look-ahead).
- Don't write `bias` or an index as a percent: they are fractions.

## Pairs well with

- `vike-hl-asset-briefing` (adds funding, OI, liquidations), `vike-hl-cohort-backtest` (history), `vike-hl-trader-profile` (one wallet from the positions list)
