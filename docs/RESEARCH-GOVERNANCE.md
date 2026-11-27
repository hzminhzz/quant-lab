# Research governance hardening (#119)

## RG-01: metadata-only preflight (#120)

`python scripts/preflight_trial.py SPEC [--db PATH]` checks the registered
hypothesis and contract identity, static strategy entry source, pinned exploratory
dataset, execution schema, and the existing selection-alignment clock. It uses
a read-only registry. It does not import a strategy, execute an evaluator, create
a registry snapshot, or return a performance statistic or strategy verdict.

A successful preflight is not admission or scientific qualification. Full code
closure, dynamic strategy requirements, and eligibility remain governed checks.
A caller-controlled source outside strategies/ is refused. The retired snapshot
and --workdir path are deliberately unavailable.

## RG-02: paired diagnostics (#121)

`warden_evidence.benchmark_comparison.compare_return_artifacts` is a library
operation over existing return artifacts, not an additional CLI or strategy
execution path. Both ReturnTape references require artifact, code and
configuration digests, and matching ComparisonBasis (dataset, calendar,
information set, execution, cost, risk, simulation epoch, observation unit and
annualisation). The evaluator must supply the complete eligible timestamp grid.
Missing observations, shifted clocks, legacy untimestamped tapes, mismatched
identities and invalid or missing artifact bytes are refused. No intersection
or padding is permitted. Zero-return observations remain in the sample.

BootstrapSettings explicitly records block length, seed, resample count,
confidence level, minimum observations and minimum full blocks. The reported
increment is candidate minus benchmark annualised after-cost Sharpe, calculated
using sample standard deviation. A paired circular block bootstrap resamples
the same contiguous index blocks for both tapes; its two-sided percentile
interval is descriptive and is not a multiplicity-adjusted acceptance test.
The result reports evidence fingerprints, the expected clock digest, sample
counts, statistics, settings and DIAGNOSTIC or INCONCLUSIVE status. Degenerate
statistics or bootstrap draws are inconclusive, not silently discarded.

The comparison does not authenticate caller-supplied policy/plan identities,
establish statistical power, apply a selection-adjusted acceptance criterion,
or authorize qualification. The later governed integration must construct
identities from recorded evidence and bind the policy before outcomes are
observed. There are no default policy thresholds in this slice.

## Delivery boundary

These two slices use synthetic fixtures only. Principal approval of prospective
candidate budget representation and legacy-debt mapping is required before
RG-03; comparison acceptance/multiplicity, independent conformance, and holdout
exposure rules remain separate decisions. No production manifest was signed,
no deployment or holdout access was authorized, and no research verdict or
trading permission follows from these engineering tests.

## RG-03: candidate budget migration design

The principal requested "Implement next" after the proposal to retain 3 selectable
candidates per hypothesis and 6 per related lineage, share a slot between the
same frozen screen and full trial, retain interrupted attempts without refund,
and preserve historical debt. Implementation proceeds on the isolated feature
branch. The exact legacy mapping below is a conservative implementation choice
pending explicit principal ratification before manifest signing or merge.

- Retain the existing global protected evaluator constants, not a second venue
  policy or a caller-controlled limit. The human-approved manifest covers the
  evaluator and the candidate identity implementation; no manifest is signed here.
- Add candidate and operation identities through the existing registry migration.
  Full-trial counters, receipts, return artifacts, selection-group membership
  and statistical floors are never rewritten. The old full-trial counter remains
  a full-trial counter, not the new selectable-candidate count.
- Charge a historical full-trial slot, an unmatched historical screen receipt
  (including interrupted), and historical selectable grid evidence conservatively.
  Do not infer that an old screen and full trial share a slot without frozen
  executable identity. No legacy receipt is silently attached to a new candidate.
- New candidate identity binds the registered hypothesis, contract digest,
  dataset, code closure/content, model declaration, effective parameters and
  evaluator epoch. Linked screen and full-trial receipts consume one slot.
  Both admissions are exclusive; interrupted work remains spent. A finalized
  screen can be followed by the identical full evaluation once.
