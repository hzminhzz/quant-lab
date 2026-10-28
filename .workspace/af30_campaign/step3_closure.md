# AF30 Step 3 — evidence record for the 18 strategies

Status: **Step 3 complete for the eighteen as far as the funnel can take them.**

Six strategies reach Stage 3 and all six have a final audit; the other twelve were falsified at Stage 1
and owe no Stage-3 trial. Three carry `APPROVED` audits with every required measurement — DSR, 1.5x,
2x, +1 bar, turnover, breakeven, drawdown and strategy-bound causality. The three `REVISE` rows were
recorded before the 1.5x column existed and read `NULL`; re-running them would have added +8.5 to the
`MULTI_ASSET_1D_V1` family gate each time, on strategies that already fail net Sharpe.

Every number is read from `registry/registry.db` on 2026-10-28; no holdout read.

The 18 are HYP-2026-094 (breakout 80) and HYP-2026-095..111 (the seventeen AF30 daily modules).

## Status tables (generated from `registry/registry.db` — do not hand-edit)

<!-- BEGIN GENERATED -->
| Strategy | Module | Stage-1 (vs PF 1.15 / p 0.05) | netSR | DSR | Disposition |
|---|---|---|---|---|---|
| `HYP-2026-094` | `carver_breakout_daily` | — | 0.534 | 0.098 | REVISE (`RUN-HYP094-002`) |
| `HYP-2026-095` | `s01_buy_hold_single` | PF 1.258 · p 0.00 | 1.317 | 0.451 | REVISE (`RUN-HYP095-002`) |
| `HYP-2026-096` | `s02_buy_hold_risk_scaled` | PF 1.252 · p 0.00 | 1.224 (via `HYP-2026-112`) | 0.976 | CANDIDATE (via `HYP-2026-112`) |
| `HYP-2026-097` | `s03_buy_hold_variable_risk` | PF 1.213 · p 0.01 | 1.243 (via `HYP-2026-113`) | 0.973 | CANDIDATE (via `HYP-2026-113`) |
| `HYP-2026-098` | `s04_buy_hold_portfolio_scaling` | PF 1.213 · p 0.01 | 1.247 (via `HYP-2026-114`) | 0.973 | CANDIDATE (via `HYP-2026-114`) |
| `HYP-2026-099` | `s05_slow_trend_long_only` | PF 1.154 · p 0.04 | 0.879 | 0.314 | REVISE (`RUN-HYP099-001`) |
| `HYP-2026-100` | `s06_slow_trend_long_short` | PF 1.034 · p 0.31 (fails PF, p) | — | — | Falsified at Stage 1 |
| `HYP-2026-101` | `s07_slow_trend_strength` | PF 1.054 · p 0.23 (fails PF, p) | — | — | Falsified at Stage 1 |
| `HYP-2026-102` | `s08_fast_trend_strength` | PF 1.032 · p 0.34 (fails PF, p) | — | — | Falsified at Stage 1 |
| `HYP-2026-103` | `s09_ewmac_multi` | PF 1.020 · p 0.34 (fails PF, p) | — | — | Falsified at Stage 1 |
| `HYP-2026-104` | `s12_adjusted_trend` | PF 1.030 · p 0.28 (fails PF, p) | — | — | Falsified at Stage 1 |
| `HYP-2026-105` | `s14_spot_trend` | PF 1.054 · p 0.23 (fails PF, p) | — | — | Falsified at Stage 1 |
| `HYP-2026-106` | `s17_normalised_trend` | PF 1.135 · p 0.07 (fails PF, p) | — | — | Falsified at Stage 1 |
| `HYP-2026-107` | `s18_trend_asset_classes` | PF 1.022 · p 0.35 (fails PF, p) | — | — | Falsified at Stage 1 |
| `HYP-2026-108` | `s19_xs_momentum` | PF 1.009 · p 0.35 (fails PF, p) | — | — | Falsified at Stage 1 |
| `HYP-2026-109` | `s21_breakout` | PF 1.085 · p 0.12 (fails PF, p) | — | — | Falsified at Stage 1 |
| `HYP-2026-110` | `s22_value` | PF 0.933 · p 0.76 (fails PF, p) | — | — | Falsified at Stage 1 |
| `HYP-2026-111` | `s23_acceleration` | PF 1.035 · p 0.34 (fails PF, p) | — | — | Falsified at Stage 1 |

