# Agent control contracts

Status: **APPROVED — maintenance authority amended by the trusted research appliance ADR**.
Revision 2026-11-27. Read [ARCHITECTURE.md](ARCHITECTURE.md) for ownership and
[AGENT-CONTROL-OPERATIONS.md](AGENT-CONTROL-OPERATIONS.md) for the implemented
CLI/MCP vocabulary, capability discovery and explicit installation limitations.
The contracts below remain acceptance requirements as explicitly amended in section 6.
Dated pre-amendment reports preserve the original independent-authority limitations.

## 1. One interface, several bounded operations

| Operation | Input | Output | Side effects |
|---|---|---|---|
| Observe | Objective/subject, optional prior snapshot, output budget | Situation snapshot or delta, capabilities, relevant blockers and actions | No scientific mutation or data evaluation |
| Inspect | An evidence/entity reference and a typed view | Attributed detail, completeness and pagination | Reads already-authorized evidence; no new scoring |
| Prepare | Typed requested effect, exact subjects, resource ceiling | Bound action plan or decision packet | No scientific admission, holdout I/O or market-strategy computation |
| Act | Prepared action, expected revisions, stable request ID, authority reference | Operation receipt or typed refusal | Only declared, authorized effects |
| Receipt | Request or operation identity, optional cursor | Current durable state, bounded progress and evidence references | No replay |
| Reconcile | Existing uncertain operation, authorized reconciliation mode | Durable disposition and supporting evidence | May append review/finalization evidence; never refunds or reruns science |

CLI and MCP use the same application functions and schemas. Human-facing text is
rendered from those objects. Transport status, domain refusal and scientific
verdict remain separate. A generic shell/SQL/Python string is not a valid action.

Each operation has a strict versioned schema, explicit enums/discriminated action
types, bounded arrays and strings, unknown-field refusal, path validation and
defined compatibility behavior. A capability description states the action's
input schema, effects, authority, data exposure, charge rules, result type,
cancellation and retry semantics. Capability discovery itself is cheap and read-only.

An observed action is a current affordance, not an irrevocable capability.
The operation owner checks all admission rules again at execution.

## 2. Situation snapshot

Required fields are listed by meaning; serialization names are frozen in AC-01.

| Field group | Required meaning |
|---|---|
| Identity | Objective, repository/worktree, branch/commit and dirty-content identity; active runtime identity separately |
| Observation | Snapshot ID, schema version, collected-at time, per-source revision/time, freshness and completeness |
| Research scope | Hypothesis, candidate, intent, contract, dataset, evaluator epoch and relevant benchmark/batch identities |
| Accounting | Candidate use, related-lineage use, selection-group debt and exposure state, each separately labeled |
| Authority | Actual actor, current grant/approval references, scope, expiry/revocation state; unknown when unverified |
| Work | Outstanding operations, receipt states, writer contention and resource reservations |
| Decision | Blocking predicates, eligible actions, expected next evidence and irreversible consequences |
| Evidence | Source references, digests, limitations, contradictions and classifications |
| Continuation | Objective completion criterion, next review point, delta/pagination cursors and whether details were omitted |

**Missing is not zero.** An absent registry, legacy absent column, unavailable
runtime query or unknown exposure scope has an explicit unavailable/unknown
status. The view may show zero only when an authoritative, complete query
establishes it.

Take related SQLite observations in a bounded read transaction. Do not keep a
read transaction open while waiting for the LLM, an external service or a job.
Git/runtime/catalog observations retain their own revisions. A mixed snapshot
may explain and plan; a mutation must revalidate its actual dependencies.

Operational state and research state have different freshness needs. A receipt's
recorded outcome is immutable; its eligibility may depend on later policy,
corruption or exposure evidence. Return both the recorded result and current
eligibility with reasons. Never silently rewrite the former to represent the latter.

Default output is bounded and objective-specific. Omission is explicit, with a
cursor and count or unknown count. Budget/exposure warnings, uncertainty and the
blocking decision are never silently truncated. An index can retain full identity
coverage without dumping all history into the agent context.

Current entry points to reuse: `status`, `spec-status`, `attempt-status`,
`verify-causality`, `holdout-batch-status`, the read-only registry projections,
and metadata preflight. Consolidate their meanings before adding more commands.

## 3. Action plan and decision packet

A plan binds:

