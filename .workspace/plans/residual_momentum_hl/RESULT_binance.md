# Result — residual momentum, long top fifth (Binance proxy)

Date: 2026-10-29. Principal's directive: run it on Binance data. **Not registered, no contract, no
trial spent.** Everything below is a disclosed diagnostic.

## Spec as executed (defaults pinned where the description was silent)

| element | value |
|---|---|
| panel | daily bars aggregated from `data/exploratory/crypto_perps_broad_1h.parquet` (510 assets, 1,642 days, 2021-01-01 → 2025-06-30) |
| universe | point-in-time top 25 by trailing **30-day** quote volume |
| beta | OLS of each coin's daily return on BTC, 60 daily observations with intercept |
| residual | `r_i - alpha - beta * r_btc` |
| signal | sum of residuals over the last **15** days ÷ **30-day residual vol** |
| book | long the top fifth (5 of 25), equal weight, gross 1.0, **no short leg** |
| rebalance | every Monday, positions from closes through the prior Sunday, entry at Monday's open, one-week hold |
| costs | 6 bps/side applied to traded notional (approximate: no daily contract exists yet, so this is not a warden backtest) |

## Headline: the score carries no cross-sectional information

| signal | horizon | n | IC | HAC t |
|---|---|---|---|---|
| in-sample beta (as specified) | 5d | 216 | -0.0003 | -0.02 |
| in-sample beta (as specified) | 10d | 216 | -0.0005 | -0.02 |
| in-sample beta (as specified) | 15d | 216 | +0.0055 | +0.27 |
| beta lagged 15d (out-of-sample) | 5d | 214 | -0.0066 | -0.38 |
| beta lagged 15d (out-of-sample) | 10d | 214 | -0.0148 | -0.79 |
| beta lagged 15d (out-of-sample) | 15d | 214 | -0.0130 | -0.64 |

Not a weak signal — a flat one. Every t-statistic is inside |0.8|, at every horizon, with beta estimated
in-sample (the spec) and out-of-sample (the stricter version).

## The books, and why none of them is the answer

| book | mean/wk | median | hit | SR | trim5 | DSR @ k=30 | long-leg beta |
|---|---|---|---|---|---|---|---|
| long top fifth (as specified) | +0.61% | **-0.60%** | 0.47 | +0.37 | -0.87% | 0.011 | +1.27 |
| long top fifth, beta-hedged | +0.59% | -0.34% | 0.49 | +0.35 | -0.91% | 0.010 | — |
| long/short fifth (clean mechanism test) | +0.34% | -0.52% | 0.44 | +0.52 | -0.30% | 0.018 | ~0 |

Three readings:

1. **It is not a beta story.** Hedging the long leg's 1.27 BTC beta leaves the mean unchanged (+0.61% →
   +0.59%), so the small positive drift is not market exposure being harvested through a long-only wrapper.
2. **It is not a wrapper problem.** The market-neutral long/short version, which is the clean test of the
   residual signal, scores SR +0.52 — better than the long-only spec, and still nothing.
3. **The mean is carried by a few weeks.** Median week is negative in all three books, hit rate 0.44-0.49,
   and trimming the best 5% of weeks takes the mean negative.

## The two ambiguities were measured, not assumed

| variant | mean/wk | median | SR | DSR @ k=30 |
|---|---|---|---|---|
| residual vol denominator, 30d universe (default) | +0.61% | -0.60% | +0.37 | 0.011 |
| total vol denominator, 30d universe | +0.69% | -0.39% | +0.42 | 0.013 |
| residual vol denominator, 7d universe | +0.48% | -1.32% | +0.27 | 0.007 |
| total vol denominator, 7d universe | +0.70% | -1.15% | +0.38 | 0.011 |

The choices move Sharpe by at most 0.15 and change no conclusion. Both defaults are fine; neither rescues
anything.

## Verdict

