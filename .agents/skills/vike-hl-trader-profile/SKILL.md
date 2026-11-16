---
name: vike-hl-trader-profile
description: Full profile of one Hyperliquid wallet - equity, open positions, cohort labels, lifetime PnL against deposits, win rate and risk metrics, per-symbol record, closed trades, funding paid, deposits and withdrawals, orders, TWAPs, leverage changes, liquidations, builder routing, and a live feed of its fills and closed trades. Use when the user gives a Hyperliquid address and asks "is this trader any good" / "profile this HL wallet" / "what is this wallet's track record" / "show the fills, funding or deposits of 0x..." / "watch this trader".
allowed-tools: Bash(vike-data:*)
---

# vike-hl-trader-profile

One address, answered from the wallet's own data (not from scanning a leaderboard). 1 credit per call. Get the address from `vike-hl-top-traders`, `vike-hl-position-match`, `hl market --view positions` or `vike-data search <name>` (its `traders` section).

## Chain

```bash
W=0xfc667adba8d4837586078f4fdcdc29804337ca06
vike-data hl wallet $W --window 30d --json                         # equity, open_positions, cohorts, lifetime, ledger_by_type, metrics
vike-data hl wallet $W --view trades_summary --window 30d --json   # closed/open trades, medians, per-symbol record
vike-data hl wallet-trades $W --limit 50 --json                    # closed round trips, newest close first
vike-data hl wallet-activity $W --view funding --limit 50 --json   # funding paid or received
vike-data hl wallet-activity $W --view ledger --limit 50 --json    # deposits, withdrawals, sends, vault events
vike-data hl liquidations --wallet $W --json                       # has it been liquidated
```

Optional, by question: `hl wallet-activity $W --view fills --symbol BTC` (raw fills), `--view builders` (which front-ends it routes through), `hl orders --view wallet --address $W` (what became of its orders, 7 days by default, at most 30), `hl twaps --wallet $W`, `hl leverage-changes --wallet $W`, `hl market --view positions --wallet $W` (open positions with liquidation price). `--window` is `1d|7d|30d|all` (default `all`); page with `nextCursor` -> `--cursor`.

## Reading

- **A wallet we hold nothing for is an error**, not zeros: `hl wallet` exits 1 with "no Hyperliquid data for wallet 0x...". Say "no Hyperliquid history for this address", not "a flat trader" or "the tool failed". `last_fill_ts` is `null` for a wallet with no fill; `has_metrics: false` on an active wallet means no trades in the window, which is not zero skill.
- `equity.account_value` is the live account; `cash_balance_usd` can be hugely negative on a cross-margin account next to a healthy account value: don't quote it as cash.
- **Units**: `*_usd` and `account_value` are USD. `win_rate`, `long_win_rate` are FRACTIONS (0.49 = 49%); `pnl_pct`, `roi`, `*_drawdown_pct` are percents. `perp top-traders` returns `win_rate_pct` as a percent: never mix the two in one table.
- **`max_drawdown_pct`** is a percent from 0 to 100 of the wallet's own equity (deposits and withdrawals taken out) over the window; `null` means too little equity history to say, not 0. `calmar_ratio` and `recovery_factor` derive from it and are `null` with it.
- **Lifetime vs ledger**: compare `lifetime.cum_realized_pnl` with `net_deposit_usd` before praising PnL. The ledger records `send` one-sided and signed, so net-flow sums from it lean negative; trust `deposit` and `withdraw`, treat `send` as movement to or from other accounts.
- **Mean vs median** (`trades_summary`): a few large trades drive the mean (a live 30-day record: `avg_gain` $73.6k against `median_gain` $4.5k). Report medians beside means, with `payoff_ratio`, `profit_factor`, `expectancy`.
- `trade_count` here counts open and closed trades; the leaderboard counts only completed trades, so the two never match.
- **Funding**: `payment_usd` is signed from the wallet's side, negative = the wallet PAID. **Fees**: a negative `fee` on a fill is a maker rebate.
- Cohorts (`size`, `pnl`, style tags) are today's labels. `avg_leverage` is the notional-weighted leverage of the open positions (real since 2026-10-25); 0 = no open position, not unlevered.
- Identity: a Hyperliquid address is an EVM address; `vike-data labels <addr>` may name it. Sub-accounts and vaults are separate addresses with no merge.

## Watch it live

```bash
vike-data stream hl --channel hl.fills hl.liquidations hl.ledger hl.closed_trades --filter wallet=$W --filter min_usd=0
```

`hl.fills` alone defaults to fills of $1,000 or more; `min_usd=0` is every fill. `hl.closed_trades` sends each round trip once, when it is written as closed, in batches every 30 minutes (a trade that closed at 08:12 arrives around 08:35); its `data` is the item of `hl wallet-trades` (`realized_pnl`, `entry_price`, `exit_price`, `duration_seconds`, `max_size_usd`) and `min_usd` on it is the trade's largest size. A close shows at once as an `hl.fills` event whose `data.action` is `close`. One key holds one `stream hl` connection: add channels to this command, never start a second one. A stored rule instead: `vike-data alerts create --type hl_wallet --wallet $W ...` (see `vike-alerts-setup`).

## Verdict rubric (rules of thumb, not tool output)

| Pattern | Label |
|---|---|
| positive `lifetime.cum_realized_pnl` AND window `profit_factor` above 1.5 AND 50+ closed trades AND positive median gain | **Consistent** |
| big PnL, `win_rate` near 0.5, mean far above median | **Few large winners** (size matters more than hit rate) |
| high win rate on fewer than 10 closed trades | **Unverified** |
| `net_deposit_usd` much larger than `cum_realized_pnl` | **Equity driven by deposits**, not trading |
| realized PnL positive but `total_unrealized_pnl` very negative on a large open book | **Open risk** |

## Anti-patterns

- Don't call a wallet "the best trader" from a 1d or 7d window, or from one number.
- Don't give copy-trade advice. The record is past, one venue, labels as of today.
- Don't read a `null` drawdown as 0.

## Pairs well with

- `vike-hl-top-traders` (rankings), `vike-hl-position-match` (find the wallet), `vike-wallet-who-is` (on-chain identity)
- `vike-hl-cohort-positioning` for how that cohort is positioned
