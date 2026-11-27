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
fingerprinted at approval and checked before scoring. The paired outcome gate
remains RG-06; incremental qualification is refused until that gate exists.

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
