# Agent Control milestone issue projection

This is the materialization plan for [AGENT-CONTROL-PLAN.md](AGENT-CONTROL-PLAN.md), not another specification. The architecture, control contracts, acceptance matrix A01–A25 and resource targets in that plan remain binding. AC identifiers map one-to-one to delivery slices. Integrate #119 on the #109 baseline before starting AC-01. Use one direct executor, existing Warden/ML4T/SQLite seams, hermetic fixtures, and durable evidence; no external agent delegation is required. Do not sign, migrate production, install privileged components, read sealed holdouts, place trades or change scientific policy without the exact separate authority.

### Milestone: Agent Control and Trusted Research Appliance (AC-01-10)

The [2026-11-27 amendment](adr/2026-11-27-trusted-research-appliance.md) authorizes routine
maintenance and eligible production transition after verification. It narrowly revises
A13/A14/A24 and supersedes A23 for releases. Original GitHub bodies and acceptance history
remain preserved; all other behavioral and scientific criteria remain binding.

**Issue 1 — [AC-01] Freeze control vocabulary, effects and behavioral fixtures**

Depends on: #119 integration (with #109 as the existing foundation).

- Inventory current CLI, runner, read model, policy, admission and receipt semantics. Freeze strict versioned action/result schemas and an effect catalog mapping current behavior to the target interface. State authority, costs, charge, refusal, cancellation and retry semantics per operation.
- Record current read-only baselines, unknowns, legacy compatibility and protected scope. Reuse integrated RG fixtures and define the acceptance witnesses before adding commands.
- Acceptance: one vocabulary and action catalog; strict schema fixtures and current-to-target mapping; missing/unavailable facts explicit; each action has owner and receipt semantics. Preserve current science and authority. Establish the baseline for A01–A04, A20 and A22.

**Issue 2 — [AC-02] Expose a truthful bounded situation view and resume packet**

Depends on: AC-01.

- Deepen existing read-only projections, status/preflight and checkpoint seams. Distinguish worktree, executable runtime, canonical ledger, evidence, scientific standing and authority. Expose relevant blockers and actions, source revisions, explicit unknowns, pagination/deltas.
- Remove mutable registry construction from observational handoff. No scorer, migration, full dataset read or sealed access in observation; distinguish operational logging from scientific writes.
- Acceptance: A01–A04/A20. Cold start and resumption use one bounded default situation response (target <=12 KiB), not unconditional history dump. Test dirty checkout vs runtime drift, absent/legacy ledger, missing facts and side-effect freedom; measure baseline resource use.

**Issue 3 — [AC-03] Prepare, submit and recover typed governed actions**

Depends on: AC-02.

- Implement one public control path through existing domain admission, registry transactions and CLI/MCP adapters. Freeze exact subjects and relevant revision preconditions; reserve before scoring; map stable request identities to existing receipts through the canonical migration.
- Recover lost responses without replay; deny changed payloads and stale prerequisites. Preserve candidate, lineage, debt, benchmark, data/fence, novelty and exposure rules. Do not create a competing scientific state machine.
- Acceptance: A05–A12/A22. Concurrent last-slot race, duplicate request, changed digest, lost reply and interrupted admission tests; rejected screen cannot advance; zero duplicate computation/admission on receipt lookup. Preserve existing CLI/runner behavior and historical evidence.

**Issue 4 — [AC-04] Build immutable release candidates without blocking development**

Depends on: AC-01.

- Separate build identity and mechanical unsigned inventory from verification evidence and release authorization. Bind exact source/patch, lock, protected inventory, policy, trusted check results and compatibility/rollback references. Build and verify in isolation.
- Demonstrate a candidate beside a separately pinned authorized runtime. Do not remove current integrity checks or change the protected manifest/keys.
- Acceptance: build identity is reproducible and exact; stale development inventory does not mutate runtime; altered candidate bytes fail verification; omissions and failures recorded. A14/A15 preparation; candidate-controlled tests cannot replace trusted required checks.

**Issue 5 — [AC-05] Separate delegated maintenance from scientific authority**

Depends on: AC-01, AC-04. Amended by the accepted trusted research appliance ADR.

