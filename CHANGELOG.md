# Changelog

Notable changes to quant-lab. Commit refs are pushed to origin/main unless noted local. Whenever
protected files change, the governance manifest (`.warden_manifest.sha256`) is re-signed by the human
principal — or by an agent under explicit in-session delegation, recorded in an approval trail.

## 2026-11-24

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