- Resolve lineage transitively through registered parent links, normalized module
  aliases and exact code-content equivalence. Different filenames or contract/
  cadence changes do not reset a known lineage's allowance. Economic equivalence
  beyond exact code/registered relationships is explicitly a later RG-07 review.
- Existing unbound low-level historical/fixture reservation interfaces remain
  chargeable, never a free slot. RG-04 owns complete accounting of grid/stress
  execution and scheduler/diagnostic outcome paths. This slice does not claim
  the entire governance program is complete.

## RG-04: durable operation and search accounting (#123)

Schema v5 extends the existing transactional registry with write-once
robustness plans, scenario reservations and scenario outcomes. The runner freezes
the plan before the first scored screen or full trial. Changing it after
admission is refused. A historical v4 admission lacking a frozen plan requires
review before it can advance. No existing evidence is rewritten.

- Screen, full evaluation, 1.5x analytical cost, 2x simulated cost, +1-bar latency,
  and each fixed neighbor have durable admission before execution. Stage 3
  cannot finalize without all planned scenarios. Engine scenario evidence
  includes an explicit UTC clock, returns, metrics and a digest. The analytical
  cost scenario records its derived statistic. Neighbor rows and scenario
  outcomes finalize atomically. Failed finalization exposes no successful
  performance report; interruption retains the charged identity.
- The evaluator owns the mandatory cost/latency plan. For fixed neighbors,
  only declared numeric baseline axes may change, with no data-universe
  change, at most two distinct alternative values per axis and no more than
  twice the number of changed axes in distinct configurations. Duplicate
  evaluations remain raw operations. This cardinality restriction is an
  implementation choice, not a scientific definition of a local perturbation:
  no numerical distance bound has been approved. Wider or structural sweeps
  must use separately admitted candidates. Choosing a tested neighbor as a
  subsequent baseline costs a candidate slot; it cannot inherit qualification.
- Raw operation count includes incomplete reservations and fixed scenarios.
  The statistical selection count treats a prospectively frozen screen/full
  candidate as one search identity, using the completed full return tape in
  preference to the screen, not whichever has better performance. Fixed
  scenarios add no independent search identity. Historical pre-v5 outcome rows
  retain their former selection debt and unknown-epoch unresolved receipts are
  conservatively charged in each queried epoch. Stored inherited floors are
  preserved. New data/Stage 0 technical refusals are recorded separately, without
  claiming a scored attempt. The scheduler checks registered candidate budgets
  before new work; transactional admission remains authoritative.
- The unregistered outcome-producing commands af30_book_report,
  xs_panel_diagnostics, xs_residual_momentum_diag and xs_residual_reversal_1h_diag
  are retired; use the registered Warden evaluation path instead. Historical
  implementations remain in Git. Shadow engine parity replay/scaling is limited
  to synthetic engineering fixtures with matching dataset digests. Read-only
  diagnostics over recorded evidence and technical causal checks remain
  available.
- Boundary: this governs supported Warden entry points, not arbitrary Python,
  shell, or same-UID filesystem/SQLite access. A user who can modify protected
  code or invoke the internal numerical kernel directly is outside this trust
  boundary. This is not an operating-system sandbox. Manifest approval, the
  principal's legacy-mapping ratification, further research-intent/benchmark
  gates and independent conformance remain prerequisites to deployment.

Only scratch synthetic fixtures are used for this slice's scientific execution
tests; no production registry migration, manifest signing, or live holdout access.

## RG-05: frozen research intent and independent conformance (#124)

Schema v6 adds append-only intent, review and candidate-binding records to the
existing registry. The principal approved the policy of independent review for
each materially distinct candidate before its first scored evaluation. A frozen
candidate can advance from screen to full without another review; a refused or
unavailable judge never passes.

### Operator sequence

