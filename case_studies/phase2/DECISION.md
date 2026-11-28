# Phase 2 — consolidated decision packet

Status: **synthetic engineering only; not scientific admission**.

## Evidence and completed technical recovery

Base: b7912b9951bb92758508ea4fe7ddd16ef309ce2c. The installed trusted research
appliance was observed ACTIVE at release
0f801b4c4c860aa4ebe71a0c9672921b844b9c98e32d8d06f9ae0029963b22e0, ledger schema 10.
Its maintenance delegation expressly does not supply scientific authority.

The current ML4T-data v2 catalog uses singular `crypto_perp` and `index`,
while Warden's semantic requirement uses `crypto_perps` and `indices`.
The consumer translation in `src/warden/resolve.py` repairs that mismatch;
current v2 OHLCV discovery excludes noncanonical, unaccepted, absent,
errored, funding, mark-price and open-interest stores. No data is rewritten.
Legacy catalog interpretation is retained.

Read-only development resolver, with explicit ML4T_DATA_STORE binding, returned:

| Requirement | Exact canonical store | Catalog first observation | Decision |
|---|---|---|---|
| D01 1m OHLCV | crypto_futures_minute_BTCUSDT_PERP | 2019-09-08T17:57:00Z | compile_required |
| A01 1m OHLCV | index_minute_ES | 2011-09-19T06:31:00Z | compile_required |

No missing symbols. Catalog identity
f0ff8c6bfaede039620bd291c89349b925bb569964d6576ba3c23fe6792a9422,
generated 2026-11-27T08:36:19.295914Z. This is catalog availability only.
The selected ES store is a Dukascopy index proxy, not CME futures. The BTC
trade OHLCV store has no embedded funding field; funding lives separately
in crypto_futures_funding_BTCUSDT_PERP (catalog-reported 7,906 rows).
The predictive D01 requirement is therefore OHLCV only, not a false
assertion of a funding-joined dataset. The downstream economic evaluation
must resolve official funding cash flows before opening a strategy trial.

The default lake-root heuristic expects a sibling store and cannot discover
the nested canonical lake from the appliance worktree. Supply the explicit
environment binding documented in README.md. The technical patch is in the
development branch, not a claim of runtime activation.

## Scientific decisions still required

### 1. Venue policy and data

Production `contracts/policy/venue_policy.yaml` is absent. Existing signed
contract candidates also lack declared price-basis metadata for these
requirements. `compile_required` describes coverage, not policy trust.
`warden_ops.venue_policy.load_venue_policy` still requires an authenticated
policy, exact HMAC manifest entry and valid selection-group mapping.
The trusted-appliance ADR delegates unsigned maintenance only; it does not
supersede this scientific policy input. No example cost floors, date fences
or selection groups were adopted and no signature/key was manufactured.

Required approval subject: a versioned venue-policy document with actual
approved costs, leverage, common train/validation/holdout fences and
reconciled existing selection debt, plus authorized signing/verification.
Then resolve the exact one-instrument requirement, compile only eligible
whole exploratory partitions through Warden and verify timestamp/coverage/
price-basis/funding provenance. For A01 verify that the selected proxy's
session-open/close quotes exist and the intended exit is executable; do not
substitute the cash closing print for a fill. Holdout is not part of this
assignment.

### 2. Native forecast evaluation (proposed, not implemented)

Current `src/warden/protocols.py::compute_execution_forward_returns`
constructs next-open forward returns and demeans them by timestamp.
`prepare_causal_model_dataset` and `run_causal_model_strategy` then own
the fold builder and cross-sectional IC path. With one asset the existing
forward_label becomes zero. The model protocol has no admissible
realized-variance label or single-instrument non-demeaned target. Neither
mislabeling variance as return nor computing scores in Marimo is acceptable.

Smallest proposed semantic change: one explicitly declared target/evaluation
kind at the existing Warden model-dataset preparation seam. Preserve
existing strategy families and all their default behavior. Add an opt-in
single-series forecast contract with:

- immutable target identity and source clock, label interval start/end and
  label availability; registered causal feature and target specifications;
- one Warden-owned chronological fold builder that reuses ML4T
  MLDatasetBuilder and WalkForwardCV, with purging through actual label
  availability plus declared embargo; physically strip target columns from
  validation estimator inputs;
- exact matched benchmark/candidate observations and frozen loss objective:
  QLIKE for variance and squared error for non-demeaned return, alongside
  calibration and paired dependence-aware uncertainty;
- explicit separation of forecast diagnostic evidence from existing
  strategy economic gates. A forecast-only outcome must not confer a
  Stage 1 or qualification pass, refund debt, or weaken existing gates;
- immutable target/sampling/model/benchmark identity in admission, evidence
  and candidate/lineage accounting; existing one-shot exposure restrictions.

Use the pure timing/feature/label primitives in volatility.py and
closing_momentum.py as the synthetic conformance reference. The final
governed path must import the same approved implementation (move or register
that closure rather than copying it). The current synthetic runner only
generates its own artificial inputs; it is not a production evaluator.

Acceptance before activation: synthetic one-asset nonzero return and exact
variance labels; missing minute and delayed availability rejection; exact
session and early close mapping; same-sample benchmark comparisons; actual
fold boundary witnesses; no change to existing cross-sectional labels/IC;
no scientific writes in notebook presentation. Freeze forecast materiality,
block length and minimum independent sample count with the principal
before market evaluation, not after seeing results.

### 3. Downstream economic comparison

D01 requires one frozen base strategy before inspecting market forecasts.
Approve its identity, risk-budget units, inverse-volatility mapping,
leverage/capacity constraints and execution/cost/funding assumptions.
A01 requires a frozen return-to-position map, session exit contract and
matching risk budget. RG-06 paired economic comparison and all applicable
absolute gates remain mandatory. The existing contract cost floors,
selection debt, robustness scenarios, independent conformance and holdout
restrictions are unchanged. Forecast gains alone cannot qualify either
strategy.

## Disposition

Approve the specific semantic forecast extension and scientific policy
subjects separately, then admit each frozen intent through the native
Warden sequence. No research registration, scored market-data evaluation,
manifest signing, contract promotion, registry migration, sealed-data
access or trading occurred. P2-B01 and P2-C01 remain deferred.
