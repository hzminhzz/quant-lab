# Agent control implementation plan

Status: **AMENDED — trusted research appliance maintenance lifecycle**.
The [accepted ADR](adr/2026-11-27-trusted-research-appliance.md) narrowly revises A13/A14/A24 and explicitly
supersedes A23 for software maintenance. All other behavioral and scientific criteria
remain binding. The original [acceptance report](AGENT-CONTROL-ACCEPTANCE-20261127.md)
and [blocked cutover packet](AGENT-CONTROL-CUTOVER-20261127.md) preserve the pre-amendment
history; their unavailable independent authority was not demonstrated or retroactively passed.
Current operation is described in [AGENT-CONTROL-OPERATIONS.md](AGENT-CONTROL-OPERATIONS.md).
The [appliance acceptance and cutover report](AGENT-CONTROL-APPLIANCE-ACCEPTANCE-20261127.md)
records the completed amended lifecycle and actual deployed identity.

## Objective and completion

Implement the [architecture](ARCHITECTURE.md) and [control contracts](AGENT-CONTROL-CONTRACTS.md)
so one directly operating agent can carry an authorized research objective to an
evidence-backed conclusion with bounded context, safe continuation and minimal
principal effort.

Completion requires observable behavior through the public interface, preservation
of existing scientific semantics, actual lifecycle verification and runtime observation,
retained scientific authority boundaries, and measured operational costs. Documentation, a prototype,
passing unit tests or a green status indicator cannot establish that completion.

The existing #109/#119 review fixes, dirty work and production evidence remain
independent work. Preserve the original
[RG acceptance report](RESEARCH-GOVERNANCE-ACCEPTANCE-20261127.md) as historical evidence.
Do not rewrite that report to count this target architecture as implemented.

## Execution contract

Use one top-level objective and one direct executor. Continue through eligible
dependencies without asking the principal to sequence tickets. Deterministic
test/compute workers are optional; external coding-agent delegation is not required.

Before implementation, inspect the actual branch, diff, instructions, issue
dependencies, active runtime, canonical ledger ownership and available approval
capabilities. Record precise source/evidence identities. Preserve concurrent work;
use isolation only where needed. Never infer production state from the feature branch.

Implement vertical slices with behavior-first regression witnesses at the real
public seam. Run focused tests while changing a slice, then the required integrated
suite and compatibility checks at release candidates. Repeat tests to resolve a
specific risk or satisfy a required gate. Record failures, skips, expected failures,
warnings and untested limits separately from verified passes.

### Authorized completion scope — amendment 2026-11-27

The principal explicitly authorizes source amendment, direct implementation, verification,
commit/push/integration, owner-permitted deployment, additive canonical migration and
operational recovery after acceptance. Continue eligible dependencies without ticket-by-ticket
approval. Never bypass host permissions, access sealed holdouts, attest historical exposure,
invent independent conformance, allocate capital or trade.

The trusted appliance replaces the uninstalled independent release authority. Build a clean
immutable candidate, run the complete verification baseline, pause the configured legacy
writers, take a WAL-consistent backup, rehearse and verify the additive migration, then
activate and probe the actual pinned runtime. Preserve the concurrent development checkout.
Keep lifecycle receipts and continuation state. A compatible runtime repair or affected
operation halt is permitted; rewinding the scientific ledger is not.

Separate IMPLEMENTED, VERIFIED, MERGED, DEPLOYED and SCIENTIFICALLY QUALIFIED.
JUDGE_UNAVAILABLE remains a scientific limitation, not a software-maintenance dependency.
The original no-production execution scope is superseded only by this explicit amendment.

## 1. Delivery slices

