# FX_MAJORS Stage 3 — closure ledger

Status: **five mechanisms registered and run on `FX_MAJORS_1D_V2`, zero qualified, five `REVISE`, no
holdout read.**

The set is S01–S05 of the AF30 daily suite, re-homed from the blocked `FX_MAJORS_1D_V1` attempt
(HYP-2026-115..119) to HYP-2026-120..124 on the rebuilt contract. S21 is not in the set:
`HYP-2026-109` recorded PF 1.085 / p 0.125 against bars of PF ≥ 1.15 / p < 0.05 on
`MULTI_ASSET_1D_V1`, so the breakout mechanism has already failed its own Stage-1 bar.

`FX_MAJORS_1D_V2` is a Dukascopy daily panel (8 majors, 2014-01-01 → 2025-06-30, 312.73 bars/yr,
digest `e1afda17…9764`), `execution_mode: NEXT_BAR`. V1 could not run at all: it declares
`QUOTE_SIDE` and no store in the repo or the lake carries `bid`/`ask` for a currency pair, so
`execution_returns` fails closed before a single bar is simulated.

Every number below is read from `registry/registry.db`; regenerate with
`uv run python scripts/build_fx_stage3_closure.py` and verify with `--check`.

<!-- BEGIN GENERATED -->
Blocker for `FX_MAJORS_1D_V2`: none — contract is executable

| Strategy | Module | Stage-1 (PF 1.15 / p 0.05) | netSR | DSR | Disposition |
|---|---|---|---|---|---|
| `HYP-2026-120` | `s01_buy_hold_single` | PF 1.258 · p 0.00 (via `HYP-2026-095`) | 0.656 | 0.847 | REVISE (`RUN-HYP120-001`) |
| `HYP-2026-121` | `s02_buy_hold_risk_scaled` | PF 1.252 · p 0.00 (via `HYP-2026-096`) | 0.651 | 0.845 | REVISE (`RUN-HYP121-001`) |
| `HYP-2026-122` | `s03_buy_hold_variable_risk` | PF 1.213 · p 0.01 (via `HYP-2026-097`) | 0.540 | 0.800 | REVISE (`RUN-HYP122-001`) |
| `HYP-2026-123` | `s04_buy_hold_portfolio_scaling` | PF 1.213 · p 0.01 (via `HYP-2026-098`) | 0.532 | 0.796 | REVISE (`RUN-HYP123-001`) |
| `HYP-2026-124` | `s05_slow_trend_long_only` | PF 1.154 · p 0.04 (via `HYP-2026-099`) | -0.076 | 0.335 | REVISE (`RUN-HYP124-001`) |

Counts: 0 CANDIDATE, 5 REVISE with a Stage-3 row, 0 blocked before any trial, 0 falsified at Stage 1, 0 unscreened — 5 total.

| Stage-3 run | Source hyp | netSR | 1.5x | 2x | +1 bar | turnover | breakeven | maxDD | stability | DSR | audit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `RUN-HYP120-001` | `HYP-2026-120` | 0.656 | 0.656 | 0.655 | 0.656 | 0.4x | 677 bps | 5.7% | 1.000 | 0.847 | REJECTED |
| `RUN-HYP121-001` | `HYP-2026-121` | 0.651 | 0.651 | 0.650 | 0.650 | 1.0x | 422 bps | 9.0% | 0.991 | 0.845 | REJECTED |
| `RUN-HYP122-001` | `HYP-2026-122` | 0.540 | 0.536 | 0.532 | 0.504 | 8.3x | 46 bps | 10.6% | 0.965 | 0.800 | REJECTED |
| `RUN-HYP123-001` | `HYP-2026-123` | 0.532 | 0.530 | 0.529 | 0.529 | 6.0x | 130 bps | 22.2% | 0.855 | 0.796 | REJECTED |
| `RUN-HYP124-001` | `HYP-2026-124` | -0.076 | -0.086 | -0.097 | -0.127 | 17.8x | -2 bps | 10.9% | -3.706 | 0.335 | REJECTED |

### AF30 coverage: all 18, and why only five were re-tested on FX