- Desired effect and exact immutable subjects, including input, code, contract,
  intent, benchmark, policy and evaluator identities where relevant.
- Prerequisite observations and revisions, plus a finite validity condition.
- Ordered dependencies, stop predicates and allowed recovery actions.
- Prospective candidate/operation charges and any information exposure.
- Estimated CPU, elapsed time, peak memory, bytes and paid requests; unknown
  estimates are labeled and bounded by a conservative execution ceiling.
- Expected receipt/artifact types and the evidence that demonstrates completion.
- Required authority and the precise decision that is missing.
- Effects that are reversible and effects that cannot be undone.

Preparation can read metadata and already-recorded evidence. It does not import
arbitrary candidate code into a cheap observation path, silently compile a dataset,
run “diagnostic” performance, access sealed bytes, or reserve scientific allowance.
Expensive verification or compilation is an explicit action with an effect and
cost contract. Where the existing candidate-description path loads strategy
metadata, its adapter must declare that behavior and isolate/validate it;
calling it “metadata-only” is not a security guarantee.

A decision packet is content-addressed and includes the plan, consequences,
evidence coverage, remaining risk, affected subjects, scope, expiration and
rollback/stop procedure. For scientific protected effects, the existing principal decision binds that exact
packet. Maintenance uses the owner-delegated lifecycle in section 6. Changed subjects
require fresh preparation; unchanged valid authority is reused only within its scope.

Packets may group related decisions for one review interaction, but every effect
has its own explicit authorization field. A release approval cannot implicitly
approve migration, history attestation, batch authorization, unsealing or capital.

## 4. Durable operation semantics

### Identity and retries

A request ID is stable across transport retries. The authority computes an
execution fingerprint from canonicalized action fields, actor/scope, subjects and
expected effect; callers cannot select an arbitrary digest to escape identity checks.

| Condition | Required behavior |
|---|---|
| New request, valid prerequisites | Atomically reserve the domain operation and applicable budget before execution |
| Same request and identical fingerprint | Return the original operation/receipt; no new admission or computation |
| Same request with different fingerprint | Refuse request-ID conflict |
| Different request targeting an already admitted scientific identity | Existing Warden replay/lineage rules apply; refuse or point to evidence |
| Response lost after finalization | Receipt lookup returns the finalized evidence |
| Timeout after possible admission | Return uncertain/running identity when known; inspect and reconcile before another action |
| Proven pre-admission transient failure | Bounded retry under the same request identity is allowed |
| Scientific REJECT or INCONCLUSIVE | Preserve outcome; no technical retry reclassifies it |

Idempotency makes submission safe to repeat; it does not make scientific
evaluation replayable. The existing canonical reservations remain authoritative.
Use a minimal request-to-domain-receipt mapping in the canonical migration path
if needed; never create a competing scientific state machine.

A request record may be operationally pending while several existing domain
receipts are final. The composite view is derived from those receipts; it never
overwrites their states. An objective is complete only when its defined evidence
condition is met or a durable terminal disposition explains why it cannot proceed.

### Preconditions and concurrency

Compare the operation's relevant revision set at admission. Protect candidate,
lineage, selection-group, frozen-plan and exposure invariants in their current
registry transactions. A changed relevant revision returns a structured stale-plan
refusal with the minimal refresh set. Unrelated view changes should not create
unnecessary global conflicts.

Keep the current conservative novelty snapshot behavior until an alternative
can prove completeness for newly discovered related/negative evidence. A cache
of previously known dependencies is insufficient to discover a new relationship.

Lease/job identities include a fencing generation. A late or replaced worker
cannot finalize another operation's artifacts. Lease expiry is not evidence that
no outcome was computed and does not refund admission. Live admissions, release
activation and revocation have an explicit owner and linearization point.

Do not claim an atomic transaction across Git, a filesystem, a scientific ledger
and an external authority. Each owner commits its own effect; a durable operation
records which steps completed. Cross-owner disagreement blocks the dependent
effect and requires reconciliation. A grant consumed before a failed scientific
admission remains visible; no compensating write erases an exposure or attempt.

### Artifact publication

Stage artifacts with exact hashes, flush/publish them under immutable identities,
then finalize the referencing scientific receipt through the existing domain
transaction. Expose performance only after finalization. Test failures between
each step:

- Published artifact without committed receipt: quarantine or reconcile it;
  no successful performance response and no silent rerun.