| ID | End-to-end outcome | Dependencies | Main existing seams | Completion evidence |
|---|---|---|---|---|
| AC-01 | A frozen vocabulary, effect catalog and behavioral fixture set | None | CLI/policy, registry, existing RG tests | Strict input/result schemas; current-to-target mapping; captured read-only baselines; every target action has authority/charge/retry semantics |
| AC-02 | One reliable situation view and resumable evidence inspection | AC-01 | Read-only projections, status/preflight, checkpoint | Cold start and resume explain actual state without migration/scoring; missing facts remain unknown; bounded output/deltas |
| AC-03 | Prepare, submit and recover one governed action through CLI and MCP | AC-02 | Admission, registry transactions, CLI/adapter | Same request produces one admitted effect; stale/conflicting requests refuse; lost response recovers receipt; existing scientific gates remain binding |
| AC-04 | Build an immutable release candidate while development remains mutable | AC-01 | Integrity, locked environment, isolated test fixtures | Exact artifact/provenance and verification bundle; unsigned build identity distinct from authorization; current runtime untouched |
| AC-05 | Separate maintenance scope from scientific authority | AC-01, AC-04 | Existing scientific gates and exact lifecycle requests | No fabricated scientific approval; request/subject conflicts refused; zero principal CLI steps |
| AC-06 | Install and activate verified immutable releases under trusted maintenance | AC-03, AC-04, AC-05 | Candidate inventory, Git, canonical leases, lifecycle journal | Exact bytes/environment verified; actual runtime identity; interrupted recovery or halt without history rewind |
| AC-07 | Drive a bounded objective with recoverable jobs and shared resource limits | AC-03 | advance, leases, evaluation kernel, deterministic worker | Partial progress survives restart; no replay on timeout/cancel; CPU/RAM/I/O budgets enforced; terminal condition stops the objective |
| AC-08 | Reuse evidence and repairs through scoped, contradiction-aware findings | AC-02, AC-03 | Outcome memory, novelty, lessons, regression fixtures | Relevant finding carries sources/limits; mandatory negative history remains complete; invalidated lesson triggers recheck |
| AC-09 | Prove integrated compatibility and rehearse production transition | AC-06, AC-07, AC-08 | Snapshot/rehearsal tools, CLI/runner/MCP, protected runtime | Full adversarial matrix, exact preservation evidence, resource measurements and reviewable cutover packet |
| AC-10 | Complete the authorized verified lifecycle transition | AC-09 and actual owner-permitted operational gates | Pinned runtime and canonical state | Observed identities/health, writer quiescence, additive migration, preserved history and safe recovery; no scientific authority implied |

AC-01–04/07–09 source and prior evidence are reused. AC-05/06/10 follow the explicit
amendment above. A23 is superseded, never recorded as an independent-authentication pass.

The first useful release is AC-01/02: a truthful read interface and restart packet.
The manifest-friction path is AC-04/05/06; it does not wait for a new research-memory
platform. AC-07/08 reuse the same contracts and can follow without another control
stack. AC-09 validates the whole system before AC-10.

```mermaid
flowchart TD
  A["AC-01: contracts"] --> B["AC-02: situation"]
  B --> C["AC-03: governed action"]
  A --> D["AC-04: immutable build"]
  D --> E["AC-05: effect scope"]
  C --> F["AC-06: lifecycle"]
  E --> F
  C --> G["AC-07/08: continuation and reuse"]
  F --> H["AC-09: acceptance and rehearsal"]
  G --> H
  H --> I["AC-10: authorized cutover"]
```

## 2. Slice constraints that prevent a second system

### AC-01/02: observe before orchestrating

Freeze the public contract before adding aliases. Inventory existing commands and
actual effects. Reuse `ResearchRegistry(..., readonly=True)` and its narrow projections.
Fix checkpoint ledger reads through that same seam; publishing a checkpoint must
not create schema/quarantine rows. Explicitly separate observational audit logging
from scientific writes.

Keep current verdicts, exit codes and persisted semantics. New result facets are
additive. Define missing/legacy/unavailable states and use the actual source
revision rather than a count as a completeness proof.

Initial context is a situation summary and the task branch from
[CONTEXT-MAP.md](../CONTEXT-MAP.md). No unconditional full historical memory dump
is part of the target. Changes to protected startup instructions require their
own reviewed release; this documentation does not override current AGENTS.md.

### AC-03: deepen current admission

The action implementation invokes existing domain rules directly, not shell text
assembled by an LLM. Retain direct runner regression coverage for supported
non-CLI paths. Add request identity and receipt lookup at the current transaction
seam; do not store a duplicate scientific verdict in an orchestration database.

First vertical witness: submit an eligible synthetic screen, lose the response,
resume, retrieve the same receipt, and advance only if the recorded screen passed.
Prove no new candidate charge on lookup and no forced advancement on rejection.

### AC-04/05/06: one maintenance lifecycle

Retain the immutable candidate and checksum gates. Replace release grants, fake principal
proof adapters and privileged installation prerequisites with the owner-delegated lifecycle.
Separate code/dependencies from canonical state. Bind source, verification, request and runtime
identities. Fixed full-suite/inventory/AST checks cannot be narrowed by inherited test options.
Durable intent precedes live effects; retries inspect actual state. Scientific signatures,
review, promotion, exposure and trading gates remain outside maintenance scope.

### AC-07: preserve progress, not repeated attempts

Extend existing advance/lease behavior. Implement a durable job identity, bounded
progress, shared host limits, fencing and cancellation results. Isolate untrusted
candidate execution from canonical writes/keys where the installed trust boundary
permits it; demonstrate permissions with synthetic fixtures and dedicated test
identities. Do not describe same-UID execution as a secure sandbox.

A technical retry before admission may be cheap. A retry after uncertain scoring
or holdout claim is forbidden. Reconciliation reads and validates existing
artifacts; it does not reproduce the experiment to guess what happened.

