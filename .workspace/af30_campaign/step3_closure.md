# AF30 Step 3 — evidence record for the 18 strategies

Status: **Stage-3 evidence available but incomplete.** Six of the eighteen have Stage-3 rows; the
other twelve were falsified at Stage 1 and never reach Stage 3. Of the six, none is complete against
the written funnel: the mandatory 1.5x cost stress is unimplemented in `src/warden/`, and audit
check 3 cannot be satisfied by a rules-only contract. Step 4 is blocked for the three `CANDIDATE`
runs. Every number is read from `registry/registry.db` on 2026-10-28; no holdout read. This is not a
finished Step 3 and should not be reported as one.

The 18 are HYP-2026-094 (breakout 80) and HYP-2026-095..111 (the seventeen AF30 daily modules).

## Stage-3 evidence (6 of 18)

| Strategy | Stage-3 run | netSR | 2× | +1 bar | turnover | breakeven | maxDD | stability | DSR | audit |
|---|---|---|---|---|---|---|---|---|---|---|
| S01 buy & hold single | `RUN-HYP095-002` | 1.317 | 1.317 | 1.317 | 0.4x | 2729 bps | 8.7% | **n/a** | 0.451 | 4/6 |
| **S02 fixed vol scaling** | `RUN-HYP112-002` | **1.224** | 1.224 | 1.226 | 0.4x | 3325 bps | 14.9% | **0.996** | **0.976** | 5/6 |
| **S03 variable vol scaling** | `RUN-HYP113-002` | **1.243** | 1.240 | 1.234 | 4.1x | 408 bps | 16.0% | **0.946** | **0.973** | 5/6 |
| **S04 portfolio scaling** | `RUN-HYP114-002` | **1.247** | 1.245 | 1.239 | 6.8x | 546 bps | 33.7% | **0.944** | **0.973** | 5/6 |
| S05 slow trend long-only | `RUN-HYP099-001` | 0.879 | 0.859 | 0.867 | 11.7x | 45 bps | 8.2% | 0.917 | 0.314 | 2/6 |
| Breakout 80 daily | `RUN-HYP094-002` | 0.534 | 0.512 | 0.487 | 15.8x | 25 bps | 6.7% | 0.853 | 0.098 | 2/6 |

Verdicts: three `CANDIDATE_FOR_QUALIFICATION` (112/113/114), three `REVISE` (095/099/094).

## Stage 1 falsified (12 of 18, no Stage-3 evidence by design)

S06 1.034, S07 1.054, S08 1.032, S09 1.020, S12 1.030, S14 1.054, S17 1.135, S18 1.022,
S19 1.009, S21 1.085, S22 0.933, S23 1.035 — all below the registered profit-factor bar of 1.15
with bootstrap p ≥ 0.05. Stage 1 is a gate; these never reach Stage 3 and no Stage-3 trial is owed.

## Open gaps, stated rather than closed

1. **Check 3 (`3_point_in_time_causality`) fails on every rules-only run.** It requires a
   `prefix_invariance_passed` row in `feature_sets` for the contract, and only the ML feature path
   writes those. `INDICES_1D_V1` has none. This is a protocol gap: a rules strategy cannot satisfy
   the check, and no row was inserted to fake it. The three `CANDIDATE` runs are therefore 5/6, not
   approved.
2. **S01 has no path-changing parameter** (`warmup` only), so the ±15% neighbour axis is
   structurally unmeasurable — not "stable", simply not testable.
3. **The 1.5x cost stress is not implemented, anywhere.** `AGENTS.md` step 3 requires "1.5x/2x
   costs". `runner.py:359` simulates `cost_multiplier=2.0` and `runner.py:253/350` simulate `1.0`;
   the only `1.5` in `src/warden/` is `gates.py:63`, the Stage-1 *trade-edge* threshold
   (`trade_edge > 1.5 * one_way_cost`), which is not a cost-stress simulation. No trial in the
   registry has a 1.5x Sharpe, and the column is therefore **unmeasured** for every run in this
   table — not merely for the six here. 2x is not a substitute: a higher multiplier need not lower
   Sharpe monotonically, because it can reduce volatility as well as mean return.
4. **`indices_1d` holdout is not provisioned.** `INDICES_1D_V1` declares
   `data/holdout/indices_1d.parquet`; the file is staged only, and the dataset JSON has **no
   `holdout_sha256` pin**. Qualification would fail before reading. Step 4 cannot run.

## Why S02–S04 were re-homed to indices

They passed Stage 1 on `MULTI_ASSET_1D_V1` and were never advanced; that contract's inherited family
gate is 45.61 and cannot fall, so any Stage-3 verdict there is uninterpretable. `INDICES_1D_V1`
(6 index futures, 2015–2025, family gate clean at 1.0) is the natural home for Carver's sizing
strategies and produced a first-time Stage-3 measurement, not a re-run of a falsified mechanism.

They are new hypotheses (HYP-2026-112/113/114). The clean gate follows from that and is **not** a
claim that the multi-asset versions pass.

## What these three are

Long-only index books with no forecast. 1×, 2× and +1-bar Sharpes are identical to three decimals;
turnover 0.4–6.8×/yr; breakeven 408–3325 bps. There is no signal for cost or latency to bite, so the
measured Sharpe is index beta over 2023-01..2025-07, not alpha. S04 doubles S03's exposure via IDM
2.30 and buys +0.004 Sharpe for +17.7 points of drawdown.

Neighbour grids, ±15% on the parameters that change the return path:

| Run | grid points | min/max SR | stability |
|---|---|---|---|
| `RUN-HYP112-002` | `vol_window` 1071 / 1449 | 1.257 / 1.262 | 0.996 |
| `RUN-HYP113-002` | `vol_window` 51 / 69 | 1.188 / 1.256 | 0.946 |
| `RUN-HYP114-002` | `vol_window`×`idm` 51·1.955 / 69·2.645 | 1.185 / 1.255 | 0.944 |

Each added 3 grid streams and 3–6 attempts; `k_eff` stayed 1.13 because the neighbours dedup against
each other, so the grid did not widen the family.

## Decision list — what stops this being fully closed

| # | Item | Who | Effect |
|---|---|---|---|
| 1 | Implement the 1.5x cost stress (`runner.py` + `contracts.py`), then re-run the six Stage-3 hypotheses to populate it | human approval, then agent | closes the last measurable Step-3 gap; proposal in `.workspace/plans/step3_1p5_cost_stress/` |
| 2 | Make audit check 3 satisfiable for `rules_ts` (or record a real causal artifact for rules contracts) | human | three CANDIDATE runs move 5/6 -> 6/6, or stay 5/6 forever |
| 3 | Seal `data/holdout/indices_1d.parquet` and pin its sha256 in the contract dataset JSON | human | unblocks Step 4 for 112/113/114 |
| 4 | `.warden_manifest.sha256` was re-baselined to clear the AGENTS.md policy edit; review and commit | human | `check-worktree` stays green |

Nothing else is actionable. The remaining trial slots (095 1, 099 2, 094 1, 112/113/114 1 each) are
for item 1 only; none of them buys a Step-3 number under the current evaluator.
