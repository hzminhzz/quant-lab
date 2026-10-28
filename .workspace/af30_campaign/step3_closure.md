# AF30 Step 3 — closure record for all 18 strategies

Status: **closed.** Every strategy in the AF30 set now has a recorded Step-3 disposition. Three carry
`CANDIDATE_FOR_QUALIFICATION` on a clean indices contract. Every number below is read from
`registry/registry.db` on 2026-10-28. No holdout was read.

The 18 are HYP-2026-094 (breakout 80) plus HYP-2026-095..111 (the seventeen AF30 daily modules).

## Disposition

| Strategy | Module | Stage 1 | Stage 3 run | netSR | DSR | Disposition |
|---|---|---|---|---|---|---|
| S01 buy & hold single | `s01_buy_hold_single` | PASS PF 1.258 | `RUN-HYP095-002` | 1.317 | 0.451 | REVISE — buy-and-hold beta, no mechanism to revise |
| **S02 fixed risk scaling** | `s02_buy_hold_risk_scaled` | PASS PF 1.252 | **`RUN-HYP112-001`** (indices 1D) | **1.224** | **0.976** | **CANDIDATE_FOR_QUALIFICATION** |
| **S03 variable risk scaling** | `s03_buy_hold_variable_risk` | PASS PF 1.213 | **`RUN-HYP113-001`** (indices 1D) | **1.243** | **0.973** | **CANDIDATE_FOR_QUALIFICATION** |
| **S04 portfolio scaling** | `s04_buy_hold_portfolio_scaling` | PASS PF 1.213 | **`RUN-HYP114-001`** (indices 1D) | **1.247** | **0.973** | **CANDIDATE_FOR_QUALIFICATION** |
| S05 slow trend long-only | `s05_slow_trend_long_only` | PASS PF 1.154 | `RUN-HYP099-001` | 0.879 | 0.314 | REVISE — all four revision axes passed, both revisions refused |
| S06 slow trend long/short | `s06_slow_trend_long_short` | REJECT PF 1.034 | — | — | — | Falsified at Stage 1 |
| S07 slow trend + strength | `s07_slow_trend_strength` | REJECT PF 1.054 | — | — | — | Falsified at Stage 1 |
| S08 fast trend + strength | `s08_fast_trend_strength` | REJECT PF 1.032 | — | — | — | Falsified at Stage 1 |
| S09 multi-EWMAC | `s09_ewmac_multi` | REJECT PF 1.020 | — | — | — | Falsified at Stage 1 |
| S12 adjusted trends | `s12_adjusted_trend` | REJECT PF 1.030 | — | — | — | Falsified at Stage 1 |
| S14 spot trend | `s14_spot_trend` | REJECT PF 1.054 | — | — | — | Falsified at Stage 1 |
| S17 normalised trend | `s17_normalised_trend` | REJECT PF 1.135 | — | — | — | Falsified at Stage 1 (closest miss: 1.135 vs 1.15) |
| S18 class composite | `s18_trend_asset_classes` | REJECT PF 1.022 | — | — | — | Falsified at Stage 1 |
| S19 cross-sectional momentum | `s19_xs_momentum` | REJECT PF 1.009 | — | — | — | Falsified at Stage 1 |
| S21 breakout 80 | `s21_breakout` | REJECT PF 1.085 | — | — | — | Falsified at Stage 1 |
| S22 value | `s22_value` | REJECT PF 0.933 | — | — | — | Falsified at Stage 1 (negative edge) |
| S23 acceleration | `s23_acceleration` | REJECT PF 1.035 | — | — | — | Falsified at Stage 1 |
| Breakout 80 daily | `carver_breakout_daily` | n/a | `RUN-HYP094-002` | 0.534 | 0.098 | REVISE — no open axis; inert trailing stop fixed |

Counts: 3 CANDIDATE, 3 REVISE with Stage-3 evidence, 12 falsified at Stage 1.

## Why S02–S04 were re-homed to indices

They passed Stage 1 on `MULTI_ASSET_1D_V1` and were never advanced. That contract's inherited family
gate is 45.61 — set by the seventeen screens plus four prior trials — and cannot fall, so any
Stage-3 verdict there is uninterpretable. `INDICES_1D_V1` (6 index futures, 2015–2025, family gate
clean at 1.0) is the natural home for Carver's sizing strategies and gives a first-time Stage-3
measurement rather than a re-run of a falsified mechanism.

These are new hypotheses (HYP-2026-112/113/114), not the multi-asset ones moved. The clean family
gate is a consequence of that, and it is **not** a claim that the multi-asset versions now pass.

## What the three CANDIDATES actually are

All three are long-only index books with no forecast. Their 1x, 2x-cost and +1-bar Sharpes are
identical to three decimals (S02: 1.224/1.224/1.226), turnover is 0.4–6.8x/yr and breakeven cost is
408–3325 bps. There is no signal for costs or latency to bite. The measured Sharpe is index beta over
2023-01..2025-07, not alpha.

| Run | netSR | 2x | +1 bar | turnover | breakeven | maxDD | PF | DSR |
|---|---|---|---|---|---|---|---|---|
| `RUN-HYP112-001` | 1.224 | 1.224 | 1.226 | 0.4x | 3325 bps | 14.9% | 1.250 | 0.976 |
| `RUN-HYP113-001` | 1.243 | 1.240 | 1.234 | 4.1x | 408 bps | 16.0% | 1.230 | 0.973 |
| `RUN-HYP114-001` | 1.247 | 1.245 | 1.239 | 6.8x | 546 bps | 33.7% | 1.232 | 0.973 |

S04 doubles S03's exposure via IDM 2.30: +0.004 Sharpe for +17.7 points of drawdown.

Two measurement caveats:

- `param_neighbor_stability = 1.000` for all three is the **no-grid default**, not a measurement.
  No `param_grid` was supplied, so the axis was not tested.
- Six-check audit is **5/6** for all three, failing only `3_point_in_time_causality`, which requires
  a `prefix_invariance_passed` row in `feature_sets` for the contract. Only the ML feature path
  writes those, so a rules-only contract can never satisfy check 3. This is a protocol gap, not a
  strategy failure — and it is not worked around by inserting a row.

## Step 4 is blocked for these three

`INDICES_1D_V1` declares `holdout_data_path: data/holdout/indices_1d.parquet`. That file is
**staged, not sealed** (`data/holdout_staging/indices_1d.parquet`), and the contract's dataset JSON
carries **no `holdout_sha256` pin**. Qualification would fail before reading anything. Unblocking it
requires promoting the file and pinning its digest — a contract change, not a trial.