| Hypothesis | Module | Stage-1 on `MULTI_ASSET_1D_V1` | Stage-3 (anywhere) | FX Stage-3 |
|---|---|---|---|---|
| `HYP-2026-094` | `carver_breakout_daily` | — (never screened) | `RUN-HYP094-002` REVISE 0.534 | not run — breakout: Stage-1 REJECT as `HYP-2026-109`, Stage-3 REVISE as `HYP-2026-093` |
| `HYP-2026-095` | `s01_buy_hold_single` | PASS · PF 1.258 · p 0.00 | `RUN-HYP095-002` REVISE 1.317 | `RUN-HYP120-001` · netSR 0.656 |
| `HYP-2026-096` | `s02_buy_hold_risk_scaled` | PASS · PF 1.252 · p 0.00 | `RUN-HYP112-003` CANDIDATE 1.224 | `RUN-HYP121-001` · netSR 0.651 |
| `HYP-2026-097` | `s03_buy_hold_variable_risk` | PASS · PF 1.213 · p 0.01 | `RUN-HYP113-003` CANDIDATE 1.243 | `RUN-HYP122-001` · netSR 0.540 |
| `HYP-2026-098` | `s04_buy_hold_portfolio_scaling` | PASS · PF 1.213 · p 0.01 | `RUN-HYP114-003` CANDIDATE 1.247 | `RUN-HYP123-001` · netSR 0.532 |
| `HYP-2026-099` | `s05_slow_trend_long_only` | PASS · PF 1.154 · p 0.04 | `RUN-HYP099-001` REVISE 0.879 | `RUN-HYP124-001` · netSR -0.076 |
| `HYP-2026-100` | `s06_slow_trend_long_short` | REJECT · PF 1.034 · p 0.31 | — | not run — REJECT is terminal on the 17-instrument book |
| `HYP-2026-101` | `s07_slow_trend_strength` | REJECT · PF 1.054 · p 0.23 | — | not run — REJECT is terminal on the 17-instrument book |
| `HYP-2026-102` | `s08_fast_trend_strength` | REJECT · PF 1.032 · p 0.34 | — | not run — REJECT is terminal on the 17-instrument book |
| `HYP-2026-103` | `s09_ewmac_multi` | REJECT · PF 1.020 · p 0.34 | — | not run — REJECT is terminal on the 17-instrument book |
| `HYP-2026-104` | `s12_adjusted_trend` | REJECT · PF 1.030 · p 0.28 | — | not run — REJECT is terminal on the 17-instrument book |
| `HYP-2026-105` | `s14_spot_trend` | REJECT · PF 1.054 · p 0.23 | — | not run — REJECT is terminal on the 17-instrument book |
| `HYP-2026-106` | `s17_normalised_trend` | REJECT · PF 1.135 · p 0.07 | — | not run — REJECT is terminal on the 17-instrument book |
| `HYP-2026-107` | `s18_trend_asset_classes` | REJECT · PF 1.022 · p 0.35 | — | not run — REJECT is terminal on the 17-instrument book |
| `HYP-2026-108` | `s19_xs_momentum` | REJECT · PF 1.009 · p 0.35 | — | not run — REJECT is terminal on the 17-instrument book |
| `HYP-2026-109` | `s21_breakout` | REJECT · PF 1.085 · p 0.12 | — | not run — REJECT is terminal on the 17-instrument book |
| `HYP-2026-110` | `s22_value` | REJECT · PF 0.933 · p 0.76 | — | not run — REJECT is terminal on the 17-instrument book |
| `HYP-2026-111` | `s23_acceleration` | REJECT · PF 1.035 · p 0.34 | — | not run — REJECT is terminal on the 17-instrument book |

Coverage: 5 measured on FX (three of them also reached Stage 3 on `INDICES_1D_V1` via `HYP-2026-112/113/114`), 12 Stage-1 REJECT carried as terminal, 1 excluded on the mechanism, 0 unclassified — 18 total.
<!-- END GENERATED -->

## Why there is no `QUOTE_SIDE` row

