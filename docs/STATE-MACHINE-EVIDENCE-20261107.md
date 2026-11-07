# Research Evidence Ledger — implementation evidence and acceptance blockers

**Date:** 2026-11-07. **Program:** [#49](https://github.com/hzminhzz/quant-lab/issues/49). **Source baseline:** `b5243d9b8241219b2d153faba019dce54caf5e20`. **PR:** [#69](https://github.com/hzminhzz/quant-lab/pull/69) (DRAFT, NOT READY). **Human/signed rollout:** NOT AUTHORIZED.

## Architecture decision

Warden/SQLite is the single authority for immutable research evidence and one-shot lineage admission. The lifecycle is composed of separately attributable facts, not one generic `status` enum. Mutation-capable Warden CLI/MCP commands share a nonrecursive process lease; the scheduler checks committed artifact and trial receipts before archiving; external reports, branch indexes and campaign files are read-only projections; protected human approval and model-specific position episodes remain distinct. Administrative checkpoint and integrity code moved into protected `src/warden_ops/`; causal feature cache and data-admission proof live in protected `src/warden_evidence/`. Principal-approved Warden core LOC limit is 3,000 (2026-11-07); scientific and holdout gates are unchanged.

**Six source-level hazards:** (1) CLI and new *reviewed* MCP adapter share the same lease; original locally untracked MCP server is **not** integrated or activated. (2) Holdout lineage reservation is now atomic and durable **before** reading any bytes. (3) Attempt identity, budget reservation, trial row and Parquet hash receipt have monotonic, interruption-safe transitions with no auto-refund; uncertain orphan artifacts require review. (4) Hourly backtests no longer archive on an exit/sentinel alone; valid committed SQLite+Parquet receipt is required. (5) Native `verify-causality --run-id` and the restricted MCP route now verify an exact immutable run-bound source/contract/data/cache proof; contract-wide legacy rows cannot approve an unrelated run. (6) Qualification now uses Warden's exact `execution_returns` and `simulate_positions` for NEXT_BAR/QUOTE_SIDE, with no optimistic close fallback.

## Ticket disposition

A code change or passing unit test is **not** final issue acceptance. Engineering steps are recorded separately from user/human authority and production integration.

| Ticket | Engineering progress | Remaining required acceptance |
|---|---|---|
| [T01 #50](https://github.com/hzminhzz/quant-lab/issues/50) | Original and GitHub revisions inventoried; consistent source DB snapshot; additive migration on copy | Final canonical/untracked-source decisions and full deployment/service matrix |
| [T02 #51](https://github.com/hzminhzz/quant-lab/issues/51) | CLI single-flight, SIGKILL, reader availability including a live uncommitted SQLite writer; native MCP-to-CLI BUSY tests | Cross-process real scheduler/MCP fault matrix; no independent live MCP server activation |
| [T03 #52](https://github.com/hzminhzz/quant-lab/issues/52) | New restricted stdio MCP, typed flags, tracked/clean executable specs, no arbitrary shell/SQL/holdout; 16 tests | Original untracked HTTP server intentionally NOT_INTEGRATED; no live operational acceptance |
| [T04 #53](https://github.com/hzminhzz/quant-lab/issues/53) | Atomic pre-read `holdout_claims`; synthetic contention and legacy backfill, no automatic release | Independent crash matrix covering all protected opener boundaries |
| [T05 #54](https://github.com/hzminhzz/quant-lab/issues/54) | Shared validation/qualification simulator; hand-priced NEXT_BAR/QUOTE_SIDE long+short, lag, turnover, fees and 8h funding verified | Exact real venue/PIT economic qualification remains separately owned |
| [T06 #55](https://github.com/hzminhzz/quant-lab/issues/55) | Collision-resistant default run IDs, atomic trials and now Stage 1 screening reservations, monotonic caps and durable fsynced Parquet receipts | Full external-process kill matrix remains a separate hardening test |
| [T07 #56](https://github.com/hzminhzz/quant-lab/issues/56) | `reconcile-attempt`, `attempt-status`, interrupted/drift classification and fsynced artifact directory; orphan-DB and caller-response fault cases | Explicit SIGKILL after every publication boundary not independently verified |
| [T08 #57](https://github.com/hzminhzz/quant-lab/issues/57) | Hourly scheduler uses `warden spec-status` across NEW/PASS/REJECT/STAGE3/REVIEW; recovers committed runs even with zero caller stdout; mismatched CLI/ledger IDs and failed-verdict receipts go to review; no duplicate Stage 1 replay | Real live scheduler daemon/fault soak not executed; code-level fixture acceptance passed |
| [T09 #58](https://github.com/hzminhzz/quant-lab/issues/58) | Read-only `scripts/campaign_projection.py`, Carver `--report-only` reconstruction; repeated report is byte-identical | Full campaign crash/restart CLI scenario and provenance conflict cases |
| [T10 #59](https://github.com/hzminhzz/quant-lab/issues/59) | Effective eligibility, immutable receipt status, exact source-bound causal audit, read-only `spec-status` projection, checkpoint drift | Real legacy data projection remains contingent on source import, not fabricated here |
| [T11 #60](https://github.com/hzminhzz/quant-lab/issues/60) | Native SQLite evidence takes precedence over branch result index; structural PR/commit validation; index/SQLite conflict tests | Independent validation of off-main Git identities and source evidence remains pending |
| [T12 #61](https://github.com/hzminhzz/quant-lab/issues/61) | Input/contract/module+params cache identity, SHA receipts and immutable `spec_causality` run proof; stale/invalid/missing proof denied; positive CLI verification tests | Cold-restart with actual archived market data and huge cache remains separate |
| [T13 #62](https://github.com/hzminhzz/quant-lab/issues/62) | History-first fsynced append, atomic current pointer and idempotent typed read-only drift check; 3 tests | External process SIGKILL acceptance and legacy checkpoint reconciliation |
| [T14 #63](https://github.com/hzminhzz/quant-lab/issues/63) | Separate staged/source-verified/contract-admitted classifier with external HMAC verifier, fixture-only 3 tests | Human-origin attestation/approval and actual recovered-data staging integration NOT COMPLETED |
| [T15 #64](https://github.com/hzminhzz/quant-lab/issues/64) | OMP scope invalidation, installer ownership tests; 37 Bun and agent compatibility pass | No optional agent deployment/production permission changes |
| [T16 #65](https://github.com/hzminhzz/quant-lab/issues/65) | Pinned reviewed untracked breakout snapshot, two deterministic stop-latch/sparse-bar golden tapes pass | Candidate is not tracked in GitHub `main`; no scientific behavioral promotion |
| [T17 #66](https://github.com/hzminhzz/quant-lab/issues/66) | Pinned untracked liquidation-reversal snapshot, deterministic expiry tape passes | **BLOCKED:** sparse union inactive bar exits early (strict xfail); funding-spike family, short trails and full coverage not qualified |
| [T18 #67](https://github.com/hzminhzz/quant-lab/issues/67) | Synthetic ML fold/rebalance tape passes purge, future shocks, fold-local fitting and held positions | Other ML candidates, missing-feature aborts and complete adversarial fold matrix not covered |
| [T19 #68](https://github.com/hzminhzz/quant-lab/issues/68) | M01–M25 ownership matrix; WAL-consistent SQLite backup; four additive tables idempotently migrated; complete source-checkout Python suite now green with explicit environmental skips | **NOT RELEASED:** independent human signing/production rollout and scientific T17 semantics remain separate |

## M01–M25 state owners, projection and tests

The complete **owner / event / transition / failure / recovery / evidence matrix** for all 25 workflows and C01–C14 is [STATE-MACHINE-TRANSITIONS-20261107.md](STATE-MACHINE-TRANSITIONS-20261107.md). The summary below is an index, not an authorization to run research.

| State | Authority | Engineering result |
|---|---|---|
| M01 Hypothesis intake | SQLite / Warden | Existing uniqueness, T06 receipts; partially verified |
| M02 File queue | SQLite `screen_receipts` + `attempt_receipts` projection | T08 Stage 1 PASS/REJECT/recovery and Stage 3 receipts fixture-verified |
| M03 Funnel stage/verdict | Immutable Warden tables | T06/T09 projection, partial |
| M04 Trial lifecycle | SQLite `attempt_receipts` | Atomic reservation/finalization, fixture verified |
| M05 Budget/lineage | Hypotheses/SQLite | Atomic cap with concurrent fixture verified |
| M06 Search debt/DSR | `selection_attempts` and contract floor | Existing monotonic guard untouched, no automatic retry |
| M07 Causality | `spec_causality`, feature_sets, immutable cache/proof bytes | Positive run-bound CLI verification and false contract-wide proof denial |
| M08 Six-check audit | Warden audit evidence + exact source-bound causal proof | Legacy audit rows unchanged; unrelated feature proof cannot approve |
| M09 Eligibility | Trial+audit+quarantine+dispositions+receipt | Fail-closed receipt state added |
| M10 Holdout | SQLite `holdout_claims` | Unique pre-read admission, legacy lineage import |
| M11 Manifest/signature | Human trust boundary | Preserved; new manifest approval not performed |
| M12 Dataset origin | Contract+manifest and independent verifier | Staged/verified/admitted classifier, opt-in only |
| M13 Salvage staging | Market-data files | No production data imported; admission integration pending |
| M14 Off-main memory | Native SQLite, optional branch index | SQLite precedence, structurally validated source refs |
| M15 Handoff/history | Fsynced history and current JSON pointer | Crash/tamper drift tests passed |
| M16 MCP/CLI/lease | Warden CLI lease; stdio adapter transport; `ReadOnlyResearchRegistry` skips all schema migrations | Writer BUSY; `warden status` serves an existing WAL snapshot during uncommitted SQLite writes; HTTP server not adopted |
| M17 OMP routing | Task/session-scoped coverage | Revive/empty/agent rebinding denied |
| M18 Feature cache | Parquet and independently checked proof | Stale/modified source invalidates reuse |
| M19 Breakout latch | Strategy-specific position episodes | Pinned golden 2 tapes, no economic change |
| M20 Trailing/rebalance | Strategy-specific positions | Partial breakout long stop tape |
| M21 Funding-spike lifetime | Strategy-specific positions | Not verified; no canonical source admitted |
| M22 Liquidation-reversal lifetime | Strategy-specific positions | Pinned expiry tape, sparse-bar defect unresolved |
| M23 ML fold/rebalance | Purged CV and model-local positions | One baseline synthetic fold/hold tape |
| M24 Campaign | Durable `screen_receipts` and native screening attempts | Report reconstructed from SQLite; scheduler resumes without repeating Stage 1 |
| M25 Adapter installer | Explicit installer-owned symlinks | Existing idempotent install/clean tests passed |

C01=T06–T07, C02=T02–T03, C03=T04, C04=T05, C05=T08, C06=T03, C07=T10, C08=T16–T18, C09=T15, C10=T11, C11=T13, C12=T12, C13=T14, C14=T09. **No universal strategy FSM was introduced**; model-specific state remains local.

## Preservation and rollback evidence

- Original checkout `/home/quant/dev/quant-lab` remains HEAD `b8deedb4d63672699edb99f9f73d54d81e0160f7`, **490** pre-existing dirty/untracked entries. Original queued experiment/hypothesis YAMLs **7 / 6**, restored after earlier fake-CLI test isolation incident. Never reset or clean this checkout.
- Original read-only registry SHA256 **`7b100ec72c623f18f91c9da341fd5c08ece375ac90ffb45293fc3b3d08192eba`** unchanged on repeated check. Counts: **149 hypotheses, 75 trials, 230 selection attempts, 6 qualifications**.
- WAL-consistent snapshot: `/tmp/quant-lab-sm-20261107-readonly/registry.db`, SHA256 `e3a9b16516ca3996d539f7695bda03dba5a4d64b6fb112a681c2f861bbe1aa40`, SQLite `integrity_check=ok`. No source writes.
- Migration fixture: `/tmp/quant-lab-sm-20261107-migration-v2/registry.db`, all pre-existing table row hashes unchanged across two runs. Additive schema reconstructs **75 finalized attempt receipts, 115 Stage 1 screen receipts, six spent holdout lineage claims, zero existing run-bound causality proofs**. Source SHA256 remains `e3a9b...e1aa40`. Original snapshot remains for rollback **only in disposable fixtures**; never overwrite an active DB with an old copy.
- Reviewed untracked strategy SHA256: breakout `0103874548183421d06e604bd41c9a0e0fba2acdde3f6351ae2f769d5b61dea0`; liquidation reversal `385618ac9f404774ba15790d493d36eae868386f75b3058aa036786bc17bd6b1`. Both copied into `tests/fixtures/untracked_review/` solely for deterministic tests. Not deployed, not admitted into production research candidate catalog.
- `warden-mcp.service` observed inactive; no new MCP service enabled. `quant-social-research.timer` and `quant-archive-discovery.timer` observed active/registered, but **no service configuration changed**, nor any executable research started by this implementation.
- Rollback source-code path: return to the prior exact GitHub `main` commit/release through reviewed reversible Git operations; keep archived evidence, append-only SQLite history and consumed claims. Never delete new DB tables or reset historical selection debt as part of an automatic rollback. Restoring a database backup requires separate human outage, reconciliation and explicit authorization.

## Verification and known limitations

- Final focused suite after read-only status fix: **115 passed, 1 expected failure, 5 warnings**; modified module Ruff, shell syntax, Warden LOC and `git diff --check` pass. Bun skill-router suite: **37 passed**.
- New focused synthetic test modules cover attempt lifecycle, holdout parity, pre-read claim, cache proof, checkpoint crash, campaign recovery, MCP contract, source staging, breakout/reversal episodes and ML folds. An additional adversarial test found that `status` initially blocked on runner-init migrations during a SQLite `BEGIN IMMEDIATE` writer transaction. Fixed via strictly read-only `src/warden_ops/readonly.py` for `status`; writer-held SQLite regression now passes. Existing agent-installer/Bun suites still pass.
- Warden core measured **2,568 / 3,000 LOC** on the latest source check (recalculate at final commit). Principal approved the new 3,000-line cap on 2026-11-07. CLI, adapter, protected admin and evidence modules are included in the regenerated *test-only* checksum inventory. No human signing, approved manifest or protected service cutover occurred; do **not** publish a self-approved manifest as production authority.
- **Final source-checkout full Python suite**: `PYTHONPATH=. uv run --frozen pytest -q` -> **295 passed, 13 skipped (historical pinned market/source fixtures absent), 1 strict expected failure (untracked liquidation-reversal inactive-bar bug)**. No unexpected failures. Conditional skips do not fabricate recovered datasets or scientific results. The missing Carver contract now causes a transparent module-level skip instead of a collection crash.
- Full `ruff check .` currently reports **8 pre-existing errors** in untouched `scripts/salvage_crypto_majors.py` and `scripts/salvage_crypto_top100.py` (imports, broad silent catches, redundant `.keys()`). Affected/new modules should pass focused Ruff; broad repo lint is not accepted.
- No production-holdout unseal, real market backtest, threshold relaxation, production model training, capital trade, sudo, signing key, production service modification, PR merge or issue closure was performed. A synthetic ML fold fixture trained a test-only Ridge model to verify leakage isolation. No alpha or deployment claim is made.

**Final disposition:** RESEARCH EVIDENCE LEDGER ENGINEERING BASELINE PASSES SOURCE-CHECKOUT TESTS; PARTIAL 19-TICKET ACCEPTANCE; NO PROTECTED DEPLOYMENT. Deferred: original untracked strategy-specific inactive-bar behavior (T17), externally sourced historical market/contract data, external-process soak and independent human signing/cutover. These are not reasons to relax scientific gates. Scientific governance issues #4–#14 remain independently owned.