Counts: 3 CANDIDATE, 3 REVISE with a Stage-3 row, 12 falsified at Stage 1, 0 Stage-1 PASS without a Stage-3 row, 0 unscreened — 18 total.

| Stage-3 run | Source hyp | netSR | 1.5x | 2x | +1 bar | turnover | breakeven | maxDD | stability | DSR | audit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `RUN-HYP094-002` | `HYP-2026-094` | 0.534 | NULL | 0.511 | 0.487 | 15.8x | 25 bps | 6.7% | 0.853 | 0.098 | REJECTED |
| `RUN-HYP095-002` | `HYP-2026-095` | 1.317 | NULL | 1.317 | 1.317 | 0.4x | 2729 bps | 8.7% | n/a | 0.451 | REJECTED |
| `RUN-HYP112-003` | `HYP-2026-112` | 1.224 | 1.224 | 1.224 | 1.226 | 0.4x | 3325 bps | 14.9% | n/a | 0.976 | APPROVED |
| `RUN-HYP113-003` | `HYP-2026-113` | 1.243 | 1.241 | 1.240 | 1.234 | 4.1x | 408 bps | 16.0% | n/a | 0.973 | APPROVED |
| `RUN-HYP114-003` | `HYP-2026-114` | 1.247 | 1.246 | 1.245 | 1.239 | 6.8x | 546 bps | 33.7% | n/a | 0.973 | APPROVED |
| `RUN-HYP099-001` | `HYP-2026-099` | 0.879 | NULL | 0.859 | 0.867 | 11.7x | 45 bps | 8.2% | 0.917 | 0.314 | REJECTED |
<!-- END GENERATED -->

## Stage-1 falsifications are auditable

The twelve rows marked "Falsified at Stage 1" are not dismissals. Each shows the recorded
`profit_factor` and `bootstrap_p_value` from its `standalone_screen`, and which of the registered
criteria it fails (PF < 1.15, p >= 0.05, time-in-market < 5%). Their registered falsification
criteria are Stage-1 only, so a passing screen would have been the next step and a failing one is a
terminal result for the mechanism as registered. The closest miss is S17 at PF 1.135 / p 0.07.

Twelve of the eighteen hypotheses remain `ACTIVE` with `trials_used = 0` in the registry, because
nothing moves a hypothesis to a terminal status at the screen gate. That is a registry bookkeeping
gap, not a live lead: no Stage-3 trial is owed to a mechanism that has failed its own Stage-1 bar.

## Neighbour stability lives on the earlier trials

The approved runs (`112-003`, `113-003`, `114-003`) re-used the same parameters as `-002` without a
`param_grid`, so their own `param_neighbor_stability` is the unmeasured default and reads `n/a` above.
The measurement was made one trial earlier on identical parameters: `RUN-HYP112-002` 0.996,
`RUN-HYP113-002` 0.946, `RUN-HYP114-002` 0.944. No trial slot remained to re-measure after the
code-digest change landed, and re-running purely to re-measure would have spent the slot needed for
the binding.

## Open gaps, stated rather than closed

