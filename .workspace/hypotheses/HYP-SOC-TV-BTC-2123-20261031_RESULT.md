# Native Warden Stage 1 result — HYP-SOC-TV-BTC-2123-20261031

**REJECT FROZEN.** Source-inspired BTCUSDT Binance USD-M perpetual
2-hour UTC seasonality; *not* an original-instrument replication.
Original public publisher (EdgeTools, 2024-10-30):
https://www.tradingview.com/script/IzFZxayj/
Current HTML response SHA256:
`a4d4ee62841aaad48207547cd7e7a39ab14130197e4943f9a6bf75017eac0c0f`
Source original historical-vintage hash NOT independently available.

Contract `AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1`,
fixed 21:00 UTC next-bar entry, 23:00 UTC next-bar exit,
single BTCUSDT long/flat, native 4.5bps fee +2bps slippage per side plus
funding. Publisher-selected 21-23 window implies unknown source-wide
multiple comparisons. Existing rejected NYSE-close 18h MAX(10) BTC variant
remains frozen and must be counted as related selection history.

Registered through shared governance-guard Warden writer lock:
`.workspace/hypotheses/HYP-SOC-TV-BTC-2123-20261031.md`.
Spec: `experiments/active/tv_btc_2123_20261031.yaml`.
Module: `strategies/candidates/tv_btc_2123_20261031.py`.
Synthetic regression tests:
`uv run pytest -q tests/experiments/test_tv_btc_2123_20261031.py`
= 3 passed. Ruff check PASS; protected Warden worktree PASS.

Native durable verification job `warden-verify-56018173e949e488d3`:
terminal `PROCESS_OK`, structured Warden
`{"status":"PASS","prefix_invariance":true,"cached":true,
"rows":21888,"data_provenance":"verified_market",
"market_eligible":true}`.

Native durable screen job `warden-screen-73ef2384c8a9be8056`:
terminal `PROCESS_NONZERO` because native Stage1 `passed:false`,
NOT an execution outage. Structured metrics from original journal:

```json
{
  "strategy_family": "rules_ts",
  "passed": false,
  "metrics": {
    "avg_holding_bars": 2.0,
    "bootstrap_p_value": 0.065,
    "conditional_edge": 0.00026520479301439265,
    "profit_factor": 0.8259843772167974,
    "time_in_market": 0.08333333333333333,
    "trade_edge": 0.0005304095860287853
  },
  "data_provenance": "verified_market",
  "market_eligible": true
}
```

Immutable native selection attempt: `ATT-5657f0c12d4653af-001`,
Stage `STAGE_1_SIGNAL` (verified from read-only SQLite).
Profit factor fails >=1.15, bootstrap p fails <0.05, and 2h
gross trade edge 5.30bps fails >1.5x native 6.5bp one-way
cost (=9.75bps). TiM 8.33% passes >=5%.
No Stage3 or protected holdout, no live broker order. Do not retry,
invert, or parameter-tune this rejected adaptation under any ID.
