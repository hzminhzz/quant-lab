# FX_MAJORS Stage 3 — preflight, frequency decision, and both blockers

**Status 2026-10-28: resolved and executed.** The principal approved `NEXT_BAR` on a Dukascopy daily
panel, so V2 was built and all five hypotheses ran. Final evidence is in
`.workspace/fx_stage3/stage3_closure.md` — 0 qualified, 5 `REVISE`, 0 holdout read. Sections 4 and 5
below are kept as the record of why V1 could not run; each carries a one-line resolution.

Read-only inspection plus registry writes. No holdout read, no evaluator change.
Registry and files read on 2026-10-28.

## 1. Preflight

| Check | Result |
|---|---|
| `uv run warden check-worktree` | exit 0 — `Worktree integrity verified` |
| `scripts/check_loc_budget.py` | `2604 / 2700` in `src/warden/`, **96 LOC headroom** |
| FX contracts with an exploratory panel | all seven: `1M, 5M, 15M, 30M, 1H, 4H, 1D` |
| `contract_search_state` rows for FX | **none — family gate clean** |
| `contracts` table rows for FX | none; the first `register-hypothesis` registers the contract lazily |

Panel facts, all eight majors, `funding_rate = 0.0` on every row:

| Contract | rows | span (exploratory) | bars/asset | cost/side |
|---|---|---|---|---|
| `FX_MAJORS_1D_V1` | 26,576 | 2015-01-01 .. 2025-06-30 | 3,281–3,350 | 0.7 bps |
| `FX_MAJORS_4H_V1` | 136,390 | 2015-01-01 20:00 .. 2025-06-30 20:00 | 16,908–17,143 | 0.7 bps |
| `FX_MAJORS_1H_V1` | 526,720 | 2015-01-01 22:00 .. 2025-06-30 23:00 | 65,417–66,128 | 0.7 bps |

Every contract validates on `val_start 2023-01-01` .. `val_end 2025-07-01`, so every Stage-3 number
below would come from a **2.50-year window** regardless of frequency.

Carry is unavailable as stated: `funding_rate` is exactly zero, so `carry_rv` mechanisms have no
input. Style, momentum and value do.

## 2. Scope of the mechanism set

Read from `selection_attempts` (`kind='standalone_screen'`), not from prose:

| Hypothesis | Module | Stage-1 | PF | p |
|---|---|---|---|---|
| HYP-2026-095 | s01 buy-and-hold single | PASS | 1.258 | 0.00 |
| HYP-2026-096 | s02 buy-and-hold risk-scaled | PASS | 1.252 | 0.00 |
| HYP-2026-097 | s03 buy-and-hold variable-risk | PASS | 1.213 | 0.01 |
| HYP-2026-098 | s04 buy-and-hold portfolio-scaling | PASS | 1.213 | 0.01 |
| HYP-2026-099 | s05 slow trend long-only | PASS | 1.154 | 0.045 |
| HYP-2026-109 | s21 breakout 80 | **REJECT** | 1.085 | 0.125 |

**S21 is out.** The brief lists "S01-S05 and the breakout" as the AF30 modules that qualify, and in
the same sentence puts s21 with the rest that failed Stage 1. The registry agrees with the second
half: `HYP-2026-109` recorded PF 1.085 / p 0.125 against registered bars of PF ≥ 1.15 and p < 0.05,
so the breakout mechanism has already failed its own Stage-1 bar on another contract. Re-testing it
on FX is selection shopping, which the runbook names as not-coverage. The candidate set is therefore
**S01–S05 only** (five mechanisms, five hypotheses).

Disclosure on independence: `MULTI_ASSET_1D_V1` already contains these same eight FX pairs. The
Stage-1 PASS above therefore already included FX legs, so an FX-only Stage-3 row is the FX leg of an
already-passing test, not an independent second observation of the same mechanism. It is still a
distinct contract with its own universe, cost floor and family gate, which is what licensing a
Stage-3 measurement requires; it is not new evidence about the mechanism's edge.

