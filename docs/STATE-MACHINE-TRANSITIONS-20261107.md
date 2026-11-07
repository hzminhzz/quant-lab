# State-machine event / failure / recovery contracts — quant-lab

**2026-11-07.** Parent [#49](https://github.com/hzminhzz/quant-lab/issues/49), acceptance [#68](https://github.com/hzminhzz/quant-lab/issues/68). Source implementation: PR [#69](https://github.com/hzminhzz/quant-lab/pull/69). **Engineering fixture map, not permission to run protected science or trade.**

## Abstraction boundary

Only Warden-owned admissions, claims, and evidence receipts are authoritative. Files, reports, agent notes, checkpoint JSON and campaign queues are *projections* of already-recorded facts. Strategy episodes have different economics and must **not** be coerced into a universal execution state machine. An admitted but incomplete attempt is **REVIEW**, never `NEW`.

| ID | Authority / state | Admitted event -> transition | Failure / recovery (no auto-retry) | Fixture evidence / disposition |
|---|---|---|---|---|
| M01 | Warden hypothesis registry | Register frozen mechanism -> ACTIVE | Duplicate/contract drift -> deny | `test_registry.py`, `test_attempt_lifecycle.py` |
| M02 | Stage1 + trial SQLite receipts; YAML is queue view | Spec-status NEW -> Stage1; PASS -> trial; REJECT -> failed; finalized -> archive | Lost CLI result -> reconcile via receipts | `test_hourly_queue_reconciliation.py`, `test_screen_receipts.py` |
| M03 | Warden screening/trial immutable rows | Valid Stage1 or Stage3 computation -> persisted verdict | Partial/unknown -> REQUIRES_RECONCILIATION | `test_screen_receipts.py`, `test_attempt_lifecycle.py` |
| M04 | `attempt_receipts` | RESERVED -> FINALIZED (atomic trial row and SHA) | Orphan, corrupted artifact -> INTERRUPTED_REVIEW/EVIDENCE_DRIFT | `test_attempt_lifecycle.py` |
| M05 | `hypotheses.trials_used` + `attempts_json` | BEGIN IMMEDIATE reserves unique run, consumes 1 of 3 slots | Duplicate/lineage cap -> denial; no refund | Concurrent and restart lifecycle fixtures |
| M06 | `selection_attempts`, `contract_search_state` | Each screen / parameter trial -> monotonic search debt | Historical rejection preserved, no DSR reset | `test_issue4_search_accounting.py`, `test_research_outcome_memory.py` |
| M07 | `feature_sets` + `spec_causality` + proof Parquet | Verify causal prefix and exact module/contract/input -> append proof | Source/data/cache drift -> deny or re-verify, never promote from cache existence | `test_run_bound_causality.py`, `test_feature_cache_proofs.py` |
| M08 | `audit_evidence`, exact Warden proof | Audit checks -> approved/rejected immutable evidence | Missing run-bound proof -> failed audit | `test_issue4_eligibility.py`, `test_run_bound_causality.py` |
| M09 | Effective eligibility projection (trial + dispositions) | Reconcile historical verdict/quarantine/audit/receipt -> current eligibility | Superseding revocation -> ineligible, history unchanged | `test_issue4_eligibility.py` |
| M10 | `holdout_claims` per lineage | Human-authorized claim -> mark READ_STARTED **before** opening -> spent | Crash/duplicate -> irrevocably spent/review, no release | `test_holdout_claims.py`, `test_holdout_execution_parity.py` |
| M11 | Human-held signature + Warden checksums | Human approves separately from checksum publication | Missing/unverified signature -> no privileged cutover | `src/warden_ops/integrity.py`; operational cutover intentionally not performed |
| M12 | Contract + independent origin attestation | STAGED -> PROVENANCE_VERIFIED -> CONTRACT_ADMITTED | Digest-only evidence cannot self-promote | `test_data_admission.py` |
| M13 | Provider acquisition/staging (not a trial) | Acquire raw bytes -> verify identity -> stage candidate | Interrupted source/missing attestation -> STAGED/BLOCKED | No production ingest changed; intentionally separate from Warden |
| M14 | Canonical SQLite; optional branch-result index | Verified prior negative study -> deny same config automatically | Index mismatch/invalid source -> REVIEW; SQLite wins | `test_research_outcome_memory.py` |
| M15 | Append+fsync history, atomic current checkpoint | Handoff -> history receipt -> current pointer; continue -> read-only replay | Crash between writes -> DRIFT; never execute stored shell | `test_checkpoint_recovery.py`, `test_herdr_and_handoff.py` |
| M16 | Warden CLI mutation lease; restricted stdio adapter | Single writer admitted; other writer BUSY; reads available | SIGKILL unlock; WAL reader no mutation | `test_warden_mutation_lease.py`, `test_mcp_warden_adapter.py` |
| M17 | Optional OMP router scoped coverage | Valid scope/skills -> permission to request action | Missing/revived scope -> deny mutation | 37 Bun tests; Warden remains independent |
| M18 | Immutable feature Parquet and sidecar proof | Verified source/code/parameter/panel identity -> cache hit | Missing proof/tamper -> revalidate or operator review | `test_feature_cache_proofs.py` |
| M19 | Breakout strategy local latch | Entry -> peak tracking -> STOP_LATCHED -> reset -> new entry | Closed market must not reset latch | Pinned untracked fixture `test_breakout_latch_fixture.py`; no production import |
| M20 | Strategy-specific trailing/rebalance state | Active bars update extrema and rebalance policy | Stop/gap/inactive observation -> strategy-specific guard | Partial golden breakout tapes; other models intentionally not homogenized |
| M21 | Funding-spike holding policy, not Warden | First signal -> holding state -> contract-defined expiry | No canonical admitted source; do not invent counter behavior | **NOT_INTEGRATED**, deferred scientific episode fixture |
| M22 | Liquidation-reversal holding policy | Entry -> timer -> exit/reset/re-entry | Known inactive-union-bar clock defect (strict xfail) | `test_liquidation_episode_fixture.py`; source is untracked; no trading approval |
| M23 | ML4T purged CV / baseline model | Train in fold -> predict OOS -> rebalance -> hold | Future/shocked data cannot change prior decisions | `test_ml_fold_episodes.py` + `test_warden_leakage.py` |
| M24 | SQLite attempt receipts, campaign report is projection | Screen committed -> report rebuilt on restart | Lost report -> read-only reconstruction, no rescreen | `test_campaign_projection.py` |
| M25 | Installer-owned adapter symlinks | Install/reinstall/clean explicitly -> known links only | Collision/user-owned file -> preserve and refuse | `test_agent_compatibility.py` |

## Cross-cutting C01–C14 acceptance

C01 (trial reservation and reconciliation): M04–M06, fixture acceptance. C02 (CLI/scheduler/MCP single-flight): M02/M16, fixture acceptance. C03 (pre-read one-shot holdout): M10, fixture acceptance only. C04 (uniform fill economics): M03/M10; `test_execution_math_ledger.py` hand-prices next-bar/quote-side long/short/funding. C05 (receipt-gated queue): M02, fixture acceptance. C06 (restricted MCP policy): M16, statically reviewed stdio route. C07 (effective eligibility): M08/M09; missing causal proof fails closed. C08 (position episodes): M19–M23; **T17 explicitly incomplete** due untracked strategy source. C09 (agent adapter scope): M17/M25. C10 (off-main memory): M14. C11 (checkpoint): M15. C12 (causal cache): M07/M18. C13 (source staging): M12/M13, only synthetic verified. C14 (campaign): M24.

**Governance authority stays separate:** existing issues #4–#14 remain independent. No live market data, production holdout, contracts, SQLite source or trading services have been modified. A source code merge must not be reported as production deployment or scientific qualification.