- Retain exact request/subject identity and explicit effect scope. Local maintenance receipts
  never satisfy scientific intent, conformance, exposure, promotion or trading requirements.
- Preserve existing scientific signature readers; checksum refresh does not fabricate approval.
- Acceptance: amended A13/A24 and the A23 replacement. Original independent authentication is
  explicitly superseded, not passed. Complete maintenance without principal CLI work.

**Issue 6 — [AC-06] Install and activate verified immutable appliance releases**

Depends on: AC-03, AC-04, amended AC-05.

- Reuse the candidate builder, Git, locked environment, canonical leases and registry migration.
  Keep a separate pinned runtime, fixed verification baseline and append-only lifecycle journal.
- Verify actual executable identity, schema, source/environment integrity and safe halt/recovery.
  Recover interrupted effects by observation; never restore an older database over accrued history.
- Acceptance: amended A13/A14/A24, unchanged A15/A17, and the A23 replacement. The accepted
  owner maintenance scope replaces independent release UI/key/installer requirements.

**Issue 7 — [AC-07] Continue bounded objectives with recoverable jobs and shared resource limits**

Depends on: AC-03.

- Extend existing advance/lease/evaluation seams with durable job identity, fencing, bounded progress and cancellation, shared CPU/RAM/I/O limits and explicit resource receipts. Worker outputs remain scoped; no worker keys or canonical ledger writes.
- Keep objective completion and scientific stop conditions explicit. No repeat scoring on timeout/cancel or stale worker finalization; retries only for proven pre-admission technical failures.
- Acceptance: A16/A21 and A07 recovery parity. Late-worker/cancel/lease tests; bounded scheduling and contention observations; objective survives restart without duplicated research debt. Measure foreground latency and host cost; do not introduce a worker platform without need.

**Issue 8 — [AC-08] Accumulate scoped evidence-linked findings and reusable repairs**

Depends on: AC-02, AC-03.

- Reuse registry, branch index, lessons and immutable artifacts. Provide bounded deterministic finding views with applicability, support, contradictions, time/cutoff, limits and revalidation conditions. Turn recurring concrete failures into regressions or reusable procedures.
- Full mandatory negative history remains in admission even when display is compact. Preserve conservative RG-07 snapshot invalidation unless a completeness-preserving replacement is separately approved.
- Acceptance: A12/A19/A20. Seeded prior failure/repair discovered with provenance; contradictory evidence retained; stale finding rechecked; omitted display cannot hide debt or authorize new work. No second memory store required.

**Issue 9 — [AC-09] Verify integrated behavior, resource cost and migration rehearsal**

Depends on: AC-06, AC-07, AC-08.

- Run A01–A25 integrated witnesses, current #109/#119 regression and full hermetic suite; verify old/new CLI/runner/MCP parity and all relevant historical evidence. Use existing WAL-consistent backup and canonical additive migration on an isolated snapshot.
- Prepare exact compatibility/rollback evidence and reconcile local-only contract references and unknown legacy exposure inventory without opening sealed data. Benchmark authorized unsealed large-dataset same-byte verification and resource overhead against measured baselines.
- Acceptance: all required scenarios and measures accounted for, failures/skips/limits explicit; preserve existing history, concurrent work and production state; exact cutover packet with separately required decisions. No production migration or activation.

**Issue 10 — [AC-10] Perform the separately authorized production transition**

Depends on: AC-09, accepted owner maintenance scope and verified operational preconditions.

- The principal's architectural amendment authorizes the eligible lifecycle transition. Confirm exact source/environment/verification, canonical registry and compatible recovery; pause legacy writers, acquire both locks, rehearse a fresh snapshot, apply only the canonical additive migration, activate the exact runtime and observe identity/health. Reconcile interruption through receipts.
- Preserve all accrued research history and spent exposures. Historical attestation, frozen-batch authorization, sealed reads and trading remain separate decisions.
- Acceptance: observed active executable/ledger/policy/grant identities, health, preserved evidence and compatible recovery verified; report remaining blockers honestly. No implied scientific qualification or capital authority.