## 3. Frequency: `FX_MAJORS_1D_V1`

The decision is about **effective sample size, not rows**, and the numbers below are the argument.

### 3.1 Row count buys no statistical power

An annualised Sharpe's significance is fixed by **calendar time**, not bar count. Warden's DSR
(`registry.py:110`) is `Φ((sr − noise)/√(var/(n−1)))` with `sr` and `noise` in **per-bar** units, so
the per-bar Sharpe shrinks by `√bars_per_year` exactly as `√n` grows. Measured for `SR_ann = 1.0` on
the validation window:

| Frequency | bars in val | iid t-stat at SR=1.0 |
|---|---|---|
| 1D | 805 | **1.59** |
| 4H | 4,120 | **1.59** |
| 1H | 15,857 | **1.59** |

Identical to two decimals. Choosing 1H multiplies the row count by 19.7 and multiplies the evidence
by **1.0**. Any claim that 1H "gives more data" is a row-count claim, and row count is not the
quantity the gate measures.

### 3.2 Autocorrelation at 1H is not the friendly kind

Lag 1–20 autocorrelation of the panel's own returns on the validation window, per asset, median
across the eight majors, and the Lo variance-inflation factor `VIF = 1 + 2Σρ_k`:

| Frequency | VIF(20) | tail kurtosis of bar returns |
|---|---|---|
| 1D | 0.83 | 536 |
| 4H | 0.66 | 2,275 |
| 1H | 0.85 | 7,303 |

The raw price series is **negatively** autocorrelated at intraday sampling (bid-ask bounce), so it
does not inflate the t-statistic directly — but it also means the 1H series is mostly microstructure
noise, and the kurtosis growth (536 → 7,303) says the tail of the 1H distribution is dominated by a
handful of bad prints rather than by tradeable moves. More importantly, this VIF is measured on the
*price*, not on a *strategy*: a mechanism whose position changes on a multi-day scale produces a
return series at 1H that is ~20 overlapping observations of one decision, which is positive-VIF by
construction and would inflate the z-statistic by `√VIF ≈ 4.5`. Sampling finer cannot create
independent decisions out of a mechanism that makes two a year.

### 3.3 Cost against the bar the mechanism actually trades

`execution_mode: QUOTE_SIDE` means a long pays the ask and receives the bid, so a round trip is the
full spread, and `total_one_way_cost_decimal()` (0.7 bps) is charged on top per unit turnover.

| Frequency | median \|bar return\| | round-trip cost | cost as % of a median move |
|---|---|---|---|
| 1D | 25.2 bps | 1.4 bps | **5.6%** |
| 4H | 9.6 bps | 1.4 bps | 14.6% |
| 1H | 4.8 bps | 1.4 bps | **29.2%** |

At 1H a single round trip eats 29% of the median hourly move. For S01–S04 (turnover 0.4–6.8×/yr on
the indices analogue) cost is second-order at any frequency, but the *panel* must still be able to
host the mechanism without the cost floor swallowing the signal, and it cannot at 1H.

### 3.4 The mechanism is defined in days

The five modules are the book's daily implementation: `vol_window 1260` is 1,260 **days** (5 years)
in S02, `EWMAC(64, 256)` in S05 is 64 and 256 **days**. Re-basing those windows to 1H gives 7 weeks
and 11 days — a different strategy, not the one that passed Stage 1. Testing the passing mechanism
faithfully requires the frequency it was defined on.

**Decision: `FX_MAJORS_1D_V1`.** 4H and 1H add 5× and 20× the rows, zero statistical power, 3× and
5× the cost burden relative to the median move, and require re-defining the mechanism's windows.

## 4. Blocker B1 — the FX family cannot execute at all

`FX_MAJORS_*` declares `execution_mode: QUOTE_SIDE`. `warden.runner.execution_returns` requires
`bid` and `ask` columns for that mode and fails closed otherwise. Measured with the same call the
runner makes:

