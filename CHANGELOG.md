# Changelog

Notable changes to quant-lab. Commit refs are pushed to origin/main unless noted local. Whenever
protected files change, the governance manifest (`.warden_manifest.sha256`) is re-signed by the human
principal — or by an agent under explicit in-session delegation, recorded in an approval trail.

## 2026-11-27

### Unified contract schema and shared selection debt (#113, spec #109 lane F)

- `EvaluationContract.policy` is now an explicit field instead of #94's `InitVar` carrier. It is
  persisted in an additive `contracts.policy` TEXT column, survives `asdict()`/`replace()`/pickle
  natively, and is excluded from `spec_hash_payload()`/`to_dict()` so all 36 legacy v1 digests and
  every v2 digest are byte-identical (verified against the pinned table in `tests/test_p2_t08.py`).
  A projection can no longer collapse a v2 contract into a v1 one, which was the DSR-reset path
  #109 names.
- `register_new_contract(..., venue_policy=|repo_root=)` makes the signed venue policy the sole
  authority for a new contract's `selection_group_id`: an unmapped `(asset_class, cadence)` domain
  fails closed with the domain named (`SelectionGroupResolutionError`), and a declaration that
  contradicts the policy is refused. `register_contract` records the group for every contract,
  including a policy-mapped legacy one (`contract_governance.group_source` = `declared` | `policy`).
- `map_selection_groups(...)` maps pre-existing contracts onto their policy group as an explicit,
  auditable step (additive `INSERT OR IGNORE` only; unmapped domains are reported, never guessed),
  and `selection_group_debt(contract)` / `recorded_policy(contract_id)` expose the persisted count
  and ledger-side governance block. A legacy contract's own scope and `asset_class:frequency` floor
  are unchanged — the mapping widens the group's debt, it never narrows a hurdle.
- Registry schema `user_version` 3: two additive columns (`contracts.policy`,
  `contract_governance.group_source`), no row rewrite, no new trigger, no changed table DDL. The
  down-path is data-only: a v2 build refuses a v3 registry, so rollback is `PRAGMA user_version=2`
  with both columns left in place and ignored.
- Library-only: `src/warden/cli.py` and the live registry are untouched (lane A/J own the CLI
  wiring and the manifest re-sign).

## 2026-11-25

### P2 research refactor (#79, #94–#106)

