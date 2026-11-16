# Reproducing trends-research S183 on the ML4T stack

Walkthrough of what it takes to port a published futures composite onto our own data, what
survives the port, and what the harness says about it. Everything below is re-runnable from
a clean checkout.

**Outcome: the composite fails the Stage-1 signal gate on our panel — profit factor 1.11 for my
first reconstruction and 0.93 for a port of the repo's own code — and its DSR against this
contract's recorded search floor is 0.000. The published edge does not survive the port. The
reconstruction was too generous; see "Fidelity" below.**

## Correction: a real cross-asset bug, found after the first run

The first screen (`ATT-d107ad4f9fcac118-001`) ran on a pipeline where `ewm_mean`,
`pct_change(256)`, `pct_change(63)` and the forecast scaler were **not grouped by asset**.
On a symbol-major panel that seeds each asset's first windows with the previous asset's
last rows — cross-asset contamination, exactly the failure `ml4t-compute-features` warns
about. It is not time leakage (the bleed runs backwards across assets, never forwards in
time), but it is a bug and the first result could not be used.

Fixed by moving every windowed term behind an explicit `.over("symbol")` and splitting
sleeve construction into a raw pass and a scaling pass, so `forecast()` owns the only
window in its chain. Verified with warden's own checks:

```
verify_prefix_invariance(pipeline, panel, contract)                  PASS  (103 s)
verify_asset_independence(S183PerAssetPipeline(), panel, contract)   PASS  ( 28 s)
```

The second check runs on the per-asset sleeves only: the cross-sectional z-score and the
FDM are same-date cross-sectional quantities by construction, so shocking one asset is
*supposed* to move the others. Their time-causality is covered by prefix invariance.

Re-running the screen after the fix (`ATT-d107ad4f9fcac118-002`) gives:

| | buggy | fixed |
|---|---|---|
| profit factor | 1.111848 | 1.111899 |
| conditional edge | 1.19835e-4 | 1.19850e-4 |
| bootstrap p | 0.120 | 0.120 |

**The verdict is unchanged and the numbers move in the fourth decimal.** The bug was real
but numerically immaterial at 3,359 bars: the contaminated window is the first ~256 rows
of each asset, and the forecast scaler is dominated by the asset's own subsequent history.
Both attempts are in the registry; neither consumed a trial.


## What is reproduced, and what cannot be

| S183 component | Status here | Why |
|---|---|---|
| Trend sleeve (TS EWMAC 16/64, 32/128, 64/256 + XS z-scored trend) | **reproduced** | pure price; constants copied from `ig_strategy_183.py` |
| Skew sleeve (−252-bar skew) | **reproduced** | pure price |
| Vol-of-vol sleeve | **reproduced** | pure price |
| Carry sleeve (25% of the composite) | **omitted** | the lake has no futures term structure: no multi-contract curves, no open interest |
| Sigmoid vol / drawdown overlays | **shipped, not in the first build** | I wrongly said their form was in the unshipped fitted layer. It is in `Live/2_Engine/trading_book.py:157-167` (`s_g_vol`) and `Live/3_Portfolio/portfolio.py:32-35` (gdd), the reference port uses `s_g_vol`; `gdd` is engine-path only |
| 62 instruments, 1990–2026, Panama-adjusted continuous futures | **not available** | our contract has 17 instruments, 2015-01 → 2025-06, single continuous series per instrument |

## Fidelity: what the first build actually was

The first build (`s183_composite.py`, HYP-2026-126) was a **reconstruction from the flagship
file's constants**, not a port. The repo ships a working reference implementation in `Live/`
and I did not read it before building. Divergences, now measured:

