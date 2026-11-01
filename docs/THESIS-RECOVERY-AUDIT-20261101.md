# Quant-lab thesis recovery audit — 2026-11-01

**Scope:** 149 hypotheses in the live source-checkout Warden registry at `/home/quant/dev/quant-lab/registry/registry.db`. Source checkout `b8deedb`, with extensive dirty/untracked work. Registry was inspected read-only. No new trials, screens, contracts or holdout accesses were performed. This report is **not** a claim that all hypotheses have been qualified.

## Reconciled counts

- Hypotheses: **149**, statuses **139 ACTIVE, 2 PROMOTED, 4 REJECTED, 4 REJECTED_ON_HOLDOUT**.
- Historical trials: **72** — 50 `STAGE_3_STRESS/REVISE`, 11 `STAGE_3_STRESS/CANDIDATE_FOR_QUALIFICATION`, 9 `STAGE_1_SIGNAL/REJECTED_EARLY`, 2 `FAILED`.
- Selection attempts: **184**.
- Among hypotheses: **38** have at least one full trial; **111** have none. Of the 111, **55** have no recorded screen attempts and **56** have at least one.
- The standalone selection-attempt outcomes (across hypothesis identities, **not additive unique groups**) cover 57 with REJECT, 9 with PASS, 19 with GRID, 2 with ERROR, 69 with no attempt. A hypothesis can occur in multiple outcome categories.
- Zero-trial, no-attempt distribution: **42** `CRYPTO_PERPS_TOP100_1H_V1`, **8** `CRYPTO_MAJORS_1H_V1`, **5** `FX_MAJORS_1D_V1`.
- Hypothesis entries with zero-trial Stage 1 PASS: **HYP-2026-096**, **HYP-2026-097**, **HYP-2026-098**. They require lineage and selection-history validation before a full trial.

## Reconciliation before running anything

1. **Already falsified:** Keep Stage 1 REJECT and Stage 3 REVISE as historical evidence. `ACTIVE` is a registry workflow state and must **not** be mistaken for an unevaluated scientific result. Preserve attempt counts and never refill the three-trial budget via aliases.
2. **Already consumed holdouts:** `HYP-2026-112/113/114` and `HYP-2026-066` have `REJECTED_ON_HOLDOUT`; no second unseal. `HYP-2026-065/069` are `PROMOTED` despite earlier audit concerns; audit provenance before any qualification claim, not by rerunning their protected window.
3. **Synthetic reference:** `RUN-ML-001` has an unusually high historical score (net Sharpe 8.576). Do not classify it as live market alpha without independently confirming its `synthetic`, provenance and effective-eligibility record.
4. **Unscreeened 42 crypto cross-sectional theses:** Registry records do not bind a strategy module. Match registered source mechanism to existing `strategies/candidates/cs_*.py` via independently checked formula and required observables. Only available fields may be used; funding / order flow / bid ask / book-dependent features must have real PIT evidence, not zeros. First run causal unit tests and `warden verify`; then Warden Stage 1 under the frozen contract. No unrelated new contract or new hypothesis ID to erase selection history.
5. **Unscreeened 8 crypto time-series theses:** Match original mechanisms to `strategies/candidates/ts_*.py`, confirm actual contract and source history, then complete Warden Stage 1 as permitted. Do not assume a seemingly similar filename is a faithful mapping.
6. **Five original FX hypotheses HYP-2026-115..119:** Registered on `FX_MAJORS_1D_V1`, with `QUOTE_SIDE` and no bid/ask. A related Dukascopy daily OHLCV `NEXT_BAR` research lineage already exists as `HYP-2026-120..124`, each with one Stage 3 `REVISE` (net Sharpe .656/.651/.540/.532/-.076). Preserve original frozen quote contracts and both cohorts. Do not claim they are interchangeable or blindly duplicate failed trials. The principal's CFD proxy allowance applies only to appropriately labeled new exploratory studies.
7. **Blocked author/source cards:** Review `.workspace/campaigns/SOURCE_CURSOR_20261030T0807Z.md` and the newer `AI-research-loop/examples/THESIS_FACTORY_20261101_BATCH*.json` before promoting source-only mechanisms. Stock-universe and limit-order rules are not replaceable by one index CFD; incomplete paid/public rule descriptions are not executable strategies.
8. **Data integrity:** The canonical lake is `/home/quant/dev/quant/ml4t-data/.data/store`. Check the live catalog and readiness contract; do not use a missing *future* tail as a blocker if it lies outside the frozen validation window. Prior FX5m LSE and Dukascopy sources disagreed on significant discontinuities; quarantine these periods instead of masking them with a proxy switch.

## Suggested execution order

A. Resolve pending recorded **PASS** candidates 096/097/098 with existing registered economic mechanisms and stable evidence. Avoid unapproved backtest attempts and implicit DSR budget resets.

B. Bind and causally preflight the **8** crypto-major theses, then the **42** cross-sectional theses, sorting by source-field readiness. Skip derivative-only signals until source evidence is independently present.

C. Reconcile the **5** FX original lineages against the existing daily Dukascopy experiments; create new versioned research only for a genuinely distinct market/timeframe/economic hypothesis and with appropriate contract approval.

D. Reconcile the **38** historically trialed hypotheses against eligibility, audit reports, terminal falsification and the six recorded qualifications. Resume only scientifically justified unspent work; do not make 1:1 copies or use spent holdout windows.

E. Continue screening distinct source-complete mechanisms, logging every Warden attempt. Close only individual scientific claims with actual reproducible checks. A run passing Stage 1 is not scientific qualification.

## Verification boundary

The source checkout's `warden check-worktree` returned integrity verified. No new experiments or qualifications were run for this inventory. Existing status and counts are factual database observations as of the inspection, **not** projected research outcomes. The recoverable workload remains open.