```
FX_MAJORS_1D_V1 FAIL ExecutionDataError : QUOTE_SIDE contract FX_MAJORS_1D_V1 requires ['bid', 'ask'];
                                       a close-price fallback is not a valid execution basis
FX_MAJORS_4H_V1 FAIL (same)
FX_MAJORS_1H_V1 FAIL (same)
```

No exploratory panel carries `bid`/`ask` — all seven FX panels and every other panel in
`data/exploratory/` are `[timestamp, asset, open, high, low, close, volume, funding_rate]`. The lake
does not have them either: `forex_minute_*` stores in `ml4t-data` are
`[timestamp, symbol, open, high, low, close, volume]`. There is no quote data anywhere in this repo
or its data lake.

Consequences:

1. **No FX contract has ever produced a trial.** The `trials` table has rows for eight contracts and
   none is FX; the seven FX contracts are the repo's only `QUOTE_SIDE` contracts. The quote-side
   code path in `runner.py:93-97` has never executed on real data, and no test exercises it against a
   real panel (the only QUOTE_SIDE tests use a synthetic frame in
   `tests/test_issue4_execution_alignment.py`).
2. **A `warden backtest` attempt would burn a slot and leave a FAILED row.** `run_spec` calls
   `register_trial_start` (which does `trials_used = trials_used + 1`) at `runner.py:307`, and the
   `ExecutionDataError` is raised 9 lines later at `runner.py:316`, inside the `except Exception`
   handler that writes `verdict='FAILED'`. Five strategies × one attempt = five permanently spent
   slots and five FAILED rows for zero evidence, plus family-gate pollution. **The backtest step was
   therefore not attempted**; `preflight` (which runs the identical path against a registry
   snapshot) was, and it refuses for the same reason at zero cost.

This is already recorded independently: `.workspace/oracle_sig/SIG-02_feasibility_matrix.md:47`
reaches the same conclusion ("the entire FX asset class is currently unusable"). It is a contract
defect, not a thesis verdict.

**Resolved** by the principal approving `NEXT_BAR` on a Dukascopy daily panel: the lake carries no
FX quotes at any frequency, so quote-side had no input, and V2 fills at `open₊₁`. The cost of that
choice is stated, not hidden — NEXT_BAR charges no spread (see §6).

## 5. Blocker B2 — the FX panel contains corrupt bars

Even with B1 repaired the panel is not fit for a Stage-3 verdict. `normalize_panel` validates OHLC
*ordering* but never cross-bar plausibility, so bars whose whole OHLC sits at a wrong price level
pass. Criterion used here: a bar whose close is more than 2% away from where the market reopened on
the next bar. At 1D, 16 bars qualify; classifying each one:

**Corrupt — spike and full reversal within one bar**, i.e. `|r_in| ≥ 2%`, `|r_out| ≥ 2%`, opposite
signs, and the two returns cancel to within 30% of the first:

| Asset | Date | close | r_in | r_out | max move inside one source minute |
|---|---|---|---|---|---|
| USDCAD | 2024-12-20 | 1.09843 | −23.7% | +30.7% | 23.33% |
| USDCAD | 2025-01-10 | 1.29037 | −10.4% | +11.7% | 10.49% |
| USDCAD | 2024-11-08 | 1.31320 | −5.3% | +5.9% | 11.88% |
| USDCAD | 2024-11-22 | 1.26885 | −9.3% | +9.8% | 8.44% |
| USDCAD | 2024-11-01 | 1.29301 | −7.2% | +7.7% | 6.55% |
| USDCAD | 2024-09-06 | 1.30633 | −3.2% | +3.8% | 3.73% |
| USDCAD | 2024-10-25 | 1.35518 | −2.2% | +2.5% | 2.45% |
| AUDUSD | 2022-10-09 | 0.73580 | +16.8% | −14.5% | 0.11% intra-minute, **15.71% minute-to-minute** |
| AUDUSD | 2024-11-29 | 0.63283 | −2.7% | +2.7% | 2.79% |
| AUDUSD | 2024-10-04 | 0.66386 | −3.1% | +2.4% | 2.26% |