- Warden owns panel loading and shares one evaluation kernel across verification and execution. Run-bound evidence ties causality checks to the inputs and strategy code used by the evaluator.
- Contract governance is tracked separately from the frozen evaluation identity. Typed strategy parameters reject unknown keys, and neighbor stress varies eligible numeric parameters rather than silently reporting stability for an empty grid.
- Holdout qualification supports prefix warmup and explicit refit policy. Validation returns are timestamped and aligned before reserving a run, and related contracts share selection-group DSR accounting.
- Reporting and preflight use read-only registry access. Dataset tools add reproducible builds, PIT crypto and funding inputs, NY-close FX and pinned composite adapters, plus an exploratory fence and signed-origin admission for v2 market datasets.
- The software merge does not activate replacement datasets, touch the production holdout, or archive legacy builders. Those steps stay open under [#106](https://github.com/hzminhzz/quant-lab/issues/106). See [What changed in P2](docs/P2-REFACTOR.md).

### T13 carry mechanism + Check 1 holding gate (#99)
- `carry_rv` Stage 1 now requires the strategy's own `causal_carry_outputs`: an active run that
  declares none raises `CarryMechanismRequiredError` **before** the screen receipt, the selection
  attempt, or the trial slot is written (`screen` and `backtest` both resolve the series before the
  ledger moves). The equal-weight panel proxy survives only under `allow_legacy_replay` and is
  recorded as `carry_series_source="panel_proxy"` with `carry_mechanism_policy`. Every carry trial
  records the policy that authorized its series, so a replay is never mistaken for an active screen.
- Audit Check 1 now gates the measured **holding** cadence as well as the decision cadence: a hold
  that diverges from the horizon the hypothesis declared (``expected_holding_bars``) fails without a
  valid declared alignment, and `AUDIT_<run>.md` prints the measured decision/holding numbers. Only
  *completed* holding epochs are judged; a position still open at the last bar is right-censored and
  reported as such. A declaration now has to name a reason **and** a cadence that agrees with the
  tape, so a bare narrative cannot waive a divergence. Check 4 (fold proof) and the linear-first
  entry guards are unchanged.
- The shipped carry test strategy (ticket 09) migrated from the panel proxy to its own pair spread +
  funding net carry; no shipped strategy declares `carry_rv` today, so no module needed new economics.

### T20 contract lifecycle, exploratory fence & signed-origin admission (#106, software only)
- Added `contracts/lifecycle/contract_lifecycle.json` + `src/warden/lifecycle.py`: an external,
  read-only lifecycle document (no writer exists) that closes the 10 `DEPRECATED_UNEXECUTABLE`
  and 3 `LEGACY_SURVIVORSHIP_BIASED` legacy contracts to **new** hypotheses, in both
  `register-hypothesis` and `advance`'s auto-registration. A corrupt/ambiguous document fails
  closed; `status`, `spec-status`, `verify-causality` and `audit-check` never import it, so
  historical trials read back unchanged and all 36 production contract `spec_hash()` values are
  byte-identical to the frozen base.
- Added `warden data fence` (`src/warden_evidence/exploratory_fence.py`): exits 0 only when every
  Parquet under `data/exploratory` is owned by exactly one dataset manifest whose digest it
  matches and whose contract's `holdout_start` is strictly after its last bar. Read-only; the
  Parquet reader is injected from `warden.market_data` so the AST boundary is unchanged.
- Wired the previously orphaned `warden_evidence.data_admission` into `provenance.resolve_dataset`:
  a **v2** market dataset now needs a verified HMAC origin attestation
  (`contracts/attestations/<ID>.json`, operator-held `WARDEN_ADMISSION_HMAC_KEY`) to be market
  eligible; v1 contracts report `legacy_grandfathered` and keep their historical eligibility.
- The `#105` extensions no longer move a pre-existing dataset identity: `canonical_payload()` prunes
  an absent `seal_source` and an absent per-input `sha256`, so the T17 fixture hash is unchanged.
- Replacement rehearsal fixtures under `tests/fixtures/p2_t20/` (PIT crypto broad 1h, NY-close daily
  FX, Syncster CME ES/NQ 15m composite) declare the inherited `selection_group_id`; a recorded-trial
  rehearsal proves the v2 contract inherits the legacy DSR floor. Nothing is installed under
  `contracts/`, no dataset is built, sealed or archived: those are host steps in
  `docs/T20-CUTOVER-RUNBOOK.md`.

### T12 typed strategy parameters & genuine numeric neighbors (#98)
- Added `src/warden/params_model.py`: `ParamsModel` (Pydantic, `extra="forbid"`, frozen),
  `resolve_params_model`, `validate_strategy_params`, `classify_numeric_neighbors` and
  `evaluate_neighbor_requirement`. Spec parameters are validated against a strategy's
  module-level `params_model` before a screen slot or trial is reserved; only the declared
  field subset is validated so runner-injected keys (`target_symbols`) still flow through.
- Closed the empty-`param_grid` neighbor-stability bypass: `CANDIDATE_FOR_QUALIFICATION`
  now requires **≥2 distinct, genuine numeric** neighbors whenever the declaration has a
  numeric field (effective baseline = model defaults + spec parameters) or the spec supplies
  numeric values without a model. Duplicates, baseline-restating entries and non-numeric
  edits are still executed and recorded as selection attempts, but never count; the
  diagnostics carry `neighbor_grid`, `neighbor_requirement` and, for a zero-numeric-field
  model, `neighbor_exemption="parameter_free"`.
- `strategies/baselines/causal_{ridge,ipca,linear_portfolio}_1h.py` and
  `strategies/candidates/causal_rules_drawdown_template.py` now declare typed models and
  validate through them; `.workspace/templates/experiment.yaml` shows the ±15% grid form.
- Stage-3 sign-safe stability math (`gates.evaluate_neighbor_stability`) is unchanged; the
  gate only feeds it distinct evaluated configurations.
- `tests/test_p2_t12.py` pins typed refusal (zero trials consumed), the empty/omitted/
  degenerate/duplicate REVISE outcomes, the parameter-free exemption, effective-default
  baselines, and reference-funnel evidence for both reference templates.

## 2026-11-24

### Ticket 01 host holdout containment, dataset manifest pinning & principal-delegated manifest re-sign (#87; `be5cfe1`, local `main`)
- Under explicit in-session principal operator authorization (`"I authorize you to act as my operator for #87, including running sudo and carrying out the privileged host containment... Use the host's existing approved privilege and signing mechanisms"`, `"/implement Approve you fix all on my hehalf, can remove what unecessary, contineue toil ticket 1 pass"`, `"I want you to make sure it won't happen again"`, `"Fix all"`):
  - Sealed all 18 staging-only Parquets into `data/holdout/` (`root:trader`, `0640`; 29 total sealed files, POSIX ACLs stripped), deleted `data/holdout_staging/`, archived 286 post-`2025-07-01` + 2 uncontracted broad crypto files out of `data/exploratory/` to `/var/backups/quant-lab-exploratory-archive-20261124/` (`root:trader`, `0750`/`0640`), provisioned dedicated unprivileged `agent` (`uid=1002`), locked post-cutoff `ml4t-data` partitions/files to `quant:trader`, and installed `/usr/local/sbin/enforce_ml4t_holdout_fence.py` (`5.2s` idempotent runtime, `20 * * * *` root cron with `flock -n`, `timeout 60s`, `nice -n 19`, `ionice -c3`).
  - Pinned `holdout_path`, `holdout_sha256`, `holdout_sealed_by`, and `holdout_pinned_by` (`hashlib.sha256` without reading price/return statistics) across 24 `contracts/datasets/*.json` manifests alongside 9 previously pinned manifests. (`CME_ES_15M_SYNCSTER_V1`, `CME_NQ_15M_SYNCSTER_V1`, and `FX_MAJORS_1D_V2` intentionally have no built holdout Parquet per `scripts/prepare_syncster_cme.py` and `scripts/build_fx_majors_1d_v2.py`.)
  - Executed `uv run --frozen warden generate-manifest --human-approve-manifest` (`WARDEN_NON_INTERACTIVE` and `WARDEN_HUMAN_SIGNING_KEY` unset on host, matching `origin/main` `mode=interactive`, `approver=human-operator`), producing `manifest_sha256=b1899904b0f8c9d637b3b47e7e1c11df28419d92408a83f932c7193500efb31d`; `uv run --frozen warden check-worktree` (`rc=0`).

### CLI lazy-loading & parallel pytest speedup (`0351039`, `11ade10`; local `main`)
- Lazy-loaded ML4T modules in `src/warden/cli.py`, `contracts.py`, `gates.py`, `registry.py`, and
  `src/warden_ops/checkpoint.py` to avoid heavyweight import overhead on fast CLI invocations.
- Enabled parallel test execution via `pytest-xdist` (`pyproject.toml` `addopts = "-n 4 --dist worksteal"`)
  and capped per-worker thread pools in `tests/conftest.py` (`POLARS_MAX_THREADS=2`,
  `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`) to eliminate CPU thread oversubscription.

### Architectural overhaul — Tickets 01–11 (parent #71, issues #72–#82; `d0569f6..ec463ce`; local `main`)
- **Causal inversion & templates (`3d6c7a1`, #79):** Froze legacy `generate_positions()` for read-only
  historical replay only; enforced `CausalModelStrategy` and `CausalRulesStrategy` on `ACTIVE`
  hypotheses in `src/warden/protocols.py`; shipped `strategies/baselines/causal_ridge_1h.py` and
  `strategies/candidates/causal_rules_drawdown_template.py`.
- **Execution engine cutover (`f5bb928`, #77; `2699659`, #78):** Added `ml4t.backtest.Engine` parity
  harness and runtime profiler (`scripts/shadow_engine_parity.py`), then cut over Stage 2, Stage 3,
  and `qualify` to `ml4t.backtest.Engine` (`src/warden/engine_sim.py`). Replaced Stage-1 bar-level
  profit factor, added analytical `1.5x` cost stress (`stress_1p5_cost_sharpe`) alongside `2.0x`
  Engine re-simulation (`cost_2x`), and exported backtest artifacts under
  `registry/backtest_results/<simulation_epoch>/<contract>/<run_id>`.
- **All four strategy families wired (`d2f9a44`, #80):** Implemented Stage-1 and Stage-3 gates for
  `carry_rv` (`evaluate_stage1_carry_rv`) and `meta_overlay` (`apply_causal_meta_overlay`,
  `evaluate_stage1_meta_overlay`), completing `rules_ts`, `cross_sectional_ml`, `carry_rv`, and
  `meta_overlay`.
- **`ml4t-models` baselines & deep-model guardrails (`f8d6f2d`, #81):** Added
  `strategies/baselines/causal_linear_portfolio_1h.py` and `strategies/baselines/causal_ipca_1h.py`,
  and enforced linear-baseline cost-breakeven hurdles before executing `model_tier == "deep"` models.
- **Typed queue coordinator & MCP stdio alignment (`ec463ce`, #82):** Replaced bash `grep`/`cut` queue
  parsing in `scripts/run_hourly_pipeline.sh` with `scripts/hourly_queue_coordinator.py` and aligned
  the reviewed stdio MCP adapter (`scripts/mcp_warden/adapter.py`).
- **AST boundaries & dead-surface pruning (`1f41d0d`, #73):** Retired `scripts/check_loc_budget.py`
  (LOC cap) in favor of repo-wide (`ml4t.live`) and `strategies/` (`sqlite3`, `subprocess`,
  HTTP/socket, `ml4t.backtest.Engine`) AST import boundary checks in `warden check-worktree`
  (`warden_ops.integrity.check_ast_boundaries`); centralized gates and removed
  `scripts/mcp_warden/server.py` and `src/warden/causality.py`.
- **Integrity, data admission & statistical hardening (`8d98417`, #72; `ed89957`, #74; `20670f3`, #75; `828cece`; `9640674`, #76):**
  Bound `code_closure`, pre-reservation `target_symbols` guard, empirical Stage-0 holding volatility
  (`gates.empirical_holding_volatility`), and `scripts/inspect_env.py` updates; enforced `FeedSpec`,
  `OHLCVValidator`, `SessionAssigner`, and atomic salvage (`src/warden_evidence/market_validation.py`);
  enforced `trimmed_sharpe`, frequency-aware fold purging, sign-safe neighbor stability
  (`gates.evaluate_neighbor_stability`), structured `failed_gates` on `REVISE`, non-ratchet DSR,
  `simulation_epoch` isolation, `source_published_at` cutoffs (`RETROSPECTIVE_REPLICATION_ONLY`), and
  shadow `ml4t-diagnostic` calibration.
- **Repository cleanup & agent onboarding (`2026-11-24`, local `main`):** Removed obsolete
  pre-implementation docs (`PULL_REQUEST.md`, `IMPLEMENTATION_PLAN.md`, `AGENT_EXECUTION.md`),
  superseded `perf_audit/` scratchpad, and stray 0-byte root `registry.db`; archived historical design,
  audit, and approval docs under `docs/archive/`; synced `README.md`, `AGENTS.md`, `AGENT_RUNBOOK.md`,
  `docs/WORKFLOW.md`, `docs/BLOCKER-RECOVERY.md`, `STAGE3_CAMPAIGN_PROMPT.md`, and
  `.workspace/templates/experiment.yaml`; resolved `ruff` lint errors in `tests/`.
- **Post-review reconciliation & principal-approved manifest re-sign (`2026-11-24`, local `main`):**
  Under explicit in-session principal delegation (`"Fix them, I approve you on my hehalf to fix the
  agents.md"` / `"I want to fix"` covering `AGENTS.md` L46 & L70–110 in `377d69d` and `f2cdbfa`),
  re-signed `.warden_manifest.sha256` + `.warden_manifest.sha256.meta.json` via
  `warden generate-manifest --human-approve-manifest`, corrected `TRIMMED_SHARPE_FLOOR = 0.0`,
  `carry_rv` (`adf_p < 0.05`), and `RUNNABLE_HYPOTHESIS_STATUSES` in `docs/WORKFLOW.md`, and moved
  unreferenced dated audit docs (`THESIS-RECOVERY-AUDIT-20261101.md`,
  `STATE-MACHINE-HARDENING-BASELINE-20261107.md`) to `docs/archive/`.
- **Immutable specs + single idempotent `warden advance` (`2026-11-24`, local `main`, principal-approved `"Approve option A"`):**
  - Replaced mutable queue-folder moves (`experiments/queue/`, `.workspace/hypotheses/queue/`,
    `scripts/hourly_queue_coordinator.py`) with immutable specs in `experiments/active/` and
    `.workspace/hypotheses/` driven by `uv run --frozen warden advance <spec>|--pending --json`.
  - Enriched `ResearchRegistry.spec_status` (`hypothesis_registered`, `hypothesis_status`,
    `hypothesis_trials_used`, `audit_verdict`, `receipt_state`, `STAGE3_CANDIDATE_UNAUDITED`) and
    made `register_hypothesis` idempotent after `record_hypothesis_module()` binds `strategy_module`.
  - Added `"advance"` to `warden.policy.ALLOWED`, `cli.MUTATING_COMMANDS`, and
    `scripts/mcp_warden/adapter.py`; pointed `scripts/run_hourly_pipeline.sh` to
    `warden advance --pending` under `flock`; added `cache/features/*.proof.json` to `.gitignore`;
    removed `status: QUEUED` and added `data_cutoff` / `evidence_type` to `.workspace/templates/hypothesis.md`.
- Verify: `uv run --frozen warden check-worktree` (`rc=0`) and `uv run --frozen pytest -q` (full suite green on local `main`).

## 2026-11-19

### Origin reconciliation merge (`7b67a04`, consistency `215205b`; PUSHED to origin/main)
- Local main had diverged from origin/main (ahead 70 / behind 6). Reconciled NON-destructively by
  merging origin/main — the six remote PRs (#36/#43/#48/#69: durable evidence ledger, safe recovery,
  failed-strategy memory, Syncster CME data, carver daily decisions). Both merge parents preserved
  (`4edbc10` local refactor + `b654352` origin); no force-push. Local and remote main now synced.
- Adopted remote's production architecture where stricter/newer: durable attempt receipts
  (`reserve_screen` → `record_trial` FINALIZED) with `spec-status`/`attempt-status` crash
  reconciliation; `execution_returns` with no close fallback; the `warden_evidence` package; the
  mutation lease; the `research_outcome_memory` superset; the principal-approved 3,000 LOC budget.
- Preserved from the local refactor: `codeid.code_closure` + `CODE_DEPENDENCIES`, the strategy dedup
  (`cs_common`/`ts_common`/`recovery_common`), the sibling-import bootstrap grafted into remote's
  `runner.load_module_from_path`, and an explicit `strategy_code_sha256` binding grafted into remote
  `qualify`.
- Superseded: the local one-process / cache-first / screen→backtest-reuse perf optimizations are
  replaced by remote's separate-stage state machine; their pinning tests (`test_perf_core_fixes`) and
  the upstream-removed `test_target_symbols_calendar` were deleted.
- Reconciled seams: `HypothesisBudgetExhaustedError` and `PER_HYPOTHESIS_CAP` unified to registry
  ownership (gates imports them); remote holdout/redteam fixtures adapted to the code binding.
- Verify: `uv run --frozen pytest -q` (observed 493 passed, 8 skipped, 1 xfailed) ·
  `uv run --frozen warden check-worktree` (rc=0); manifest re-signed under standing delegation.

### Documentation contract (docs merge `2a8d0fc`; AGENTS.md region principal-approved)
- `AGENTS.md` gains a marker-delimited agent-maintained region (`omh:agent-instructions`): shared
  strategy scaffolds (`cs_common`/`ts_common`), the `CODE_DEPENDENCIES` declaration rule, evidence
  binding (fail-closed qualify, immutable contracts), unwired-family status, verified commands, the
  manifest generated-file map, and costed pitfalls. Hand-written sections are byte-for-byte unchanged.
  The write required the repo protection guard's explicit approval (granted by the principal); the
  manifest was re-signed under standing delegation (`approver=human-operator`).
- `CHANGELOG.md` added; `AGENT_RUNBOOK.md` rule 9 (full code closure + qualify binding + contract API);
  `docs/WORKFLOW.md` Stage-1 unwired families, holdout code binding, and symbol refs replacing stale
  line numbers.

### Strategy dedup backlog (commit on `refactor/dedup-backlog-20261119`)
- Trailing-ATR-stop overlays: the verbatim-identical stop loops across 20 `rules_ts` modules now call
  three shared `ts_common` helpers (`trailing_atr_stop_symmetric` / `_long_only` / `_symmetric_obs`),
  selected by exact-body clustering. Bit-identical on synthetic + real pinned panels.
- Legacy asset-column normalization: 38 top-level candidate modules call
  `ts_common.normalize_asset_column` instead of an inline `symbol`→`asset` guard. Bit-identical on
  asset- and symbol-column panels.
- Deferred (grounded): the cross-directory `symbol`→`asset` guards in `carver_daily/`, `books/`,
  `baselines/` stay inline — centralizing them would require reintroducing the per-module `sys.path`
  bootstraps the untangle removed. (`recovery/cs_*_v2` were deduped in the follow-up pass below.)
- Verify: `uv run --frozen pytest -q` (403 passed, 8 skipped); `uv run --frozen warden check-worktree`
  (rc=0; strategies are unprotected, so the manifest signature is unchanged).

### Recovery scaffold dedup (final pre-production pass)
- The four `recovery/cs_*_v2` historical variants now call `recovery/recovery_common.py::recovery_positions`,
  which owns their older walk-forward scaffold once (`contract.val_start` split, per-factor warmup,
  purged folds, top-k/bottom-k dollar-neutral book). The genuine structural differences between the
  copies are explicit named axes — `k_floor_one`, `ic_std_guard` (1e-8), `fill_missing_feature_assets` —
  never silent forks; each module keeps its own feature pipeline and public signature.
- Behavior bit-identical (`np.array_equal` on positions/ic_series/mean_ic) on synthetic + real pinned
  panels for all four. These are unwired historical artifacts: behavior is preserved, but their code
  identity (`strategy_code_sha256`) changes — re-run to re-bind before any future qualification.
- Verify: `uv run --frozen pytest -q` (403 passed, 8 skipped); `uv run --frozen warden check-worktree`
  (rc=0; strategies unprotected, manifest signature unchanged).

### Evidence integrity (merge `35335de`, commit `c081fba`)
- Code identity is now complete: `warden.codeid.code_closure` walks all imports (module-level and
  function-local) and unions a module's `CODE_DEPENDENCIES` declaration of runtime-loaded paths. The
  AF30 book declares its constituents; `s183_composite` declares its subdir helper. Editing any covered
  file now flips the recorded `strategy_code_sha256`.
- `warden qualify` no longer substitutes a baseline strategy: it fails closed on a missing/absent trial
  module and binds qualification to the trial's recorded `strategy_code_sha256` (the recomputed closure
  must match). Legacy trials without a code binding must be re-run to bind.
- `scripts/salvage_crypto.py` registers contracts through `ResearchRegistry.register_contract`
  (idempotent for an identical contract, refuses a changed one) instead of raw `INSERT OR REPLACE`,
  restoring contract immutability.
- New `tests/test_evidence_integrity.py` pins closure coverage, the `CODE_DEPENDENCIES` enforcement,
  contract immutability, and fail-closed qualification.
- Semantics change approved by the principal; design + authorization trail in `EVIDENCE_INTEGRITY.md`.
- Verify: `uv run --frozen pytest -q` · `uv run --frozen warden check-worktree` ·
  `uv run --frozen python scripts/check_loc_budget.py`.

### Whole-pass untangle (merge `4bb411c`, commit `c228019`)
- Behavior-preserving refactor: cross-sectional Ridge modules share
  `strategies/candidates/cs_common.py::cs_positions`; rules_ts sizing shares
  `strategies/candidates/ts_common.py::capped_waterfall_weights` (standard / `_clamp` / `_feasibility`
  variants). Every rewrite was proven bit-identical to HEAD on a parity harness before acceptance.
- Warden core: gate vocabulary + trial-budget cap centralized in `gates.py`; registry gained a public
  read surface; the lineage parent-walk cycle guard was fixed; file-sha256 / provenance-token /
  `HypothesisSpec` construction deduplicated; dead gates, methods and `Strategy` subclasses removed.
- Removed dead files (`run_vertical_slice.py`, `create_pr.sh`, one-shot audit/migration scripts,
  duplicate air templates); removed restating comments; tests cleaned (library tautologies removed,
  weakened asserts fixed, fresh-checkout bootstrap fix).
- Authorization + full triage: `UNTANGLE_APPROVAL.md`, `UNTANGLE_DECISION_LOG.md`.
- Verify: same commands as above.