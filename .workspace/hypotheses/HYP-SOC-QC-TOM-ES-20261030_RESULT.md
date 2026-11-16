# HYP-SOC-QC-TOM-ES-20261030 — native Stage 1 outcome

**Status: STAGE_1_REJECT (one newly counted selection attempt, no full backtest).**

## Original evidence and binding adaptation

- Original public source: [QuantConnect — Turn of the Month in Equity Indexes](https://www.quantconnect.com/research/15377/turn-of-the-month-in-equity-indexes/) captured 2026-10-30. The page's original publication time was not independently obtainable. It describes buying the SPY ETF just after the last NYSE session opens and holding about three trading sessions; its example `on_data` increments a callback count, so the exact order timing must not be inferred from the short excerpt.
- Distinct economic thesis: US equity-index demand around pension/portfolio month-end rebalancing and new monthly allocations.
- Adaptation: ES CFD daily bars and *deterministic XNYS exchange calendar*, not the original SPY security or fills. At last NYSE session **after its daily bar is complete**, request +1 ES target; Warden executes next available daily bar. After third NYSE session of new month, request 0, fill on next available daily bar. Partial Sunday futures/CFD trading bars exist and may be actual fills; this is not an SPY opening-order replication. Source text is unversioned retrospective evidence.
- Pinned market dataset: `data/exploratory/indices_1d.parquet` under `INDICES_1D_V1`, SHA256 `0e4677355587831849598a1217ab9987b991d9ed8ff0dc2f3369782fad36ba27`; ES 3,111 daily rows from 2015-01-01 to 2025-06-30, including partial Sunday sessions. Label=5 daily bars (ex-ante calendar 125 complete monthly windows, maximum 5 recorded CFD bars), embargo=1; NEXT_BAR execution=1. Fee 0.5bps + slippage 0.5bps per side, matching the already approved contract. No new contract required.
- Preregistered hypothesis `HYP-SOC-QC-TOM-ES-20261030`; strategy `strategies/candidates/quantconnect_turn_month_es_cfd_1d.py`; spec `experiments/active/qc_turn_month_es_20261030.yaml`.
- Source article capture excerpt/checks were performed solely on the public original page. No historical article-text vintage can be asserted before validation.

## Reproducible native results

- Eight deterministic tests passed for exact NYSE month-end + third trading session scheduling, Sunday retention, future exchange-calendar prefix invariance, stale/invalid ES observation, horizon and parameter fail-closed checks. Together with NQ source trial, 13 tests passed.
- Durable Warden causal verification `warden-verify-82262b8969d1733afe`: PASS, 3,111 rows, `prefix_invariance=true`, `cached=true`, verified-market provenance.
- Durable Warden Stage1 screen `warden-screen-d89a9c73b033321b61`: terminal `PROCESS_NONZERO` exit1 due scientific screen rejection, not infrastructure error.
- Native immutable attempt `ATT-b56aee52fba281bc-001`, stage `STAGE_1_SIGNAL`, `outcome=REJECT`.

| Native metric | Value | Required |
| --- | ---: | ---: |
| Net profit factor | **0.9286388123227758** | >=1.15 (FAIL) |
| Bootstrap p | **0.54** | <0.05 (FAIL) |
| Conditional edge | -0.00017183097384127874 | Positive (FAIL) |
| Time in market | 0.15167095115681234 | >=0.05 (PASS) |
| Trade edge | -0.0006758684971090297 | Positive (FAIL) |
| Average holding | 3.933333333333333 dataset daily bars | Informational |

No positive evidence for this **late-entered ES CFD adaptation**. The original SPY opening-order strategy is not falsified by this test; the adaptation is a materially different execution environment.

**Disposition:** Freeze as Stage1 REJECT. Do not adjust session count, choose a different month day or invert signal based on these results, or call a full Warden backtest. Warden contract already had 6 prior registered selection attempts; the current attempt adds one. No protected holdout read and no live orders.
