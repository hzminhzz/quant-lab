# Result — residual reversal, registered trial RUN-XSRR-001 (HYP-2026-128)

Date: 2026-10-29. Principal's instruction: test the opposite of "the top-performance coin continues to
perform" — test that it **reverses**. This is the harness result, not a diagnostic.

**Verdict: `REVISE` at Stage 3 on both trials, audit `REJECTED` (3 of 6) on both. The signal is real;
the strategy is falsified by its own cost arithmetic, and the one targeted revision made it worse.
Hypothesis closed at 2 of 3 trials.**

## What ran

| element | value |
|---|---|
| contract | `CRYPTO_PERPS_TOP100_1H_V1` — 100 assets, 2023-01-01..2025-06-30, 1h, `NEXT_BAR`, delay 1 |
| validation window | 2024-07-01..2025-07-01 (8,760 bars) |
| hypothesis | `HYP-2026-128`, pre-registered with mechanism, counterparty, falsification criteria |
| module | `strategies/candidates/cs_residual_reversal_1h.py` |
| spec | `experiments/active/hyp_2026_128.yaml` |
| score | `sum(r_i − beta_i·r_BTC, 168h) / std(same, 720h)`, beta OLS over the trailing 1440h |
| book | long the low-score fifth (recent idiosyncratic losers), short the high-score fifth, dollar-neutral, gross 1.0, 10 names a side |
| rotation | every 24 bars |
| cost | 6 bps one-way (4 taker + 2 slippage), 12 bps round trip — contract floor, not a choice |

Funnel output, in order: `check-worktree` ok → `register-hypothesis` ok → `verify` **PASS**
(prefix-invariance true) → `screen` **passed** → `backtest` **WARDEN_RUN_COMPLETE** → `audit-check`
**REJECTED**.

## The signal gate passed, comfortably

```
screen: {"mean_ic": 0.05716867271796264, "ic_hac_tstat": 6.913639289409275,
         "passed": true, "notes": "Mean IC=0.0572, HAC t=6.91"}
```

Rank IC of the traded reversal direction, per rebalance, over 365 rebalances. The gate needs
mean IC ≥ 0.015 and HAC t ≥ 2.0. Pearson on the same series is 0.0153 — an order of magnitude weaker,
which is why the earlier "flat" reading used the wrong statistic (see `RESULT_binance.md` §2).

## The trial

| metric | value | pre-registered bar | result |
|---|---|---|---|
| gross Sharpe | 1.228 | — | looks excellent |
| **net Sharpe** | **0.124** | > 1.2 | **fail** |
| Sharpe at 1.5x cost | −0.314 | — | fails before 2x |
| Sharpe at 2x cost | **−0.750** | > 0.50 | **fail** |
| Sharpe at +1 bar latency | **−0.091** | > 0 | **fail** |
| DSR | 0.063 at k_eff 11.8 | ≥ 0.90 | **fail** |
| profit factor | 1.004 | — | a coin flip |
| hit rate | 0.504 | — | no edge in sign |
| max drawdown | 17.13% | — | |
| annualised turnover | **233.8x** | — | |
| breakeven one-way cost | **8.40 bps** | ≥ 2.5x the 6 bps charged | **1.40x — fail** |

All four Stage-3 conjuncts fail. Nothing had to be argued down: the pre-registered criteria named these
and the run missed all of them.

## Why the thesis cannot be rescued by a revision

This is the part that settles the remaining trials. Using only warden's recorded numbers:

- net Sharpe 0.124 at vol 16.00% ⇒ net return **1.99%/yr**; gross Sharpe 1.228 ⇒ gross **19.65%/yr**.
  Cost drag is 17.7%/yr on 233.8x turnover.
- The strategy is linear in cost: `SR(c) = 1.228 − 233.75·c/0.16`. It reaches zero at **c = 8.40 bps**,
  which is warden's own `breakeven_cost_bps`.
- Clearing net Sharpe 1.2 needs `SR(c) = 1.2`, i.e. **c ≤ 0.19 bps** — a **32x** reduction from the
  6 bps actually charged, and **turnover ≤ 7.4x per year**.
- The reversal's IC is **gone by ~10 days** (daily: −0.044 at 1d → −0.002 at 10d; hourly decays the same
  way), so the slowest rotation the mechanism permits is ~**36x/yr**.

7.4 required versus 36 available: the thesis needs its costs to be five times cheaper than the
mechanism's own decay allows. No parameter inside this family closes that gap. That is why trials 2 and
3 are not spent — a revision that cannot clear the bar by construction is noise-fitting, and the budget
is three trials *ever*.

**The stronger version of the same fact:** at *zero* cost the Sharpe is 1.228, and the bar is 1.2. The
entire gross edge sits 0.028 Sharpe — 0.45%/yr of return — above its own threshold. No execution
improvement can rescue a thesis whose frictionless upper bound barely clears the bar; the only lever is
turnover, and the mechanism's decay forbids it.

## The one honest near-miss to record

Gross Sharpe 1.228 on a 16% annualised vol means the raw mechanism is not nothing — a 24h
idiosyncratic reversal earned ~19.7%/yr before friction on a dollar-neutral 20-name book. The failure is
entirely the 233.8x annual rotation. Any future work on this family must start from
`breakeven_cost_bps` and a target turnover, not from gross Sharpe: ask first how many times a year the
signal can be harvested before the IC dies, and whether costs fit inside that budget. Here the answer
was no by a factor of five.

