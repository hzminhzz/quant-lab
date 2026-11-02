# Four source-complete crypto cross-sectional studies — 2026-11-02

## Disposition

**Four additional predeclared research adaptations received native Warden
Stage 1 screens: one PASS, three REJECT. The one Stage 1 passer reached a
real Warden Stage 3 full trial and received REVISE with a failed six-check
audit. No scientifically qualified strategy, no authorized protected holdout
read, no paper/live trading.**

This is a separate stacked continuation of [quant-lab PR #40](https://github.com/hzminhzz/quant-lab/pull/40).
Prior Syncster CME studies (2 REJECT), ATS/NTR 72h studies (2 provisional
FAIL) and PR #41 arXiv studies (4 Stage1 REJECT) were not rerun.

### Data and inherited research accounting

| Boundary | Audited fact |
|---|---|
| Frozen Warden contract | `CRYPTO_PERPS_TOP100_1H_V1` |
| Binance USD-M 1h original panel | 2,174,396 rows, 100 perps |
| Source SHA-256 | `a7e13ed71f8ac09a11f98bdb6d711d93f91a4021105141a2070fd79696af99bc` |
| Exploratory cutoff | `2025-07-01T00:00:00Z` exclusive |
| Validation interval | `2024-07-01T00:00:00Z` through `2025-06-30T23:00:00Z` |
| Observed validation bars | 8,760 hours; 876,000 asset-hours |
| Validation internal missing asset-hours | **0** (100 instruments every hour) |
| Zero-trade observations | 100 assets at `2024-10-28T20:00:00Z`; excluded at the signal timestamp, with future entry/exit availability checked for the IC label only |
| Position timing | Signal at observed close of hour t; Warden next observed open at t+1; forward 4h label open(t+1) to open(t+5); UTC 4h non-overlapping decision anchors |
| Registered search history before additions | **149 hypotheses, 75 full trials, 230 selection attempts** |
| Snapshot | Read-only SQLite online backup including WAL, local hash `6088140643ff5d66733cd1008f8c3fbbcde0500c41401a92a3b9f258c8720d7f` |
| Working state after studies | **153 hypotheses, 76 full trials, 234 selection attempts** |

`scripts/prepare_four_xs4h_snapshot.py` copied the byte-identical
pinned original panel to a fresh isolated worktree. It reused an
automatically initialized Warden ledger **only after confirming that
all evidence tables were empty and its five seeded synthetic
quarantines matched the source rows by ID and reason**, then used
SQLite's WAL-consistent `backup` to import authoritative prior
trial history. It never wrote the primary checkout or accessed a
sealed holdout. Snapshot metadata is committed under
`.workspace/campaigns/four-xs4h/source-snapshot.json`.

The fixed Top-100 membership is **not independently point-in-time
universe-validated**; this affects conclusions about historical
survivorship, delisting and implementability. Original price provenance
is independently digest-pinned, not necessarily complete venue fills.

## Predeclared ideas and source fidelity

All four use real, already-approved Binance 1h OHLCV/funding fields
only, no external or invented quotes, cap, trade-count or signed trades.
The features are strictly trailing and are tested against the future
*only in the evaluation IC series*. Each is a research adaptation,
**not an exact replication** of its cited source.

1. **BTC-led beta continuation.** Estimate each asset's trailing
   168h covariance beta to BTC and rank `beta_168 * BTC_4h_return`.
   Concept: information spillover and beta repricing. Source:
   [Guo et al., Cross-Cryptocurrency Return Predictability](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3974583).
   Earlier HYP-2026-033 is related; not an independent family.

2. **Liquidity-conditioned weekly momentum.** For the top 50 assets
   by trailing 24h observed `volume*close`, rank the trailing 168h
   close return as an explicit four-hour adaptation of
   [Fičura and Colak, Impact of Size and Volume on Cryptocurrency Momentum and Reversal](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4378429).
   The source's weekly-horizon finding is **not evidence for 4h**;
   volume units are normalized to quote dollars for ranking.

3. **Sign-only short-term reversal.** Rank `-sign(close_t-open_t)`,
   ignoring candle magnitude; long all negative-signed altcoins
   (0.5 gross), short all positive-signed altcoins (0.5 gross)
   at each four-hour decision if both sides are sufficiently populated.
   Excludes BTC. Explicit horizon extension of
   [Kitron and Wengrowicz (2026), arXiv:2608.21888](https://arxiv.org/abs/2608.21888).
   The source describes **15m** directional reversals and gross edge
   smaller than benchmark round-trip costs; this study tests a very
   different **4h** economic proposition.

4. **Price movement + rising volume confirmation.**
   `return_24h * max(log(dollar_volume_24h/dollar_volume_previous_24h),0)`.
   Rationale: stronger participation may sustain informational drift.
   Inspired by the distinction between price-based and volume-based
   predictability in
   [Tan and Tao, Trend-based Forecast of Cryptocurrency Returns](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4222864).
   Source does not supply this formula; original price-volume
   hypotheses HYP-2026-038/060 count as search debt.

Every formula, source provenance, rebalance schedule, universe, model
`linear` (deterministic one-factor ranking, **not trained Ridge**),
transaction-cost basis, anchor frequency, and rejection threshold was
recorded in `.workspace/hypotheses/HYP-XS4H-*.yaml` **before
the four screens**. No thresholds, directions or horizons were changed
after observing results. Warden's `cross_sectional_ml` family accepts
deterministic IC vectors; this terminology is inherited from the
frozen evaluator, not a claim a predictive ML model was trained.

## Native Warden Stage 1

Warden `verify` on the **real 2,174,396-row panel** returned PASS for
all four factors (sampled prefix invariance); all four `warden screen`
calls created an immutable selection-attempt row. Warden's
`evaluate_stage1_cross_sectional_ml` uses **mean IC ≥ 0.015** and
**HAC t-stat ≥ 2.0**, conjunctive; its reported t-stat is not an
independent multiplicity-corrected p-value.

| Hypothesis | Mean rank IC | HAC t-stat | Warden verdict | Attempt |
|---|---:|---:|---|---|
| `HYP-XS4H-BTC-SPILLOVER-20261102` | −0.016818 | −2.79783 | REJECT | `ATT-55ee4a3397e01826-001` |
| `HYP-XS4H-LIQ-MOM-20261102` | −0.041216 | −8.94025 | REJECT | `ATT-9d45a649dd34bb76-001` |
| `HYP-XS4H-SIGN-REV-20261102` | **+0.023200** | **+7.54440** | **PASS** | `ATT-997670f59b65ec2b-001` |
| `HYP-XS4H-PRICE-VOLUME-20261102` | −0.038796 | −13.56281 | REJECT | `ATT-5d6d2cf388e6a93c-001` |

All IC series were produced from exact next-open +4h labels,
excluding unavailable quote-side observations, and all four
returns/IC streams are stored with SHA-256 evidence in the versioned
`.workspace/campaigns/four-xs4h/evidence.json`.

The three negative scores are *not* permission to flip the sign and
rescreen on the same validation window without recording a new
hypothesis and incremental search penalty.

## Stage 3 — sign-only reversal

Native `warden backtest`:
`RUN-XS4H-SIGN-REVERSAL-20261102-001`.
**Full trial recorded: `STAGE_3_STRESS`, Warden verdict `REVISE`.**

| Measure | Frozen observed-market validation result |
|---|---:|
| Gross Sharpe | **+3.16428** |
| Net Sharpe | **−8.17541** |
| Profit factor (hourly net-return basis) | 0.74092 |
| Max drawdown | **66.47272%** |
| Annualized turnover | 2488.02 |
| Break-even one-way friction | **1.65822 bps** |
| Modeled one-way cost | **6.00 bps** (4 fee + 2 slippage), before separate funding |
| Net Sharpe at 2× cost | **−18.24977** |
| Net Sharpe with additional +1h delay | **−11.23182** |
| DSR probability | **1.56e−53** |
| Multiplicity-adjusted effective trial count | 42.3018 |
| Contract selection attempts/full-trials total for this test | 59 |
| Persisted net-return Parquet SHA-256 | `a9d87f73bcf47dc66ce49753f4247016b64df3bb0d0fbd2a2d651d157d10b56d` |

**Interpretation:** Cross-sectional sign reversal is statistically
detectable as a gross directional rank effect, but at the frozen
four-hour rebalance frequency its **net economic edge is strongly
negative**. This is one of the core false-positive distinctions:
passing a predictive Stage 1 does not imply follower realizable PnL,
positive fee-adjusted Sharpe, or capital allocation permission.

The existing portfolio simulator assumes next-bar open fills and
fixed modeled slippage. It does not reconstruct actual source
or follower order books, broker position constraints, market impact
or an explicit zero-trade-bar no-fill model, so even the net-return
path may be **optimistic** for some fills. This does not reverse
the negative disposition.

### Six-check Warden audit

Command: `uv run --frozen warden audit-check --run-id
RUN-XS4H-SIGN-REVERSAL-20261102-001 --json`.

Audit status: **REJECTED**; chronology, prefix causality and
fold/embargo contract checks passed. Outlier sensitivity,
cost/latency survival, and selection-bias gate failed. This audit
did not access the protected holdout. The negative audit is stored
immutably in the local Warden `audit_evidence` table; an audit
Markdown report is committed in `.workspace/hypotheses/`.

## Reproducibility and scientific limitations

Preparation in a **fresh** worktree (the snapshot tool intentionally
refuses to overwrite an already-populated local research ledger):

```bash
uv run --frozen python -m scripts.prepare_four_xs4h_snapshot
uv run --frozen warden check-worktree
uv run --frozen warden register-hypothesis .workspace/hypotheses/HYP-XS4H-SIGN-REV-20261102.yaml --json
uv run --frozen warden verify experiments/active/four_xs4h_sign_reversal.yaml --json
uv run --frozen warden screen experiments/active/four_xs4h_sign_reversal.yaml --json
uv run --frozen warden backtest experiments/active/four_xs4h_sign_reversal.yaml --json
uv run --frozen warden audit-check --run-id RUN-XS4H-SIGN-REVERSAL-20261102-001 --json
uv run --frozen python -m scripts.export_four_xs4h_evidence
```

For full reproduction register and screen **all four** first
according to their immutable experiment files. Screens are not
idempotent (repetition counts as an extra selection attempt).
The script deliberately fails closed on a changed source or an
already-populated local registry, and the evidence exporter refuses
to overwrite a different published artifact.

- Source primary checkout `/home/quant/dev/quant-lab` had 46 ahead /
  3 behind and substantial unrelated dirty/untracked work. None was
  modified. An isolated branch/worktree was used.
- Stage 1 rank test excludes future unavailable entry/exit bars **only
  as label evidence**, not as post-hoc decision inputs.
- Validation asset-hour panel is complete (zero missing hours), but
  has one real 100-symbol no-trade hour. Warden open-to-open simulator
  is not a real broker fill model.
- Source membership is a fixed later-known Top-100 and lacks separate
  delisting/universe PIT attestation, which remains a qualification
  caveat.
- No statistical gate was changed: Warden's frequency-dependent
  bar-level PF calibration problem described in independent PR #41
  remains unresolved. These four signals use the Warden
  cross-sectional IC gate, and the sole passer was still destroyed
  by trading frictions at Stage 3.
- Four research factors are versions of **known mechanism families**;
  they are not four independent evidence families. Earlier selection
  history is inherited, and the one trial uses the full contract-wide
  DSR floor. No protected holdout read, no deployment.

## Engineering recovery and verification

A broader clean-worktree suite initially failed due to absent
exploratory fixtures, absent canonical transition directory and
`contracts/multi_asset_1d_v1.yaml` (a historical **uncommitted**
contract in the dirty primary checkout). The separate, SHA-restricted
`scripts/hydrate_verified_exploratory.py` restored **11 of 12**
approved exploratory dataset bindings from the original primary
checkout or previously verified Syncster worktree. Its source/
destination SHA audit is in
`.workspace/campaigns/four-xs4h/exploratory-hydration.json`, SHA-256
`46bf9d4761a1d6065804bd2da7e1fd29b4cab2ac002910c5d2fe35b77a3efdcd`.
It never read, changed or created protected holdout datasets.
The legacy `INDICES_1H_V1` digest does not match available
`indices_1h.parquet`, whereas `INDICES_1H_V2` verifies: the
original contract/digest was not silently rewritten.

An ignored `.workspace/transitions/.gitkeep` was restored as the
required directory skeleton in the isolated worktree. The broad suite:

```bash
uv run --frozen python -m pytest -q --disable-warnings --maxfail=6 \
  --ignore=tests/experiments/test_carver_daily_suite.py
```

returned **256 passed**, while the entire unfiltered suite is
still **blocked at collection** by missing approved
`contracts/multi_asset_1d_v1.yaml`. This is not a claim that
all tests passed. Specific new deterministic tests
`tests/test_four_xs4h.py` and `tests/test_four_xs4h_snapshots.py` returned **19 passed** (including immutable SQLite snapshot and source-digest refusal tests).
Ruff and Warden protected-file integrity passed before release.

Research evidence exported to
`.workspace/campaigns/four-xs4h/evidence.json`, SHA256
`e0fedb48c3805fdb7506f3403df41fb789400f81fa9c513fd414f35367cc7dd3`.
The one native Stage3 return Parquet is 52,072 bytes with the
recorded SHA256 above; it is an evidence artifact, not a price source.

**Conclusion:** Screened the requested four source-complete new
variants. One genuine Stage-1 predictive screen PASS,
**zero qualified net economic candidates**. The trade-sign
effect is a research observation, not a feasible four-hour
perp portfolio at these costs.