Quote-side is the better fill model and it is unavailable for FX. Dukascopy FX in the lake is OHLCV
only (`forex_daily_*`); the only quote-bearing store in the FX class is `forex_tick_XAUUSD`
(`bidPrice`/`askPrice`, 190.2M ticks), which is gold. Where quotes do exist the spread is a measured
quantity — median 1.72 bps on gold, p90 2.28, p99 3.71 — and `NEXT_BAR` charges none of it: it fills
at the open mid and takes only `turnover × 0.7 bps`. **Every Sharpe in this ledger is therefore
optimistic by roughly the half-spread, and the floor was deliberately left at V1's value rather than
re-priced inside a data change.** For S01 the effect is nil (0.4×/yr turnover); for S05 at 17.8×/yr it
is not.

## Cost and latency are inert where the mechanism has no turnover

| Run | turnover | 1x | 1.5x | 2x | +1 bar |
|---|---|---|---|---|---|
| `RUN-HYP120-001` | 0.4×/yr | 0.656 | 0.656 | 0.655 | 0.656 |
| `RUN-HYP121-001` | 1.0×/yr | 0.651 | 0.651 | 0.650 | 0.650 |
| `RUN-HYP122-001` | 8.3×/yr | 0.540 | 0.536 | 0.532 | 0.504 |
| `RUN-HYP123-001` | 6.0×/yr | 0.532 | 0.530 | 0.529 | 0.529 |
| `RUN-HYP124-001` | 17.8×/yr | −0.076 | −0.086 | −0.097 | −0.127 |

S01–S04 barely move: a book that never resizes has no cost path to stress, so the gate is not
testing costs there, it is testing drift. S05 is the only row where cost and latency bite, and it is
already negative at 1x — a breakeven of −1.8 bps means it loses money before costs are charged.

S01's stability of `1.000` is **measured, not the unmeasured default**: the book has no
path-changing parameter, so the two-point grid perturbs `warmup` only and the two streams are
identical by construction. S05's `−3.706` is the opposite extreme — a ±15% change to either EWMAC
span flips the sign of the Sharpe, which is a genuine instability rather than a grid artefact.

## Every audit fails the same check

Five audits, all `REJECTED`, all on **check 2 (outlier sensitivity)**; S04 adds check 6 (selection
bias) and S05 adds checks 5 and 6.

Check 2 failing everywhere is the expected signature of a long-only FX book measured over 2.5 years:
the return is carried by a handful of dollar-move bars, so removing the top 5% best bars collapses
it. That is not a defect in the audit, it is the finding. Check 3 passes for all five because
`verify-causality` binds evidence to the run on this contract — the first time a `rules_ts` audit on
a fresh contract has had strategy-bound rather than incidental causality evidence.

## What the carried Stage-1 rows do and do not say

Every Stage-1 number in the coverage table was recorded on `MULTI_ASSET_1D_V1` — a **17-instrument
book** (8 FX majors + 6 indices + 2 metals + crude) — by the earlier AF30 campaign, not on FX. The
FX campaign read those rows; it did not re-run them on multi-asset. Three consequences, none of them
cosmetic:

1. **These modules are not FX strategies.** They are instrument-agnostic AF30 sizing and trend rules
   whose registered home was the 17-instrument book. Applying them to an FX-only universe is a new
   contract, which is why it produced new hypotheses (120–124) instead of re-running 095–099.
2. **A carried REJECT is universe-specific.** It says the mechanism failed to clear PF 1.15 / p 0.05
   *on that book*. It is not an FX Stage-1 bar, and this ledger does not claim one exists.
3. **The same applies to the five that passed.** S01–S05 cleared Stage 1 on the 17-instrument book
   too, so the FX Stage-3 rows above have no FX-specific Stage-1 screen behind them. Their sizing
   also differs by construction: `weight = vol_target / n_assets / vol_ann` divides by 8 on FX and by
   17 on the multi-asset book, so the FX book is a different portfolio at ~2.1× the per-leg weight,
   not a re-levered copy of the tested one. Cross-contract Sharpe comparisons (0.66 FX vs 1.32
   multi-asset for S01) are therefore not like-for-like.