Ten bars, and the same test clears the eleventh: GBPJPY 2023-07-27's −2.1%/+2.1% shows a maximum
intra-minute move of 0.40% and a maximum minute-to-minute move of 0.40%, so it is a genuine two-day
round trip and is **not** a defect. **Nine of the ten fall inside the 2023-01..2025-07 validation
window**; AUDUSD 2022-10-09 is in the training window.

Root cause is upstream, not in the salvage aggregation. For USDCAD 2024-12-20 the lake row
`forex_minute_USDCAD` 21:59 has `open=1.43271` and `close=1.09843` — one bad minute. The daily
resample takes `close = last` and `low = min`, so a single corrupt minute poisons the whole daily
bar. The AUDUSD 2022-10-09 partition is a stub: 192 minute rows for a full day, jumping between two
price levels. Two more level-shift bars fail the reversal test only because the recovery took two
bars: USDCAD 2022-11-11 (close 1.13213 → next open 1.32680, −15.1% then +17.2%) and 2022-11-12.

**Genuine market events, retained:** GBPJPY 2016-06-24 (−8.6%, Brexit), GBPUSD 2016-06-24,
CADJPY 2020-03-06, USDCHF 2015-01-15 (SNB de-peg), USDJPY 2022-12-20 (BoJ), GBPJPY 2023-07-27. Each
moves once and stays moved, which is why none of them fails the reversal test. The discriminator is
the reversal, not the speed: USDCHF 2015-01-15 really did move 6.84% inside one minute, and that is
the de-peg, not a bad row.

Why it matters for *these* mechanisms specifically: S01–S04 are long-only books sized by
`vol_target / trailing_vol`. A fake ±5–30% one-day move (a) enters the trailing volatility estimate
for `vol_window` bars, collapsing the position for up to 1,260 days under S02, and (b) makes the
simulator trade a real weight change at a fake price. The seven USDCAD bars fall inside the
2023-01..2025-07 validation window, so they are in the exact window the verdict would be computed
on. The Sharpe, drawdown, CVaR and DSR of any FX Stage-3 row are uninterpretable until these are
repaired, and repairing them changes the pinned `data_snapshot_sha256` — a data and contract change.

Also present: zero-range placeholder bars (`open=high=low=close`) — 55 at 1D in USDCAD, 47 in
EURUSD, 11 in USDJPY, 3 in AUDUSD, 4 in GBPUSD — and a stale-flat bar in USDCAD 2022-10-08.

**Resolved** by rebuilding the 1D panel from `forex_daily_*` (Dukascopy) instead of resampling the
LSE minutes, which is what `scripts/build_fx_majors_1d_v2.py` does. Dukascopy daily is clean on every
affected date (USDCAD 2024-12-20 close 1.43735 vs LSE 1.09843; AUDUSD 2022-10-09 0.63601 vs 0.73580;
AUDUSD 2024-11-29 0.65068 vs 0.63283). The LSE stores are left untouched — the defect is recorded,
not written over. One new find while building: `forex_daily_USDJPY` labels every row `symbol="JPY"`,
so the asset key is taken from the store name and the mismatch is recorded in the manifest.

## 6. What was done

Executed end to end, 2026-10-28, under the principal's approval of `NEXT_BAR` + Dukascopy:

1. `contracts/fx_majors_1d_v2.yaml` — NEXT_BAR, 313 bars/yr, digest `e1afda17…9764`, costs unchanged
   from V1 (0.2 + 0.5 bps) so the data change carries no cost change.
2. `data/exploratory/fx_majors_1d_v2.parquet` — 28,755 rows, 8 majors, 2014-01-01 → 2025-06-30,
   312.73 bars/yr, 0 rows at or after `holdout_start`. No holdout file was created or read.
3. `HYP-2026-120..124` registered, parented to the blocked `HYP-2026-115..119`.
4. Five specs with 2–4 point ±15% grids; all five preflighted clean on a registry snapshot (781 bars
   each, deterministic across repeated runs).
