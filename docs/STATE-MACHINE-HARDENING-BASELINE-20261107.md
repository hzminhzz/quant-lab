# State-machine hardening: source and safety baseline (2026-11-07)

This is an engineering checkpoint, **not** T01 or T19 acceptance, governance approval, production deployment, or scientific qualification. Parent: [#49](https://github.com/hzminhzz/quant-lab/issues/49); tasks: #50–#68.

## Source and preservation

- Verified GitHub `main`: `b5243d9b8241219b2d153faba019dce54caf5e20`.
- Original `/home/quant/dev/quant-lab` checkout: `b8deedb4d63672699edb99f9f73d54d81e0160f7`, 46 commits ahead / 5 behind GitHub main; 25 modified tracked paths and 465 untracked entries. **Do not reset, stash, clean, or overwrite**.
- Dedicated DevSpace worktree: `/home/quant/.devspace/worktrees/quant-lab-98478a21`, branch `feat/state-machine-hardening-20261107`, created directly from the verified GitHub `main` SHA.
- Canonical Warden core: tracked `src/warden/` at remote main plus separately reviewed additions; local untracked `warden-mcp` and retry-memory code are **NOT_INTEGRATED**. Existing tracked `scripts/research_outcome_memory.py` is not proof that all untracked branch-sidecar material has been integrated.
- Original registry read-only observed counts: **149 hypotheses / 75 trials / 230 selection attempts / 6 qualifications**. No production science execution, protected holdout access, trading, sudo, service restart, or signing performed during this work.
- Reproducible isolated SQLite WAL backup rehearsal: `tests/test_state_machine_registry_backup.py` uses SQLite's online backup API and verifies historical qualification preservation, `integrity_check`, and legacy lineage backfill. **Not a verified backup of the live registry**; that remains T01 acceptance work.

## State-machine ownership and pending acceptance

The names and scopes are from parent #49. `PARTIAL` is not accepted evidence.

| ID | Owner / source of truth | Target and relevant ticket | Observed disposition |
|---|---|---|---|
| M01 | Warden SQLite hypotheses | Idempotent intake, T06/T01 | PENDING |
| M02 | SQLite projection of file queue | Durable archive/reconcile, T02/T08 | PARTIAL: missing final CLI receipt routed to review, no SQLite reconcile |
| M03 | Warden CLI and SQLite evidence | Funnel transition ledger, T06/T10 | PENDING |
| M04 | Warden SQLite | Trial reservation/receipt, T06 | PENDING |
| M05 | Warden SQLite | Monotonic budgets, T06 | PENDING |
| M06 | Warden SQLite | Search debt and DSR floor, T06/T10 | PENDING |
| M07 | Warden causality receipts | Validated identity, T12 | PENDING |
| M08 | Immutable audit evidence | Audit projection, T10 | PENDING |
| M09 | Trials, dispositions, quarantine | Eligibility projection, T10 | PENDING |
| M10 | Warden SQLite | Exclusive pre-read claim, T04/T05 | PARTIAL: fixture claim tests only |
| M11 | Human-owned contracts / OS boundary | Preserve approval, T14 | PENDING; human-only |
| M12 | Contract and dataset provenance | Exact-source admission, T14 | PENDING |
| M13 | Recovered data staging | Distinguish staged/approved, T14 | PENDING |
| M14 | SQLite authority + branch-sidecar | Cross-branch negative evidence, T11 | NOT_INTEGRATED sidecar |
| M15 | Checkpoint JSON/history | Atomic read-only resume, T13 | PENDING |
| M16 | Warden CLI + optional MCP | Shared mutation lease, T02/T03 | PARTIAL: CLI lease tests only |
| M17 | Optional OMP router | Scope invalidation, T15 | PARTIAL: revive, empty prompt and agent scope invalidation tests pass; installer not audited |
| M18 | Feature cache + SQLite | Causal proof validation, T12 | PENDING |
| M19 | Breakout strategy position state | Sticky latch episodes, T16 | PENDING |
| M20 | Trailing-stop strategy state | Peak/trough/rebalance episodes, T17 | PENDING |
| M21 | Funding strategy position state | Holding-period episodes, T17 | PENDING |
| M22 | Reversal strategy position state | Position lifetime, T17 | PENDING |
| M23 | ML strategy fold/rebalance state | Purged fold and persistence, T18 | PENDING |
| M24 | SQLite screening history | Campaign resume without rerun, T09 | PENDING |
| M25 | Optional adapter installer | Owned install/clean transitions, T15 | PENDING |

Candidate ownership: C01=T06–T07, C02=T02–T03, C03=T04, C04=T05, C05=T08, C06=T03, C07=T10, C08=T16–T18, C09=T15, C10=T11, C11=T13, C12=T12, C13=T14, C14=T09. Existing governance #4 and #5–#14 remain independently authoritative; neither their acceptance nor their scientific status is superseded here.

## Evidence and blockers

- Source-level hazard independently confirmed: `scripts/run_hourly_pipeline.sh` previously swallowed `backtest` failures with `|| true` and unconditionally archived the spec. A fixture-only guard now routes failed/malformed backtests to `experiments/active/review/` and avoids automatic retry; four scheduler fixture cases pass. This does **not** establish durable SQLite receipt reconciliation or complete T08.
- Source-level hazard independently confirmed: `qualify_candidate()` checked lineage use and opened holdout bytes before its final UNIQUE qualification INSERT. An additive `holdout_claims` table now reserves and marks read-start before `resolve_dataset()` SHA read or Parquet load; fixture races pass. Full fixture evaluator parity and migration rollback are not yet done.
- CLI mutation lease tested under cross-process contention, SIGKILL and `warden status` read path. The scheduler still has its separate whole-job lock; the locally untracked MCP server was not reviewed/imported. Full T02/T03 acceptance is not yet claimed.
- OMP router source incorrectly reused previous task coverage for revived/empty prompts and a different agent. Added scope invalidation and regression test: both bundled Bun test files pass **37/37**. Installer ownership and lifecycle coverage remain open under T15.
- The unchanged protected Warden LOC policy is **2,400 lines**. The additive draft exceeds it (`2,478` as measured); this is a hard acceptance failure. Do **not** silently relax `WARDEN_MAX_LOC`, regenerate approvals, or present the draft as deployable. Refactor without compromising module ownership or obtain separate human governance approval.
- The clean checkout omits several historical market fixtures and `.workspace/transitions`; fixture-dependent legacy tests fail for those preexisting reasons. Do not synthesize market returns to make old tests pass.
- **Incident and recovery:** the first scheduler fixture test inadvertently followed the script's original hard-coded `REPO_DIR` into the original checkout, using a fake `uv` command. The test moved seven pre-existing experiment queue YAMLs and six hypothesis queue YAMLs into their failed directories. All **13 exact files** were restored by targeted, no-overwrite moves; original file modification timestamps were retained. No real Warden science was invoked in the test. The script now accepts a fixture-only `QUANT_LAB_REPO_DIR` and `QUANT_LAB_HOURLY_LOCK_FILE` override, defaulting to historical production locations; four isolated cases pass. Verify original queue state again before any later operation.
- No production registry backup, protected service activation, PR merge, or deployment is asserted by this document.