## What would change the answer

1. **A cheaper execution venue or maker-side fills** does *not* fix it under this criterion: at 1.5 bps
   one-way, drag is 3.5%/yr and net Sharpe lands near 1.01 — still short of 1.2. You would need
   ~0.2 bps, plus a slower rotation. Venue is a principal's decision and the contract floor is a taker
   assumption, but it is not the binding constraint here; the frictionless bound is.
2. **A signal that survives a two-week hold.** Reversal at this horizon does not, so a slower frequency
   is a different mechanism, not a tuning of this one.
3. **Sizing that captures the median ordering rather than the mean.** The quintile means are dominated
   by a right tail of continued idiosyncratic pumps (`RESULT_binance.md` §3); equal weight cannot hold
   the median structure. That is a second hypothesis with its own three-trial budget, and it does not
   address the cost arithmetic above.

## Protocol notes

- `model_type` is `linear`, not `rules`. The harness refuses `cross_sectional_ml` with a non-linear
  `model_type` until a Ridge/linear-baseline trial exists for the same hypothesis. There is no fitted
  model here — the score is a linear combination of past residual returns — so `linear` is truthful and
  *is* the baseline the guard asks for. Recorded in `HYP-2026-128.md`; the first attempt raised
  `LinearBaselineRequiredError` before `register_trial_start`, so it consumed no trial.
- Contract `label_horizon_bars: 4` is shorter than the 24-bar label the IC is measured on. Declared
  `[MISSING]` in the hypothesis file rather than silently accepted; the purge window is therefore too
  short for this strategy's label.
- Selection count: 159 unregistered diagnostic cells preceded this trial, so the honest k is
  ≥ 160, not warden's `k_eff = 11.8`. At that count DSR is lower still, so the recorded 0.063 is
  generous.

---

# Trial 2 — the targeted revision made it worse (RUN-XSRR-002)

The net Sharpe of 0.124 was a cost failure, so trial 2 attacked cost and risk allocation and left the
signal untouched: hold 72 bars instead of 24 (the measured IC is flat from forward-24h to forward-72h),
and weight each leg by `1/residual_vol` instead of equally (the score already divides by residual vol;
an equal-weight book over-weights exactly the high-vol names that carry the pump tail). Pre-registered
in `HYP-2026-128.md` before the run, including the expected range.

| metric | trial 1 (24h, equal) | trial 2 (72h, inv-vol) | bar |
|---|---|---|---|
| mean rank IC | 0.0572 (t 6.91) | 0.0409 (t 2.48) | ≥ 0.015 / t ≥ 2 |
| **gross Sharpe** | **1.228** | **0.199** | — |
| **net Sharpe** | **0.124** | **−0.521** | > 1.2 |
| 2x-cost Sharpe | −0.750 | −1.079 | > 0.50 |
| +1-bar-latency Sharpe | −0.091 | −0.719 | > 0 |
| DSR | 0.063 | 0.010 | ≥ 0.90 |
| profit factor / hit | 1.004 / 0.504 | 0.984 / 0.500 | — |
| max drawdown | 17.13% | 22.16% | — |
| annualised turnover | 233.8x | 133.9x | — |
| breakeven one-way cost | 8.40 bps | **2.13 bps** | ≥ 2.5x the 6 charged |
| verdict | REVISE | REVISE | |
| audit | 3/6 | 3/6 | |

Turnover fell exactly as predicted (233.8x → 133.9x, not the 3x one would guess: a 72-bar rebalance
replaces 1.06 of the book rather than 0.64). Everything else collapsed. **Gross** Sharpe fell 1.228 →
0.199 — 84% of the edge gone — and breakeven cost fell with it, 8.40 → 2.13 bps, so the revision lost
more edge than it saved in cost.

## The cost-horizon frontier has no feasible point

Two filled points on the same contract, same validation window, same signal:

| holding | gross Sharpe | cost drag | net Sharpe | breakeven |
|---|---|---|---|---|
| 24 bars | 1.228 | 14.0%/yr | +0.124 | 8.40 bps |
| 72 bars | 0.199 | 7.7%/yr | −0.521 | 2.13 bps |

Doubling the cost efficiency cost 84% of the edge. Extrapolating the same slope, a holding long enough
to make 6 bps affordable (~1 week) has essentially no edge left. **The mechanism's half-life is shorter
than the minimum viable holding period**, which is a different and stronger statement than "costs are
high": there is no point on this frontier where both sides work.

And the ceiling is low regardless of execution: at *zero* cost the Sharpe is 1.228 against a 1.2 bar.
Even free trading only clears the bar by 0.028 Sharpe. The thesis's best possible outcome is a
technicality, which is why trial 3 is not spent — it would have to be a different mechanism, and a
different mechanism is a different hypothesis with its own budget.

## What the two trials settle

1. The reversal is real and strong at a 24-hour horizon (rank IC 0.0572, t 6.91, 365 rebalances, on an
   independent validation window from the diagnostics).
2. It is not tradable at the contract's taker cost floor, and every affordable holding period destroys
   the edge before the cost saving arrives.
3. Risk allocation is not the problem: making the weights consistent with the score's own
   vol-normalisation made the book worse, not better.