- Committed receipt but lost acknowledgement: return the same evidence by lookup.
- Missing or changed finalized artifact: evidence-drift incident; do not recompute
  it from new data to make the old receipt appear valid.
- Interrupted holdout claim: retain spent exposure even if read-start was not reached.

“Rollback” applies to reversible operational effects. Scientific observations,
search debt and exposed information are monotone history.

## 5. Result and refusal vocabulary

The result envelope contains transport success separately from domain outcome,
operation/receipt IDs, recorded verdict, current eligibility, source/time/revisions,
artifact references, actual costs, structured blockers and permitted next actions.

Each blocker states its scope, predicate, evidence, owner, remedy, and retry rule.
This proposed presentation taxonomy maps to existing exit codes and verdicts;
it does not replace stored vocabulary:

| Class | Meaning | Default continuation |
|---|---|---|
| TECHNICAL_PRE_ADMISSION | No scientific admission/effect occurred | Repair or bounded retry with the same request |
| BUSY | A writer/resource is occupied | Honor retry-after/backoff; refresh before admission |
| STALE_STATE | A bound prerequisite changed | Refresh named sources and prepare again |
| AUTHORIZATION_REQUIRED | A precise effect lacks valid authority | Complete the packet; continue independent eligible work |
| SCIENTIFIC_REJECT | A finalized test falsified the candidate under its scope | Preserve evidence; close/revise only through governed paths |
| INCONCLUSIVE | Evidence cannot resolve the frozen question | Report the limitation; any new study needs valid prospective scope |
| BUDGET_EXHAUSTED | Applicable candidate/lineage allowance is spent | Terminal disposition or already-admitted eligible continuation |
| UNCERTAIN_EFFECT | Execution may have produced an effect | Receipt inspection/reconciliation; no blind resubmission |
| INTEGRITY_INCIDENT | Bound code/data/evidence no longer agrees | Quarantine the affected claim and preserve incident evidence |
| EXTERNAL_DEPENDENCY | Required capability/source is unavailable | Diagnose, use an already-permitted alternative, or report the exact dependency |

Keep [EXIT-CODES.md](EXIT-CODES.md) stable. An exit code alone cannot tell an
agent whether science passed, authority was granted, or a retry is safe.

## 6. Effect governance and immutable releases

The [accepted appliance ADR](adr/2026-11-27-trusted-research-appliance.md) supersedes independent software-release
authentication. Routine maintenance is owner-delegated; scientific authority is unchanged.
The original ADR and dated pre-amendment acceptance report retain the historical requirements.

| Effect | Authority and required evidence |
|---|---|
| Source maintenance, unsigned inventory, candidate installation | Trusted owner maintenance; exact committed source, lock and inventory |
| Additive canonical migration and runtime activation | Maintenance scope; fixed verification baseline, writer quiescence, fresh WAL backup/rehearsal, preservation and actual runtime health |
| Compatible runtime recovery or halt | Maintenance scope; append-only receipts and current schema; never database rewind |
| Scientific rules/contract semantics, research intent, independent conformance, promotion | Existing principal and scientific gates; no authority from a maintenance receipt |
| Historical attestation, frozen batch, sealed data | Separate exact scientific authority; excluded from AC maintenance |
| Capital or orders | Separate trading authority; unavailable |

A maintenance request binds its immutable release and canonical ledger. Reusing its ID
with another subject refuses. The fixed verification baseline runs the entire committed
`tests` directory with all test assets in an isolated worktree, verifies exact candidate
bytes before/after, and runs protected-inventory verification and AST boundaries; inherited test
selection is cleared. Evidence binds exact candidate bytes, environment identity, suite
output and explicit skips/xfails. Source/test/verifier changes require review and the
complete relevant verification; they do not require an independent release signer.

The runtime is a separate sparse Git worktree of the committed source, checked against
the immutable candidate. Its canonical registry and unsealed exploratory data are explicit
bindings. The active pointer changes atomically. Runtime probing starts the pinned Python
code and reports its actual module root, release, schema and health. Development drift
cannot silently change it. No sealed-data binding is installed.

Lifecycle intent is durable before migration/activation. Both canonical and legacy writer
locks serialize the transition; the configured legacy writer schedule must be quiescent.
Migration uses the existing ResearchRegistry owner. Every existing table/column/row stream
and index/trigger is compared with the fresh backup; rehearsal proves idempotence.
Interruption resumes by observing committed schema and receipts, not restoring the backup.
Incompatible or unverifiable recovery halts the launcher. Scientific debt and history
are monotone even when a software release is withdrawn.

