# Plan: Deepen the existing ML4T–Warden publication seam

> Revised 2026-11-28. Implements sibling spec.md, DG-AC01–11, in four gated work packages. Supersedes the previous eight-issue plan. One autonomous cross-repository objective. This plan does not itself authorize scientific effects or establish a release receipt.

## Non-negotiable architecture

Follow merged Quant-Lab refactor PR #151 (bd222e2) and its decision record docs/ARCHITECTURAL-UNTANGLING-20261128.md. One existing owner per effect; delete competing coordination rather than hide it behind wrappers.

| Existing owner | Responsibility in this work |
|---|---|
| ML4T Data publication/cutover | One committed data/evidence generation; one existing family policy; coherent catalog references |
| Native ML4T loader | Discover and read committed inventory through existing interfaces |
| Warden resolve.py | Semantic matching, metadata-only discovery, explicit diagnostics |
| Warden compile.py and existing dataset builder/provenance | Pin selected generation, verify authorized input bytes, produce existing immutable contract/artifact |
| ExperimentRunner, ResearchRegistry, EvaluationKernel | Unchanged scientific progression, persistence and computation |
| CLI/control/jobs and trusted appliance | Existing adapters, resource bounds and verified release lifecycle; unchanged ownership |

Readiness/health/freshness are derived observations, not publication or scientific authority. A funding update must not block an otherwise intact FX generation. Compilation consumes a selected coherent input, not a globally changing readiness report; a newer unrelated catalog identity alone cannot invalidate a valid pin. Subsequent research uses the existing compiled contract and exact artifact verification, not a live producer generation.

Use the existing Parquet lake and publication modules. Compare a minimal immutable generation directory with safe unchanged-leaf reuse against an immutable manifest referencing immutable partition objects, or another simpler existing mechanism justified by actual filesystem evidence. Do not prescribe another platform. Verify the Dukascopy cutover's sequential-rename crash gap rather than treating exception rollback as process-crash recovery. Choose one commit/visibility seam and one qualification path; the family-specific evidence rules stay behind that seam. No service, database, scheduler, generic repository interface, new scientific lifecycle or duplicated provider rules in Warden.

## Dependencies and deliverables

| Package | Depends on | Deliverable | Spec trace |
|---|---|---|---|
| DG-01 — Prove | None | Real BTCUSDT publication-to-compile vertical slice, invariant and architectural witness | 01–04, 06, 08, 10–11 |
| DG-02 — Consolidate | DG-01 | All active publication paths use the same authority; semantics and interval policy preserved | 01–09, 10 |
| DG-03 — Reconcile | DG-02 | Safe adoption, resource measurements and full producer baseline repair | 02–04, 08–11 |
| DG-04 — Ship | DG-03 | Final review, both full gates, compatible production verification, separate readiness follow-up | 01–11 |

Do not start migration beyond the first vertical slice until DG-01 passes. Investigate baseline collection failures from the beginning, in parallel with read-only inventory where useful; do not defer discovery to final review. For every substantive change use the same public-interface loop: actual isolated producer publication -> native catalog/loader -> Warden resolution and authorized compilation when eligible -> assertions -> correction. Keep both workspaces available. Record exact candidate revisions and evidence after each gate. No independent producer/consumer projects, no agent delegation.

### Milestone: Committed dataset publication

**Issue 1 -- DG-01: Prove the invariant through one complete real-data vertical slice**

Both repositories. Establish the boundary before changing broad infrastructure.