5. Five backtests, five `verify-causality` PASS (5 cut points each), five `audit-check` →
   `REJECTED`, all on check 2 (outlier sensitivity); S04 adds check 6, S05 adds 5 and 6.
6. `scripts/build_fx_stage3_closure.py` regenerated the ledger; `--check` passes. One trial slot per
   hypothesis used, so 2 of 3 remain everywhere.

Result: 0 qualified, 5 `REVISE`, net Sharpe 0.656 / 0.651 / 0.540 / 0.532 / −0.076, DSR 0.847 / 0.845
/ 0.800 / 0.796 / 0.335. Family gate `FX_MAJORS_1D_V2` now exists at `k_eff` 1.6226, noise 0.1948.

Remaining open item, stated rather than closed: the spread is still unmodelled (NEXT_BAR fills at the
open mid and charges only 0.7 bps per turn). S01's 0.656 does not care — 0.4×/yr turnover makes
1x/1.5x/2x/+1bar identical to three decimals — but S05 at 17.8×/yr is already negative at 1x, so no
FX verdict here is flattered by it. A cost floor calibrated to measured FX spreads is a separate
principal decision, tracked in `.workspace/fx_stage3/CONTRACT_CHANGE_PROPOSAL.md`.

## 7. The 1H and 4H contracts: measured, and deliberately not opened

Asked whether `FX_MAJORS_1H_V1` / `_4H_V1` should follow 1D onto a Dukascopy panel with `NEXT_BAR`.
Answer: **no.** Same 2.5-year validation window, eight majors, measured on the existing panels:

| freq | val bars | equal-weight long SR | S01 recorded | ann. vol | median \|bar\| | round trip | bars that can pay a round trip | corrupt bars |
|---|---|---|---|---|---|---|---|
| 1D | 805 | 0.657 | 0.656 | 4.1% | 24.75 bps | 1.4 bps | 95.2% | 10 |
| 4H | 4,119 | 0.584 | — | 4.8% | 9.82 bps | 1.4 bps | 90.4% | 9 |
| 1H | 15,852 | 0.567 | — | 5.3% | 4.88 bps | 1.4 bps | 82.7% | 14 |

Four independent reasons, each of them measured rather than asserted:

0. **Trades do multiply — 20× — and that is the problem, not the benefit.** Counted on the val window
   by running each module on each panel: S02/S03 go 313 → 1,623 → 6,284 trades/yr and S05 goes
   313 → 1,622 → 6,234 at 1D → 4H → 1H; S04 282 → 1,425 → 5,255. The reason is mechanical: each rule
   re-sizes its position **every bar** from a rolling statistic, so sampling finer multiplies
   transactions without multiplying signals. Only S01 does not change — 0.4 trades/yr at all three
   frequencies — because a constant `1/n` weight has nothing to rebalance. Converted to cost:
   S03 turnover 8.3 → 48.8 → 194.8 ×/yr, S05 17.8 → 76.5 → 290.2 ×/yr, and at 1H both go **negative at
   2× cost** (S03 −0.033, S05 −0.052) while their gross Sharpes are 0.314 and 0.584. "More bars" is
   not "more evidence"; in this family it is mostly "more commission".

1. **Frequency costs Sharpe and buys no power.** The same drift sampled finer adds only variance
   (ann. vol 4.1% → 5.3%), so the equal-weight book's Sharpe falls 0.657 → 0.567. The t-statistic of
   an annualised SR of 1.0 is 1.59 at all three frequencies, because the DSR denominator uses the
   per-bar Sharpe, which shrinks as fast as `√n` grows. Twenty times the rows, 1.0× the evidence.
2. **`NEXT_BAR` would be the wrong model here, not merely the only one.** At 1H a round trip is 29%
   of a median bar move and only 82.7% of bars clear it at all. A model that fills at the open mid
   and charges 0.7 bps manufactures edge at exactly the frequency where the real spread dominates.
   That is the opposite conclusion from 1D, where turnover is 0.4–17.8×/yr and the spread is noise.