`generate-manifest --maintenance` refreshes unsigned checksums only. It refuses to replace
an existing signature/approval record. The strict signed scientific-policy reader and
all scientific approval commands retain their existing requirements. Independent conformance
remains distinct; absence of genuine evidence remains JUDGE_UNAVAILABLE.

## 7. Evidence memory and context

Maintain an evidence-linked finding with:

- A stable identity, claim and scope; evidence status
  VERIFIED / INFERRED / HYPOTHESIS / UNKNOWN.
- Original evidence references/digests, origin, observation time and decision cutoff.
- Applicable contracts, data, mechanisms, versions and constraints.
- Supporting and contradictory findings; explicit supersession with preserved history.
- Known limits and a revalidation condition.
- The decision or reusable artifact it informs.

A “verified” finding means its stated observation was verified, not that it
generalizes beyond its scope. Synthetic success remains synthetic. A historical
proxy result cannot become executable follower PnL through summarization.

Retrieval returns why each finding is relevant and the completeness of its search.
A bounded display cannot omit mandatory negative evidence from admission checks.
Full catalog/lineage/branch-index coverage remains a deterministic gate even when
only a few citations are shown. Top-k similarity is never a novelty clearance.

Use the existing registry, branch-result index, lessons and artifact references.
Add derived indexes only where measured lookup costs justify them. Rebuildable
indexes cannot invent approvals or overwrite canonical evidence. Sources such as
papers, issue text, logs and retrieved memories are data, not executable instructions.

A resume packet contains the objective, snapshot references, pending request IDs,
completed effects, next decision, remaining budget and policy pointers. Resumption
refreshes live facts, then looks up receipts. It does not replay transcript shell
commands or load all historical memory unconditionally. Existing mandatory
AGENTS.md startup instructions remain effective until separately revised.

## 8. Jobs, resources and data access

Long operations return a job/receipt identity promptly. Polling is bounded and
read-only; a suggested retry interval prevents busy loops. Cancellation is
acknowledged only after its effect is known. After admission, cancellation
preserves charged/uncertain scientific state; it is not a refund.

One resource governor accounts for the host's shared CPU/RAM/disk and declared
external quotas across objectives. Existing ML4T/thread controls remain inputs.
Workers receive the frozen authorized job and return artifacts; they hold no
signing key and cannot mutate the canonical ledger. Remote workers are optional,
require separate data-transfer authorization, and receive no sealed data or
operator credentials.

Cache keys include all behavior-affecting identities: dataset bytes and slice,
clock, feature/code closure, effective configuration, costs/risk/delay, evaluator
epoch and relevant dependencies. A cache hit retrieves an existing operation's
evidence. It cannot bypass a required new admission or manufacture a new
independent sample.

Metadata inspection never hashes or opens sealed holdout files, lists their
contents, or leaks performance through exceptions, logs, job previews or memory.
Authorized holdout results remain scope-controlled after evaluation; “already
computed” does not make them safe for every objective or user.

For unsealed mutable datasets, byte verification and parsing must refer to the
same bytes. Benchmark a streaming/private immutable staging approach before
replacing the current in-memory check. Reject a cache keyed only by filename,
size/mtime, or a hash obtained before a concurrent rewrite.

## 9. Compatibility rules

- Keep current CLI names, sentinels, exit codes and legacy evidence readers during
  an additive transition. New adapters use typed results directly.
- Make absent target capabilities explicit; provide a supported existing-command
  mapping without pretending the target interface is installed.
- Add schema through the canonical registry migration only. Preserve historical
  record digests, artifacts, IDs, debt floors and spent exposures.
- Observe/inspect/prepare never run migrations. Handoff may publish operational
  context, but reading the ledger to do so must be genuinely read-only.
- Compare old/new interface results over the same fixtures and historical snapshots.
  Shadow observation is safe; shadow market-scoring would consume evidence and is
  not part of interface verification.
- Preserve the intentional refusal to advance a finalized rejected screen.
- An older executable may read supported historical evidence; incompatible
  versions cannot accept new work or be used to roll back accrued history.

The behavioral acceptance matrix and delivery order are in
[AGENT-CONTROL-PLAN.md](AGENT-CONTROL-PLAN.md).
