---
hypothesis_id: HYP-2026-XXX
contract_id: CONTRACT_ID_HERE
title: One line, no marketing
strategy_family: rules_ts # rules_ts | cross_sectional_ml | carry_rv | meta_overlay
economic_mechanism: >
  Who pays, and why do they keep paying? State the mechanism, not the pattern.
counterparty_or_friction: >
  Name the counterparty or the friction the edge is paid from.
expected_holding_bars: 1 # match the intended hold in contract bars
falsification_criteria: >
  Replace with the applicable family and contract gates, research-specific
  rejection conditions and any required comparative policy. No gate may be lowered afterwards.
parent_hypothesis_id: null # required for meta_overlay; use existing related lineage
source_provenance: raw/clippings/<publication>/<file>.md:LINE-LINE
source_published_at: null # actual publication date or explicit unknown
llm_model_id: <model>
---

# <Title>

Draft term sheet: complete before implementation or outcome inspection. Use the
applicable ML4T skills and the intake sequence in AGENT_RUNBOOK.md. Registration
reads only front matter; the complete finalized Markdown text (including this
body) goes into the approved intent plan's `thesis` string, not a mutable path.
Remove instructions and placeholders before approval.

## Research question and prior art

- Purpose: absolute qualification or incremental improvement; label replication
  versus adaptation separately. State the falsifiable prediction and mechanism.
- Search registered hypotheses, related outcomes and lessons. Record search
  scope, IDs, negative evidence, novelty rationale and lineage disposition.
  Reuse exact duplicates; resolve related/uncertain ideas before admission.
- Prior evidence: source dates, claimed results, tested periods, known biases
  and previously exposed data. Distinguish observations from assumptions.
  Published performance is not independent qualification.

## Source rules and adaptations

| Component | Original rule and source file:line | Proposed choice and reason |
|---|---|---|
| Universe and eligibility | [MISSING] | … |
| Signal / entry | [MISSING] | … |
| Exit / holding period | [MISSING] | … |
| Sizing / risk / stop | [MISSING] | … |
| Decision / execution timing | [MISSING] | … |

For an original hypothesis, state the proposed rules and evidence motivating
them instead of claiming a source defined them. Keep missing source rules
visible; resolve material ambiguities as explicit preregistered adaptations,
or stop if they cannot be resolved.

## Data and decision clock

- Reference the semantic DataRequirement (see docs/DATA-REQUIREMENT.md).
  Specify required observations/fields, information availability, history
  and point-in-time eligibility; resolve through #109. If the requirement
  cannot express a necessary input, report the gap rather than substitute data.
- Separate observation/bar cadence, decision clock (timezone/session, incomplete
  sessions, timestamp availability), target/holding horizon and first eligible
  execution. State handling of missing inputs and zero-exposure periods.
- Reference the resolved contract for costs, capital/risk constraints, execution
  basis and evaluation dates. Record provenance/evidence tier and data cutoff;
  do not override contract policy. Explain liquidity, lot size and capacity
  assumptions or limitations relevant to the proposed size.

## Strategy family (complete only the applicable section)

- **rules_ts:** causal indicators, thresholds, entry/exit, position state
  transitions, sizing and order timing.
- **cross_sectional_ml:** feature availability, target and horizon, fold-local
  preprocessing, purged/embargoed validation, simple linear baseline and
  forecast-to-position mapping.
- **carry_rv:** tradable legs, spread definition, net carry including costs,
  convergence/stationarity thesis, hedge ratio and rebalancing/exit.
- **meta_overlay:** registered parent, intervention trigger, permitted exposure
  changes, timing and tail-risk/turnover objective relative to parent.

For each family state directions, exposure, allocation, rebalancing and risk
behavior sufficiently precisely to implement without outcome-driven choices.

## Evaluation and bounded search

- Reference applicable absolute contract gates and define hypothesis-specific
  acceptance, rejection and inconclusive conditions. No retrospective metric or
  threshold selection.
- Incremental only: reference the frozen executable benchmark; explain why it
  is economically relevant. Apply RG-06's policy in docs/RESEARCH-GOVERNANCE.md
  (paired after-cost returns, matched clock, materiality, dependence,
  multiplicity and sample adequacy). Justify a longer bootstrap block if used.
  Absolute mode must not claim incremental alpha.
- Name allowed revisions and their rationale; empty if none. Distinguish fixed
  robustness checks from selectable variants; stop on decisive failure or no
  justified revision. Existing hypothesis/lineage budgets and selection debt
  remain authoritative.
- List unresolved scientific decisions. Ready for approval only when none
  prevents causal implementation or interpretation of the frozen evaluation.