| Component | Reference (`Live/2_Engine/trading_book.py`) | My reconstruction | Effect |
|---|---|---|---|
| volatility | `0.7*EWM_std(32) + 0.3*rolling_std(2560)` of **close differences**, annualised | `realized_volatility(35)` — std of **returns** | different sizing input everywhere |
| EWMAC | raw price difference, normalised by its own mean-abs | `(ema_f - ema_s)/close/vol_bar × Carver scalar`, then mean-abs | different signal |
| normaliser | rolling 256 sum/count, needs a full window, **no shift** | EWM(256) mean-abs, min 64, **shifted** | different scale path |
| VoV | 21-bar vol → 64-bar std → 256 mean | 64-bar vol → 64-bar std → 256 mean | different nesting |
| skew | `rolling_skew(256)` | `higher_moments(252)` | minor |
| FDM | `clip(1/sqrt(w'Cw),1,2)` on the EWM covariance of 4 sleeve means | pairwise-average ρ, `sqrt(N/(1+(N-1)ρ))` | same under equicorrelation, different estimator |
| weights | `0.25` each, carry included | `1/3` each, carry renormalised away | my version inflated gross by 33% |
| IDM | `min(4, 1/sqrt(w'Cw))` | **absent** | my version under-sized in calm regimes |
| vol gate | `s_g_vol`, floor 0.5 | **absent** | |
| drawdown gate | `gdd`, 64-bar, −10%, floor 0.5 | **absent** | |
| buffer | `|target−held| > 0.10*|held|` | `> 0.10*|target|` | |
| carry | present (term structure) | absent (no curves in the lake) | unavoidable |

## The reference port

`s183_reference.py` (HYP-2026-127) imports the repo's own module by path and calls its
functions in its own order — `ewmac`, `normalise_signal`, `skew`, `vov_*`, `s_g_vol`,
`cross_sectional_z`, `ewm_corr_4`, plus an `idm_from` transcribed from `portfolio.py`. This is
a **reference-based approximation, not the strategy**: carry is absent, the drawdown gate is
not applied, integer contract sizing and roll costs are replaced by fractional weights, and
pointsize/FX are dropped because the algebra cancels at FX=1. Its 1/4 carry slot is held at
zero rather than renormalised.

| | reconstruction (HYP-2026-126) | reference port (HYP-2026-127) |
|---|---|---|
| profit factor | 1.1119 | **0.9276** |
| conditional edge | +1.2 bps/bar | **−1.6 bps/bar** |
| avg holding | 10.1 bars | 4.3 bars |
| gross exposure | ~0.9 | 2.6 avg, 12.1 max |
| turnover | — | 0.44/day (137×/yr) |
| Stage-1 | REJECT | **REJECT** |

The verdict is unchanged; the sign is not. **No isolated cause is established.** The two
builds differ in six components at once — volatility estimator, normaliser, EWMAC
construction, VoV nesting, FDM estimator, and the presence of IDM, the vol gate and the
carry slot — plus sizing and execution details. A targeted ablation (swap one component at a
time) was not run, so "the reconstruction was too generous" is an observation about the two
builds, not an attribution to any one defect.

**Provenance of the first build.** `s183_composite.py` was rewritten in place and then
restored functionally, so it is *not* the file that produced `ATT-d107ad4f9fcac118-001/-002`.
No strategy-code hash was recorded for those screens (only returns-stream hashes
`848aeb44...481e` and `c8c4a430...369c`); the original bytes are unrecoverable and those two
attempts cannot be reproduced from this tree.

One attempt (`ATT-453bfc8cee3e3c7c-001`) ran with the vol scaling inverted (divided by
`sqrt(256)` instead of multiplying): 256× leverage, identical PF 0.9276 because profit factor
is scale-invariant, holding 0.02 bars. Corrected in `-002`; both are recorded.

**The drawdown gate is not exercised by the screen.** `gdd` needs the account equity path,
which only an execution engine has; warden's runner simulates positions itself and returns
no equity, so applying it inside `generate_positions` would mean a hand-rolled P&L loop —
which the house rules forbid. It belongs in the engine path (`reproduce_s183.py`).

## The five steps

### 1. Pin the data to a contract

`contracts/multi_asset_1d_v1.yaml` → `MULTI_ASSET_1D_V1`: 17 instruments, daily,
312 bars/year, `execution_mode: NEXT_BAR`, `execution_delay_bars: 1`, fee 0.5 bps +
slippage 0.5 bps. The panel digest is verified against the contract pin on every run
(`reproduce_s183.py:load_panel`); a mismatch aborts.

### 2. Compute the features with ml4t-engineer

```python
from ml4t.engineer import compute_features
compute_features(df, [
    {"name": "realized_volatility", "params": {"period": 35, "annualize": True, "trading_periods": 312}},
    {"name": "higher_moments", "params": {"window": 252}},                      # skew, kurtosis
    {"name": "volatility_of_volatility", "params": {"vol_period": 64, "vov_period": 64, "annualize": False}},
], group_col="symbol", calendar=None)
```

Two things bite on a sparse union panel: `calendar=None` is required (the default
bar-grid contract demands a declared feed grid we do not have), and `higher_moments`
declares `input_type: returns`, so a `returns` column must exist before the call.

