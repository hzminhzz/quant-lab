# Agent control: implemented source and operational boundaries

This guide describes AC-01–09 source behavior. It does not attest a production
installation. The approved architecture, contracts and acceptance matrix remain
in AGENT-CONTROL-PLAN.md. Engineering tests use isolated synthetic fixtures.
The original scientific ledger and its existing Warden/ML4T owners remain authoritative.

## One typed application

The native CLI and reviewed stdio MCP tool `warden_control` call the same
`warden_ops.control.Control`. Requests require schema_version 1, reject extra
fields, and have a 128 KiB wire limit. The CLI prints one typed JSON result.
A typed request refusal exits 2; the existing CLI exits and run sentinels remain
unchanged. MCP also retains its pre-existing requirement that executed specs and
strategy modules be committed and unchanged under the allowlisted paths.

| Operation | Meaning and effects |
|---|---|
| observe | Read-only checkout, runtime, ledger, authority and continuation facts; default summary at most 12 KiB |
| inspect / capabilities | Request/result schemas, effect ownership, availability and permission requirements |
| inspect / findings | Derived ledger/index/lesson findings, source revisions, scope, conflicts and omissions |
| prepare | Freeze the exact plan and relevant metadata/code revisions; no reservation, strategy import or data scoring |
| act | Revalidate and call existing Warden screen/backtest admission; preserve the original request-to-receipt link |
| receipt | Read original recorded disposition; artifact eligibility remains unknown unless the existing owner rechecks it |
| reconcile | Explicit interrupted-review disposition under the existing writer lease; no refund or repeat scoring |
| enqueue | Persist a bounded objective and exact action plan; science is admitted only on dispatch |
| job-status | Read durable progress, resource reservations, fence, costs and stop disposition |
| cancel-job | Cancel an undispatched job or request a running owner to stop; free resources only after actual exit |

`ok` describes completion of the requested interface operation. A completed
screen can have `recorded_verdict=REJECT`. It is not eligible to advance because
transport succeeded. `current_eligibility=UNKNOWN` is intentional for a stored
passing verdict: subsequent approval, artifact and domain checks still apply.

Agent/operator examples:

```bash
uv run --frozen warden control --json
uv run --frozen warden control --request '{"schema_version":1,"operation":"inspect","view":"capabilities"}' --json
uv run --frozen warden control --request '{"schema_version":1,"operation":"inspect","view":"findings","hypothesis_id":"HYP-EXAMPLE","limit":5}' --json
```

After prepare, persist and submit that exact returned plan with a durable request
ID. Do not reconstruct it after losing a reply. An identical submission returns
the existing receipt; a changed payload under the same ID is refused. A missing
or old ledger is observed explicitly. New control mutations require an already
migrated schema 10 ledger and never authorize that migration implicitly.

The default runtime observer and principal authority are unavailable. A development
checkout, its dirty identity and the canonical ledger are not an active runtime
attestation. Handoff publishes operational context without migrating the ledger,
hashing trial artifacts or presenting an unevaluated eligibility result as a pass.

## Bounded objectives and recovery

`python -m warden_ops.job_runner --root <checkout> --registry <ledger> --job <id>`
supervises a previously enqueued exact action. It is an entry point for the trusted
Warden owner. It is not a replacement scientific runner and it is not a claim
that arbitrary strategy imports in that owner are sandboxed.

The canonical schema adds append-only job identity/events and one immutable host
resource policy. The default reservation ceiling is 4 CPUs, 8 GiB RAM and 16 GiB
logical I/O. Jobs reserve distinct available CPUs. The process supervisor applies
CPU affinity, per-process address-space/CPU/file-size limits, wall time, sampled
tree RSS/I/O limits, and cancellation checks. Thread-pool environment limits are
explicit, and CPU affinity is applied before the child command can fork.
Cancellation, expiry and missing heartbeats retain resource reservations until
the trusted supervisor verifies the owned process group has stopped. Parent exit
alone is insufficient; surviving children are terminated, and unconfirmed exit
keeps capacity held. No lease expiry grants a new scientific attempt.