3. **The corruption is relatively worse, not better.** One bad minute becomes one bad 1H bar, so the
   23% fake USDCAD close is a 470× outlier against a 4.88 bps median move (93× at daily), and the 1H
   panel carries 14 corrupt bars against 10 at 1D.
4. **The qualified intraday universe is five majors, not eight.** The production contract makes LSE
   canonical for Wave-1 FX and scopes it to exactly USDJPY, EURUSD, GBPUSD, AUDUSD, USDCAD.
   CADJPY, GBPJPY and USDCHF are frozen Dukascopy-legacy minute stores, 211–235h stale and
   `unqualified` in the lake catalog, so a compliant 1H/4H panel drops three of the eight legs. The
   existing `fx_majors_4h/1h` panels were built before that distinction mattered and are pinned
   snapshots; they are not evidence a rebuild is permitted.

There is also a mechanism-identity problem that no data fix addresses. The five modules' parameters
are **bar-denominated**: S05's `EWMAC(64, 256)` is 64/256 days at 1D, 2.7/10.7 days at 1H and
10.7/42.7 days at 4H. Running it at 1H is a different, much faster trend rule — and fast trend was
already falsified at Stage 1 in this family (S08 16/64 PF 1.032, S23 acceleration PF 1.035). S01–S04
are constant-weight, so for them frequency genuinely changes nothing but noise, which is exactly what
row 1 of the table shows: a strictly worse measurement of the same bet.

So 1H and 4H stay `BLOCKED_EXECUTION`. What would open them, in order: a quote-bearing intraday FX
source (Dukascopy tick exists only for gold), a qualified eight-major minute universe, an explicit
spread floor, and — separately — a 4H-native hypothesis with its own Stage-1 bar, not a re-run of a
daily rule on finer bars.

**One number in this table is a live lead, and it is not the reason to open the contract:**
`S05` `EWMAC(64,256)` on the 4H panel scores gross 0.867, net 0.779 at 1× cost and 0.690 at 2×, on
76.5 ×/yr turnover — the only intraday configuration that survives cost, against −0.040 on 1D in the
same window. It is not an artefact of the corrupt bars: dropping all ten raises it to 0.901. It is,
however, a *different mechanism* (10.7/42.7-day EWMAC, not 64/256-day), measured on one parameter
set over one 2.5-year window with no Stage-1 screen and no spread charge. If it is to be pursued it
becomes a new hypothesis on a new contract, pre-registered with its own falsification bars — the
window it passed in is the window every other FX number here came from, so one lucky period cannot
be told apart from a real effect without it.

Provider attribution caveat: the lake's generated catalog labels every `forex_*` store
`provider=dukascopy`, while `docs/dukascopy-wave1-production-contract.md` states LSE is the canonical
Wave-1 provider and the Dukascopy FX updater is retired. The minute bars are LSE in substance; the
catalog's `provider` field is stale. That does not change any number above (the panels are pinned
files), but it does mean provider attribution for FX should not be read from the catalog alone.

## 8. Governance state at close

- `uv run warden check-worktree` → exit 0.
- Five trials at `STAGE_3_STRESS`, all `data_provenance: verified_market`, all pinned to digest
  `e1afda17…9764`, zero synthetic rows, one trial slot each, five `causality_evidence` rows.
- **The manifest's approval record is stale and this is not mine to fix.** `.warden_manifest.sha256`
  now lists `contracts/fx_majors_1d_v2.yaml` (`06150fea…`) and
  `contracts/datasets/FX_MAJORS_1D_V2.json` (`95b291cc…`) with digests that match the files, but
  `.warden_manifest.sha256.meta.json` still records `manifest_sha256: b7e27e86…` and
  `approved_at: 2026-10-28T09:05:02Z` for an older manifest, and `git status` shows the manifest as
  modified and uncommitted. So the file was regenerated after the recorded approval by something
  other than this session. Re-run
  `uv run warden generate-manifest --human-approve-manifest` to refresh the record, and do not commit
  the dirty manifest before that. Trial evidence is unaffected — its pin is the dataset manifest and
  the contract digest, both independently verified by `resolve_dataset`.