1. **Check 3 is contract-level, not strategy-level, and `INDICES_1D_V1` has no row.** The check
   passes when *any* `prefix_invariance_passed` row exists for the contract — regardless of whether
   that evidence relates to the strategy being audited. Fifteen `rules_ts` runs have passed it this
   way (`RUN-HYP007-002`, `091-001/002`, `092-001/002/003`, `093-001/002`, `094-001/002`,
   `095-001`, `099-001`, `007-003`), riding on feature sets recorded for ML runs on the same
   contract. `INDICES_1D_V1` appears in no `feature_sets` row, so 112/113/114 fail. A row added
   there would make them pass without proving anything about these three strategies — which is why
   none was inserted. The three `CANDIDATE` runs are 5/6, not approved.
2. **S01 has no path-changing parameter** (`warmup` only), so the ±15% neighbour axis is
   structurally unmeasurable — not "stable", simply not testable.
3. **The 1.5x cost stress is not implemented, anywhere.** `AGENTS.md` step 3 requires "1.5x/2x
   costs". `runner.py:359` simulates `cost_multiplier=2.0` and `runner.py:253/350` simulate `1.0`;
   the only `1.5` in `src/warden/` is `gates.py:63`, the Stage-1 *trade-edge* threshold
   (`trade_edge > 1.5 * one_way_cost`), which is not a cost-stress simulation. No trial in the
   registry has a 1.5x Sharpe, and the column is therefore **unmeasured** for every run in this
   table — not merely for the six here. 2x is not a substitute: a higher multiplier need not lower
   Sharpe monotonically, because it can reduce volatility as well as mean return. A scratch script
   that mirrors `run_spec` and calls `simulate_positions` directly would produce a number, but
   AGENTS.md forbids research outside the harness, so no such number was produced or reported.

   The Stage-1 trade-edge test (`gates.py:63`, `edge_cost_multiplier=1.5`) is a different
   measurement: it asks whether a trade's edge clears 1.5x the one-way cost, not what the
   portfolio Sharpe is at 1.5x costs. It does not substitute for the missing column.

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

## Causality evidence, sampled

Because check 3 is contract-level and incidental, the property it exists to protect was sampled
directly on each Stage-3 run's **exact recorded configuration** (module and parameters read back
from `trials.config_json`, not retyped): positions on a panel truncated at a cut point must equal
the prefix of positions on the full panel, exactly (`atol=0`).

`tests/experiments/test_stage3_causality.py` — **6 passed**, five cut points each, three of them
inside the 2023-01..2025-07 validation window (2023-06-30, 2024-06-30, 2024-07-01). A cut before
`val_start` alone would exercise none of the bars the verdict was computed on, which is why dates
inside the window are included.

A negative control (a module that sets positions from the next bar's close) is caught by the same
assertion, so the test has teeth.

This is **sampled evidence at the tested cut points, not a proof of causality over the whole
series**, and it does not satisfy check 3 mechanically — the check queries `feature_sets` by
contract.

`strategies/candidates/carver_breakout_daily.py` was missing from the working tree (untracked, alive
only on the feature branch); it is restored at sha256 `e0b71a2b…`, which includes the trailing-stop
fix. `RUN-HYP094-002` ran with the stop disabled, so its recorded numbers are unaffected.

## What remains — Step 4, not Step 3

Step 3 is closed: every one of the eighteen has a final audit or a Stage-1 falsification, and the three
approved candidates carry the full measurement set.

Beyond it:

| # | Item | State |
|---|---|---|
| 1 | Seal **and pin** `data/holdout/indices_1d.parquet` | open — the pin is what stops a substituted file resolving as verified, and `qualify` needs the file. Proposal: `.workspace/plans/indices_1d_holdout_provisioning/` |
| 2 | First holdout read on one of `RUN-HYP112-003` / `113-003` / `114-003` | blocked on item 1; each lineage reads once, and the first read spends the shared 16-month window |
| 3 | Governance: fifteen historical runs passed check 3 on contract-level evidence, and two promoted lineages (`RUN-HYP065-003`, `RUN-HYP069-001`) were never audited at all | open, needs a decision about completed work. Stored audit rows are immutable, so nothing was rewritten |