EWMAC has no registry feature, so the three speed pairs are built from
`ewm_mean` with Carver's Table-29 scalars — signal maths, not a duplicate of a library
function.

### 3. Assemble the composite

Each sleeve is normalised to `mean|forecast| = 10` on a trailing window and capped at ±20;
the composite is their equal-weight mean times an FDM from the trailing correlation of the
sleeve forecasts, capped at √3. Sizing is Carver forecast-to-position with a 0.5
per-instrument cap and a 10% trade buffer. Implementation:
`strategies/candidates/s183_composite.py` (one module, no duplicate logic).

### 4. Pre-register, then screen

```bash
uv run warden register-hypothesis .workspace/hypotheses/registered/HYP-2026-126.yaml --json
uv run warden screen experiments/active/hyp_2026_126_screen.yaml --json
```

`HYP-2026-126` was registered **before** the run, with the falsification criteria and the
contract's DSR floor written into the spec. Registration is immutable.

```json
{"passed": false,
 "metrics": {"avg_holding_bars": 10.08, "bootstrap_p_value": 0.12,
             "conditional_edge": 0.0001198, "profit_factor": 1.1118,
             "time_in_market": 1.0, "trade_edge": 0.001208},
 "notes": "PF=1.11, Edge=1.2bps, TiM=100.0%, Holding=10.1h"}
```

Recorded as `ATT-d107ad4f9fcac118-001`, `STAGE_1_SIGNAL`, **REJECT**. The `rules_ts` gate
needs profit factor ≥ 1.15, edge > 1.5× one-way cost and bootstrap p < 0.05. Zero trials
consumed (0/3) — the harness stops at the signal gate.

### 5. Stop there

No Stage-3 backtest was run. A failed gate is evidence, not permission to try the next
threshold.

## What the ML4T stack reports on the same signals

Before the governance correction the sleeves were also run through `ml4t.backtest`'s
event-driven engine (`reproduce_s183.py`) — **this run is an unregistered diagnostic and
must not be read as a trial verdict.** It is kept because it measures execution realism,
which the harness's vectorised simulator does not.

```python
config = BacktestConfig(
    execution_mode=ExecutionMode.NEXT_BAR, execution_price=ExecutionPrice.OPEN,
    commission_type=CommissionType.PERCENTAGE, commission_rate=0.5 / 10_000,
    slippage_type=SlippageType.PERCENTAGE, slippage_rate=0.5 / 10_000,
)
```

| vol_target | Sharpe | ann_ret | ann_vol | maxDD | PSR | DSR vs floor |
|---|---|---|---|---|---|---|
| 0.20 (S183 nominal) | **0.453** | 3.2% | 7.60% | −13.14% | 0.940 | **0.000** |
| 0.13 (overlay-equivalent) | **0.391** | 1.9% | 5.20% | −8.69% | 0.911 | **0.000** |

(Corrected twice. The 0.69/0.76 figures quoted earlier in this file were **long-only**:
`BacktestConfig.allow_short_selling` defaults to False, so the engine dropped every short
leg. With shorts enabled the same book scores 0.39–0.45. The earlier 0.74/0.75 pair was
also pre-grouping-fix. Only the values above are current.)

Read the two probability columns together, because this is the exact error the source repo
makes: **PSR 0.995 says only "Sharpe > 0".** Deflated against this contract's recorded
search floor (k_eff 45.61 → expected max Sharpe 1.394 under the null), the same returns
score **DSR 0.000**. A single-strategy probability is not a discovery claim.

## What `ml4t-diagnostic` says about the signal itself

`analyze_signal` on the trend sleeve (17 assets, ~2,860 dates per horizon), then
`compute_ic_hac_stats(..., label_horizon=H)` for overlap-robust inference — a 21-bar
forward return sampled every bar overlaps ~21×, so the naive t is inflated:

| horizon | IC | naive t | HAC t (p) | IC_IR | monotonicity | turnover |
|---|---|---|---|---|---|---|
| 1D | 0.0137 | 2.08 | **2.13 (0.034)** | 0.039 | 0.10 | 0.26 |
| 5D | 0.0148 | 2.26 | **1.22 (0.222)** | 0.042 | 0.10 | 0.38 |
| 21D | 0.0367 | 5.83 | **1.76 (0.079)** | 0.109 | 0.30 | 0.59 |