1. Recheck Git state, PR #48/#152, instructions, actual active services/writer paths, installed ML4T APIs, filesystem, capacity, current acceptance owners, legacy readers and protected/compiled references. Read the merged refactor, producer ADRs 0002–0004 and existing cutover/publication code. Inspect all qualification routes and baseline full-suite collection failures (retired LSE/PMXT, supervisor and other actual failures); identify owners and a safe reconciliation path without absorbing unrelated changes. Record current state rather than relying on dated counts.
2. Reproduce the five latest P1 failures on disposable copies of authorized stored Parquet. Preserve earlier regressions. Establish a shared RED lifecycle witness with positive as well as negative expected outcomes. Measure comparable publication, discovery and resolver baseline; identify where the previous catalog validation spent time. Inspect the previous golden scientific witness before modifying any code.
3. Decide the minimal producer implementation at the existing seam. Record identity and interval semantics, one family qualification owner, immutable references, atomic commit/reopen, conflict handling, pin lifetime and rollback. Validate on the actual filesystem. Separate catalog authority (reference index) from derived readiness. Use a short existing synchronization/compare-and-publish mechanism where justified; do not serialize unrelated acquisition or invent a distributed protocol. Apply the deletion test to direct qualification code and redundant interfaces. No design expansion beyond actual requirements.
4. Implement one BTCUSDT minute route across native writer, committed publication, catalog/query and Warden resolve/compile. Use actual October/November 2026 data copied into independent scratch roots; check actual counts and bytes. Initial publication plus two updates preserve old historical bytes, rows and bounds. Generation identity binds membership, digests and required evidence; equivalent republish is idempotent. No broad price-byte reading by Warden before signed policy and fence.
5. Use the existing compiler, dataset builder and provenance to produce a positive write-once artifact from a legitimate isolated test policy and actual permitted exploratory input. Verify authorized selected bytes as actually read (not only a separate hash of a path that may change); no read of excluded/fence-straddling data. Verify actual missing-policy refusal. No new compile API that takes fabricated real authority, no canonical registry/data/cache symlinks, no production scientific admission. Reuse existing compiled-contract path afterward; later producer changes do not change the compiled artifact.
6. Exercise actual abrupt process termination at durable boundaries, deterministic concurrent same-parent/different-dataset commits and reader pins, retry/reopen, absent/renamed/replaced committed leaves, same-size/restored-time replacement, unlisted leaves, stale metadata/evidence and changed input between resolve/read/commit. Assert previous complete state survives failed update, no partial successful new state, no lost update or output on consumer refusal. Include independent publication and stale readiness while the valid BTC pin remains usable. Do not require equality of whole globally changing resolution reports to validate the selected pin.
7. Run focused producer/consumer regressions and the existing golden research lifecycle witness. Record actual data IDs, timings, bytes, fault results and the exact compatible candidate revisions. Commit coherent vertical-slice work if authorized. Do not deploy or expand until the end-to-end architectural gate passes.

Gate: complete positive native publication/query/resolve/compile plus all invariant fault witnesses, scoped Warden changes and preserved lifecycle reference. No other producer family migration until proven. If a genuine external filesystem or authority blocker exists, state it precisely rather than simulate success. DG-AC01–04/06/08/10–11.

**Issue 2 -- DG-02: Consolidate the active producer paths behind the same interface**

Both repositories, producer-primary. Requires DG-01. Keep one test surface and existing native readers. Migrate in small batches; after each batch prove actual producer-to-consumer behavior.

- Funding: use the existing authoritative funding validator and event/settlement policy under the shared publication contract. Generic and dedicated publishers must not independently qualify funding or rewrite unrelated OHLCV entries. Test both orders with missing/valid audit, stale metadata versus fresh audit (prior 93/175-row defect), renamed and changed actual partitions, two updates, concurrency and interrupted publication. Funding events, OHLCV, mark data and executable combined basis remain distinct.
- Dukascopy: preserve existing per-unit writer receipts, acceptance schemas, source mapping, price side and legitimate closures/source-empty classifications. Reuse existing publisher and cutover domain semantics. Minute and second index receipt gaps cannot be accepted from quality alone. Recover uncovered intervals through authorized existing source/publisher when available. Test actual valid and missing/mismatched daily/monthly evidence, including the prior ES one-second witness and index-minute gaps; qualify only the requested intervals supported by authoritative evidence. Failed/unaccepted tails must not block valid previously committed intervals unless the existing policy requires it. Never manufacture source receipts or weaken categorical acceptance.
- Real native FX/metals positives: re-audit actual EURUSD, GBPUSD, USDJPY, XAUUSD and XAGUSD using authoritative evidence. Assert actual native queries and eligible OHLCV resolution. Assert quote-side, CFD/exchange-futures and proxy substitution refusals. If evidence prevents a positive, recover it or explicitly report the genuine blocker; do not count a negative-only test as passing interoperability.
- Remaining active writers: follow DG-01 inventory, including Binance aggregate 1h, remaining maintained crypto/Hive, specialized daily/auxiliary publishers, repair paths and scheduled entry points. For each migrate or explicitly quarantine with owner/reason. Remove competing direct qualification logic after callers migrate. Preserve keys, aliases, actual provider precedence and required receipts. Readiness remains derived and cannot be used as a global qualification lock. Legacy mutable formats can be diagnostic; no downgrade for new compilation. Existing valid immutable contracts remain independently verifiable.
- Native flat archives: inspect actual GC/SI/ES/NQ and other supported files through authoritative loaders; publish or discover actual content identity, bounds and continuous/instrument semantics with explicit limitations. Do not invent contract maturities, roll verification, centralized volume or Hive support. Keep discovery separate from executable research qualification.
- Preserve complete-family behavior for aggregate and per-symbol stores, aliases, missing/unqualified/untyped members and alternative sources. Do not replace historical PIT membership with today's catalog. Verify identity-compatible legacy requirements and fail closed for explicitly unproven semantics. Replay all six frozen original requirements unchanged plus explicit FX/metals/quote/maturity probes. Test unrelated dataset updates and stale observation handling across actual families.
- Reconcile both prior PRs' useful work. Remove or simplify redundant catalog mutation paths and repeated provider policy from Warden; document the before/after call surface and actual owners. Do not introduce another orchestrator or scientific module to support the data seam.

