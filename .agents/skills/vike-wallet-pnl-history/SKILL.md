---
name: vike-wallet-pnl-history
description: Daily net-flow series for a wallet over the last N days — a directional proxy for daily PnL. Use when the user asks "how has wallet X done over time" / "show me daily flows for Y" / "is this wallet trending up or down".
allowed-tools: Bash(vike-data:*)
---

# vike-wallet-pnl-history

## Command

```bash
vike-data wallet pnl-history <address>
  [--chain ethereum|bsc|base|hyperevm]
  [--days N]   (max 365, default 30)
  [--json]
```

Chains: `ethereum`, `bsc`, `base`, `hyperevm`, one chain per call.

## Output

```json
{
  "address": "0x...",
  "chain": "ethereum",
  "days": 30,
  "total_net_flow_usd": 12345.67,
  "series": [
    { "date": "2026-04-25", "net_flow_usd": +120.50, "gross_volume_usd": 1500, "tx_count": 4, "unique_tokens": 2, "cumulative_net_flow_usd": 120.50 },
    { "date": "2026-04-26", "net_flow_usd": -50.30,  "gross_volume_usd": 800,  "tx_count": 2, "unique_tokens": 1, "cumulative_net_flow_usd": 70.20 },
    ...
  ]
}
```

## Important caveat

**This is NET INFLOW USD, not realized PnL.** True realized PnL requires WAC (weighted-average cost) accounting per token, which we don't do in this fast tool. A wallet that bought $10K and sold $10K of the same token at the SAME price would show $0 here — but if they sold higher, true PnL would be positive (and this tool wouldn't reflect that). Use this for direction + trend, not for tax-grade PnL.

There is no reliable realized-PnL figure for a wallet in the CLI: `wallet discover` carries a `realized_pnl` column, but it is only an ordering key (many stored values are wrong), so never quote it as dollars.

## Reading

- The series is **sparse**: one point per day that had activity, not one per day of the range. Zero-fill the calendar before charting or diffing.
- `cumulative_net_flow_usd` ramping up = wallet has been accumulating $
- Big positive day with high `tx_count` = single large buy (deposit / acquisition / token purchase)
- Big negative day with high `tx_count` = sell-down or large transfer out
- `unique_tokens > 5` on a single day = farmer / spray-and-pray / airdrop claim
- All `net_flow_usd` near zero with high `gross_volume_usd` = market-maker / churn

## Anti-patterns

- Don't report this as "realized PnL" — it isn't.
- Big single-day spikes can be legitimate (token unlock, ICO buy, airdrop claim) — don't auto-flag without context
- A `null` or empty series means the wallet has no activity on the requested chain in the window — surface that honestly

## Pairs well with

- `vike-data wallet summary <addr>` for the aggregate context
- `vike-data wallet counterparties <addr>` to see WHO they traded with