1. Register the hypothesis, then approve the research plan before candidate
   evaluation. Use the existing DataRequirement format and resolver (#116).
   For a compiled sandbox (#117), the requirement must match its sealed
   artifact exactly. Sandbox data remains non-market-eligible.
2. Record the returned intent digest in the experiment YAML under
   `research_intent_sha256`.
3. Describe the candidate without scoring it. Provide this exact binding to
   an independent reviewer. The description loads strategy metadata and
   resolves the exploratory data identity, but does not run a screen or
   simulation.
4. The principal attests the independent review, then the regular screen/full
   commands enforce it and reserve the RG-03 candidate slot atomically.

```bash
warden register-hypothesis hypothesis.yaml --json
warden approve-research-intent HYP-ID plan.yaml --principal PRINCIPAL --human-confirm YES_APPROVE_INTENT --json
warden describe-candidate experiment.yaml --json
warden record-conformance experiment.yaml review.yaml --principal PRINCIPAL --human-confirm YES_RECORD_REVIEW --json
warden screen experiment.yaml --json
warden backtest experiment.yaml --json
```

Plan example (replace with the actual registered hypothesis, data and thesis):

```yaml
author: strategy-author
thesis: Inventory pressure predicts continuation
strategy_module: strategies/candidate.py
data_requirement:
  asset_class: crypto_perps
  frequency: 1h
  symbols: [BTC, ETH]
  min_start: "2025-01-01T00:00:00Z"
  price_basis: ohlcv_funding
  execution_mode: NEXT_BAR
purpose: absolute
evaluation_criteria: Reject unless contract gates and after-cost edge pass
allowed_revisions:
  sizing: Change exposure without changing the economic thesis
```

Incremental intent requires `purpose: incremental` and a `benchmark_spec`
path under `experiments/`. Its declared config and strategy closure are
fingerprinted at approval and checked before scoring. RG-06 adds the frozen
comparative development gate below; incremental holdout execution requires
an authorized frozen paired batch under RG-08.

Review YAML requires the exact `binding_sha256` returned by describe-candidate,
`verdict` (PASS, REFUSED, or JUDGE_UNAVAILABLE), `reviewer` (required for
PASS/REFUSED), and nonempty `evidence`. A material revision also requires a
`revision` key from the approved plan and its admitted
`predecessor_candidate_id`. Each new identity consumes the ordinary RG-03
slot; approved intent itself cannot be replaced, even before a score.
After admission, the review binding cannot be replaced. Prior attempts
cannot receive a retroactive initial approval. A changed thesis, contract,
dataset or other plan-level fact requires a new related hypothesis, subject
to existing lineage and selection-group accounting.

The immutable record binds the hypothesis, plan, resolved contract, exploratory
dataset and manifest, venue policy/group when present, optional benchmark,
candidate closure and effective parameters, and RG-04 robustness plan.
Qualification checks the recorded binding before any holdout access. Historical
receipts and artifacts remain readable and their debt is retained; missing
historical conformance is not silently synthesized for new qualification.

Both approval mutations are interactive principal-only CLI actions. Reviewer
identity and evidence are principal-attested, not cryptographically authenticated;
the CLI confirmation is not OS-level access control. The ledger establishes
ordering of governed approvals and scored attempts, not proof that nobody
edited source code or inspected outcomes outside Warden. The protected manifest
must still receive actual human approval before deployment.

Legacy evaluator-mechanics tests explicitly opt into a scratch-ledger-only
pre-RG-05 policy double; they continue to exercise actual numerical, data,
causality, RG-03/04 and receipt gates. Dedicated RG-05 tests, installed CLI
advance, and compiled-sandbox integration exercise the real approval boundary
with synthetic fixture review evidence. No production review, manifest signing,
live registry migration, holdout access, or scientific qualification was
performed by this implementation.

## RG-06: benchmark-relative qualification (#125)

The principal approved the comparative policy on 2026-11-27. For an
incremental intent, Warden freezes the policy and the benchmark fingerprint
together in the existing immutable RG-05 document. Absolute plans retain their
existing digest and behavior. An older incremental intent without this
comparative policy cannot be retroactively upgraded; register a related
hypothesis under the normal lineage budget.

### Frozen decision rule

- Primary statistic: candidate annualized after-cost daily Sharpe minus
  benchmark annualized after-cost daily Sharpe. Material improvement is +0.25.
- Observation unit: eligible UTC calendar dates (Warden's daily clock), formed
  by compounding the complete matched raw bar tapes. Empty market dates are
  not invented, zero-return eligible dates are retained. UTC dates are not a
  claim of exchange-local overnight session equivalence. Annualization is fixed
  at 365 for crypto perpetuals and 252 for other contracts.
- Minimum 120 daily observations and 20 non-overlapping blocks of data.
  Paired circular block bootstrap, default five days, 20,000 draws, seed 731.
  A longer block requires a nonempty preregistered justification.
- One-sided alpha 0.05, Bonferroni over the six permitted lineage candidates,
  even when fewer have been evaluated. RG-02's symmetric percentile interval
  is calculated at confidence 1 - 2 * (0.05 / 6); its lower tail is the
  one-sided adjusted bound. PASS requires the lower bound strictly above
  +0.25. An upper bound at/below +0.25 is REJECT; unresolved, inadequate or
  degenerate comparisons are INCONCLUSIVE. This is approximate dependence-aware
  bootstrap inference, not a proof of power or program-wide error control.
- Absolute Stage 1-3, selection-group DSR, robustness, audit, provenance and
  causal gates are unchanged. A comparative PASS is necessary but not sufficient.

Use the RG-05 operator sequence with these plan fields:

```yaml
purpose: incremental
benchmark_spec: experiments/benchmarks/reference.yaml
comparative_block_length: 5
# comparative_block_reason: required for a block longer than five days
```

The comparator is fixed, not a second search candidate: no parameter grid,
no alternative universe, no benchmark switching. Both legs use the same
eligible panel and evaluation slice, contract costs, delay, nominal initial
capital and risk limits. No ex-post volatility rescaling is performed.
Benchmark code must implement an active causal protocol and pass the existing
prefix checks. Separate kernel instances prevent cache collisions across
strategy modules.

The existing RG-04 operation ledger admits the benchmark before execution;
its finalized evidence binds both raw return Parquet tapes, exact clocks,
code/config and basis digests, causal evidence, settings and comparison.
The benchmark operation adds one raw operation, not a second selectable
candidate. Stage 3 cannot finalize without the planned paired evidence.
The published candidate return tape must agree with the comparative leg
(on the existing selection grid, where structural zero projection is allowed).
The comparison itself never intersects or pads the two eligible clocks.
Missing/corrupted artifacts fail reconciliation. Interrupted reservations
remain spent and cannot be replayed.

A non-PASS adds `incremental_comparison` to failed gates and cannot qualify.
The production holdout path additionally requires a finalized comparative
development PASS and a human-approved frozen paired batch under RG-08
(#127) before any holdout claim. The development decision
policy is immutable and must be reused by that batch. No new holdout exposure
or data acquisition is authorized here.

No new schema migration: RG-06 extends the frozen RG-04 operation plan and
evidence payload under registry v6. Existing absolute and historical evidence
remain readable. Verification uses only scratch synthetic fixtures. No
production registry writes, manifest signing, or scientific qualification.

## RG-07: prior evidence, economic novelty and terminal dispositions (#126)

RG-07 adds prospective novelty enforcement at the existing candidate-admission
transaction. The authoritative ledger is still the Warden SQLite registry;
the packaged research-outcome-memory index remains a read-only source of
off-branch evidence. No semantic classifier, vector database or memory
service is introduced.

### Review before admission

Run `warden describe-candidate experiment.yaml --json` after registering the
approved research intent. In addition to the RG-05 binding, the output now
contains `prior_memory`: a versioned snapshot of the lessons file identity
and headings, branch-result index and provenance, canonical hypothesis
catalog, approved theses and data requirements, prior outcome and decision
references, terminal dispositions, and deterministic relationship hints.

Read `.workspace/memory/LESSONS.md` and the cited evidence; compare the
mechanism, signal construction and universe against prior work. The
independent reviewer and principal must attest the disposition in the
existing conformance review YAML. Example for a related prior hypothesis:

```yaml
novelty:
  snapshot_sha256: <prior_memory.snapshot_sha256 from describe-candidate>
  lessons_disposition: Retain the prior cost and turnover falsification lesson.
  search_disposition: Reviewed the canonical catalog and branch outcome index.
  relationships:
    HYP-PRIOR:
      relation: RELATED
      mechanism: Same liquidity provision mechanism as the registered parent.
      signal: Adds a frozen participation filter rather than a new mechanism.
      universe: Same instruments, with an explicitly documented contract change.
      outcome_disposition: Prior negative evidence remains applicable and charged.
```

The full review also contains the existing RG-05 fields. For a genuinely
new idea, relationships can be empty only if there are no mandatory hints or
reviewer-identified related ideas. Each indicated relationship must have an
explicit RELATED or DISTINCT disposition and substantive explanations on all
three dimensions and prior outcomes. UNRESOLVED never authorizes admission.
Exact declared parent/module/content aliases must share the existing lineage;
a reviewer identifying another economic relationship must use the registered
parent lineage as well. Exact matching normalized mechanism or thesis text
is a review hint, not proof of economic equivalence. Human review remains
responsible for identifying non-obvious relationships.

New admissions validate the actual code/config/data identity and reviewed
memory snapshot inside the SQLite reservation transaction. Exact executable
duplicates on the same contract, dataset and epoch are denied even when
renamed; historical trial duplicates with a matching recorded closure are
also refused. Missing/malformed sources and stale snapshots fail closed.
Branch exact outcomes remain locked; branch-related outcomes with no
canonical evidence reconciliation cannot reset lineage debt. The branch
index is retained unchanged. A frozen screened candidate can advance using
its original review, but newly discovered related branch outcomes block
replay. The review snapshot is part of the append-only conformance record
referenced by candidate admission. It includes prior outcome references,
not newly computed candidate performance.

This is a conservative, principal-attested application boundary, not a
claim of automated economic equivalence, completeness of unrecorded research,
or OS-level reviewer authentication. A newly recorded unrelated catalog or
outcome change may require re-describing and refreshing an unadmitted review.
Historical v6 review and outcome bytes remain readable. A pre-RG07 approval
without prospective novelty cannot authorize new scoring; obtain a new
review before first admission, or review the already-admitted historical
evidence without silently rebinding it.

### Terminal research disposition

Use the interactive principal-only command (unavailable to noninteractive
schedulers):

```bash
warden close-hypothesis HYP-ID --status EARLY_STOPPED \
  --reason "The principal elects to stop without spending the remaining budget." \
  --principal PRINCIPAL --human-confirm YES_CLOSE_HYPOTHESIS --json
```

Supported statuses: REJECTED (requires negative scientific evidence),
INCONCLUSIVE (recorded inconclusive/failed/interrupted evidence),
EXHAUSTED (budget spent and no advanceable candidate), and EARLY_STOPPED
(principal discretion, including before scoring). Terminal records include
attribution, reason, budget/lineage snapshot and immutable evidence
references. A principal must reconcile a reserved writer/robustness operation
before closure. No slot is refunded, no trial rewritten, and no terminal
hypothesis can be reactivated. A related new hypothesis still inherits its
lineage usage. Governed candidates at an exhausted cap with no pending or
advanceable candidate automatically receive EXHAUSTED evidence; a successful
screen may still advance. Historical statuses and qualification rows remain
unchanged. Closed hypotheses cannot score or newly claim holdout.

`spec-status`, `advance` and the packaged outcome inventory expose terminal
status and reason. The existing branch index remains authoritative for its
unreconciled branch evidence, not an automatic import into SQLite.

Registry v7 is an additive migration (one terminal table and immutable
triggers). No historic review, trial, return, qualification or selection
debt is rewritten. All verification here uses synthetic scratch ledgers;
no production database migration, approval signing, market scoring or
holdout exposure is authorized.

## RG-08 — Frozen holdout batches and exposure

Registry v8 adds immutable batch authorizations, exposure records, legacy
scope reconciliation, read-start receipts and batch outcomes. The existing
lineage claim remains binding. Before any holdout data is hashed or opened,
the evaluator verifies the signed policy, frozen identity, intent and novelty
review, development evidence, six-check audit, and run-bound causal proof.
It atomically claims both exposure and lineage, then durably records read-start.
A claim is spent even if execution stops before read-start. No retry, release,
adaptive candidate addition, or benchmark replacement is permitted.

The principal owns `contracts/holdout_exposure_policy.yaml`; it must be
covered by the verified human manifest HMAC. Its scope describes the whole
readable holdout artifact, not merely the scored slice. Example schema
(illustrative only; not an approved production policy):

```yaml
version: rg08-exposure-v1
history_attestation: "Principal reviewed the complete prior exposure inventory."
windows:
  EXAMPLE_CONTRACT:
    dataset_sha256: "<actual 64-character SHA-256>"
    start: "2025-01-01T00:00:00Z"
    end: "2025-12-31T23:59:59Z"
    symbols:
      BTCUSDT: BTC
legacy_scopes:
  RUN-EXAMPLE:
    scope:
      start: "2024-01-01T00:00:00Z"
      end: "2024-12-31T23:59:59Z"
      symbols:
        BTC: BTC
    evidence: "Principal-reviewed evidence of the actual historical exposure."
external_exposures: {}
```

Canonical underlying identifiers are stable across cadence, contract and
provider aliases. Same-lineage exposure is denied even on a different
window. Unrelated research overlapping a previously exposed artifact hash,
or an inclusive UTC window and underlying, requires an explicit principal
independence explanation for every overlapping exposure. Unrelated,
non-overlapping research does not need that explanation. Unknown legacy
scope, unresolved identity, or incomplete history blocks qualification;
never infer that missing evidence means no prior exposure.

Operator sequence:

1. Inspect the signed policy and resolve historical scopes using evidence;
   sign the protected manifest separately. Do not sign on behalf of the
   principal or open holdout data to discover a scope.
2. Run `warden describe-holdout-batch --run-id RUN-ID --json`. Review the
   exact document and batch digest. This is a metadata-only operation.
3. Author an approval document containing `batch_sha256` and an
   `independence` mapping keyed by every overlapping exposure ID, with
   substantive explanations (empty mapping only for zero overlaps).
4. Run `warden authorize-holdout-batch --run-id RUN-ID --approval approval.yaml
   --principal PRINCIPAL --human-confirm YES_AUTHORIZE_HOLDOUT_BATCH --json`.
   Authorization freezes one absolute candidate or one incremental candidate
   and its existing benchmark. It does not read or claim the holdout.
5. Only with separate explicit human unseal authorization, run
   `warden qualify --run-id RUN-ID --batch-id BATCH-ID --unseal-holdout
   --human-confirm-unseal YES_UNSEAL_HOLDOUT --json`.
6. Inspect `warden holdout-batch-status --batch-id BATCH-ID --json`.
   An interrupted claim remains spent; a changed or corrupt return artifact
   is reported as evidence drift. An observed scope incident blocks subsequent
   qualification pending explicit reconciliation; it never releases exposure.

A governed incremental batch evaluates the preregistered benchmark and
candidate on the identical holdout panel and clock, under the same contract
execution and refit policy, through the existing ML4T evaluation kernel.
The RG-06 comparison policy is unchanged. Both the existing absolute
holdout tests and comparative PASS are required for promotion. A comparative
INCONCLUSIVE is recorded as such in the batch result and cannot promote.
The legacy qualification row and its verdict vocabulary remain compatible.

Migration preserves historical qualifications and claims, including reused
lineages with distinct historical run IDs. It does not manufacture historical
scope attestations. Production policy provisioning, signed manifest approval,
and reconciliation of the live ledger's historical exposures are separate
principal-controlled steps. All RG-08 tests use synthetic fixtures and
scratch databases; no production holdout or live registry migration is
authorized by implementation approval.