Gate: every active research-qualifying path uses one generation invariant or is explicitly quarantined; no order dependence, no missing index receipt bypass, no silent legacy or semantic fallback. Positive real source reads and requirement matching where evidence permits; actual negative refusals; no producer-specific acceptance in Warden. DG-AC01–09/10.

**Issue 3 -- DG-03: Reconcile the existing lake, resource cost and producer gate**

Both repositories. Requires DG-02. No canonical mutation during rehearsal.

- Adopt actual legacy data in disposable independent roots. Inventory active references, old artifacts, provider receipts, data semantics, disk, actual filesystem and rollback candidates. Reuse existing bytes/evidence; use bounded copies or safely independent verified data objects as allowed by repo rules, not canonical symlinks. Preserve historical receipt/contract/trial IDs and approved interval policy. Unverified inputs remain discoverable/unqualified; recover stale evidence using native maintenance, source gaps using authorized providers. No blanket redownload, full-lake mirror, destructive replacement or synthetic receipt.
- Rehearse representative legacy adoption plus complete catalog scope, process interruption/reopen, restart, competing publication, stable old pins, rollback and old artifact reuse independently of the live generation. Bound retention to actual reader/compile and recovery needs; preserve reachable references and avoid speculative GC. Verify that old compiled artifacts require no live-lake pin. Determine cutover compatibility and exact failure recovery before live release.
- Measure same-real-data comparisons from DG-01: initial adoption, unchanged republish, incremental publication, exact-symbol vs full inventory discovery, resolve and eligible authorized compile. Report repetitions, elapsed distributions, bytes scanned/hashed/copied, peak memory/disk and service budgets. Show unchanged input reuse and no unnecessary whole-lake scans, copying or unrelated audits. Distinguish fresh byte verification from parsed metadata caching; no aggregate stat-only acceptance or weakened gates. Compare historical 1.15 vs 119.14 seconds only as context.
- Close actual producer full-suite baseline failures identified at the beginning. Determine which tests/dependencies are genuinely retired and which remain required. Reconcile LSE/PMXT/supervisor and other reproduced macro/CCXT/Node/canary failures with their actual owners. Preserve concurrent dirty/untracked work; integrate only coherent authorized changes. Never ignore collection failures, remove valid regression coverage, or restore retired subsystems without need. Run complete applicable producer suite plus native scheduled maintenance gates.
- Re-run final shared interface matrix on representative adopted real stores and complete scope. Check existing research reference, exact contract compatibility, protected boundaries and strict performance witnesses. Record stage/effect owners, changed production modules/dependencies, resource evidence, recovery limitations and baseline deviations. Prefer deletion of redundant paths over more abstraction.

Gate: reproducible adoption and rollback evidence, bounded changed-data cost under verified applicable service budgets, complete producer verification green, historical compatibility, golden scientific behavior preserved. All engineering blockers addressed or explicitly irreducible; no release on failed gate. DG-AC02–04/08–11.

**Issue 4 -- DG-04: Ship the exact compatible pair and verify actual operation**

Both repositories. Requires DG-03. Use existing trusted appliance/maintenance authority; scientific effects remain separate.

1. Review standards and specification together against actual final diffs, active writer inventory, exact candidate pair and all DG-AC. Attack the publication invariant rather than enumerate superficial helper defects. Check qualification bypass, data/identity TOCTOU, interrupted commit, unrelated readiness, interval semantics, no PIT/holdout access, duplicate effects and resources. Confirm no new scientific orchestrator/ledger/state machine/service, no registry schema/gate changes, no duplicated provider policy, no unnecessary edits outside resolver/compiler seam. Verify the golden lifecycle reference and unchanged strict witnesses. Resolve findings autonomously and rerun relevant/full exact gates when source changes.
2. Run final full applicable producer suite, real-data lifecycle harness and exact immutable Warden verification in its own locked environment. Record actual skips/xfails, checks, test/verification receipts and exact revisions. No cached or previous receipts promoted to new source identity. Refresh integrity manifest only via authorized maintenance. Preserve original scientific policy and signed evidence. Commit/push coherent changes and document ownership, diagnostics and supported operation paths.
3. Reinspect current main/PRs, installed runtime, active producer/service definitions, compatibility and permissions. Merge in dependency order only after both final gates pass. Use existing operational release/migration/rollback paths; preserve unrelated primary work and old compatible references. Coordinate/quiesce affected writers/readers with existing mechanisms; verify atomic visibility of the compatible publication view, exact source/runtime identity and restart/retry behavior. No mismatched producer/consumer activation.
4. Observe actual post-release scheduled producer-to-consumer operation: maintained incremental cycle, native reads, Warden resolution of current committed evidence, old compiled contract verification, history preserved, no partial publication, safe retry/recovery and healthy services. If a gate fails, stop or recover compatibly. Installed inactive candidate is not deployment.
5. Separate readiness follow-up: replay the six frozen bulk-intake requirements and reassess fourteen research tracks from actual evidence. Recover ordinary data blockers within existing authority. Advance native compile/prepare or other operations only where existing scientific governance permits. Missing venue policy, intent, conformance, required data/entitlement or PIT evidence is a reported scientific/data blocker, not a requirement to change thresholds or force admission. Engineering acceptance does not require alpha, positive scored outcomes, any minimum research-track count or policy expansion.
6. Report exact commits, merged PRs, active producer/data/runtime identities, native integration commands/results, performance costs, fault/recovery receipts, scientific readiness and genuine remaining blockers. Update the existing durable docs and PRs; do not rewrite historical evidence.

