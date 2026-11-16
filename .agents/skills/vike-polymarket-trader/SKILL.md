---
name: vike-polymarket-trader
description: Profile a Polymarket wallet — realized PnL over a window, hit-rate on resolved markets, total volume, top categories traded, recent trades and open positions. Use when the user gives a Polymarket wallet address.
allowed-tools: Bash(vike-data:*)
---

# vike-polymarket-trader

## Command

```bash
vike-data polymarket wallet <address> [--window 24h|7d|30d|all] [--json]
```

## Example

```bash
vike-data polymarket wallet 0xabcdef1234... --window 30d
vike-data pm wallet 0xabcdef1234... --json     # `pm` alias
```

## Output

Per-wallet profile (`--window` defaults to `all`):

- `realized_pnl_usd`, `resolved_markets`, `hit_rate`, `hit_rate_ci`: scoped to the window, cut by **resolution** time (only markets that resolved inside the window count)
- `trades_window`, `volume_window_usd`: scoped to the window, cut by trade time
- `trades_all`, `volume_all_usd`, `avg_trade_usd`, `markets_count`, `first_seen`, `last_seen`: lifetime
- `markets` (max 30), `trades` (max 100), `open_positions` (max 30), `open_exposure_usd`, `equity_curve`, `category_breakdown`, `daily_activity`, `correlated`, `tags`, `streak`: not window-scoped

There is no `hit_rate_pct`, `volume_<window>_usd` or `trades_<window>` field.

## Reading

- **`hit_rate` is a fraction (0.41 = 41%) and volume-weighted, not a count of wins.** It is the share of the wallet's resolved dollar volume that sat in markets where it made money, so an integer number of won markets will not reproduce it. `hit_rate_ci` is its confidence half-width: with few resolved markets it is wide, so don't rank on the point value.
- **`realized_pnl_usd` is realized only.** An open position adds nothing until its market resolves; markets resolved invalid are excluded.
- `open_positions` and `open_exposure_usd` understate: positions without a recent quote are left out.
- Zeros in a window (`trades_window: 0`, `realized_pnl_usd: 0`) mean "nothing resolved in this window", not "no activity".

| Pattern | Read |
|---|---|
| `hit_rate` > 0.60 with `resolved_markets` > 20 | **Skilled** — consistent over a real sample |
| `hit_rate` > 0.80 with `resolved_markets` < 10 | **Lucky — too few data points** |
| Positive PnL but `hit_rate` < 0.50 | **Asymmetric bettor** — wins big when right, loses small |
| Negative PnL with high volume | **Volume trader / MM-style** — pays the spread |
| One category dominates >80% of `category_breakdown` | **Specialist** — only trust their reads in that category |

## Watch it live

```bash
vike-data stream polymarket --channel pm.positions --filter wallet=<address> --filter action=open,close
```

Each trade of the wallet as what it did to its position in that outcome: `data.action` (`open`, `increase`, `reduce`, `close`, `flip`), `shares_before`, `shares`, `avg_price` (its average BUY price in that outcome after the trade), and the market in `subject` (`title`, `outcome`, `condition_id`). Drop `--filter action=...` for every change; `wallet` takes up to 1,000 comma-separated addresses. It runs about four minutes behind the chain and the first events come one to three minutes after you start. `shares` count trades only (shares from a split, merge or transfer are not on the tape, so a wallet that sells them goes negative). One key holds one `stream polymarket` connection: see `vike-stream`.

## Anti-patterns

- Don't claim "this trader is good at predictions" from <10 resolved markets. Below that, `hit_rate` is noise.
- Always read `resolved_markets` before quoting `hit_rate`: a wallet with 100 trades but only 5 resolved has a hit rate based on those 5.
- Multi-category specialists are rare. If the wallet trades 5+ categories, treat `hit_rate` as a generic "good at probabilities" signal, not "deep edge".
- A wallet with no recorded trades is refused with an error, not an empty profile.

## Pairs well with

- `vike-polymarket-market-detail` — see which markets they're currently in (its holder lists give the `proxy_wallet` addresses to profile here)
- `vike-pm-market-diligence` — the market-level playbook that ends with this wallet check
- `vike-polymarket-smart-money` — whether the profitable-wallet cohort is moving in the same markets