### AC-08: improve knowledge yield

Start with existing ledger/index/lesson sources and a deterministic derived
finding index. Include off-branch outcomes and incomplete histories. Add a reusable
regression or documented remedy for a concrete recurring failure. Preserve
contradictions and retired findings with their evidence.

Display may be compact; novelty checks cannot become a top-k search. Keep the
current conservative RG-07 snapshot invalidation unless a new method demonstrates
complete discovery of relevant changes and receives the required policy review.

## 3. Behavioral acceptance matrix

All market-like execution in this matrix uses synthetic fixtures or explicitly
authorized unsealed test data. Historical-ledger checks use isolated consistent
snapshots. No production holdout is opened, hashed, sampled or permission-probed.

| ID | Scenario | Required observable result |
|---|---|---|
| A01 | Cold start in a dirty feature worktree with a different active release | Situation distinguishes checkout, dirty content, runtime and ledger identities |
| A02 | Missing ledger; old schema; unavailable runtime query | Explicit unknown/unavailable fields; no created database, DDL or false zero |
| A03 | Observe/prepare/handoff under a read-only scientific ledger | No scientific mutations, scorer calls or sealed-file I/O; declared operational logging only |
| A04 | Invalid action field, unsupported schema or capability | Typed refusal before side effect; supported next step/schema identified |
| A05 | Two concurrent candidates contend for the last hypothesis/lineage slot | At most one admission; all applicable debt and operation counts preserved |
| A06 | Same request submitted twice; same ID with changed payload | One effect for identical submission; conflict refusal for changed payload |
| A07 | Response lost after commit; process killed after reservation | Existing result retrieved, or uncertain receipt retained; no silent rerun/refund |
| A08 | Finalized rejected screen submitted through CLI, runner and MCP | Full advancement refused with the existing rejection evidence |
| A09 | Plan/code/contract/data/review changes after preparation | Relevant stale-state refusal before computation; refresh set named |
| A10 | Input bytes change after resolution; excluded symbol crosses temporal fence | Same-byte/fence safeguards still reject; no cached-metadata bypass |
| A11 | Benchmark, clock, risk/cost basis or paired evidence changes | Incremental evaluation refuses/inconclusive as governed; absolute PASS cannot override |
| A12 | Renamed candidate, changed cadence, branch failure omitted from display | Existing alias/lineage/selection debt and mandatory novelty checks still apply |
| A13 | Maintenance receipt/name/chat presented as scientific approval; lifecycle subject changed | Existing scientific gates remain binding; request/subject conflict refuses; no invented scientific authority |
| A14 | Source/tests/verifier change or inherited options narrow verification | Fixed complete verification and actual-diff review required; exact bytes/environment bound; no independent signer required |
| A15 | Development inventory becomes stale while an authorized runtime is active | Development diagnostics remain usable; runtime identity/authority unchanged |
| A16 | Late worker, duplicate result, cancel/lease expiry during execution | Fencing rejects stale finalization; actual effect known or explicitly uncertain |
| A17 | Activation fails; old binary cannot read newly accrued ledger state | Compatible rollback or affected-operation halt; no database/history rewind |
| A18 | Unknown legacy exposure; changed holdout pin; crash before/after read-start | Existing scope/claim refusals or spent/uncertain incident remain; no information refund |
| A19 | Retrieved lesson contradicted by newer scoped evidence | Both sources preserved; applicability re-evaluated; no automatic policy mutation |
| A20 | Small context budget or paginated history | Explicit completeness/cursors; no hidden blocker or missing mandatory negative gate |
| A21 | Multiple jobs exceed CPU/RAM/I/O quota or a job stops heartbeating | Bounded scheduling/termination, accurate resource receipt, conservative scientific state |
| A22 | Public old/new interface over the same historical fixture | Equivalent stored identities/verdicts/debt; backward-compatible exit/sentinel behavior |
| A23 | Original independent release authentication | SUPERSEDED by accepted ADR; replacement is a verified local lifecycle completed without principal CLI steps |
| A24 | A genuinely required permission or scientific capability is absent | Explicit blocker without fabricated proof; absent independent release infrastructure no longer blocks maintenance |
| A25 | Full integration test and migration rehearsal | Concurrent work, protected files, original database/evidence and sealed data remain unchanged |

A01–A04/20 belong to AC-02; A05–A12/22 to AC-03; A13–A15/17/23/24
to AC-04/05/06; A16/21 to AC-07; A12/19/20 to AC-08. A18/25 are required
again at AC-09 with the existing RG holdout/exposure and migration suites.

## 4. Measure ergonomics and resource cost

These remain the approved engineering acceptance targets. Measured results and
limits are recorded in the [acceptance report](AGENT-CONTROL-ACCEPTANCE-20261127.md).

