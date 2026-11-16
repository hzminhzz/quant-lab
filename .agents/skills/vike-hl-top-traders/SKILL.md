---
name: vike-hl-top-traders
description: Rank Hyperliquid perp traders by PnL / ROI / win rate / volume / trade count / drawdown over a window - the quick top list, or the full leaderboard with Calmar, profit factor, streaks, cohort filters, paging and PnL net of deposits - and see where one address ranks. Use when the user asks "top HL traders" / "best Hyperliquid wallets" / "HL leaderboard" / "who made the most on Hyperliquid this week" / "top traders net of deposits".
allowed-tools: Bash(vike-data:*)
---

# vike-hl-top-traders

## Command

```bash
vike-data perp top-traders
  [--window 1d|7d|30d|all]
  [--sort pnl|roi|win_rate|volume|trade_count|drawdown]
  [--order asc|desc]
  [--min-trades N]       # default 3; 0-2 are treated as 3
  [--min-win-rate P]     # percent, 0-100
  [--min-volume USD]
  [--size N]             # default 20, max 100
  [--json]
```

## Common queries

| Question | Flags |
|---|---|
| "Top HL traders this week" | `--window 7d --sort pnl` |
| "Best ROI traders last 30d w/ >=10 trades" | `--sort roi --min-trades 10` |
| "Sharpshooters: win rate >75% min $1M vol" | `--sort win_rate --min-win-rate 75 --min-volume 1000000` |
| "Most active traders today" | `--window 1d --sort trade_count` |
| "Smallest drawdown winners" | `--sort drawdown --order asc --min-trades 20` |

## Output

An object `{ "traders": [...], "total": N }`; each trader:

`address`, `total_realized_pnl` (USD), `roi_pct`, `win_rate_pct`, `long_win_rate_pct`, `short_win_rate_pct`, `win_count`, `loss_count`, `trade_count`, `total_volume` (USD), `long_pnl`, `short_pnl`, `max_drawdown_pct`, `account_value` (USD), `avg_leverage`.

All `*_pct` fields are percents: `12.5` means 12.5%. (Before 2026-10-19 `roi_pct` and `max_drawdown_pct` came back 100× too large; a transcript showing a 1,750% ROI is from that period.) `avg_leverage` is the notional-weighted leverage of the open positions (real since 2026-10-25); 0 = no open position. There is no `pnl_usd` and no `max_drawdown_usd`.

## Reading

- **`roi_pct` is realized only.** It is the window's realized PnL divided by the wallet's CURRENT account value. Open positions are not counted, and a wallet that has since withdrawn most of its equity looks better than it was (and a recent deposit makes it look worse).
- **`max_drawdown_pct` is account-level and all-time** here, a percent from 0 to 100 built from the account's equity history; `null` = too little equity history to say. It does not change with `--window`.
- **Windows are UTC days:** `1d`, `7d`, `30d` mean today plus the previous 1, 7 or 30 days; `all` is the last 365 days.
- **`trade_count` counts the trades CLOSED in the window** (filed under the UTC day they closed, whenever they opened). A trade still open is not counted, so a wallet's count here differs from a per-wallet trade summary (which counts open trades too).
- A wallet with 90% win rate on 4 trades is statistical noise. Min trades 10–20 for any "skill" claim.
- Top by PnL ≠ top by ROI. Whales win big in $ but small in % terms; ROI sort surfaces smaller-account discretionary skill.
- The leaderboard rewards survivors: wallets that blew up don't appear, so the PnL distribution is right-skewed.

## `hl leaderboard`: the full board

```bash
vike-data hl leaderboard
  [--view ranking|flow_adjusted] [--window 1d|7d|30d|all]
  [--sort pnl|realized_pnl|roi|volume|trades|win_rate|profit_factor|expectancy|calmar|max_drawdown|current_drawdown|fees|win_streak|...]   # an unknown key is refused
  [--order asc|desc] [--cohort <label> --axis size|pnl|pnl_abs]
  [--min-trades N] [--min-win-rate F] [--min-volume USD]   # win rate is a FRACTION here: 0.6 = 60%
  [--limit N]            # default 25, max 500; page with nextCursor -> --cursor (resend the same filters)
  [--json]
```

Rows are keyed `wallet` (not `address`) with `account_value`, `total_realized_pnl`, `pnl_pct`, `roi`, `win_rate` (a fraction), `profit_factor`, `expectancy`, `max_drawdown_pct`, `current_drawdown_pct`, `calmar_ratio`, streaks, `size_cohort`, `pnl_cohort`. Prefer `pnl_pct` to `roi` (`roi` is unguarded on a near-empty account). Here the drawdown fields are computed over the window's equity (0-100; `null` = too little equity history), unlike `perp top-traders`. `--view flow_adjusted` ranks fills-derived trading PnL, which excludes deposits, and takes only `--window`, `--min-trades`, `--limit`, `--cursor`; when its `flow_unbalanced` is true the flow figures are unreliable.

## Is this specific address any good?

Profile the wallet itself: `vike-hl-trader-profile` (`vike-data hl wallet <address>`). To see where it ranks, scan a leaderboard for it:

```bash
vike-data perp top-traders --window 30d --sort pnl --size 20 --json
vike-data perp top-traders --window 7d  --sort pnl --size 20 --json
vike-data perp top-traders --window 30d --sort roi --min-trades 10 --size 20 --json
vike-data perp top-traders --window 30d --sort win_rate --min-trades 10 --size 20 --json
```

- **Found** → report that row for that window and sort (all columns above). That is the whole profile you can give.
- **Not found** → say "not in the top N for this window and sort". Absence says nothing about quality; `hl leaderboard --limit 500` pages deeper.
- `vike-data wallet summary` has **no Hyperliquid data** (it reads on-chain transfers on one chain): don't present it as the HL profile. `vike-data labels <addr>` shows whether the address is a known entity.

Classification of a found wallet:

| Pattern | Label |
|---|---|
| Top-20 PnL + high ROI + many trades + low drawdown | **Sharp** |
| Top-20 PnL + low ROI + huge volume | **Whale** (size matters more than skill) |
| High win rate on <10 trades | **Unverified — too few data points** |
| One big trade dominates PnL (`win_count` small, PnL large) | **One-shot** (lucky directional bet) |

## Anti-patterns

- Don't claim a wallet is "the best HL trader" from a 1d window. Anything < 30d is short-term.
- Don't claim "this is one of the best HL traders" from win rate alone — need volume + trade count for context.
- Pass an exact `--sort` value: a typo is not rejected, it silently falls back to a PnL ranking.
- Don't describe a wallet outside the top 20 as bad.

## Pairs well with

- `vike-hl-trader-profile` for one wallet's full record; `vike-hl-position-match` to find the wallet behind a share card first
- `vike-perp-funding` to see whether the leaders are positioned against funding
- `vike-candles` for the price action behind a period of PnL