Screening the twelve on FX is possible without spending trial budget — `run_stage1_screen` records a
`selection_attempt`, not a trial — but it is not free. `validation_attempt_count` counts every
attempt, while `selection_history` excludes `STAGE_1_SIGNAL` streams from the observed set, so each
screen is *unvectorised* and adds `UNVECTORIZED_PENALTY = 0.5` to `k_eff_gate`. The FX family gate
currently sits at 1.6226 (5 trials + 14 vectorised grid attempts, 0 unvectorised); twelve screens
would make it 1.6226 + 0.5 × 12 ≈ **7.6**, and push the noise floor up with it. That is the
principal's call, not a step to take quietly.

## What the book actually is (quote convention is not normalised)

`FX_MAJORS_1D_V2` mixes quote conventions: three pairs are `XXX/USD` (AUDUSD, EURUSD,
GBPUSD) and three are `USD/XXX` (USDCAD, USDCHF, USDJPY), plus two USD-free crosses
(CADJPY, GBPJPY). **Nothing in the pipeline normalises this.** `execution_returns` builds
`open₊₁/open − 1` per pair and `simulate_positions` sums `Σ position × return`, so a long is
long *the pair as quoted* — long USDJPY is long USD and short JPY, the opposite dollar
direction from long EURUSD.

Converting S01's equal 1/8 weights into currency exposure gives the real position:

| | EUR | GBP | AUD | CAD | CHF | JPY | USD |
|---|---|---|---|---|---|---|---|
| S01 | +1/8 | **+2/8** | +1/8 | 0 | −1/8 | **−3/8** | **0** |

USD nets to exactly zero — an accident of having three pairs on each side, not a design
choice. What is left is a **37.5%-of-gross short-JPY book**, and that bloc carries the result:

| variant (S01, exact harness accounting) | Sharpe | ann. return | ann. vol |
|---|---|---|---|
| as registered | **0.657** | 2.71% | 4.13% |
| USD-base legs inverted (all legs "long foreign") | 0.548 | 2.83% | 5.16% |
| crosses dropped, 6 USD pairs only | 0.485 | 0.99% | 2.05% |
| **JPY bloc only, 3 legs** | **0.639** | 2.28% | 3.57% |
| non-JPY legs only, 5 legs | 0.221 | 0.43% | 1.95% |

Three JPY legs produce 84% of the return and 97% of the Sharpe. So this is not a "long FX
majors" book; it is predominantly a short-JPY position. **`funding_rate = 0` is nevertheless the
correct setting for it, not a hole**: a spot book's P&L already embeds the rate differential
through covered interest parity — the yen's low rate shows up as USDJPY drifting up, which is the
+1.21% that leg earned. Charging an explicit carry series *on top* of spot returns would double
count it. What the spot-only model genuinely cannot do is (a) decompose the +6.32% into carry
versus trend, so a carry bet can pass as a style or momentum edge, (b) model rollover markup above
CIP, which is what a retail/CFD venue actually charges, and (c) represent crash risk: a
carry-earning book loses its year in a week when the funding currency rallies. The verdict is
therefore "this is an un-decomposed carry position", not "this is free money".

Two smaller caveats: P&L is summed in each pair's own quote currency with no conversion to the
account currency (second-order, but "gross 1.00" is not a clean USD notional), and inverting
the USD-base legs makes the book *worse*, so "fixing" the convention is a different strategy,
not a correction.

These variant numbers are unregistered diagnostics on S01's recorded position path, not
trials; no budget was spent and nothing was registered.

## Reading the disposition column

- `CANDIDATE` / `REVISE` — a Stage-3 trial exists for this hypothesis.
- `BLOCKED_EXECUTION` — the contract cannot produce a trial. An engineering gate, **not** a thesis
  verdict.
- `Falsified at Stage 1` — a recorded `standalone_screen` `REJECT`. Terminal for the mechanism.

The blocker cell is derived by calling the same `require_execution_columns` the runner calls, so it
would turn into `none — contract is executable` on any future contract that cannot execute.