The 21-bar IC of 0.037 looks strong at a naive t of 5.8 and is **not significant once
the overlap is priced in** (HAC t 1.76, p 0.08). Only the 1-bar horizon is nominally
significant (t 2.13, p 0.03), and that is before any correction across the three horizons
tested. Every horizon also fails the tradeability gates from `ml4t-evaluate-factor`:
IC_IR 0.04–0.11 against a 0.5 floor, quantile monotonicity 0.10–0.30 against 1.0, and
turnover up to 0.59 at the horizon where the IC peaks. Weak, non-monotonic and
high-turnover is exactly the shape that dies on costs — which is what the Stage-1
edge-per-bar gate detected independently.

## Stage 3 (requested explicitly, run anyway)

Neither build passed the Stage-1 signal gate, so neither "qualified" for Stage 3. At the
principal's explicit request the reference port was taken to Stage 3 regardless:
`HYP-2026-127` → `RUN-RU-001`, trial 1 of 3, code `e8997c9e…81582`.

| measure | value | Stage-3 requirement |
|---|---|---|
| net Sharpe | **−0.4553** | > 1.0 |
| gross Sharpe | −0.3534 | — |
| 1.5× cost Sharpe | −0.5062 | > 0 |
| 2× cost Sharpe | −0.5571 | > 0.50 |
| +1 bar delay Sharpe | −0.4858 | > 0 |
| DSR | **0.0011** | ≥ 0.90 |
| k_eff gate / expected max under null | 56.11 / 1.4435 | — |
| turnover | 146.4×/yr | — |
| breakeven cost | −3.46 bps | > 0 |
| max drawdown | 25.1% | — |
| profit factor | 0.9276 | ≥ 1.15 (Stage 1) |

Verdict **REVISE**. Every economic criterion fails, and the Stage-1 gate is re-evaluated
inside the backtest and fails again (PF 0.93, edge −1.6 bps, bootstrap p 0.715).

`warden verify-causality` **passed at 5 cut points**. That is adversarial sampling, not a
proof: a feature that only differs after the last cut is not detected. It is evidence
against lookahead at those points, not a demonstration that the book is free of timing
artefacts.

Six-check audit: **3/6 — REJECTED.**

| check | result |
|---|---|
| 1 target/rebalance alignment | pass |
| 2 outlier sensitivity | **fail** |
| 3 point-in-time causality | pass (after `verify-causality` recorded the evidence) |
| 4 fold-local fitting | pass |
| 5 cost and latency survival | **fail** |
| 6 selection-bias accounting | **fail** |

The trial is spent: 1 of 3 used, and the family gate ratcheted from k_eff **45.61 → 56.11**
(expected max Sharpe 1.4435 under the null, n = 806, skew −0.31, excess kurtosis 4.70).
The exact requirement for DSR 0.90 is now **netSR 2.27** (2.51 for 0.95), computed with the
harness's own formula rather than estimated.

**Provenance.** Warden's code closure hashes only `s183_reference.py`; the executed sleeve and
gate logic is imported at runtime from the upstream clone. Pinned in
`RUN-RU-001_provenance.json`: upstream commit `0426bac`, `trading_book.py`
`f7bfac29…7f0a5`, `portfolio.py` `4f5fa856…18d48` (transcribed, not imported), Live tree
`9d07c3d8…bf7`. The clone is gitignored, so a `git pull` there would silently change future
runs; the sidecar is the only pin.

## Reconciliation: the discrepancy was a missing short-selling flag

The engine and warden disagreed by 1.16 Sharpe on the same matrix. The cause is not the
marking convention and not the weights: `BacktestConfig.allow_short_selling` defaults to
**False**, so every `ml4t.backtest` run in this investigation silently clamped shorts at
zero. All 11,341 holdings rows in that run were non-negative — a long-only book.

With `allow_short_selling=True`, the same matrix through the engine:

| run | Sharpe |
|---|---|
| engine, gate off (shorts enabled) | **−0.090** |
| engine, gate on | **−0.424** |
| engine, gate off (shorts disabled — the earlier number) | +1.033 |

and warden on the same matrix: −0.126 (close-to-close, delay 1) / −0.455 (open-to-open,
delay 1). Flipping the flag collapses the gap from 1.16 to **0.036** Sharpe on this matrix,
so it accounts for the bulk of the discrepancy. The residual is **not attributed**, and the
engine-side fixture comparison below is 3.7e-4 rather than exact, so the two simulators are
*not* proven equivalent.

