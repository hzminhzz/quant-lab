# Agent Control engineering acceptance — 2026-11-27

## Outcome and exact identities

**VERIFIED:** eligible AC-01–09 engineering is implemented and verified. **BLOCKED:**
AC-05/06 installed authority integration and A23. **NOT DEPLOYED:** AC-10 has no
production authorization. There is no new scientific qualification or trading authority.

This report is assembled before source integration. [PR #141](https://github.com/hzminhzz/quant-lab/pull/141)
is the authoritative integration receipt. The candidate was frozen before these
acceptance documents were added; later documentation does not change its tested code.

| Identity | Value |
|---|---|
| Reviewed base | `b6cbb05f3e55f74ff2aba2c6c8f0ee4b8bbc3b71` |
| Frozen tested source | `fd19e911764bf86ae733c9111f05861f3c67c27c` |
| Source tree | `7a03dd13b7f53c403de07b95d0d039db3fc04590` |
| Unsigned release | `1c78c5e2ab0069ee40660d289eed9ef691c2e2601660513eb3cea08027a8264c` |
| Candidate manifest SHA-256 | `b02fa83f1d8a02917d7deabd3cc7fff24f77f4acd8d8b40aa04e4362302452b5` |
| Dependency lock SHA-256 | `4ec2ad5bbb6dd00ebebc1c917e7c8af52cd9b7847c02135a4be19c36a7487ddd` |
| Candidate inventory | 729 files; clean included source; empty patch digest; required lesson metadata present |
| Candidate ledger compatibility | Schema 10 only; no known authorized rollback release |
| Consolidated packet | `97e351d46fb411fef8c25d1db30c2268aa983bc81d030d5e7ad27f84c884e4e8`; BLOCKED_CAPABILITY |

A second build produced the same release identity. Exact contents, executable modes,
symlinks and manifest identity were reverified. The protected live manifest and signing
policy were not refreshed or replaced. The candidate is a mechanical inventory, not an
attestation by an independent verifier.

## Delivery status

| Slice / issue | Engineering result | Remaining condition |
|---|---|---|
| AC-01 / #131 | Strict versioned contracts, one effect catalog, explicit owners/cost/retry semantics | None for engineering scope |
| AC-02 / #132 | Bounded read-only situation, unknowns, pagination/deltas and observational handoff | Real runtime observer remains unavailable |
| AC-03 / #133 | CLI/MCP share prepare, act, receipt and reconciliation through existing Warden admission | No new scientific authorization |
| AC-04 / #134 | Reproducible unsigned immutable source candidate and tamper rejection | Installed authority must independently attest it |
| AC-05 / #135 | Exact packet/proof protocol, expiry/scope/revocation refusals and concrete missing-capability packet | Independent principal-facing authenticated channel absent; A23 blocked |
| AC-06 / #136 | Separately owned admission/activation protocol, trusted-baseline seam, narrow grants, halt/recovery fixtures | Independent key/verifier/revocation/executor ownership and bootstrap absent |
| AC-07 / #137 | Durable jobs, fences, bounded scheduling, real cancellation recovery and resource receipts | Trusted owner deployment and stronger hostile-process enforcement are installation decisions |
| AC-08 / #138 | Derived scoped findings, contradictions, provenance and stale-view refusal | Full conservative novelty checks remain binding |
| AC-09 / #139 | Full hermetic suite, compatibility matrix, complete scratch migration, measurements and cutover packet | Overall completion retains the AC-05/06/A23 external dependency |
| AC-10 / #140 | Consolidated decision prepared | No live effects authorized or performed |

AC-05/06 fixtures are not substituted for their installed acceptance. AC-09 accounts
for that failure to integrate instead of marking the whole milestone complete.

## Verification

Command on the frozen source:

```bash
.venv/bin/python -m pytest -q -n 4 --dist worksteal --tb=short --junitxml=.workspace/evidence/ac-full-reviewed.xml
```

**1,405 passed; 42 skipped; 1 expected failure; 0 failed; 0 errors.**
JUnit total: 1,448, elapsed 132.399 seconds. JUnit SHA-256:
`2f172b1e863028237d47aa598659cb2b8ad0b3a46f57bbe653b054717c7f1a2d`.

[Machine-readable verification](evidence/ac-verification-20261127.json) lists every
omitted test and its reason. The 42 skips concern explicitly gated live market
panels, live-ledger checks, missing historical fixtures and legacy throughput
benchmarks. They were not enabled against production. The inherited expected failure
concerns inactive positions being counted as holding bars in a reviewed research
candidate; changing that scientific behavior remains outside this engineering scope.

The final focused authority/job/recovery/supervisor run passed 23 tests with 15
existing SQLite datetime-adapter deprecation warnings. The detached full run retained
JUnit dispositions, not the terminal warning aggregate; its exact warning count is
unknown. Ruff passed for every changed Python path. Repository AST boundary findings
were empty. Diff whitespace checks passed. No separate typecheck result is claimed;
the typed interfaces are exercised by strict Pydantic and public-interface tests.

Direct review covered Standards and Spec against the approved architecture, contracts,
plan and issue acceptance. No external coding agent was used. Independent scientific
conformance remains **JUDGE_UNAVAILABLE**; direct engineering review is not that evidence.

### Regressions found and corrected during review

- The typed MCP path initially omitted the established reviewed-Git spec/strategy gate.
  The regression now proves refusal before scientific admission.
- A documentation grant initially covered every path below `docs/`. It now covers
  only non-executable Markdown and excludes machine-read research outcomes and agent
  instructions. JSON evidence, executable code and verifier changes require exact decisions.
- A killed owner could retain an admission with no reply. The actual OS-kill witness
  proves the charge persists, the receipt is uncertain, and retry does not recompute.
- A normally exiting parent could leave a child alive while capacity was released.
  The supervisor now terminates and observes its owned process group. Unconfirmed exit
  keeps reservations held. CPU affinity is applied before descendant creation.
- Migration rehearsal previously copied a database file and compared only selected tables.
  It now uses the existing WAL-consistent backup seam and compares every old table,
  column, row stream, index and trigger.

Each behavioral defect was reproduced before its correction. The final suite covers
the corrected code. The full run before final review also passed 1,395 tests; that
earlier result is not used to claim verification of the later fixes.

## A01–A25 acceptance matrix

“Verified” below describes the stated fixture or engineering observation. It does not
mean the corresponding production authority has been installed.

| ID | Disposition and evidence |
|---|---|
| A01 | VERIFIED: dirty checkout and separately injected pinned runtime are distinguished by `test_ac09_recovery`; untracked byte identity is explicitly not attested by the default view |
| A02 | VERIFIED: missing/legacy ledgers and unavailable runtime remain unknown without database creation or DDL; `test_ac02_situation` and `test_ac07_jobs` |
| A03 | VERIFIED: observe/prepare/handoff use read-only projections; preparation does not import strategy code or read market data; `test_ac02_situation`, `test_ac03_actions`, checkpoint regressions |
| A04 | VERIFIED: strict schema/version/field/path refusal before effects; `test_ac01_control_contracts` and MCP schema tests |
| A05 | VERIFIED: last-slot atomic admission and lineage debt in existing `test_rg03_candidate_admission::test_concurrent_candidate_admission_is_atomic`; duplicate control contention also covered |
| A06 | VERIFIED: identical request returns original evidence; changed fingerprint refuses; `test_ac03_actions` |
| A07 | VERIFIED: lost replies and actual OS kill after reservation preserve charge and uncertain identity without replay; `test_ac03_actions`, `test_ac09_recovery` |
| A08 | VERIFIED: rejected screen cannot advance through direct runner, CLI, legacy MCP or typed MCP; `test_ac09_interfaces` |
| A09 | VERIFIED: changed code/spec/review/ledger prerequisites refuse before computation; exact metadata revisions plus existing RG intent/novelty tests |
| A10 | VERIFIED on synthetic fixtures: unchanged gateway exact-byte and temporal-fence gates; `test_holdout_fence`, `test_data_admission`, existing gateway regressions. No production sealed bytes used |
| A11 | VERIFIED: incremental benchmark, clock/cost/risk and paired-evidence rules remain in existing owners; `test_rg06_incremental`, `test_rg02_benchmark_comparison` |
| A12 | VERIFIED: rename/cadence/lineage/selection debt and off-branch negative evidence remain binding; RG-03/RG-07 plus findings omissions tests |
| A13 | VERIFIED protocol fixtures: forged/copied/wrong-subject, expired and revoked decisions refuse; unknown revocation fails closed; `test_ac05_decisions` |
| A14 | VERIFIED protocol fixtures: external required-check baseline remains binding; candidate verifier and machine-read “documentation” changes cannot use a docs grant; `test_ac06_authority` |
| A15 | VERIFIED build/observer fixtures: stale development integrity does not make a candidate authorized or change pinned runtime identity; `test_ac04_release_build`, `test_ac09_recovery`. Real active runtime is UNKNOWN |
| A16 | VERIFIED: wrong fences, duplicate finalization, cancellation and expiry retain appropriate uncertainty; `test_ac07_jobs`; owned-child and unconfirmed-exit regressions in `test_ac09_supervisor` |
| A17 | VERIFIED isolated executor: failed activation with incompatible accrued schema halts; observer/halt failures remain uncertain; no ledger rewind; `test_ac06_authority` |
| A18 | VERIFIED synthetic RG-08 regressions: unknown legacy scope, changed pin, claim races and faults before/after read-start retain one-shot exposure. Detailed real legacy scope remains protected/unresolved |
| A19 | VERIFIED: prior failure/repair and contradictory outcomes retain provenance and applicability limits; `test_ac08_findings` |
| A20 | VERIFIED: bounded summary and findings pages, explicit omissions, stale cursor refusal and no truncated novelty gate; AC-02/08 tests; actual default response 2,249 bytes |
| A21 | VERIFIED for the implemented Linux supervisor: shared CPU/RAM/I/O reservations, distinct inherited CPUs, quota refusal, actual wall termination, reported-overrun handling and conservative exit handling; AC-07/09 tests and measured contention. Sampled CPU/I/O are lower bounds, not hostile-process cgroup quotas |
| A22 | VERIFIED: old/new CLI, runner and MCP rejection semantics and canonical debt agree; AC-09 interfaces and existing #109/#119 regression suite; legacy sentinels/exits preserved |
| A23 | BLOCKED: no installed independently authenticated principal UI, independently owned verifier/revocation state or protected installer. Zero-human-CLI integration has not been demonstrated |
| A24 | VERIFIED: actual capability absence produces the exact content-addressed packet and missing-capability list, no approval proof and no live effect |
| A25 | VERIFIED engineering preservation: final suite, all-table WAL migration rehearsal, original source/evidence/ledger comparison and untouched protected manifest. No sealed-file access or hash was performed |

## Migration and historical evidence

[Migration evidence](evidence/ac-migration-20261127.json) records a schema **2 → 10**
rehearsal on an independent scratch backup. Backup SHA-256:
`2f2c868bd4174cc70c35231984ccd00de03773936781b5142de46e7b617ef25e`.

All 17 pre-existing user tables, all old columns and row streams, indexes and triggers
were preserved. Integrity and repeated migration checks passed. The original database
and nonempty WAL were unchanged. The migration adds 20 tables and the
`contracts.policy` / `contract_governance.group_source` columns.

Preserved counts include 153 hypotheses, 75 trials, 244 selection attempts,
119 screen receipts, 75 attempt receipts, 6 qualifications, 6 holdout claims and
20 contract records. The migrated snapshot records 6 legacy exposures, zero scope
attestations and zero frozen batches. This is an inventory, not a historical attestation.

[Contract reconciliation](evidence/ac-contracts-20261127.json) resolves 18 of 20
recorded contract identities exactly. The unresolved local-only
`FX_MAJORS_1D_V3` and `INDICES_1D_V2` retain their original files and references;
neither has a trial. All nine contract IDs referenced by the 75 trials match.
No local file was imported, deleted, relabeled or promoted to hide those exceptions.

Automatic approval review rejected printing detailed historical holdout-exposure
`record_json` because disclosure was unnecessary under the sealed-holdout restriction.
That action was not retried. Aggregate counts and preservation digests suffice for
this rehearsal; detailed scope reconciliation remains a protected decision.

[Live preservation evidence](evidence/ac-preservation-20261127.json) matches the
private baseline after final verification: 1,368 source files, 76 non-database
evidence files, worktree status, live HEAD, all 17 table digests, database/WAL and
schema 2 are unchanged. The data tree was not scanned. No sealed hash is asserted
as proof of non-access.

## Measured operational costs

The [final-source observation run](evidence/ac-observation-reviewed-20261127.json)
used five fresh processes per condition. Filesystem caches were not reset.

| Condition | Median elapsed | Maximum | Peak RSS | Response bytes |
|---|---:|---:|---:|---:|
| Existing status | 0.617 s | 0.789 s | 69.39 MiB | 230 |
| Typed situation, idle | 0.671 s | 0.831 s | 70.88 MiB | 2,249 |
| Typed situation, two CPU jobs | 0.592 s | 0.669 s | 75.13 MiB | 2,249 |

Both bounded eight-second jobs finished, with measured CPU lower bounds of 7.98 and
8.00 seconds. An oversubscribed job was BUSY. This small sequential sample supports
bounded behavior, not a speedup claim. New status exposes more information than the
old command. No evaluator ran in these measurements.

The [same-byte experiment](evidence/ac-bytes-20261127.json) used the largest
inventoried authorized unsealed exploratory Parquet: 219,155,413 bytes. All four
methods hashed to `8f9777341a28ea0a6a9efab0b66fe912c4475964bc6797ced691bce806cf1573`;
both parsing paths returned 11,873,470 rows.

| Method | Elapsed | CPU | Peak RSS | Additional staging |
|---|---:|---:|---:|---:|
| Buffer and verify | 1.188 s | 1.085 s | 277.42 MiB | 0 |
| Stream to private staging and verify | 0.906 s | 0.848 s | 70.43 MiB | 219,155,413 bytes |
| Buffer, verify and parse | 1.721 s | 1.713 s | 1,150.54 MiB | 0 |
| Stage, verify and parse | 1.740 s | 1.722 s | 936.39 MiB | 219,155,413 bytes |

**INFERRED:** staging can reduce peak verification memory at an extra I/O/storage
cost. **Disposition: EXPERIMENT.** Loader validation paths and warm-cache effects
limit timing comparisons; equal hashes/row counts do not prove full pipeline
equivalence. No model memory was measured. The production reader, PIT and same-byte
gates remain unchanged.

## Operational limits and next protected decision

The trusted existing Warden owner still imports reviewed strategies under its current
application trust model. The stateless Bubblewrap compartment demonstrates scoped
inputs/output, cleared credentials and no canonical/home/key mounts; it does not turn
same-account administration into an independent release authority. CPU/I/O process-tree
samples are lower bounds; RSS sums can count shared pages more than once. The default
synchronous act retains existing Warden limits; a declared resource envelope uses
the queued supervisor.

The only remaining program blockers are the independent authority/bootstrap binding,
the exact separately authorized production effects, protected historical/local-contract
decisions where needed, and existing independent scientific conformance. See the
[consolidated AC-10 decision](AGENT-CONTROL-CUTOVER-20261127.md). No principal approval
is inferred from implementation, tests, source merge, an unsigned inventory or this report.
