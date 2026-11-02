# PIT Low-Volatility — Exact-Hour Event-Funded Economic Preflight (2026-11-02)

## Scientific disposition

**Predictive PIT Stage-1 PASS remains preserved, but this frozen
execution variant is ECONOMICALLY REJECTED in a read-only preflight.**

Under exact observed hourly Binance prices and a historical
funding-event ledger, the unchanged PIT low-vol strategy has **negative
net Sharpe at 1x trading costs, 2x costs and +1h execution latency**.
No full native Warden Stage-3 trial was created. This is not a
reproducible exchange-order/fills simulator or an authorization for
live capital.

Original historical Stage-1 receipt remains
`ATT-480df2ef5d8788f2-001`: mean rank IC `0.0574236420`, HAC
t `11.5513183` on 364 PIT assets under
`CRYPTO_PERPS_PIT100_1H_V1` using 2024-07-01 through
2025-07-01 exclusive. This is the same temporal validation window
already examined on a hindsight-selected cohort, not an unseen holdout.
Full selection debt must remain in Warden/PR evidence.

## Frozen source inputs

| Input | Exact identity |
|---|---|
| PIT original price panel | `data/exploratory/crypto_perps_pit100_1h_v1.parquet` |
| Original approved Stage-1 panel SHA256 | `c76266d4c3bf2e37ce904bedd182cd6e54b9cfd3e6bc9fe1f337925e4a818a8a` |
| Original strategy | `strategies/candidates/arxiv_lowvol_pit100_adapter.py`, unchanged `arxiv_lowvol_xs_1h.py` |
| Original parameters | Ridge(alpha=100), every 4h rebalance, top/bottom 10 assets, 25k liquidity threshold |
| Real historical event-ledger V3 SHA256 | `6ac0630855a88983cc9576cea0b7f094b50799fc8b0cea6ad0d92312e0e661aa` |
| Monthly price exit receipt SHA256 | `28bc41728a746c2571386baa2ce7da4cdd2272a28ae680900998456060e96aa8` |
| Exact economic diagnostic receipt SHA256 | `5de4660add68bc42405f62160645b12a60adeeb8608c06ce0d82752207e45ada` |

The V3 funding archive carries **758,734** historical events (of
which **419,559** have a timestamp inside the 2024-25 validation
period). Old funding history came from 5,874 SHA-audited local Binance
source shards and 588 monthly ZIPs with officially published matching
SHA256 checksums, plus 93 actual BNXUSDT January 2023 REST events.
The original source PIT panel remains unchanged; no protected
2025-07-01+ bar was read.

## Corrected exact-hour portfolio replay

`scripts/preflight_pit_lowvol_event_economics.py` calls the frozen
Stage-1 strategy once to obtain target positions, before any raw
exit-price bridge. Frozen features, model folds, signs and parameters
are never modified or tuned to economic results. Then:

1. Enforces exactly one Warden `NEXT_BAR` open fill delay (plus one
   extra hour only in the existing latency stress), once.
2. Uses a **timestamp-exact hourly open-to-open** return for each held
   position. The sparse monthly PIT panel has 359 noncontiguous
   asset-row pairs, so `open.shift(-1).over(asset)` is **invalid** as
   a one-hour return across a disappeared asset.
3. Verifies **30/30** actual following-hour raw Binance candles at
   monthly portfolio-rotation exits against the membership source
   SHA manifest, and injects **120** additional hourly raw opens
   into an in-memory price matrix. This is *observed market
   price*, **not actual broker fills**. The original panel's
   observed opens are not overwritten.
4. Runs a fail-closed check that all **175,160 active asset-hours**
   have both exact observed current and next-hour opens (zero
   missing), without substituting a later asset bar.
5. Flattens positions at the last permissible UTC open before
   `2025-07-01T00:00:00Z`, charges the full terminal exit turnover
   and commission/slippage, and checks terminal exposure=0.
6. Charges actual archived funding-rate observations at their event
   timestamps, with separate pre-open, post-open and conservative
   ordering bounds. One-second settlement/fill ambiguity is a model
   assumption, not an observed exchange fill. The 94 funding events
   without interval metadata retain their unknown intervals.

The per-side execution-cost model is the **unchanged approved
research-contract floor**: 4bps taker fee + 2bps slippage.
The 2x stress doubles both; the +1h stress adds one more hour
to the original mandatory delay. Annualized return metrics
use Warden's existing read-only `calculate_metrics_from_returns`,
not new statistical gates or backtest registrations.

### Observed-event funding charged to post-open positions

| Metric | Base 6bps one-way | 2x 12bps one-way | +1h latency, 6bps |
|---|---:|---:|---:|
| Gross annualized Sharpe | +1.05276 | +1.05276 | +0.99783 |
| **Net annualized Sharpe** | **−0.20821** | **−1.06916** | **−0.27271** |
| Net profit factor | 0.99378 | 0.96846 | 0.99186 |
| Maximum peak-to-trough drawdown | 44.59% | 57.78% | 42.30% |
| Net compounded return | −19.84% | −47.39% | −22.07% |
| Annualized gross weight turnover | 701.5 | 701.5 | 701.5 |
| Observed-event signed funding cashflow (% initial equity) | +19.51% cost | +19.51% cost | +19.46% cost |

**Robustness to event/open ordering:** base-cost pre-open
net Sharpe = `−0.20705`; post-open = `−0.20821`;
conservative per-event bound = `−0.20898`.
Double cost pre/post/conservative = `−1.06799 /
−1.06916 / −1.06993`. The conclusion does not depend on
funding/open ordering choices.

Current replay source cost assumptions may be **optimistic**:
OHLCV open fills are hypothetical, no venue order book, depth,
partial fills, delisting liquidation or spread-capacity validation.
Historical funding rate availability of 100% within a 16h
past-as-of window does not establish complete historical cashflow
settlement availability, and the original exchange archive may lack
early delistings. These uncertainties cannot rescue a negative result
as positive qualified alpha.

## Governance and next action

This negative economic screen is **historical selection evidence**.
Do not register a new identity, adjust parameters, invert signs,
reduce fee assumptions or use the previously examined 2024-25 period
for another variant without predeclared independent lineage and
new statistical search debt.

**PIT native Stage-1:** PASS, kept exactly once.
**PIT native Stage-3:** not executed — source changes and
event-aware cashflow require a **new, separately approved economic
execution contract**. No Stage-3 qualification or six-check audit
was claimed. **Trading authorization:** none.

New data and methods are kept under independent, SHA-pinned evidence;
this report does not amend Warden protected contracts, source
market store, authoritative registry, or sealed holdout. The hourly
research Director should treat the original low-vol 4h PIT
execution configuration as *economically falsified in preflight*
and select another genuinely distinct thesis rather than optimize
this one on the consumed validation period.

### Reproduce without protected data

From an isolated worktree with the original PIT exploratory Parquet and
V3 funding event ledger from merged PRs #45/#46:

```bash
uv run --frozen python -m scripts.preflight_pit_lowvol_event_economics \
  --panel data/exploratory/crypto_perps_pit100_1h_v1.parquet \
  --ledger data/exploratory/crypto_perps_pit100_event_ledger_v3.parquet \
  --output docs/evidence/arxiv_stage1_20261102/pit_lowvol_event_economics_preflight_20261102.json
```

The result is **write-once** and fails on any unexpected source
SHA, unknown active-position price, stale event ledger or protected
boundary. Native Warden `screen`/`backtest` must not be used
as a reproduction shortcut; repeat screens consume selection budget.