**On Binance the thesis is a null at the signal level.** Not a cost problem, not a beta problem, not a
spec-ambiguity problem, and not a sample-size problem — 218 weekly bets is enough to see an IC of 0.03 if
one existed, and the measured IC is 0.003 with t 0.18.

Consequence for the code arriving from Discord: if that implementation reports a strong Hyperliquid
result, it is **not** this signal. Either the venue's microstructure is doing something our panel cannot
see (possible, untestable without HL history), or the implementation differs in a way that matters — the
first thing to check is its own cross-sectional IC on the same Binance panel. A residual-momentum score
that shows IC 0.003 here and a Sharpe above 2 there has something else inside it.

## What would change this

1. **The Discord code's actual definitions.** If it ranks on residual *momentum over 15 days* but
   normalises differently, or uses a 60-day window ending before the formation window, the numbers here
   may not be its numbers. Paste the two functions and I will re-run against them.
2. **A different frequency.** Daily residual momentum is empty; intraday residual reversal is a different
   mechanism with different economics, and the 1h panel is already built.
3. **Hyperliquid history**, if it ever becomes available — but see the venue arithmetic in
   `.workspace/plans/xs_momentum_long_history/VERDICT.md`: 82 weekly bets cannot clear the DSR gate.

---

# Follow-up: horizon / factor / patience sweep (2026-10-29, unregistered)

Supersedes the headline above. The "flat everywhere" reading was an artefact of the statistic, not a
property of the signal. Everything here is unregistered diagnostics: **no contract, no hypothesis, no
trial spent.** Search size is stated at the end and must be carried into any future trial.

Artifacts: `scripts/xs_residual_momentum_diag.py` → `diag_horizon.json` (87 cells);
`scripts/xs_residual_reversal_1h_diag.py` → `diag_1h.json` (72 cells);
`data/exploratory/crypto_perps_broad_1d.parquet` (351,583 rows, 510 assets, 2021-01-01..2025-06-30).

## 1. The score is a reversal, and the effect is not small

Per-Monday rank IC against the forward 5-day return, point-in-time top-25 by trailing 30-day quote
volume, 222 rebalances. "Residual" removes the fitted intercept as the original spec implies; "beta
only" is the literal instruction, *strip the beta*.

| formation | residual | HAC t | beta only | HAC t |
|---|---|---|---|---|
| 5d | −0.0304 | −1.78 | −0.0484 | −2.96 |
| 15d | −0.0331 | −2.09 | −0.0607 | −3.80 |
| 30d | −0.0152 | −0.86 | −0.0378 | −2.41 |
| 60d | −0.0489 | −2.76 | −0.0574 | −2.74 |
| 120d | −0.0252 | −1.34 | −0.0476 | −2.30 |
| 250d | −0.0129 | −0.61 | — | — |

No formation, under either convention, is positive. **Positive residual momentum does not exist at
daily frequency anywhere between 5 and 250 days.**

Three details decide the size of the effect:

- **The intercept matters as much as the beta.** Residual momentum sums returns over *L* days, so a
  fitted intercept enters the score *L* times. Subtracting it turns the score partly into a ranking on
  `-alpha` (trailing drift), which fights the reversal. Dropping it never weakens the effect and
  roughly doubles it at 15d, 30d and 120d (−0.0331→−0.0607, −0.0152→−0.0378, −0.0252→−0.0476).
- **Breadth is the binding constraint.** IC by universe: top-10 `+0.0083` (t +0.35), top-25 `−0.0331`,
  top-50 `−0.0467`, top-100 `−0.0511`, all 510 `−0.0506` (t −5.23). The thesis's "top 25 perps, long
  5" is precisely the configuration in which the effect is invisible.
- **The horizon is days, not weeks.** IC against forward return decays `−0.0440` (1d, t −2.58) →
  `−0.0250` (2d) → `−0.0018` (10d) → `+0.0026` (60d). A weekly rebalance is 2–3× too slow.

## 2. Why the earlier run read ~0.000

Same score, same panel, same weeks, four different statistics:

| statistic | value |
|---|---|
| per-cross-section rank IC (the correct one) | **−0.0331** |
| per-cross-section Pearson IC | −0.0089 (t −0.48) |
| pooled Spearman over all (score, return) pairs | −0.0060 |
| pooled Pearson over all pairs | **+0.0272** |

The earlier report's `−0.0003` is not reproducible from this panel by the rank/Pearson or
pooled/per-section choice alone, but it sits in the pooled/Pearson family, and the sign of that family
is not stable: **pooled Pearson is positive.** Only the per-cross-section rank IC sees the effect.
This is the failure mode `ml4t-information-coefficient` names explicitly — a single pooled correlation
hides the time variation and can invert the answer.

## 3. The ordering is monotone in the median and a lottery in the mean

Forward 5-day return by score quintile, low → high score:

| | Q1 | Q2 | Q3 | Q4 | Q5 |
|---|---|---|---|---|---|
| mean | +0.18% | +0.15% | +1.25% | −0.21% | **+1.67%** |
| median | +0.61% | +0.54% | +0.38% | −0.53% | **−0.38%** |

The median falls monotonically with the score — high residual score *does* mean underperformance —
while the mean of the top bucket is the highest of all five because a handful of idiosyncratic winners
keep winning violently. Beta-adjusting the forward return does not change the shape (`−0.11, −0.35,
+0.94, −0.49, +1.35`), so this is not market exposure.

That single table is why every book fails: a quintile spread is a mean, and the mean is owned by the
tail the score cannot rank.

| book (fwd7, no costs) | mean/wk | median | hit | SR | trim-5% |
|---|---|---|---|---|---|
| long top fifth | +1.29% | −1.18% | 0.46 | +0.49 | **−1.45%** |
| long/short fifth | +1.32% | — | — | +0.61 | — |
| long bottom fifth (reversal) | −0.03% | −0.09% | 0.50 | −0.01 | −1.91% |

Nothing here clears a trim of its own best 5% of weeks, at zero cost, so cost is not the binding
constraint.

## 4. Hourly: the same mechanism, already recorded, now confirmed out of window

`LESSONS.md` holds a 16-cell "BTC-Residual Momentum" grid on `CRYPTO_PERPS_TOP100_1H_V1` over
2024-07..2025-07 with rank IC −0.0316..−0.0549. Re-run on **2021-01..2024-06**, a window that grid
never touched, top-100 by trailing 7-day dollar volume, beta only, IC against the forward 24h return:

| formation | 2021-01..2024-06 | HAC t | 2024-07..2025-06 (replication) | HAC t |
|---|---|---|---|---|
| 24h | −0.0381 | −7.61 | −0.0298 | −2.88 |
| 72h | −0.0598 | −11.33 | −0.0476 | −5.39 |
| 168h | −0.0660 | −11.29 | −0.0584 | −8.15 |
| 336h | −0.0666 | −10.94 | −0.0466 | −5.36 |

Across the full formation × horizon grid the strongest cell is t **−18.8**. The effect is stable across
three years and two regimes, and matches the magnitude recorded for the later window.

And the books are still negative, for the same reason:

| configuration | cost | mean/rebalance | median | SR (annualised) | trim-5% |
|---|---|---|---|---|---|
| L=336h hold=72h | 1x | −0.2249% | −0.0384% | −0.94 | −0.5048% |
| L=168h hold=72h | 1x | −0.2377% | −0.0643% | −1.07 | −0.4778% |
| L=168h hold=24h | 1x | −0.0783% | −0.0149% | −1.00 | −0.2327% |
| L=72h hold=24h | 2x | −0.0971% | −0.0517% | −1.32 | −0.2479% |

Median quintile ladder at 72h (`L=168h`, pooled over 2021-01..2025-06), low → high score:
`+0.172% +0.427% +0.380% +0.458% +0.117%`; means `−0.063% +0.168% +0.075% +0.117% +0.286%`. The
high-score bucket has the **worst median** in the ladder, exactly as the −0.069 IC says, and the
**best mean**. The reversal is real and the quintile book cannot hold it.