Verified by a controlled fixture (`reconcile_sims.py`, one asset, five bars, zero costs):

- warden matches its hand-computed open-to-open P&L exactly (max diff 0.0);
- the engine matches a hand-computed share-based recursion only to **3.7e-4**. My hand model
  of the engine's sizing basis at a position flip is an approximation, so this is close, not
  exact, and does not establish the engine's accounting.

**Sign depends on latency, not just on the book.** With the contract's mandated one-bar
delay every variant measured is negative (−0.09, −0.126, −0.455, −0.424 with the gate). With
the delay removed, warden's open-to-open score is **+0.310** and close-to-close **+0.079**.
The book's sign therefore turns on execution latency; it is not "unprofitable" in an
absolute sense, it is unprofitable *at the latency the contract mandates*.

The gate makes it worse by 0.33 Sharpe under this convention (−0.090 → −0.424). One
bar of latency costs 0.77 Sharpe under open-to-open; open-to-open versus close-to-close
is worth 0.33.

### Coverage of the reference logic

| piece | status |
|---|---|
| EWMAC, normalise_signal, skew, vov_*, s_g_vol, cross_sectional_z, ewm_corr_4 | imported from `trading_book.py` |
| IDM | re-implemented; differential test vs `portfolio.ewm_corr_sum` + `idm_from`: max diff 4.4e-16 |
| pooled FDM | re-implemented; differential test vs `trading_book.pooled_fdm`: exact (4 sleeves; the upstream function hard-codes N=4, so the 3-sleeve generalisation is mine) |
| composite assembly (Sign_raw → FDM → normalise → EWM 0.5 → clip) | differential test vs a literal transcription of `trading_book.main()`'s block, on the same sleeve inputs: **exact, 39,071 rows, max diff 0.0** (`test_assembly.py`) |
| sizing algebra | differential test (`k·pointsize·price` vs `1/rel_vol`): exact |
| drawdown gate | engine path only; shape property-tested (neutral 0.8655 vs the audit's 0.866). **Not proven faithful**: its input is computed differently from the reference's `cum` |
| carry | **unavailable** — no futures term structure in the lake |
| Panama roll adjustment | **unresolved; the check is descriptive only.** `ml4t-data`'s catalogue declares `continuous_series: false`, `contract_level_futures: false`, `roll_semantics_known: false` for all 17 stores (Dukascopy), so continuity is not certified. Two observations, neither conclusive: the 40 CL moves > 8% all fall on named macro events, and mean absolute return by day-of-month shows no outlier. The second is weak — real roll dates shift with holidays and contract cycles, the day-of-month samples are not independent, and no null model or power analysis was validated. Effect on EWMAC: **not quantified.** A synthetic roll-injection sensitivity would answer it, but that needs a `data_provenance: synthetic` contract, which is human-owned; it is proposed in `STATUS.md`, not run |
| FX / pointsize assumptions | `1/rel_vol` replaces `pointsize·FX·price/sigma`; exact only when FX = 1, which is an assumption about each series being single-currency, not a verified property |
| integer contracts, roll costs, pointsize/FX | not modelled (algebra cancels at FX = 1) |

Parity tests: `test_parity.py`, 5/5. Assembly: `test_assembly.py`, exact. Simulator
reconciliation: `reconcile_sims.py` — warden exact against hand-computed P&L, the engine to
3.7e-4, so the two simulators are reconciled to 0.036 Sharpe on this matrix but not proven
equivalent.

`s183_reference.py`'s module docstring still says "our panel is already one continuous
series". That sentence is wrong for CL and is superseded by the table above; the module is
frozen because two trials are bound to its code hash, so the correction lives here rather
than in the file.

A subtlety the assembly test exposed: `normalise_signal` is stateful over the sequence it is
given, so it must be run on each instrument's own bars — the way `book_one` does — not on the
union grid with NaN gaps. Both give identical FDM and identical `Sign_raw`, and differ by up
to 11.7 in `FDM_MASTER` if you get the slicing wrong.

## Stage 3, trial 2: costs are not the constraint

**This is a labelled deviation, not the author's strategy.** The source uses
`BUFFER = 0.10` (`portfolio.py:37`); trial 2 runs `buffer_fraction=0.50`. It tests whether a
wider trade band rescues the book, and it says nothing about whether the 10% buffer has been
faithfully reproduced — that is trial 1 and the screen. `RUN-RU-002`:

| | trial 1 (band 10%) | trial 2 (band 50%) |
|---|---|---|
| gross Sharpe | −0.353 | **−0.234** |
| net Sharpe | −0.455 | **−0.300** |
| 2× cost | −0.557 | −0.367 |
| +1 bar delay | −0.486 | −0.438 |
| turnover /yr | 146.4× | 102.6× |
| DSR | 0.0011 | 0.0021 |

Cutting turnover by 30% improves net Sharpe by 0.16, and gross Sharpe is still **−0.234 at
the contract's mandated one-bar delay**.

**What that does and does not say.** It does not say the signal has no edge: the *same
matrix* with the extra delay removed scored **+0.310** gross, so the result is
latency-conditional, and latency is plainly a first-order factor rather than a footnote.
It also does not close the question, because this build omits carry and does not model
integer contract sizing or roll costs. What it does establish: widening the trade band is not
sufficient, and at this latency every configuration tried is negative.

Two of three trials are spent. The remaining one should go to a **validated fidelity fix**
(e.g. carry, if a term-structure source appears), not to further parameter tuning: trial 2 is
a deviation from the source, and the evidence does not support spending the last trial on
another one.

### Per-bar reconciliation of the two simulators

Same matrix, shorts enabled, engine (next-open fill, close mark, 1 bp) vs warden
(close-to-close, delay 1, contract cost), 806 bars:

| measure | value |
|---|---|
| correlation of daily returns | 0.922 |
| mean daily return, engine / warden | −0.0035% / −0.0060% |
| mean absolute bar difference | 1.66e-3 (16 bps) |
| max absolute bar difference | 3.0e-2 |
| cumulative, engine / warden | −4.63% / −7.33% |

The residual is therefore not noise: warden is systematically ~2.5 bps/day lower, which
compounds to ~2.7 pp over the window. It is **bounded and unattributed** — consistent with a
different cost/slippage application and fill valuation, not with a P&L wiring error, but not
proven either.

## Against the published numbers

| | S183 (published) | This reproduction |
|---|---|---|
| Sharpe | 0.97 raw / 0.78 BHY | 0.39–0.45 (Engine, long/short, full panel) |
| Profit factor | not reported | 1.11 (gate: ≥ 1.15) |
| Bootstrap p | not reported | 0.12 (gate: < 0.05) |
| Universe | 62 futures, 36 years | 17 instruments, 10.5 years |
| Sleeves | trend + carry + skew + vov | trend + skew + vov |

The Sharpe is in the same neighbourhood as the published figure; the *statistical* and
economic conclusions are not, and the two failures are different claims:

- **Single-test significance: passes.** PSR 0.99 says the Sharpe is above zero at
  conventional confidence. A 0.7 Sharpe over 3,359 bars is not noise.
- **Discovery-level significance: fails.** Against this contract's recorded search floor
  (expected max Sharpe 1.394 under the null from 45.6 effective trials), DSR is 0.000.
  Being above zero is not the same as being above what the search itself produces.
- **Economic gate: fails.** The Stage-1 edge is 1.2 bps/bar against a 1.5× cost hurdle,
  so the position does not pay for its own trading regardless of significance.

## Re-run

```bash
uv run warden screen experiments/active/hyp_2026_126_screen.yaml --json
uv run python .workspace/investigations/trends_research_20261029/reproduce_s183.py
uv run python .workspace/investigations/trends_research_20261029/signal_hac.py
```

`reproduce_s183.py` writes `reproduction/daily_returns_vol{0.20,0.13}.parquet` and
`reproduction/summary.json` (naive IC); `signal_hac.py` writes
`reproduction/signal_hac.json` (overlap-robust IC — the numbers quoted above). It is a diagnostic harness, not a research entry point:
results that could inform a decision must go through `warden`.

## What would change the answer

- **Carry.** It is 25% of the original and uncorrelated with trend (repo correlation matrix:
  0.12 average). Without a futures curve we cannot test it — and a trend+carry+skew+vol
  composite is a different object from trend+skew+vol.
- **Breadth and history.** The contract's DSR floor (1.794 netSR at k_eff 45.61) is a
  consequence of 10 prior trials on 17 instruments. A 62-instrument panel over 36 years
  would have a much lower noise floor, and the same ~0.45 Sharpe could clear it.
- **A different contract.** `MULTI_ASSET_1D_V1` is the only contract here with cross-asset
  breadth, and its floor is already 45.6. Any further trend test on it starts from that
  handicap.