| Measure | Initial target and evaluation |
|---|---|
| Cold-start state acquisition | One default situation request identifies objective, scope, authority, blockers and next actions |
| Default context size | At most 12 KiB of summary JSON, with explicit pagination/omission; detailed evidence requested separately |
| Cheap observation | Zero evaluator invocations, scientific writes, full dataset reads or sealed-file reads |
| Retry waste | Zero duplicate scientific admissions/computations in lost-response and duplicate-submit fixtures |
| Human mechanics | Zero required human CLI commands through the installed approval/release path |
| Approval repetition | Unchanged, unexpired scoped decision reused; reapproval only when its bound inputs or scope change |
| Knowledge reuse | Seeded prior failure/repair discovered with provenance and limits; mandatory negatives remain complete |
| Compute/I/O | Record elapsed time, CPU, peak RSS, bytes hashed/read and cache hits on representative workloads |
| Byte-verification overhead | Compare current behavior against bounded streaming/staging on largest authorized unsealed datasets; show incremental verification memory separately from parser/model memory |
| Host contention | Measure foreground observation latency and memory under concurrent bounded jobs; no unconstrained nested thread pools |

Measure baselines before optimizing. Set latency thresholds from that baseline
and the actual VPS/resource budget; do not invent “10x” gains. A performance change
must preserve byte identity, temporal fences, recorded counts and outcome semantics.
Skip a new cache/worker/service if it cannot demonstrate lower total cost after
including invalidation, operational effort and auditability.

## 5. Migration and cutover sequence

### Rehearsal: authorized engineering scope

1. Inventory exact source/dependency/policy identities and all dirty/untracked
   concurrent work that could be affected. Locate the actual live runtime and
   registry through authorized metadata only.
2. Take a consistent isolated SQLite snapshot using the existing backup tooling.
   Preserve the original database/WAL and evidence artifacts. Do not copy a live
   WAL database as though the main file alone were a consistent snapshot.
3. Run the one canonical additive migration on the snapshot. Compare every
   previously existing table/column/value, immutable digest, trial/operation ID,
   selection floor, claim and exposure. New rows must have explicit provenance.
4. Reconcile local-only contract/hypothesis references from the #119 acceptance
   report. Do not silently drop, import, requalify or rewrite them.
5. Preserve unknown historical exposure as unknown. Prepare an evidence inventory;
   the principal supplies any required history attestation without opening sealed
   data to discover its scope.
6. Exercise compatible upgrade, activation failure, rollback/forward repair and
   resumption using scratch artifacts and receipts. Validate old readers that are
   still supported.
7. Produce one cutover packet: exact release, verification results/omissions,
   migration diff and evidence preservation, authority requirements, resource
   measurements, observation plan and compatible rollback.

### Live execution: authorized maintenance scope

8. Complete and review the exact candidate and verification receipts under the accepted ADR.
9. Pause configured legacy writers, acquire both writer locks, take a fresh backup, rehearse,
   apply the canonical additive migration and prove preservation of all old rows/columns.
10. Atomically activate the pinned runtime; observe its actual executable identity, canonical
    schema and read-only health. Reconcile interruption without database rewind.
11. Leave legacy scientific scheduling paused until its admission path is eligible. Deployment
    creates no research intent, independent review, historical attestation or sealed-read grant.

## 6. Known prerequisites and explicit design choices

| Item | Disposition |
|---|---|
| Stale protected inventory | Refresh unsigned maintenance checksums; preserve existing scientific signatures |
| Production migration | Authorized after fresh quiescence, backup, rehearsal and preservation gates |
| Independent release UI/signer/installer | A23 explicitly superseded; no simulated replacement |
| Host permissions | Remain binding; no sudo or permission circumvention |
| Largest-dataset read-time memory overhead | Prior 219,155,413-byte unsealed measurement retained; gateway unchanged |
| Independent conformance | Existing requirement retained; JUDGE_UNAVAILABLE cannot be self-approved |
| Threat model | Accidental errors by a trusted owner-delegated agent; no hostile-host-agent isolation claim |
| User workflow | ChatGPT directly implements and operates; zero principal CLI burden; no external coding agents |

Reuse Warden/ML4T/SQLite, immutable evidence, existing leases, fixed verification,
receipts before retry, bounded context and scoped findings. Add no new service or key authority.

## 7. Completion report

For every delivered slice report IMPLEMENTED, VERIFIED and DEPLOYED separately,
with exact commit/content identity, tests and omission counts, observed effects,
remaining blockers and authority references. Scientific qualification is its own
claim supported only by governed evidence.

The final program report must account for every A01–A25 scenario and resource
measure, name the actual installed capabilities, and state which production
decisions remain pending. Stop optional work once the objective and required
verification are complete; retain a durable resume packet for a genuine blocker.