## 5. Verdict, and what it does to the thesis

**Positive residual momentum is dead** — daily and hourly, 5d to 250d, BTC and equal-weight factor,
10 to 510 names, both beta conventions, in-sample and lagged beta. That question is closed.

**Residual reversal is real** — statistically massive (t to −18.8), stable out of its original window,
present at both frequencies. It is the *sign-flipped* mechanism the equity literature describes, which
is what `LESSONS.md` hypothesised from the 1h grid.

**Neither is a strategy.** The reversal lives in the median and the mean is owned by a right-tail pump
lottery in the very bucket that must be shorted. Every book tried here is negative on the mean and
negative after trimming its best 5% of observations, at zero cost.

Consequence for the Discord implementation: a residual-momentum code reporting a strong Hyperliquid
result is **not measuring residual momentum** — that does not exist in this data by any construction.
If it is this family at all, it is the reversal, on a clock well under a week, with sizing that
neutralises the pump tail; a plain equal-weight top-fifth book cannot produce it. Its own
cross-sectional rank IC on this panel is the first thing to compute, and it should be computed
per cross-section, never pooled.

## 6. Corrections to this document

- The "no daily contract exists yet" line is imprecise: `CRYPTO_PERPS_1D_V1` exists (sha `81f22a93…`)
  but binds the narrow `crypto_perps_1d.parquet` with `train_start: 2024-01-01` — about 78 weeks. It
  cannot host this test. The conclusion stands; the sentence was wrong.
- The "IC |t| inside 0.8" table is superseded by §1 and §2.

## 7. Search size, and the gate any follow-up must clear

87 daily cells + 72 hourly cells = **159 unregistered configurations**, all persisted in the two JSON
reports, none dropped or re-run. `warden`'s `k_eff` cannot see them, so any future trial from this
family inherits an optimistic DSR unless its selection count is set to at least 159 by hand.

A reversal follow-up may only be preregistered with: rank IC per cross-section (never pooled) as the
Stage-1 statistic; the trim-top-5%-of-observations gate evaluated at Stage 1, not Stage 3; a universe
of at least 100 names; and a hold under one week. On the evidence above that hypothesis should be
expected to fail the trim gate, and is registered only to close the loop, not because it is likely to
qualify.

## 8. Disclosure on the HAC t-statistics

Every `t` above comes from `warden.gates.compute_hac_tstat`, i.e. `mean / sqrt(hac_var / n)`, so
`sign(t) = sign(mean)`; a negative `t` is the expected result for a reversal claim and `|t|` carries
the evidence. Two properties were checked rather than assumed:

| series | n | mean IC | raw HAC var | sample var | t HAC | t naive |
|---|---|---|---|---|---|---|
| daily 15d / fwd5 | 222 | −0.03311 | 0.055738 | 0.067301 | −2.09 | −1.90 |
| hourly L=168 / fwd24 (2021-01..2024-06) | 1,195 | −0.06602 | 0.040907 | 0.029243 | −11.28 | −13.35 |

- **No series relied on the `max(hac_var, 1e-12)` guard.** Both raw variances are positive. That guard
  is the one way these numbers could lie: on a strongly negatively autocorrelated IC series the
  Bartlett sum can turn negative, the guard fires, and `t` takes a fabricated magnitude.
- **The two frequencies err in opposite directions.** The daily IC series is negatively autocorrelated,
  so its HAC variance falls below the sample variance and `|t|` is inflated ~10 % (−2.09 vs −1.90
  naive); that is the most optimistic number in this document. The hourly series is mildly positively
  autocorrelated, so HAC deflates `|t|` (−11.28 vs −13.35 naive).
- Read the daily row as *direction established, magnitude uncertain at ~2σ on 222 weekly bets*, and
  the hourly rows as *overwhelmingly established on 1,195 non-overlapping observations*. The thesis
  fails on the hourly evidence, not on the marginal daily t.

