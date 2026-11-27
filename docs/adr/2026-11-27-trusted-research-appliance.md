# ADR: Trusted research appliance with effect-based governance

Date: 2026-11-27
Status: ACCEPTED by the principal's AC architectural amendment instruction in this session.
Supersedes: the independent **software release** authority requirements in
[the original AC ADR](2026-11-27-warden-control-and-release-authority.md).
The original ADR, acceptance report and blocked cutover packet remain historical evidence.

## Decision

Quant-lab is a personal, single-owner research appliance. The owner delegates routine
software engineering, integrity inventory refresh, deployment, additive migration and
operational recovery to the coding agent. The design prevents accidental errors; it
makes no claim to resist a malicious agent with unrestricted host access. Local receipts
identify observed actions and bytes. They are not independent authentication or scientific approval.

The original independent release channel was never installed. The same coding identity
owns the repository and canonical ledger and has broad host privileges. Adding another
same-host signer or principal-name field would not establish the claimed independence.
Independent infrastructure has insufficient value for this accepted maintenance threat
model. Host permissions remain binding; the amendment grants no permission to bypass them.

Retain the existing immutable candidate builder and canonical Warden owners. Replace the
fixture-only release-authority/grant protocol with one local appliance lifecycle:

1. Prepare a clean, content-addressed release and a separate pinned execution worktree.
   Bind a separate locked dependency environment per release and canonical state explicitly. Preserve concurrent
   development work. Mount only unsealed exploratory data; sealed data is not installed.
2. Verify exact bytes, the protected checksum inventory, AST boundaries, targeted regressions
   and the full relevant hermetic suite. Record skips/xfails and actual check artifacts.
   Each attempt uses a fresh test worktree; synthetic fixtures establish their own approval
   contexts without production bindings. The required suite is an explicit baseline; a
   candidate cannot choose a smaller check set.
3. Pause the configured legacy writer schedule. Acquire both the canonical mutation lease
   and the legacy scheduler lock. Rehearse the additive migration on a fresh WAL-consistent
   snapshot and compare every existing table, column, row stream, index and trigger.
4. Persist intent before live effects. Migrate through ResearchRegistry's canonical additive
   migration and verify preservation against the snapshot. Publish the active runtime pointer
   atomically. Probe the actual executable, source inventory, dependency environment and schema.
   Later health probes revalidate the verified environment; preserve earlier environments
   so compatible recovery does not reuse changed dependencies.
5. Re-entry observes durable intent, schema and runtime identity; it never restores an old
   database. Use a schema-compatible runtime for recovery or halt the affected launcher.

One operational journal records the lifecycle. Scientific state remains exclusively in the
canonical ledger. No HTTP service, privileged installer, release signing key, second research
ledger, external coding agent or independent-authentication simulation is introduced.

## Effect boundary

| Effect | Authority after this amendment |
|---|---|
| Code maintenance, tests, unsigned checksum inventory, immutable release installation | Trusted owner-delegated maintenance |
| Canonical additive migration, activation, compatible runtime recovery or halt | Same maintenance scope, with verified lifecycle preconditions and durable receipts |
| Research intent, independent conformance, evaluator/contract semantic changes, promotion | Existing scientific governance; maintenance receipts confer no approval |
| Historical exposure attestation, frozen holdout batch, sealed read/sealing | Existing separate exact scientific authorization; excluded from this program |
| Capital allocation or orders | Separate trading authorization; unavailable |

The pre-amendment interactive manifest metadata was tracked and already failed its checksum
binding; no HMAC signature was present. Its bytes are preserved unchanged in
`docs/evidence/pre-appliance-manifest-approval-20261127.json` (SHA-256
`fc3d78ad37a4459b5043697c4db3e36bb743d8d3a061fbd305ac9abc52f969c2`).
It is historical metadata, not approval of the new inventory. The prior manifest remains in Git history.

The HMAC-verified scientific-policy input reader is unchanged. A maintenance checksum
refresh never writes a human signature or human-approval metadata. An existing signature
is preserved rather than silently invalidated. JUDGE_UNAVAILABLE remains an honest scientific
limitation and does not block unrelated software maintenance.

## Acceptance amendment

| Original criterion | Disposition and replacement |
|---|---|
| A13 | Narrowly revised: maintenance scope cannot authorize scientific effects; copied names/chat, a maintenance receipt or a changed subject cannot satisfy existing scientific gates. Release retries bind exact request/subject identity. Original independent-auth fixtures remain historical, not installed evidence. |
| A14 | Narrowly revised: mandatory verification baseline and exact candidate bytes remain binding. Source/test/verifier changes require review and complete relevant verification; no independent release signer is required. |
| A23 | Explicitly SUPERSEDED for software maintenance. The uninstalled independent principal UI/signing/executor requirement did not pass. Replacement: the authorized agent completes the verified lifecycle without principal CLI commands and reports actual runtime identity. |
| A24 | Narrowly revised: actual unavailable permissions/capabilities are reported without invented proof. Missing independent release infrastructure is no longer a blocker; missing scientific authority remains one. |
| A01–A12, A15–A22, A25 and scientific RG criteria | Preserved. Operational references to the release owner mean the trusted appliance; their behavioral requirements remain binding. |

AC-01–04 and AC-07–09 engineering are retained. AC-05 becomes explicit maintenance/scientific
effect separation and exact lifecycle requests. AC-06 becomes verified local release lifecycle.
AC-10 becomes the observed lifecycle transition after AC-09 acceptance. Original issue text
and acceptance history are preserved through explicit dated amendment sections, never rewritten
as if independent authentication had passed.

## Consequences and limits

Readonly source modes and hashes detect accidents; the owner can still modify them. Test
success proves the recorded engineering scope, not scientific truth, executable trading PnL
or independent review. The legacy scheduler stays paused until its scientific admission path
is eligible under current governance. The new runtime remains usable through the verified
launcher for observation and separately eligible actions. A source change alone never
changes the active runtime. Database backups are evidence and disaster-recovery material,
not permission to erase research accrued after them.