Resource receipts state what was measured. Per-process wait4 cost is combined with
descendant samples; process-tree CPU and logical I/O are lower bounds. Sampled RSS
sums count shared pages per process. This is not an aggregate hostile-process
cgroup quota. The existing synchronous act path retains existing Warden limits;
use the queued supervisor when a declared resource envelope is required.

`sandbox_worker` is a separate stateless Linux compartment for explicitly staged
portable programs. Bubblewrap mounts only system runtime files, read-only staged
inputs and one writable output directory, with private PIDs/network and a cleared
environment. It mounts no canonical ledger, host home or keys. Tests demonstrate
those restrictions with disposable inputs and fixture secrets. Protected installation
must still establish the trusted owner/verifier boundary and decide whether stronger
host enforcement is required. No background service is installed by this source change.

## Findings and scientific safeguards

The finding view derives from the existing ledger, branch-result index and lessons.
It does not create a memory database. It preserves conflicting outcomes, original
provenance, unknown time/cutoff, scope limitations, related evidence and revalidation
conditions. Narrative lessons remain unverified claims unless their exact evidence
is reviewed. No finding changes policy or grants research authority.

Cursors bind to the current source revision. A changed source requires a fresh page.
Display truncation has explicit counts and continuations. Every scientific admission
still performs the full conservative RG-07 checks, including off-branch negative
history. The view does not replace alias, lineage, selection debt, PIT, exact-byte,
benchmark, risk, cost or one-shot exposure controls.

## Release and principal authority

`warden_ops.release.build_candidate` publishes a write-once unsigned source inventory
with code, dependency lock, mandatory lesson metadata, schema compatibility and an
optional rollback reference. It excludes scientific data/ledger state and known key
paths. Verification checks exact contents, modes, symlinks and identity. Filesystem
read-only modes in the coding account are not an independent security boundary.

`DecisionGate` verifies exact effect subjects, packet hashes, authenticated proofs,
expiry, issuer generation and revocation. Unknown revocation state fails closed.
Copied chat text and self-supplied principal names provide no authority.
Release, installation, migration, activation, policy/history decisions, batch/read
approval and capital allocation remain separately scoped effects.

`ReleaseAuthority` is a protocol/library for an independently installed owner.
Its verifier and required-check baseline are supplied by that owner, never by
candidate code. Standing grants are restricted to non-executable Markdown
documentation; machine-read research outcomes and agent instructions are excluded.
Every protected class needs an exact decision. Activation rechecks identity/schema,
records intent before an effect and observes the result. Failed recovery requests
a halt; an unverified halt remains explicitly uncertain. Incompatible old binaries
must not cause a database or history rewind.

The production authenticated principal UI, independently controlled verifier/key/
revocation state and protected installer are not installed or verified by these
fixtures. A23 remains blocked. A24 produces an exact decision packet and named
missing capabilities, with no required principal CLI command and no invented proof.

## Verification and rehearsal tools

`scripts/rehearse_state_machine_migration.py` now uses the existing read-only SQLite
online backup API. It compares every pre-existing user table, column and row stream,
checks old indexes/triggers, verifies idempotence and integrity, and records every
new table and additive column. It only migrates a new independent scratch path.
An evidence snapshot is never permission to rewind newly accrued live history.

`python -m scripts.measure_agent_control --measure observe` records cold status/
control latency and foreground cost under two bounded synthetic CPU jobs.
`--measure bytes --path <explicit-unsealed-exploratory-parquet>` compares the existing
buffered verifier/parser with temporary streaming staging under a 512 MiB input
ceiling and bounded child memory. It performs no strategy scoring and refuses
sealed paths/symlinks. Staging is a measured experiment; the production gateway
and its exact-byte/fence behavior are unchanged.

Measured evidence is in `docs/evidence/ac-*-20261127.json`. The
[acceptance report](AGENT-CONTROL-ACCEPTANCE-20261127.md) maps every A01–A25 criterion
to its witness or external blocker. The [consolidated AC-10 decision](AGENT-CONTROL-CUTOVER-20261127.md)
binds the exact frozen source candidate, verification omissions and migration evidence.
Source integration, authority integration, production activation and scientific
qualification remain separate completion states.
