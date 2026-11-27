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
