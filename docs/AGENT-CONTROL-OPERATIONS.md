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

Without the installed launcher or an explicit runtime observer, runtime identity remains
unavailable. The launcher supplies its actual appliance observer and retains the caller's
checkout identity separately. Scientific authority remains unverified unless its existing
owner establishes it. Handoff publishes context without migrating the ledger, hashing
trial artifacts or presenting an unevaluated eligibility result as a pass.

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
those restrictions with disposable inputs and fixture secrets. This optional compartment
is retained for suitable workloads; independent host ownership is not a software maintenance
prerequisite under the appliance ADR. No background service is installed.

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

## Maintenance lifecycle and scientific authority

The [appliance ADR](adr/2026-11-27-trusted-research-appliance.md) explicitly supersedes
independent release authentication. The original A23 requirement did not pass. Scientific
approvals, independent conformance, sealed data and trading remain separate.

`warden_ops.release.build_candidate` retains the exact unsigned inventory, locked dependencies,
source provenance and schema range. `warden_ops.appliance` owns local installation, verification,
quiescence, additive migration, activation, actual runtime observation and halt/recovery.
The retired `DecisionGate` and `ReleaseAuthority` fixture protocols are preserved in Git history.
They are not a second supported release path.

### Routine agent workflow

1. Work in an isolated development checkout. Preserve the canonical ledger and concurrent work.
   Run behavior tests, review the actual diff, then use `warden generate-manifest --maintenance`.
   This refreshes unsigned checksums only; it refuses existing signature/approval metadata.
2. Commit and integrate reviewed source. Build an immutable candidate with the existing builder.
   Prepare a separate environment for this release outside the mutable checkout. Use
   `UV_PROJECT_ENVIRONMENT=HOME/envs/RELEASE_ID uv sync --frozen --no-editable --project CANDIDATE`,
   then the same command with `--check`. Install the project as well as its locked dependencies:
   subprocess regressions exercise the installed CLI without relying on `PYTHONPATH`.
   Preserve prior environments; each release's interpreter binding is immutable.
3. Use `warden appliance install --home HOME --source CHECKOUT --candidate CANDIDATE
   --canonical CANONICAL_ROOT --python LOCKED_PYTHON --writer-unit UNIT ...`.
   Installation creates a separate sparse Git runtime and explicit canonical state bindings.
   It does not change the active runtime or migrate the live database.
4. Use `warden appliance verify --home HOME --release RELEASE_ID`. The fixed full-suite,
   protected-inventory and AST checks run in an isolated checkout of the exact commit with
   all test assets in a fresh worktree for each attempt. Release bytes and environment identity
   are reverified before/after; canonical state/data are not mounted. Synthetic test fixtures
   establish their own approval contexts and may create their own disposable ledgers.
   Runtime noninteractive guards remain enabled. Receipts retain exact XML/log hashes,
   dependency identity, failures, skips and expected failures.
5. Use `warden appliance pause-writers --home HOME`. Timers are disabled and stopped so the
   pause survives restart; prior enablement and unit definitions are recorded. If systemd's
   disable operation removes an external unit link, the same byte-checked definition is linked
   back without enabling or starting it. An already running service must finish naturally. Inspect quiescence. No service is killed to manufacture a clean state.
6. Use `warden appliance transition --home HOME --release RELEASE_ID --request-id STABLE_ID`.
   Both writer locks must be free. The command takes a fresh WAL snapshot, rehearses and proves
   preservation/idempotence, persists intent, performs the canonical additive migration, switches
   the active pointer and probes the actual runtime. Same-ID retries return/reconcile its receipt;
   changed subjects refuse. The ledger is never restored from a backup.
7. The installed `HOME/warden` runs pinned code after source/schema/health checks. Its
   `control --request '{"schema_version":1,"operation":"observe"}'` reports the caller's
   checkout separately from actual runtime and canonical ledger identity. `HOME/warden appliance
   observe` probes runtime health; `... halt --reason REASON` stops new launcher admissions.

`warden appliance` also has a module entry point: `python -m warden_ops.appliance`. This permits
recovery using a known verified environment if the current runtime cannot start. To recover,
install/verify a schema-compatible forward repair and transition with a new stable request ID.
If compatibility/health cannot be established, retain the halt and current ledger. A completed
historical receipt does not reactivate a later deliberately halted runtime.

The runtime is an **on-demand CLI**, not a continuously running service. Health is a real pinned
process observation, including the release's verified dependency environment. The stable
launcher selects that release's interpreter before importing application code. Dependency drift
refuses admission; compatible recovery reuses the earlier release's preserved environment.
Halt prevents new launcher admissions; it does not erase or refund an
already admitted scientific operation. Existing canonical leases still guard such operations.
Legacy scientific scheduling remains paused until eligible under current research governance.

The lifecycle journal is operational evidence. It confers no research-intent approval, independent
conformance, historical attestation, batch/read grant, contract promotion or trading authority.
`read_signed_manifest_entry` and the existing scientific CLI gates are unchanged. Local hashes
and readonly modes make no malicious-host-agent isolation claim. Never use sudo to bypass a
remaining host boundary.

## Verification and rehearsal tools

`scripts/rehearse_state_machine_migration.py` and `scripts/registry_evidence_snapshot.py`
are compatibility adapters to `warden_ops.ledger_maintenance`, the shared read-only SQLite
online backup and preservation owner. It compares every pre-existing user table, column and row stream,
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