Gate: both release gates and two-axis review pass on the exact compatible pair; actual production publication/read lifecycle and recovery verified; previous scientific lifecycle unchanged; separate honest research-readiness report. DG-AC01–11. No production or scientific claim without its own evidence.

## Shared adversarial acceptance matrix

| Operation | Required observable result |
|---|---|
| Initial real BTC publication + two tails | Exact history and unchanged old leaf bytes; correct new generations |
| Identical republish | Stable identity, no unnecessary historical work |
| Abrupt interruption before/at/after commit | Old or complete new committed state; retry converges |
| Concurrent independent/same-parent commits | No lost references or silent overwrite |
| Concurrent reader spans update | Stable selected pin, never mixed data/evidence |
| Unrelated publication; stale readiness | Unaffected valid input remains resolvable; stale observation labeled |
| Missing/renamed/replaced committed leaf | Observable damage refused; authorized byte digest catches latent change |
| Extra or staged leaf | Never silently included in a committed read |
| Fresh audit, stale metadata or missing receipt | No new accepted generation for affected interval |
| Generic/dedicated funding order | Same evidence yields same qualification |
| Minute/second index receipt gaps | Affected interval refused; legitimate accepted interval preserved |
| Real FX/metals positive; quote/CFD/maturity negative | Exact supported matches; no substitution |
| Untyped/unqualified family member or unknown PIT universe | No silent universe shrink or historical assertion |
| Missing signed policy or unauthorized/fence-straddling input | Refuse before prohibited raw reads |
| Authorized native compile | Exact selected bytes and existing artifact identity; no production authority |
| Producer advances after compile | Existing valid immutable research artifact still resolves |
| Cutover/restart/rollback | Compatible active runtime and committed data; recovery intact |
| Golden reference and full gates | No change to scientific results, effects, accounting or thresholds |

## Execution guardrails and references

- Start from producer workspace ws_b0a838de43 (/home/quant/.devspace/worktrees/ml4t-data-02cec098), consumer ws_889301ea34 (/home/quant/.devspace/worktrees/quant-lab-5ef73d27), both fix/cross-repo-data-compat-20261128; inspect current state, reuse IDs while valid. Primary checkouts /home/quant/dev/quant/ml4t-data and /home/quant/dev/quant-lab. Existing PRs hzminhzz/ml4t-data-project#48 and hzminhzz/quant-lab#152.
- Read AGENTS.md, existing ADRs, docs/ARCHITECTURAL-UNTANGLING-20261128.md, docs/ARCHITECTURE.md, CONTEXT-MAP.md, docs/DATA-REQUIREMENT.md, docs/VENUE-POLICY.md and relevant ML4T skill references. Golden witness tests/test_research_lifecycle.py and tests/fixtures/research_lifecycle_d1e9325.json.
- Producer seam: existing scripts/lib/{hive,write_authority,families,audit_common,catalog,stages}.py, sync_store_catalog.py, sync_binance_um_funding_catalog.py, native run_pipeline.py, dukascopy_rebuild/{cutover,publisher}.py, data/query.py, scripts/verify_cross_repo_compat.py and active caller inventory. Consumer seam: src/warden/{resolve,compile,provenance}.py and existing dataset-builder input/fence interfaces. Do not copy the producer's acceptance decision tree into the consumer.
- Maintain exact evidence, limits and test commands per package; use existing durable docs/receipts, not a new control plane. Preserve scientific history, no holdout probe or live order. Actual prior results are dated context, not acceptance of new code. Full applicable verification is mandatory before release.
- Four packages are one execution objective, not four manual prompts. This plan is not a published GitHub milestone, a verified implementation or a deployment receipt. Scientific follow-up remains distinct from engineering completion.
