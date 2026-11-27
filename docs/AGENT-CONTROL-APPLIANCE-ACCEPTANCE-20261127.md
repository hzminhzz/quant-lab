# AC program: trusted research appliance acceptance and cutover

Date: 2026-11-27
Scope: AC-01–09 engineering and the owner-authorized AC-10 operational transition.
Evidence: [machine-readable lifecycle and acceptance record](evidence/ac-appliance-20261127.json).

## Outcome

| Status | Observed result |
|---|---|
| IMPLEMENTED | One trusted maintenance lifecycle over the existing immutable release builder, Warden owners, Git and SQLite |
| VERIFIED | Final exact candidate: 1,407 passed, 42 skipped, 1 expected failure; all five mandatory checks passed |
| MERGED | [PR142](https://github.com/hzminhzz/quant-lab/pull/142), then the live-discovered timer correction in [PR143](https://github.com/hzminhzz/quant-lab/pull/143) |
| DEPLOYED | Actual pinned on-demand runtime healthy; canonical additive migration 2 → 10 complete; live halt/reactivation verified |
| SCIENTIFICALLY QUALIFIED | No new qualification or scientific approval. The six historical qualification records remain unchanged |

The active runtime is separate from the mutable development checkout and canonical historical
source tree. Source integration alone never changes the active pointer. The old canonical
checkout remains at `b1322ff996b0e31dfd6ac8576f59f1dd063e557b`, including its concurrent dirty
and untracked work.

## Architectural decision

**ADOPT: trusted research appliance with effect-based governance.**

The original release channel was uninstalled and required independence from the same owner who
already controlled the repository, ledger and host. For the explicitly accepted accidental-error
threat model, another local signer or principal-name field would add mechanics without providing
that independence. The owner delegated routine maintenance, deployment, additive migration and
recovery in the current instruction.

The [accepted ADR](adr/2026-11-27-trusted-research-appliance.md) replaces the independent software
release prerequisites with exact source/environment identities, fixed verification, durable
operational receipts, real process observation, fresh migration rehearsal, writer quiescence
and recovery that preserves the ledger. Scientific effects retain their existing owners.

A13/A14/A24 are narrowly amended. **Original A23 is explicitly SUPERSEDED. It did not pass.**
The replacement lifecycle was completed through the agent without principal CLI steps. The
[original ADR](adr/2026-11-27-warden-control-and-release-authority.md),
[original acceptance](AGENT-CONTROL-ACCEPTANCE-20261127.md) and
[blocked cutover packet](AGENT-CONTROL-CUTOVER-20261127.md) retain the pre-amendment history.

### Implementation and removed complexity

- `warden_ops.appliance` owns install, verify, writer pause, transition, observe, halt and recovery.
  One operational journal records these effects. Scientific state stays in the canonical ledger.
- Each release has a separate immutable interpreter binding. The stable launcher selects that
  interpreter before importing application code. Later health checks detect environment drift.
- Every verification attempt starts with a fresh complete checkout of the exact commit.
  Candidate source bytes match before and after. Canonical registry/data are never mounted there.
  Synthetic fixtures establish their own approval context; production noninteractive guards remain enabled.
- `warden_ops.ledger_maintenance` replaces duplicated snapshot/rehearsal logic with one owner.
  Legacy scripts remain thin compatibility adapters.
- Unused `DecisionGate` / `ReleaseAuthority` protocols and their superseded independent-release
  tests were removed. Existing scientific signature checks, canonical accounting, worker bounds,
  findings and public-interface compatibility were retained.
- No release signing service, privileged installer, daemon, external coding agent or second
  scientific ledger was introduced.

The prior interactive manifest metadata had no HMAC signature and already failed its checksum
binding. Its bytes were archived unchanged in
[pre-appliance manifest metadata](evidence/pre-appliance-manifest-approval-20261127.json),
SHA-256 `fc3d78ad37a4459b5043697c4db3e36bb743d8d3a061fbd305ac9abc52f969c2`.
It does not approve the new release. Maintenance refresh writes unsigned checksums and refuses
to overwrite an existing signature/approval record.

## Exact accepted and deployed identities

| Identity | Value |
|---|---|
| Runtime source commit | `4a46bd2fc3a45bacda6788d634bcd774ececfad0` |
| Runtime source tree | `5efa4874109a9253c2e698b6f8ba0acc773c2757` |
| Release ID | `117b55159078966d00278d67ab212e57c70edee3897bd874300aa60d74c42b76` |
| Verification ID | `e396a3b82f1970963d25f5baaaeb82bc839af978cbf15ae4d1e21c52d346abd8` |
| Unsigned protected inventory SHA-256 | `252f752f00ce1c88756d75363667e180272b2df50bacc14c47fb1b7f092d2144` |
| Dependency lock SHA-256 | `4ec2ad5bbb6dd00ebebc1c917e7c8af52cd9b7847c02135a4be19c36a7487ddd` |
| Environment identity | `af9e482bc80d2397879a9606a6ab94ffd7711dadbeb36adec7a0736896b44ab4` |
| PR142 merge | `c364b66a6a8cced725b88dae52a6f645ae61a666` |
| PR143 merge | `f9e91c0cf3c9eaf25056e3ce7ef989be36bf9194` |

Operational home: `/home/quant/dev/quant-lab-appliance`.
Canonical ledger: `/home/quant/dev/quant-lab/registry/registry.db`.
Runtime and Python are under `HOME/runtimes/RELEASE_ID` and `HOME/envs/RELEASE_ID`.
The runtime is an **on-demand CLI**, not a continuously running service. A health result is a
fresh process observation with its PID, time, executable, module root, source identity and schema.

Coherent implementation commits:

| Commit | Slice |
|---|---|
| `2d00ff878bf8395ada9f8b511eabe9560ed44691` | Accepted maintenance model, shared preservation owner and local lifecycle |
| `42288b5cee14321deab5e721f4fa01c863584307` | Immutable verification history and unsigned inventory refresh |
| `81b4bc231f0dc82a1c511c4b62188e7a146dec64` | Per-release environments, actual drift detection and fresh verification attempts |
| `b057466b6cf22baa59242b281e0ee42191e28e62` | Explicit incompatible-recovery witness preserving halt and accrued history |
| `4a46bd2fc3a45bacda6788d634bcd774ececfad0` | Preserve linked timer definitions while disabling the writer schedule |

This report is a later evidence publication. Its Git commit does not replace the deployed
runtime source identity above.

## Verification and retained failures

The final source was installed with `uv sync --frozen --no-editable`, including the project
wheel and 73 packages. The frozen check reported no changes. The fixed verifier ran the entire
committed `tests` directory with four workers and cleared inherited test-selection options.

**Final result: 1,407 passed, 42 skipped, 1 expected failure, 2,261 warnings in 235.99 seconds.**
Mandatory checks: full suite, protected inventory, AST boundaries, exact source integrity and
environment integrity. All passed. Changed-path Ruff and Git diff checks passed. GitGuardian
passed on both source PRs. Standards and specification review was direct; no independent
scientific reviewer is claimed.

JUnit SHA-256: `7745f0dd086b7ef6cf740a8a475a58112b6d6a6e7bc5fc4b9ecb4f02326cb79b`.
Log SHA-256: `b20a93c7426cc595ac5e3ba7c46932ef9aa36a1e06856e6db2a484413cc528b6`.
The complete command, immutable artifact directory and receipt are in the evidence JSON.

The 42 skips are unmounted historical/market fixtures and explicit opt-in live checks. The
expected failure remains
`tests.test_liquidation_episode_fixture::test_sparse_union_must_preserve_inactive_position`:
the reviewed candidate counts inactive union bars as holding bars; changing its scientific
behavior requires separate approval. The full warning log is retained.

Failures were preserved and resolved, rather than reclassified as passes:

1. The first verifier setup forced runtime noninteractive mode into synthetic approval fixtures
   and omitted the installed package needed by subprocess tests. That attempt had 101 failures.
   Fresh isolated test contexts and a noneditable project installation corrected the setup.
2. One subsequent run took 5.65 seconds against the unchanged Stage 1 five-second assertion.
   The strict isolated test and complete reruns passed. Contention is an inference, not proven
   causation. The failed receipt remains in the journal.
3. The first real timer pause removed its external definition link. The lifecycle refused before
   migration. A matching RED/GREEN regression drove the correction in PR143. The real corrected
   pause restored the same byte-checked definition without enabling or starting it.

## Canonical migration, writer quiescence and recovery

Before live effects, the final exact source was merged and verified. The configured timer was
disabled and stopped; its linked definition was retained. Both timer and service were observed
loaded/inactive, with service PID zero. No other service was changed.

The transition acquired the canonical mutation lease and legacy writer flock, took a fresh
read-only WAL-consistent snapshot, rehearsed the canonical migration on scratch, checked
idempotence, and persisted intent before changing the canonical schema.

Cutover request: `ac-appliance-cutover-20261127`.
Snapshot SHA-256: `2f2c868bd4174cc70c35231984ccd00de03773936781b5142de46e7b617ef25e`.
The snapshot and rehearsal paths are retained in the lifecycle record. No database backup was
restored.

The migration added 20 tables and two columns: `contracts.policy` and
`contract_governance.group_source`. All 17 old user tables, old column definitions and row
streams, indexes and triggers were preserved. SQLite integrity passed.

| Historical accounting | Before | After recovery and observation |
|---|---:|---:|
| Hypotheses | 153 | 153 |
| Trials | 75 | 75 |
| Selection attempts | 244 | 244 |
| Screen receipts | 119 | 119 |
| Attempt receipts | 75 | 75 |
| Qualification records | 6 | 6 |
| Holdout claims | 6 | 6 |
| Contracts | 20 | 20 |

Six existing claims were represented as legacy exposure records. Detailed scope remains
unknown. There are zero legacy-scope attestations, frozen batches, new candidate admissions,
research intents and independent conformance reviews.

The fresh preservation inventory covered 1,290 source files and 77 registry evidence files.
After cutover, halt/recovery and live measurements, their hashes and canonical dirty status were
unchanged. This allowlist differs from the earlier historical report's inventory scope; it is
compared against its own fresh pre-cutover baseline. The market-data tree was not traversed.

- Source inventory SHA-256: `d9b33033c5c7f8d7e6729083ee629da14840c969f553e947504ab0d83bb8b360`.
- Evidence inventory SHA-256: `9c1a069e3a6c237fb7140329c97c6d182e4195a6137cf1a5ebcf1da9426c8f7e`.
- Canonical database SHA-256 after cutover and again after recovery:
  `86ee4e794acba0ac49ddd0e17e6258b7b1266ce9ec42bdad37cc081e795016e0`.

The actual duplicate cutover request returned its original receipt without another transition.
A controlled halt produced an observed HALTED state; ordinary launcher admission returned
REFUSED with exit 2. A new request, `ac-appliance-recovery-20261127`, reactivated the same verified
schema-compatible runtime. The database hash and all historical evidence remained unchanged.

Interruption and concurrency witnesses remain isolated fixtures: actual SIGKILL after migration,
lost acknowledgement after durable success, both writer locks, duplicate/conflicting requests,
dependency/source drift, incompatible recovery and preservation of newly accrued evidence.
No production process was killed to manufacture a clean state.

## A01–A25 disposition

| Criterion | Disposition and evidence |
|---|---|
| A01 | VERIFIED: actual installed view separates dirty caller checkout, pinned process/runtime and canonical ledger; AC-02/09 fixtures cover differing source identities |
| A02 | VERIFIED: missing/legacy ledger and unavailable runtime stay explicit without implicit DDL or false zero; AC-02/07 suite |
| A03 | VERIFIED: read-only prepare/observe/handoff fixtures and live metadata-only observation; canonical accounting unchanged |
| A04 | VERIFIED: strict schema/field/version refusal before effects; AC-01 and MCP schema suite |
| A05 | VERIFIED: RG-03 concurrent last-slot admission and AC request contention preserve debt and one admission |
| A06 | VERIFIED: exact request replay returns original evidence; changed subject refuses; actual cutover replay also matched |
| A07 | VERIFIED: AC-03/09 lost replies and real fixture SIGKILL retain uncertain/admitted effects without refund or replay |
| A08 | VERIFIED: rejected-screen advancement refuses through runner, CLI, legacy MCP and typed MCP; AC-09 interface matrix |
| A09 | VERIFIED: changed plan/code/contract/review/metadata refuses before computation; existing RG intent/novelty regressions |
| A10 | VERIFIED on synthetic fixtures: exact bytes, temporal fences and excluded-symbol protections; no production sealed bytes used |
| A11 | VERIFIED: benchmark, clock, costs, risk and paired incremental evidence retain their existing owners and gates |
| A12 | VERIFIED: aliases, cadence, lineage, negative history and selection debt remain binding; RG-03/07 and findings suites |
| A13 | VERIFIED AS AMENDED: maintenance receipts confer no science; scientific signature/gate regressions and exact lifecycle conflicts pass |
| A14 | VERIFIED AS AMENDED: fixed full suite, actual-diff review, exact source/environment, immutable attempts and drift refusal |
| A15 | VERIFIED: development state is separate from observed pinned runtime; stale/drift fixtures and actual installed identity |
| A16 | VERIFIED: job fencing, duplicate/late finalization, cancellation and conservative exit handling; AC-07/09 supervisor suite |
| A17 | VERIFIED: incompatible recovery stays halted in fixtures; live halt/refusal/reactivation preserves schema and all accrued history |
| A18 | VERIFIED engineering gates: legacy unknown scope, pin drift, claim races and interruptions retain one-shot accounting; no historical attestation |
| A19 | VERIFIED: conflicting findings retain provenance and limits; prior repair/failure evidence is not automatic policy |
| A20 | VERIFIED: actual default response 8,784 bytes with explicit pagination/completeness; AC-02/08 bounded/delta fixtures pass |
| A21 | VERIFIED within stated Linux limits: resource reservations/fencing/termination regressions and actual bounded contention measurements |
| A22 | VERIFIED: old/new CLI, runner and MCP verdict/debt/exit behavior remains compatible; AC-09 and #109/#119 regressions |
| A23 | ORIGINAL SUPERSEDED, NOT PASSED. Replacement VERIFIED: owner-delegated install/verify/quiesce/migrate/activate/recover completed without principal CLI |
| A24 | VERIFIED AS AMENDED: real missing capabilities stay explicit; linked-unit absence refused before migration; no invented scientific proof |
| A25 | VERIFIED: complete isolated suite, pre-cutover original-state preservation, fresh scratch rehearsal and authorized additive live preservation; concurrent work and sealed-data restriction retained |

AC-01–04 and AC-07–09 engineering were reused and reverified. AC-05/06 are accepted under the
explicit effect-governance amendment. AC-10's verified lifecycle transition is complete.
Original issue text and original acceptance history remain preserved.

## Installed operating cost and limits

The installed launcher includes current source, receipt and dependency checks. Three fresh
processes per condition used one foreground CPU and a 1 GiB memory limit; filesystem caches
were not reset.

| Condition | Median | Maximum | Observed peak RSS | Response |
|---|---:|---:|---:|---:|
| Installed control, idle | 8.742 s | 9.496 s | 243.83 MiB | 8,784 bytes |
| Installed control, two bounded CPU workers | 8.710 s | 10.188 s | 244.00 MiB | 8,784 bytes |

Both 20-second synthetic workers finished. The over-capacity request was BUSY. All three
contended observations started while both workers were RUNNING; the last overlaps their
completion. All observation samples reported zero scientific writes, zero evaluator calls
and no sealed access; the independent final ledger comparison also remained unchanged.

This is a small operational measurement, not a speedup claim. The earlier 0.67-second
source-only view did not perform the installed lifecycle's runtime checks. Process-tree CPU
and I/O samples are lower bounds; summed RSS can count shared pages. No hostile-process cgroup
isolation or malicious-host-agent defense is claimed. Current observation latency is a known
operational cost; no cache or extra service was added.

Environment identity covers Python bytes and installed package names/versions, supported by
the locked noneditable installation. It is not a forensic hash of every installed package file.

## Routine entry point and remaining scientific decisions

Future agents can start with:

```bash
/home/quant/dev/quant-lab-appliance/warden control --json
/home/quant/dev/quant-lab-appliance/warden appliance observe
/home/quant/dev/quant-lab-appliance/warden appliance history
```

The [operations guide](AGENT-CONTROL-OPERATIONS.md) describes subsequent release preparation,
fixed verification, quiescence and transition. The installed interface reports
MAINTENANCE_DELEGATED and `scientific_authority=false`; affordances do not bypass domain
admission.

The legacy `quant-social-research.timer` stays disabled/inactive until its scientific admission
path is eligible. The existing MCP service stays disabled/inactive; this deployment introduces
no always-running service.

Remaining boundaries are scientific or explicitly excluded from this maintenance program:

- `JUDGE_UNAVAILABLE` remains where genuine independent conformance is absent.
- The six legacy exposures retain unresolved detailed scope. No attestation or sealed read
  was performed to resolve it.
- The prior contract reconciliation still leaves two local-only unused identities,
  `FX_MAJORS_1D_V3` and `INDICES_1D_V2`, preserved in the canonical checkout. All nine contract
  IDs used by the 75 historical trials resolved in the retained reconciliation.
- The listed live-data skips and scientific expected failure remain explicit.
- No new research approval, qualification, capital allocation or trading authorization follows
  from deployment or these receipts.
