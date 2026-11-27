# AC-10 consolidated cutover decision — 2026-11-27

**Recommendation: DEFER production cutover.** Verified source may be integrated under
the existing user authorization. Production activation remains blocked by unavailable
independent authority capabilities. This is one consolidated review packet, not an
approval proof or a request for the principal to run commands.

## Concrete subjects

| Subject | Exact identity / state |
|---|---|
| Tested source | `fd19e911764bf86ae733c9111f05861f3c67c27c` |
| Unsigned release | `1c78c5e2ab0069ee40660d289eed9ef691c2e2601660513eb3cea08027a8264c` |
| Candidate manifest SHA-256 | `b02fa83f1d8a02917d7deabd3cc7fff24f77f4acd8d8b40aa04e4362302452b5` |
| Candidate location | `/home/quant/.devspace/artifacts/quant-lab/ac-20261127/1c78c5e2ab0069ee40660d289eed9ef691c2e2601660513eb3cea08027a8264c` |
| Decision packet | `97e351d46fb411fef8c25d1db30c2268aa983bc81d030d5e7ad27f84c884e4e8` |
| Packet status | BLOCKED_CAPABILITY; authority_integrated=false |
| Release compatibility | Ledger schema 10 only |
| Original live ledger | Schema 2; unchanged |
| Current independently observed runtime | UNKNOWN |
| Compatible authorized rollback release | UNKNOWN / none supplied |

The candidate has 729 inventoried files and reproduced exactly on a second build.
The [acceptance report](AGENT-CONTROL-ACCEPTANCE-20261127.md) binds test results,
omissions, preservation and measured resource costs to that source. Acceptance and
decision documents are committed after the frozen candidate to avoid a self-referential
release identity. An installed verifier must independently validate the exact candidate.

The machine-readable [packet](evidence/ac-cutover-packet-20261127.json) binds
[installation, migration and activation subjects](evidence/ac-cutover-subjects-20261127.json)
and SHA-256 evidence references. It is valid for 24 hours from its recorded creation;
a changed subject, environment binding, source ledger or expired packet requires a new
packet. No key, signature, approval nonce or claimed principal authentication was generated.

## Separate choices in one review

| Effect | Proposed subject | Current decision readiness |
|---|---|---|
| Release admission | Exact unsigned release above | Source evidence ready; no independent verification/approval channel |
| Authority installation | `c0d3046513d484d40b235b09b024f0049c7ed449980c2f2daaec70328d9e4c28` | Requirements frozen; channel, executor and external baseline identities unbound, so not executable |
| Canonical migration | `dac854bdc5499aeab2d3d85a79e471a28b49363d44ed314f63af20d68f93b68b` | Scratch preservation evidence ready; requires fresh source comparison, writer quiescence and exact approval |
| Activation | Exact release above, schema 10 | Blocked until installation, admission, migration and actual before/after identity checks succeed |

Approving any row must not imply approval of another. Policy delegation, history
attestation, contract promotion, frozen-batch authorization, sealed reads and capital/
trading are excluded. No standing grant is requested implicitly.

### Irreducible capability prerequisites

The available coding and host credentials do not provide an independent principal
identity. No installed principal-facing authenticated UI, independently controlled
verification/revocation state or protected installer was established. Fixture HMAC
keys and mock executors are not substitutes.

An external authority owner must provide:

1. A principal-facing authenticated decision interface that binds this exact packet,
   selected effects, expiry and revocation generation.
2. Verifier/check-baseline, key/grant custody and revocation state outside coding control.
   Candidate code must not select or weaken its required checks.
3. A protected executor and runtime observer with independently verified permissions,
   writer-quiescence control, activation receipts and observable halt/recovery.
4. A bootstrap binding identifying those concrete owners, the trusted baseline and the
   actual previous runtime. Binding them changes the installation subject and therefore
   requires regeneration of this consolidated packet.

The intended installed workflow needs zero principal CLI commands. A23 remains blocked
until that path is demonstrated with an authenticated exact decision and negative
wrong-subject/expired/revoked cases. A24 is satisfied by the concrete blocked packet.
The user is not being asked to operate a shell to substitute for the missing integration.

## Proposed migration and preservation obligations

The existing canonical additive migration was rehearsed on a WAL-consistent scratch
snapshot, schema 2 → 10. All 17 old tables, rows/columns and indexes/triggers survived;
integrity and idempotence passed. Twenty new tables and two columns are added.
The original live source/evidence/ledger remain unchanged.

Rehearsal backup SHA-256:
`2f2c868bd4174cc70c35231984ccd00de03773936781b5142de46e7b617ef25e`.
See [migration evidence](evidence/ac-migration-20261127.json) and
[preservation evidence](evidence/ac-preservation-20261127.json).

The protected executor must establish an actual maintenance boundary, quiesce all
incompatible schema-2/8 writers, take a fresh consistent backup, compare the current
source to the bound rehearsal evidence, and apply only the approved canonical
migration. If the live ledger has changed, repeat the scratch comparison and refresh
the packet before the effect. Do not modify research history to make a comparison pass.

The two unresolved local-only contracts remain preserved. All nine contract identities
referenced by the 75 trials match. Six legacy exposure records remain scope-unknown;
zero attestations or frozen batches were manufactured. Their existing scientific
restrictions survive migration. Detailed scope disclosure was blocked by automatic
approval review; only aggregate counts/digests were used. No sealed data was accessed.

## Activation, health and recovery

After independently verified release admission and the separately authorized migration,
the protected executor would serialize activation, record the exact previous and next
runtime identities, and verify schema compatibility. Health checks must be read-only:
runtime ID, ledger schema/preservation, policy/grant/revocation identity and existing
fail-closed gates. Research scoring, sealed reads and orders are not health probes.

The existing protected manifest is deliberately stale for the changed source.
The authority must establish the valid protected inventory/signature chain through
its authorized mechanism; this engineering session did not refresh or sign it.

There is no known compatible authorized rollback artifact. Before migration commits,
a failed canonical transaction preserves prior state. Once schema 10 or new research
history has accrued, **never restore an older ledger or run an incompatible old writer**.
Use compatible independently authorized code, or halt affected operations while
preserving the ledger. If runtime observation or halt confirmation fails, retain explicit
uncertainty and stop dependent work.

## Limits retained at cutover

- Full hermetic verification: 1,405 passed, 42 skipped, 1 expected failure, no failures/errors.
  Live-data/history omissions are individually listed; they are not implied passes.
- The stateless Linux worker has demonstrated namespace/mount restrictions on disposable
  fixtures. The trusted Warden owner is not an untrusted-strategy sandbox.
- CPU affinity and process limits are enforced; sampled process-tree CPU/I/O remain
  lower bounds, not hard aggregate cgroup accounting. Stronger installation enforcement
  needs actual capabilities and verification.
- Final default situation response: 2,249 bytes; idle median 0.671 s over five fresh
  processes. Resource measurements make no alpha or production-scale throughput claim.
- Independent scientific conformance remains JUDGE_UNAVAILABLE. Source integration and
  cutover cannot supply that conformance, a historical attestation or executable PnL.

## Release of this decision

**Current outcome:** engineering preparation complete; production effects deferred.
The authorized source integration can proceed. AC-05/06/A23 and AC-10 remain open
until the independent capability and exact decision receipts exist. AC-09 retains that
integration dependency explicitly. No further ordinary engineering action can truthfully
manufacture these missing identities or approvals.
