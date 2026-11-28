# Phase 2 — draft scientific intents

**DRAFT / NOT REGISTERED / NOT APPROVED.** No real-market scoring or candidate
implementation under this preparation scope. The names below are case-study
identifiers, not registered hypothesis IDs. These drafts precede the synthetic
engineering examples. The proposed data requirements are metadata-only requests,
not permissions to read exploratory or sealed data.

## P2-D01 — calendar-conditioned BTC variance

- ORIGINAL: Shanaev, Vasenin and Stepanov, *The 15-minute candle effect* (2023);
  Hansen, Kim and Kimbrough, *Periodicity in cryptocurrency volatility and
  liquidity* (2024); Andersen and Bollerslev (1997); Corsi (2009).
- REPLICATION: fixed within-hour variance profile on an admitted BTCUSDT perp;
  not a claim about directional returns.
- ADAPTATION: next-five-minute realized variance conditional on a known market
  clock, above historical variance and hour controls. Mechanism: concentrated
  algorithmic activity; causal attribution to candle-based bots is unproven.
- Purpose: incremental forecast loss first. Separate downstream incremental
  economic claim against the same preselected base strategy and risk budget.
- Draft source: d01_requirement.yaml (predictive OHLCV only). The separate
  canonical funding store is required for subsequent economic evaluation.
  Start date 2021-01-01 proposed, not an authorized evaluation cutoff. End date, eligibility and exposure window must
  come from Warden. Only one instrument; no selection across instruments.
- Minute bars carry UTC open timestamp u; completed information available no
  earlier than u+1m. A decision at t (UTC minute divisible by five) uses only
  bars completed and available by t. Explicit availability is required.
- Proposed entry open t+1m; exit open t+6m. Variance target sums the five
  squared log open-to-open returns with starting timestamps in [t+1m,t+6m).
  Labels become available no earlier than the last required source observation.
  Missing minutes invalidate the complete affected windows; never forward fill.
- Reference: last completed 5m close-to-close realized variance. Benchmark:
  Ridge on RV5/RV60/RV1440 and fixed UTC-hour indicators. Candidate: identical
  features plus count of quarter-hour starts in the execution interval,
  including its left endpoint and excluding its right endpoint.
- Log-variance Ridge, positive floor 1e-12 return-squared, fixed alpha=10;
  train-residual exponential smearing restores an arithmetic-mean forecast.
  The initial raw-Ridge synthetic witness exposed QLIKE sensitivity to clipped
  negative forecasts. This positivity repair precedes scientific approval and
  uses no market outcomes. No hyperparameter search.
- Predictive metric: paired QLIKE (Patton 2011), calibration ratio and
  time-block uncertainty. Train-only scaling, strictly chronological folds,
  purge through label availability plus frozen embargo; no random splitting.
  Freeze materiality, minimum independent blocks, calendar and exact fit schedule
  with the scientific owner before market evaluation.
- Economic overlay: same parent signal and position limits, inverse forecast
  volatility; no parent chosen, no costs waived. Include turnover, fees, spreads,
  slippage, funding and latency; use Warden contract gates and RG-06 if admitted.
- Falsification: no incremental OOS information -> stop. A forecast benefit is
  not economic qualification. Incomplete sample or unresolved admission -> defer.
- Lineage: Phase 1 candle-boundary hypothesis; compare HYP-2026-001,
  HYP-2026-011 and HYP-SOC-QP-BTC-OVERNIGHT-20261031. No novelty claim until
  RG-07 review and remaining related-lineage debt are resolved.
- Revision allowance: none proposed. One candidate, one comparator, one fixed
  reference. No selectable nonlinear or cadence sweep.

## P2-A01 — conditional closing-session momentum

- ORIGINAL: Baltussen, Da, Lammers and Martens (2021), *Hedging Demand and
  Market Intraday Momentum*; Gao et al. (2018), *Market Intraday Momentum*.
- REPLICATION: rest-of-day sign predicts closing-half-hour return on exact
  source futures basis. That replication is not performed by this case study.
- ADAPTATION: ES proxy, US equity cash-session anchor (XNYS schedule, including
  early closes). Not CME settlement or contract-specific hedging evidence.
- Purpose: incremental return forecast first, eventual paired after-cost
  improvement under unchanged execution and risk policy.
- Draft source: a01_requirement.yaml. Use only one admitted product. Resolve
  source instrument basis and exact session definition before approval.
- Session decision at cash close minus 30m. Rest-of-day return from prior
  session close to last completed close at decision; RV from current
  session open through completed decision-time bars. Prior close and current
  session bars must be complete and available. Proposed entry next minute
  open, exit at scheduled close open if observable and executable on the
  target venue; never assume the cash closing print is an executable CFD fill.
- Reference: sign of ROD. Benchmark: Ridge on ROD. Candidate: Ridge on
  ROD, sqrt(RV) and ROD*sqrt(RV), same sample and train-only scaling.
- Synthetic witness uses log execution return; forecast diagnostic paired
  squared error, calibration, session-block uncertainty. No market tuning.
  Approval must freeze minimum materiality, dependence and sample adequacy.
- Future economic comparison: unchanged signal-to-position mapping, sizing,
  fees/spread/slippage, financing, latency, and session flattening; signed
  contract governs these. Reject if conditional forecast adds no information
  or economic benefit disappears under realistic fills and costs.
- Lineage: Phase 1 closing momentum mechanism; compare HYP-2026-011 and
  prior index timing strategies. No claim of actual dealer gamma or leverage-ETF
  demand from price data. Resolve RG-07 and existing selection debt.
- Revision allowance: none proposed. Do not add volume or nonlinear search.

## Not admitted

P2-B01 remains deferred for executable FX quotes/parent-order evidence.
P2-C01 remains deferred for vintage consensus/first-release inventory data
and oil-product session mapping. No placeholder applications.

## Approval prerequisites

The appliance is operational but cannot create scientific authority. Both
draft requirements now identify canonical data after the v2 taxonomy repair;
they return compile_required, not admitted data. A signed venue policy,
valid price-basis manifests, exact dataset resolution and authoritative
forecast-target/sampling evaluation must be resolved first. The existing
cross-sectional forward-return label is demeaned at every timestamp; for one
asset it is identically zero, not the proposed target. No candidate may encode
a variance target as a feature or bypass Warden. See DECISION.md.
