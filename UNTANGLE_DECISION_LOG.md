# Decision log — quant-lab whole-pass untangle (2026-11-19)

## Mandate & boundaries
- Principal invoked `untangle-codebase` on `/home/quant/dev/quant-lab`, then approved in chat:
  - Additions: **Comments** (remove restating comments) AND **Tests** (remove tests that can't catch a real bug; fix tests that fail without a bug). Tests pass LAST (regression net).
  - Scope: **include `src/warden/`** — behavior-preserving refactor only, within the 2700 LOC cap (start 2660), statistical/holdout/evidence gates never relaxed. Principal re-signs the manifest afterwards (human-only; agent never runs `generate-manifest --human-approve-manifest`).
- Hash-protected set (`.warden_manifest.sha256`, signed 2026-11-19T06:07Z): AGENTS.md, contracts/**, pyproject.toml, uv.lock, src/warden/**, scripts/{apply_trust_boundary.sh,check_loc_budget.py,lock_holdout_permissions.sh,preflight_trust_boundary.py}.
  - Warden edits approved → `check-worktree` will fail until re-sign. Worktree manifest may be regenerated locally for testing (integrity only, NOT authority; meta stays untouched). Everything outside the approved set stays byte-identical unless listed below.
- **strategies/baselines/ is FROZEN after registration (AGENTS.md) — never edit.** candidates/ is agent-owned.
- Never: read/open/stat `data/holdout/`; run `qualify`; sign manifest; delete registry rows; lower gates; sudo; touch `~/quant-research`.
- Registry: worktree holds a disposable consistent snapshot (sqlite backup API incl. WAL) of main's `registry/registry.db` (75 trials). Tests never append to the REAL ledger.
- Data: worktree `data/{exploratory,external,holdout_staging}` symlink to main checkout (read-only use; staging touched only by the repo's own shape tests). No holdout symlink. `.workspace/transitions/` created (gitignored runtime dir the bootstrap test expects).
- Concurrent perf worktrees (core/cs-batch/ultraperf at 8a4fd62) untouched.

## Workspace
- Worktree: `/home/quant/.devspace/worktrees/quant-lab-untangle-20261119`, branch `refactor/untangle-20261119` from main `8a4fd62`.
- Runner: `uv run --frozen …` (env synced). Scratch: `/home/quant/.hermes/profiles/omh-tui/cache/scratch/quant-lab-untangle-20261119/`.
- dcg guard: no `>` truncation or `mv` under home paths — use `tee` / new filenames / write_file.

## Baseline evidence (8a4fd62, worktree env fixed)
- **pytest: 396 passed, 8 skipped, 0 failed (~60s)** → `baseline_pytest_green.txt` (full capture running; first green run observed via tail).
- First worktree run had 15 env failures (missing `.workspace/transitions` dir + `data/holdout_staging` symlink) → fixed env, NOT code; recorded in `baseline_pytest_env_failures.txt`.
- ruff: 166 errors (113 auto-fixable) → `baseline_ruff.txt`. Mostly perf_audit/.workspace/bench/tests style; no new errors allowed in touched files.
- LOC: 2660/2700 PASS. `warden check-worktree` rc=0 in main pre-edit.
- Parity harness required for strategy rewrites: old (git HEAD) vs new module, bit-identical positions/ic_series/mean_ic on synthetic + real panels.

## Problems (step 3)

### Warden — hidden links
- W1 gate thresholds scattered as bare literals: runner.py:406-410 (min_sr 1.2/1.0, sr2>0.50, srd>0.0, dsr>=0.90, ic_t>=2.0), cli.py:256-261 (audit: 0.50, 0.0, 0.80, -0.001), qualify.py:53-62,118 (val_sr>0, REVISE sr>=1.0, 2x>=0.50, holdout>0.80, ratio>=0.50), gates.py stage-1 defaults. Same constants repeated; runbook rule 3 warns about this exact trap.
- W2 annualised Sharpe `mean/(std ddof=1 +1e-12)*sqrt(bars_per_year)` copied: runner.py:357,368,369,377-378,183-184; per-bar variant registry.py:94,101, gates.py:116-117. Two conventions, epsilon everywhere.
- W3 stage-0 proxy `0.0085*math.sqrt(expected_holding_bars)` duplicated runner.py:221,288.
- W4 panel-prep sequence copied screen vs spec: runner.py:227-230 vs 299-313 (read→symbol/asset rename→filter_target_symbols→sort→ts/assets→v_slice→execution_returns→fund_p→fund_mask). symbol→asset rename ALSO defensive-copied in 53 cs generate_positions + ts modules.
- W5 subcommand vocabulary in 3 places: policy.py ALLOWED/APPROVAL, cli.build_parser, cli.main cmds dict (cli.py:547). Adding a command = 3 edits.
- W6 _human_approved flags dict (cli.py:431) duplicates policy.APPROVAL membership.
- W7 linear-baseline gate: SQL `LIKE '%Ridge%' OR '%baseline%'` + is_linear set {ridge,linear,elasticnet,ols,lasso} (runner.py:276-284) — naming convention hidden in SQL, vocabulary duplicated.
- W8 causality.py CUT_DATES hardcoded calendar cuts (2023-06-30, 2024-07-01) — special case, silently skipped when absent (documented adversarial sampling; KEEP).
- W9 _funding_settlement_mask: crypto_perps+1h only → hour%8==0 (runner.py:61-64). Exchange convention; generalising changes non-1h semantics → KEEP under behavior mandate, document.

### Warden — needless links
- W10 `registry._connect()` (private) used directly by cli.py:134,226,294,298,371; runner.py:215,266,278; qualify.py:37,122; causality.py:42; leakage.py:211. Five modules depend on ledger internals.
- W11 gates.py:12 imports HypothesisBudgetExhaustedError from registry — stable math imports volatile ledger.
- W12 leakage.py:15 imports ResearchRegistry (type hint + _connect write) — proofs module reaches into ledger.
- W13 qualify.py:108 mid-function import of runner privates `_aligned_matrix`, `_funding_settlement_mask`.
- W14 qualify.py:104-107 re-implements execution returns with a CLOSE-price fallback runner.py:67-77 explicitly forbids; no QUOTE_SIDE handling. Divergent copy. KEEP behavior (legacy holdout panels), document; share the matrix helpers.
- W15 cli/qualify/causality import load_module_from_path via runner re-export (runner.py:38); child uses strategy_loader directly. Pass-through re-export.
- W16 cli.cmd_screen re-resolves contract+dataset after run_stage1_screen already did (cli.py:186-187) → resolve_dataset re-hashes full dataset bytes (provenance.py:53) — 2.8GB re-read per screen for 3 provenance fields.
- W17 ExperimentResult built with 30+ positional args at runner.py:348,415,420 (mostly zeros) — callers depend on field order (contracts.py:145-180, 33 fields).

### Warden — surplus / special cases
- W18 leakage.call_ml4t_compute_features (111-117): ZERO callers repo-wide. Dead adapter.
- W19 registry.compute_effective_trials_and_dsr (119-124): "backward-compatible" wrapper, only tests call it. Test-only API.
- W20 registry.load_prior_trial_returns (484-489): wrapper renaming one error string, only tests call it.
- W21 cli.generate_manifest references scripts/lock_governance_permissions.sh (72) — file does not exist; only reference repo-wide. Dead entry (manifest output unchanged: guarded by is_file).
- W22 provenance.py:61-62 duplicate `provenance not in PROVENANCES` check — first branch dead (overwritten at 65-66).
- W23 cli.main register-hypothesis special-cased inline (535-545) vs cmds dict for all others.
- W24 cli.cmd_verify pipeline triple-fallback `mod.pipeline or mod.FeaturePipeline or mod.compute` (167); af30 book:103 same. EVERY strategy module exposes `pipeline` (verified: only path_signature_common.py lacks it, and it is a helper, not a strategy). FeaturePipeline fallback would return a CLASS (broken anyway). Surplus.
- W25 runner.py:286 `strat_module = strat_module_str = …` double assignment.
- W26 runner.py:421-425 swallowed `(ValueError, sqlite3.Error, OSError): pass` on failure-recording — best-effort evidence; KEEP (documented), note.
- W27 registry.__init__ runs legacy RUN-ML-001 migration inline (193-204); constructors not read-only (runbook rule 8). Extract to named migration function called from __init__ (locality; behavior identical).
- W28 registry _columns vs _ensure_columns duplicate the str/datetime→TEXT type mapping (132,140).
- W29 leakage.compute_and_cache_features ver="0.2.0" + feature_names_json "[]" placeholders (200,214) — cache-key semantics; KEEP, note.
- W30 leakage.verify_fold_isolation expects `prediction_value` (188); candidate cs models return `pred`; baseline cs_ridge returns `prediction_value`. Verifier is exercised only with the baseline/test models → candidate `model` attributes are dead (see S2).
- W31 check_worktree boilerplate ×7 with inconsistent failure surfaces (sys.exit(2) vs return 2 vs WARDEN_RUN_FAILED sentinel). KEEP surfaces (tests pin them); note.

### Strategies — hidden links / surplus
- S1 **49 cs_* modules copy the same scaffold**: identical Ridge model class (constant-blind fit_predict_fold hash e5dc99c21944 ×49, __init__ f013846a3c6b ×55); generate_positions identical modulo variation axes (16 name-blind clusters, 53 modules incl. recovery). Axes catalogued (cs_catalog.py): feature columns (2-3), warmup (72/168/…), rebalance_every (4/24/72), top_k (3/5), alpha, min_train=10, IC std-guard on/off, predict rowwise-vs-batch, invert_factor (+kwargs aliases invert/invert_weights), fill_missing_assets on feature pivots. Change CV/IC/position convention → 49 edits, nothing shows it.
- S2 candidate `model = CSxxxRidgeModel()` + class: ZERO consumers (grep `.model` empty; verify_fold_isolation tests use baselines CSRidgeModel which returns prediction_value). generate_positions inlines its own Ridge loop (double copy of fit logic per module). Surplus ~25 LOC ×49.
- S3 symbol→asset defensive rename copied in every cs/ts generate_positions (also runner W4). Convention callers must know.
- S4 **33 ts_* modules carry a dead `Strategy(ml4t.backtest.Strategy)` subclass** whose on_data only does `bar_count += 1` (identical hash 6318cc7360c9 ×66/2). No instantiation/import anywhere in src/scripts/tests (grep). "Lifecycle-v1 portable" speculative generality. ~12 LOC ×33.
- S5 capped_waterfall_weights copied verbatim across 10 ts_s* modules (hash 9dc967fe0616); carver_daily has its own sizing (cash_vol_positions — different algorithm, keep).
- S6 carver per-module `sys.path.insert(0, str(Path(__file__).parent))` bootstrap (s01:23 etc.) — convention every sibling-importing module must repeat. Retire by making strategy_loader.load_module_from_path add the module's dir to sys.path (one place).
- S7 air_template_20261029-{111618,112141,114744}.py byte-identical (whole-module hash). Referenced only by their own experiments/active/air_*.yaml specs; NO registry hypotheses/trials pin them (verified sqlite). 102247 is an older different variant (keep).
- S8 ts_s07/s08 and ts_s12/s14 generate_positions pairwise identical (name-blind) — 2×2 modules; low leverage, leave.
- S9 cs_residual_reversal_1h / cs_lgbm_1h / xs_weekly_momentum_1h / s183_* have genuinely different scaffolds — leave standalone.
- S10 recovery/ v2 modules (4) — check dup vs parents in round 3.
- AF30 logic exists at two frequencies (carver_daily/sXX 1d vs ts_sXX_1h) with different sizing/overlays — NOT a verbatim copy; parameterising across families would change behavior → leave; share only the verbatim pieces (S5).

### Scripts / tests
- (pending lane A sa-0-4cbfb9b9 tooling survey, lane B sa-0-e3609d75 test classification — append findings on arrival)
- Known: scripts/run_hourly_pipeline.sh:8-9 hardcodes REPO_DIR=/home/quant/dev/quant-lab, :15 LOCK_FILE=/tmp/quant_lab_hourly.lock (memory #578: env overrides QUANT_LAB_REPO_DIR/QUANT_LAB_HOURLY_LOCK_FILE once needed; absent today).
- tests/test_bootstrap.py:60 asserts gitignored runtime dir .workspace/transitions exists → fails on any fresh checkout (test fails without a bug; fix in tests round).
- tests/test_perf_core_fixes.py:195,217 monkeypatch cli.check_worktree → suite does not depend on real-worktree hash state.

## Fixes (step 4) — each above the cluster it retires

- **FIX-C gates owns the gate vocabulary.** gates.py gains: threshold constants (MIN_SR_CS=1.2, MIN_SR_RULES=1.0, SR2_FLOOR=0.50, SRD_FLOOR=0.0, DSR_FLOOR=0.90, IC_T_FLOOR=2.0, AUDIT_DSR_FLOOR=0.80, HOLDOUT_SR_FLOOR=0.80, HOLDOUT_RATIO_FLOOR=0.50, TRIM_MEAN_FLOOR=-0.001, REVISE_SR_FLOOR=1.0, STAGE0_VOL_PROXY=0.0085), `per_bar_sharpe(r)`, `annualised_sharpe(r, bars_per_year)`, `stage0_holding_volatility(hyp)`, `evaluate_stage3_statistical(...)` (the runner conjunct verbatim), `audit_check_predicates(trial, contract, trimmed_mean, has_causal_proof)` (cli c-dict verbatim), `holdout_promoted(h_sr, ratio)` + qualify verdict predicates. runner/cli/qualify import them; literals deleted. HypothesisBudgetExhaustedError moves to gates.py; registry imports it from gates (direction fixed). Values identical → behavior identical.
  - retires W1, W2, W3, W7 (vocabulary half), W11.
- **FIX-D registry exposes public reads; callers stop touching _connect.** New methods: hypothesis_row(hyp_id)->dict|None, trial_row(run_id)->dict|None, counts()->(n_hyp,n_trials), recent_trials(limit)->list[dict], candidate_run_ids()->list[str], feature_proof(contract_id)->bool, quarantine_reason(run_id)->str|None, record_feature_set(...). cli/runner/qualify/causality/leakage migrate; exact error strings preserved. leakage drops ResearchRegistry runtime import (TYPE_CHECKING only).
  - retires W10, W12, part of W27 (migration named function), W28 (one type-mapping helper).
- **FIX-E one command table.** policy.py owns COMMANDS spec (name, handler-name, approval flag, worktree-required); cli.build_parser + dispatch + _human_approved derive from it; register-hypothesis becomes cmd_register_hypothesis. Parser insertion order & all output surfaces preserved. Dead lock_governance_permissions.sh entry removed.
  - retires W5, W6, W21, W23.
- **FIX-F runner prep + result factories.** `prepare_panel(repo_root, dataset, params)` (rename/filter/sort/axes/v_slice) + `simulation_inputs(panel, contract, ts, assets)` (ret_p/fund_p/fund_mask) shared by screen+spec; ExperimentResult keyword factories in contracts.py (stage1_rejected/stress/failed) replace 30-positional builds; Sharpe calls → gates; double-assign removed; load_module_from_path re-export removed (cli/qualify/causality import strategy_loader); cmd_screen gets provenance from the screen path without re-hashing (run_stage1_screen attaches dataset resolution to report.metrics? NO — metrics are pinned; instead cli reads the recorded selection attempt? simplest behavior-preserving: resolve_dataset memoised by (path,sha) within process? KEEP re-resolve for now if risky — decide in implementation; W16 may stay documented).
  - retires W4 (runner half), W17, W25, W15, W2 (call sites).
- **FIX-G leakage dead code out.** call_ml4t_compute_features removed; feature_sets write via registry.record_feature_set.
  - retires W18, W12 (write half).
- **FIX-H provenance dead branch removed.** retires W22.
- **FIX-I qualify shares simulation helpers.** `_aligned_matrix`→`aligned_matrix`, `_funding_settlement_mask`→`funding_settlement_mask` public in runner (all callers updated incl. tests/perf_audit); qualify imports them at top; its close-fallback return logic KEPT verbatim (behavior mandate) with a comment naming the divergence; module-fallback chain extracted to one helper.
  - retires W13, documents W14.
- **FIX-J strategy_loader adds module dir to sys.path** (before exec, position 0, like carver bootstrap). carver per-module bootstraps removed; carver_common docstring updated.
  - retires S6, enables S1/S5 shared modules without per-module boilerplate.
- **FIX-A one cross-sectional scaffold.** New `strategies/candidates/cs_common.py`: `cs_positions(panel_df, contract, *, pipeline, feature_cols, warmup, alpha=10.0, rebalance_every=4, top_k=3, min_train=10, ic_guard=False, predict_rowwise=False, invert_factor=False, invert_aliases=False, fill_missing_assets=False, **kwargs)` — extracted VERBATIM from the reference cluster, parameterised ONLY along catalogued axes (operation order, epsilons, random_state=42 preserved). Each cs module keeps its Pipeline.compute + constants + `pipeline`; generate_positions becomes a thin binding; dead model class/attr removed (S2). Scripted AST transform + per-module bit-identical parity harness (old HEAD vs new, synthetic 400×4 panel + real crypto_perps_1h_v2 slice). Modules failing parity stay untouched and are reported. baselines/ FROZEN — untouched.
  - retires S1, S2, S3 (cs half: rename lives once in cs_positions; runner still renames too — harmless, keep).
- **FIX-B ts shared pieces.** `strategies/candidates/ts_common.py` with capped_waterfall_weights (verbatim); 10 modules import it; dead Strategy subclasses + ml4t.backtest imports removed from 33 modules (after final consumer grep); generate_positions loops untouched.
  - retires S4, S5.
- **FIX-K air templates consolidated.** Keep air_template_20261029-114744.py (latest identical); repoint air_20261029-111618.yaml + air_20261029-112141.yaml strategy_module to it; delete the two duplicate modules. No registry pins (verified). 102247 kept.
  - retires S7.
- **FIX-L cli/book pipeline discovery simplified** to `getattr(mod, "pipeline", None)` + same error message. retires W24.
- **FIX-M scripts** (pending lane A): hourly pipeline env overrides with current values as defaults; salvage family dedup; dead root/perf_audit code per findings.
- **FIX-N comments**: remove restating comments in all touched files + repo sweep (keep external-convention comments: funding 8h, sys.path rationale, prefix-invariance sampling caveat, WAL/registry traps).
- **FIX-O tests (LAST)**: retarget test-only-wrapper tests to real APIs (compute_selection_report / selection_history) then remove wrappers W19/W20; remove cannot-fail/mirroring tests per lane B; fix bootstrap transitions test (fails without a bug); keep the regression net green vs baseline 396/8.

## Rounds
- **Round 1 (warden, behavior-preserving):** FIX-C/D/E(partial)/F(partial)/G/H/I/J landed.
  gates.py owns gate vocabulary (MIN_SR, SR2/SRD/DSR/IC_T floors, AUDIT_DSR/TRIM_MEAN, HOLDOUT floors, REVISE_SR, STAGE0_VOL_PROXY) + per_bar_sharpe/annualised_sharpe/stage0_holding_volatility; HypothesisBudgetExhaustedError moved gates←registry (direction fixed). registry gained public read surface (table_count, hypothesis_row, trial_row, recent_trials, candidate_run_ids, feature_proof, quarantine_reason, lineage_root, lineage_qualification, record_feature_set, linear_baseline_count); cli/runner/qualify/causality/leakage stopped touching _connect (qualify keeps its atomic qualification write). runner prepare_panel/simulation_inputs dedup screen+spec; aligned_matrix/funding_settlement_mask made public (all callers updated incl tests/perf_audit). load_module_from_path re-export removed; ~20 callers import strategy_loader directly. strategy_loader adds module dir to sys.path (retires carver bootstrap convention). provenance dead branch removed; cli dead lock_governance ref removed; register-hypothesis → cmd function; cli pipeline discovery simplified to mod.pipeline. leakage dead call_ml4t_compute_features removed.
  - Verify: **pytest 396 passed / 8 skipped (== baseline)**; ruff src/warden clean; LOC **2699/2700**; check-worktree rc=0 (worktree manifest regenerated locally = integrity only, meta untouched; principal re-sign still required).
  - Deferred for LOC budget (documented, not silently dropped): W5/W6 full command-table (policy vocabulary still 2-place), W17 ExperimentResult positional factories, W19/W20 test-only wrappers (remove in tests round), W27 migration extraction, W28 type-mapping helper.
- **FIX-A (cs scaffold):** new strategies/candidates/cs_common.py `cs_positions` (verbatim reference, parameterised axes: feature_cols, warmup, alpha, rebalance_every, top_k, min_train, ic_guard, predict_rowwise, invert_factor/invert_aliases, fill_missing_assets, exclude_symbol[str|tuple], btc_eligible). Scripted AST transform of **51/51** Ridge cs modules → thin wrapper preserving exact public signature; dead per-module RidgeModel class+attr removed. **Every module proven bit-identical** (positions/ic_series/mean_ic) old(HEAD) vs new on 2 synthetic panels + real CRYPTO_PERPS_1H_V2 panel (transform_parity.py, reports 1-3). cs_lgbm_1h + cs_residual_reversal_1h left standalone (genuinely different scaffolds). baselines/ FROZEN, untouched.
  - Verify: **pytest 396 passed / 8 skipped (== baseline)**.
  - Special cases preserved as parameters (not silently forked): BTCUSDT / (BTCUSDT,BTCUSDT_PERP) benchmark exclusion, btc-substring eligible book with subset IC. These are requirements the original design never named; now explicit in one place.

- **FIX-B/K/L (strategies cleanup):** ts_transform removed 33 dead `ml4t.backtest.Strategy` subclasses (on_data only incremented a bar counter; zero consumers) + deduped 20 identical `capped_waterfall_weights` onto new ts_common.py; 2 xs modules keep their genuinely-instantiated Strategy class. All parity-gated bit-identical. Deleted 2 byte-identical air_template duplicates (111618/112141), repointed their specs to 114744 (no registry pins). Book + cli pipeline discovery simplified to `mod.pipeline`; book `_get_module` now delegates to strategy_loader (fixes standalone sibling-import failure / test-order dependence).
  - Verify: pytest 396/8; test_af30_book passes standalone (4/4).
- **Scripts (lane A):** deleted dead run_vertical_slice.py (776-line legacy prototype, no executor, would unlink root registry.db) + create_pr.sh references. run_hourly_pipeline.sh REPO_DIR/LOCK_FILE now env-overridable (QUANT_LAB_REPO_DIR/QUANT_LAB_HOURLY_LOCK_FILE) with unchanged defaults. KEPT live: mcp_warden (external MCP server), perf_audit (active perf-branch benchmark harness), adapters (UX installers). Near-duplicate salvage/xs-diag helpers (0.82–0.946 sim, divergent) and build_*_closure identical helpers left as documented low-leverage/test-coupled candidates.
- **FIX-O tests (lane B, 49 files classified):** removed 3 library-tautology tests/asserts (F09 polars pct_change math, F10 sha256!='', F11 np.roll multiset); fixed weakened asserts (F12 'A and B or A' precedence → real 'pre-lagged' notes check; F13 no-op .replace tamper removed + overclaiming test renamed; F14 dead 'if n<=100: pass' branch). Fixed test_bootstrap canonical-dir test that failed on fresh checkout (dropped gitignored .workspace/transitions from required dirs). Removed test-only registry wrappers compute_effective_trials_and_dsr + load_prior_trial_returns (W19/W20); retargeted test_registry/test_redteam/test_hyp_2026_002 to compute_selection_report/selection_history (same behavior pinned). KEPT the data/ledger/config SHA pins (they fail on data/integrity regression — a real net, not inert) and deliberate perf/orchestration call-count pins.
  - Verify: **pytest 395 passed / 8 skipped** (396 baseline − 1 removed tautology test); ruff clean on touched files; LOC **2685/2700**; check-worktree rc=0.

## Open questions / kept special cases
- Round-end consistency check (parent, read-only): no missed call sites. Repo-wide grep confirms zero remaining references to `_aligned_matrix`/`_funding_settlement_mask`, `runner.load_module_from_path`, `compute_effective_trials_and_dsr`, `load_prior_trial_returns`, `call_ml4t_compute_features`, or the `FeaturePipeline` getattr fallback. Every loader of transformed cs/ts modules bootstraps sys.path (strategy_loader; book `_get_module` delegates to it). Package-style `strategies.candidates.*` imports touch only untransformed/frozen modules (qc_dual_thrust, alg_oversold_dax, baselines.cs_ridge) — none import cs_common/ts_common, so no sibling-import breakage. Custom test loaders target xs_weekly_momentum (standalone) and closure scripts (stdlib/warden only) — unaffected.
- cs_common self-assessment: ~13 parameters is high, but each is a genuine preserved variation axis across 51 modules (parity-proven); the interface hides ~120 lines of CV/IC/book logic behind a 4-line wrapper per module (deep relative to callers). A future deepening could collapse ic_guard/predict_rowwise/btc_eligible/exclude_symbol into named book/IC variant enums, but that is redesign churn beyond the behavior-preserving mandate — deferred, not a defect.
- W8 CUT_DATES, W9 funding mask, W14 qualify close-fallback, W26 failure swallow, W29 cache placeholders: KEPT (behavior mandate / system still needs them).
- W16 double dataset hash in cmd_screen: perf smell; only fix if behavior-identical path exists.
- gates.evaluate_stage1_carry_rv uses linregress pvalue as "ADF p" — statistical smell, NOT structural; gate semantics frozen; note only.
- carry_rv/meta_overlay stage-1 gates exist + are tested but runner only wires rules_ts/cross_sectional_ml: KEPT — they are specified family gates (AGENTS.md funnel) not yet reachable for those families, not dead surplus; removing them would break the gate tests and the documented design.

## Lane A full tooling findings — triage (delivered deleg_8757eebd; parent-verified)
ALREADY FIXED in earlier rounds: H1 (hourly REPO_DIR/LOCK_FILE env overrides), H8/N1 (runner privates → public aligned_matrix/funding_settlement_mask, all callers updated), H7 partial (funding mask public + builder docstring), S3 (run_vertical_slice.py deleted).
CONFIRMED DEAD SURPLUS — parent grep found ZERO callers (py/sh/yaml/md), safe to delete once comments lane C finishes editing scripts/:
- S1 scripts/salvage_crypto_majors.py + scripts/salvage_crypto_top100.py (7-line pass-through wrappers presetting one arg of salvage_crypto_universe; zero callers).
- S4 create_pr.sh (one-shot initial-PR automation for long-merged branch feat/initial-architecture-and-plan; zero refs; I already stripped its run_vertical_slice refs — delete whole file).
- S5 scripts/migrate_issue4_contract_hashes.py (one-shot post-Issue-4 spec_hash/provenance migration, already applied; zero refs).
- S7 scripts/audit_af30_fidelity.py (returns pre-written per-strategy conclusions from a 25-branch if/elif; only 4 live substring greps; zero callers) — pending broader json/txt evidence-citation check.
- S8 scripts/audit_multi_asset_coverage.py (computed coverage table + hardcoded narrative bar counts that can drift; zero callers) — pending broader check.
BORDERLINE KEEP: S6 scripts/generate_campaign_report.py (frozen hardcoded narrative, but cited as precedent in .workspace/plans/carver_daily_rebuild/plan.md) — keep, documented.
HIDDEN LINKS worth a behavior-preserving fix (defer until lane C done; scripts are test-coupled per H19):
- H2 WARDEN_RUN_COMPLETE/WARDEN_RUN_FAILED sentinel strings emitted/regex-waited/parsed/mocked in cli.py:198-206, herdr_dispatch.sh:41, mcp_warden/server.py:423, test_herdr_and_handoff.py:36 with no shared constant → add gates/cli constant + import.
- H5 Stage-1 gate values PF 1.15 / p 0.05 / TiM 0.05 re-hardcoded in build_af30_step3_closure.py:91-95, build_fx_stage3_closure.py:124-189, run_af30_screens.py:81, run_carver_daily_campaign.py:159 → import from gates (values already centralized there).
- H6/N3 raw registry SQL copies in 9+ scripts (preflight_thesis.py:52-64 explicitly copies ResearchRegistry.prior_search_floor) → registry now has a public read surface; replace copies where behavior-identical.
- H3/H4/H9 salvage normalize/COLUMNS triplication + closure twins (_screen/render/main 1.000, _stage3 0.977) + xs-diag math copies (0.82–0.98): real duplication but test-coupled (H19 loads scripts as APIs) and divergent; low-leverage, document as candidates rather than risk behavior drift.
NEEDLESS LINK / BUG (external tooling, optional): H13/N6 mcp_warden/server.py SUBCOMMANDS drift — verify-causality takes positional+--json but CLI requires --run-id (cli.py:473) so that tool can never succeed; 'continue' max_pos=1 with root=None rejected by build_command:251-253. Genuine bugs in a live external MCP server; fixing = behavior change (making broken tools work). Out of core scope; NOTE for principal, do not silently change.
KEPT (live, not surplus): perf_audit/ (active perf-branch benchmark harness; its 10 frozen old/*.py copies are bit-identity ORACLES that must equal git HEAD — never delete), adapters/ (optional UX but test_agent_compatibility.py pins templates/installer), scripts/mcp_warden (external MCP server, .workspace/state/exec_audit.jsonl shows live bwrap use).
APPLIED (this turn): `git rm -f` S1 salvage_crypto_majors.py + salvage_crypto_top100.py, S4 create_pr.sh, S5 migrate_issue4_contract_hashes.py, S7 audit_af30_fidelity.py, S8 audit_multi_asset_coverage.py. Broader all-file-type grep confirmed only .ruff_cache self-references (no real callers or evidence citations). S6 generate_campaign_report.py KEPT (plan precedent). Cumulative deletions now 9 files. These are zero-caller + non-manifest-protected, so removal cannot affect the suite; final full-suite verification deferred until comments lane C + verification lane D complete (avoid concurrent pytest on the shared registry snapshot).

## Lane C (comments) + Lane D (verification) outcome — deleg_467d6338
- Lane C FAILED at the mutation step (content_policy_blocked HTTP 400 after an output-contract notice hijacked its final turn) but PERSISTED its classification to scratch/remove_set.json (163 restating comments / 73 files) + comments_inventory.json (604 comments). Parent applied the removal set itself via a tokenize-guarded script (text-drift guard, full-line delete vs trailing code-preserving strip): **161 comments removed across 72 files** + 1 trailing shell comment (herdr_dispatch.sh TIMEOUT) manually = 162. Skipped: migrate_issue4_contract_hashes.py (already deleted). External-convention/provenance comments kept (funding 8h, sys.path rationale, prefix-invariance caveat, WAL/registry traps, HYP IDs, Carver book pages, shebangs).
- Lane D COMPLETED (read-only). Clean areas confirmed: gates.py threshold vocabulary (no gate literals outside gates except synthetic.py:29 data-generator noise), provenance dead branch gone, registry public read surface, cs_common/ts_common deep + parity-verified. Remaining findings triaged below.

## FIX-W2 + FIX-J completion (parent, behavior-preserving, parity-proven)
- W2: diagnostics.py inlined annualised Sharpe 3x (active_sharpe:47, benchmark_net_sharpe:63, information_ratio:64) despite gates.annualised_sharpe → replaced all 3 with gates.annualised_sharpe; dropped now-unused `import math`. **Parity: 1200 random samples (n=2..5000), 0 bit-mismatches** vs old inline formula. carver standalone suite 42/42.
- FIX-J: removed 17 redundant `sys.path.insert(0, ...parent)` bootstraps from carver_daily/s*.py (strategy_loader now owns the bootstrap); ruff --fix cleaned 51 unused sys/Path imports + import order; carver_common.py docstring updated to the centralized convention. s183_reference/s183_composite KEEP their insert (they import carver_common from the carver_daily/ SUBDIR, which the loader's own-dir bootstrap does not cover). carver_daily suite passes STANDALONE (42/42) — no sys.path-pollution dependence.

## FINAL VERIFICATION (post all rounds)
- **pytest 395 passed / 8 skipped** (baseline 396/8 − 1 removed library-tautology test F09). NOTE: the tampering test test_worktree_tampering_aborts_with_exit_code_2 requires an integrity-clean baseline; it failed once when pytest ran BEFORE the worktree manifest was regenerated after the diagnostics.py edit — re-running after `generate_manifest` gave 395/8. Always regenerate the worktree manifest before the final suite when protected files changed.
- **LOC src/warden 2684 / 2700** (16 headroom). **check-worktree rc=0** (manifest regenerated locally = integrity only; .meta.json signature untouched; principal re-sign still required).
- ruff src/warden+strategies+scripts: 47 pre-existing style errors (baseline repo-wide was 166); no NEW code errors introduced (comment removal is code-neutral; carver unused imports auto-fixed). RUF046 xs_weekly_momentum:106 is pre-existing.
- Diff: ~150 files changed, ~640 insertions / ~9900 deletions; 9 files deleted; 2 new shared modules (cs_common.py, ts_common.py).

## Lane D remaining findings — disposition (NOT silently fixed; rationale recorded)
EVIDENCE-INTEGRITY (behavior change in protected governance layer → principal decision, out of behavior-preserving mandate):
- D30 (HIGH) book af30_handcrafted_book_1h.py loads its 20 constituents at runtime from path strings; codeid.py module-level-import closure hashing does NOT capture runtime-loaded siblings → editing a constituent changes book behavior without changing the book's recorded strategy_code_sha256. Same class: D29 s183 imports carver_daily/carver_common.py via subdir sys.path insert; codeid resolves siblings only in the importer's own dir. Fixing requires changing code-closure hashing semantics, which would invalidate existing registry evidence pins → DO NOT silently change; flag for principal.
DEFERRED TRANSFORM CANDIDATES (behavior-preserving in principle, parity-gated method available, not attempted this pass):
- ATR trailing-stop sub-loops byte-identical within ts clusters (6 modules hash 9dfdfe719b, 5 hash 877bdf0263, ...): extract a shared apply_trailing_atr_stop into ts_common via the same parity-gated AST transform used for cs_common. ts generate_positions loops are heterogeneous around the shared stop block, so this is a further dedicated pass.
- recovery/cs_*_v2.py (4 modules) keep full pre-FIX-A scaffold copies + dead RidgeModel class/attr; NO active code refs (only historical registry selection_attempts); they live in a SUBDIR so `from cs_common import` would need a path adjustment. Dedup onto cs_common is possible but low-leverage (unwired recovery artifacts).
KEPT (matches original behavior / stable convention; changing = behavior drift):
- D20 cs_common **kwargs silently ignores unknown kwargs + invert_aliases param: the original per-module wrappers also accepted/ignored **kwargs; fail-loud would be a behavior change. D19 cs_common preserves the original modules' purged-CV split logic verbatim (parity-proven) rather than gates.purged_walk_forward_splits' separate-span design — switching would change folds. D17 strategy_loader stem-named sys.modules insertion has a latent collision if two dirs share a module stem (pre-existing loader design, not introduced here).
- D10 bar-level profit-factor convention gains/(|losses|+1e-12) copied in diagnostics/runner/gates (low severity, stable gating arithmetic — centralizing risks epsilon drift in a promotion metric). H2 WARDEN_RUN_COMPLETE/FAILED sentinel literal shared across cli/herdr_dispatch.sh/mcp_warden/test (cross-language protocol string; python-side constant would only partially dedup). H5 stage-1 PF/p/TiM values re-stated in campaign scripts' logic+prose (report/campaign layer, test-coupled per H19). H6/N3 raw registry SQL copies in scripts (registry now has a public read surface; replace where behavior-identical — left as candidates to avoid drift in ledger reads). Hardcoded /home/quant/dev/quant/ml4t-data store paths in build_*/salvage_* scripts (data-lake location convention; env-overridable like the hourly pipeline if principal wants).
- H13/N6 mcp_warden SUBCOMMANDS drift (verify-causality positional vs CLI --run-id; continue max_pos/root contradiction) = genuine bugs in a LIVE external MCP server; fixing makes broken tools work = behavior change, out of core scope → flagged for principal, not silently changed.

## Round 2 — lane D FULL findings delivered (deleg_467d6338 task 2); clean behavior-preserving fixes APPLIED + verified
Stale findings (already fixed in round 1, lane D read pre-fix tree): F9 diagnostics Sharpe (W2 done), F27 carver bootstraps (FIX-J done), F37 one-shot scripts (deleted).
APPLIED this round (all parity/behavior-preserving):
- F2: removed dead evaluate_stage1_carry_rv + evaluate_stage1_meta_overlay (gates.py) — parent grep confirmed ZERO callers incl tests (corrects round-1 keep-rationale that wrongly assumed tests pin them); dropped now-unused scipy.stats import. Unwired funnel branches = surplus.
- F1: PER_HYPOTHESIS_CAP moved to gates.py vocabulary; registry imports it (was literal 3 in gates + constant in registry); evaluate_stage0 uses the constant. Direction registry→gates preserved, no cycle.
- F11: lineage_root re-implemented _lineage's parent-walk WITHOUT the seen-set cycle guard (2-cycle A→B→A hung forever). Extracted shared _root_hypothesis(conn,id) guarded walk; both _lineage and lineage_root now use it. Bug fixed + dup removed; acyclic/self-parent behavior identical.
- F3: HypothesisSpec 8-positional construction copied screen↔spec → _build_hypothesis_spec(hyp_id,contract,family,h) helper; fetch+raise+family-validation kept in place (error precedence preserved exactly).
- F5: _note_failure dataset param None at all 4 call sites → removed param, data_provenance hardcoded "unknown" (was always "unknown").
- F12: "verified_market" token centralized to contracts.MARKET; provenance + registry import it (were 3 literals).
- F13: file-sha256 helper consolidated to provenance.file_sha256; cli.compute_file_sha256 removed (2 callers repointed), causality inline copy repointed.
- F14: Stage1Report.to_json + ExperimentResult.to_json removed (zero callers; canonical_json still used by spec_hash).
- F16: contracts.py docstring dropped stale "adapted from run_vertical_slice.py" (file deleted).
- F7: cli verify error message corrected "FeaturePipeline or compute() method" → "pipeline attribute" (discovery checks mod.pipeline only); test pins exit code not message.
- F22/F23/F24: removed dead CSLGBMModel class + module-level model attr (cs_lgbm generate_positions uses local LGBMRegressor), dead XSMomentumStrategy/XSCarryStrategy + CrossSectional* aliases (ts_s19/s20), dead RSIVWAPStrategy (rsi_ema_vwap) — all zero instantiations (corrects round-1 "genuinely-instantiated" claim); modules live via generate_positions; unused Strategy/Any imports ruff-cleaned.
- F25: two remaining verbatim capped_waterfall_weights variant pairs deduped onto ts_common (capped_waterfall_weights_clamp for s19/s20 cap=0.35; capped_waterfall_weights_feasibility for breakout_20/ewmac_multi target_gross clamp), imported under the original name. PARITY: 0 bit-mismatches vs HEAD across n=0/zero/negative/random arrays × cap{0.2,0.35,0.5} × gross{0.5,1,2}. s04 zero-fallback variant genuinely different, kept.
- FINAL VERIFICATION (supersedes round-1 numbers): **pytest 395 passed / 8 skipped**; **src/warden 2658 / 2700 LOC** (42 headroom); **check-worktree rc=0** (manifest regenerated before suite; meta.json untouched). ruff F401/I001 clean on all touched files.

## Lane D remaining findings — NOT applied (rationale)
FLAG FOR PRINCIPAL (behavior/evidence-integrity changes in protected or live external surfaces, outside behavior-preserving mandate):
- F18 (HIGH) qualify.py:85-88 silent fallback to default baseline strategy when trial's recorded strategy_module missing → qualification attests to code that never ran; should fail closed. Changing it alters human-only gate semantics.
- F32 (HIGH) salvage_crypto.py:184-190 raw INSERT OR REPLACE INTO contracts bypasses registry.register_contract immutability guard (deliberate "Fix 1" bypass); public API would refuse re-registration → behavior change.
- F29/F30 (HIGH) codeid closure hashing misses runtime-loaded siblings (book's 20 constituents, s183's subdir carver_common) → editing them changes behavior without changing recorded strategy_code_sha256. Fixing changes governance hashing, invalidates existing evidence pins.
- F35 run_spec_session/_hourly shell parse stage results from printed text whitespace-sensitively; format change fails OPEN (FAILED_STAGE='' archives failed specs as processed). Robust parsing = behavior change.
- F6 cli.main() eagerly constructs ExperimentRunner (opens/migrates registry.db) for every subcommand incl check-worktree/generate-manifest; lazy construction removes a side effect.
- F8 cli/causality/qualify panel-prep copies skip runner.prepare_panel's symbol→asset rename; a symbol-column panel passes screen but breaks verify/causality/qualify. Unifying = behavior change for that edge input (latent bug).
- F31 book LAST_BOOK_STATS mutable module global is an unnamed call-order diagnostic surface (second call overwrites first run's evidence); returning stats instead changes the function contract.
DEFERRED TRANSFORM CANDIDATES (behavior-preserving in principle, parity-gated method proven, not attempted — effort/diminishing returns):
- F26 ts rebalance+trailing-ATR-stop tail verbatim-identical within 4 clusters (6/5/3/2 modules) → extract shared stop/rebalance helper into ts_common (further dedicated pass; ts loops heterogeneous around the shared tail).
- F28 symbol→asset defensive rename copied in 84 candidate/book files (largest copy cluster by count, low leverage; runner.prepare_panel + cs_common own the convention).
- F21 recovery/cs_*_v2.py (4) keep full pre-FIX-A scaffolds + dead RidgeModel; unwired (only historical registry selection_attempts), subdir sibling-import path differs from cs_common.
KEPT (matches original behavior / stable convention; changing = drift):
- F19/F20 cs_common purged-CV span representation + invert_aliases/**kwargs silent swallow: original wrappers behaved identically; fail-loud / separate-span switch would change folds or reject currently-accepted kwargs.
- F4 linear-baseline vocabulary in runner is_linear model-type set + registry SQL LIKE '%Ridge%'/'%baseline%': two different mechanisms (current model_type gate vs historical name-based count); full unification not clean without behavior risk.
- F10 bar-level profit-factor gains/(|losses|+1e-12) in diagnostics/runner/gates: stable gating arithmetic, centralizing risks epsilon drift in a promotion metric.
- F17 strategy_loader stem-named sys.modules insertion + permanent sys.path insert: latent same-stem collision (candidates/ vs perf_audit/*/old/), pre-existing loader design, not introduced here.
- F33 scripts raw sqlite reads (registry now has public read surface; replace where behavior-identical — left to avoid ledger-read drift). F34 ml4t-data lake path hardcoded in 4 build/salvage scripts (env-overridable like hourly if principal wants; defaults unchanged). F36 one-run report scripts pinned to RUN-HYP091-001/002 with stale runner.py line refs. F38 build_fx_majors script-level sys.path bootstrap to import sibling salvage_multi_asset (loader doesn't cover script entrypoints).

## Manifest re-sign — principal-delegated, executed + verified
- Principal instruction (verbatim, chat): "I approve you sign on my behave". Delegation saved to OMH (mem_c9aef56d0e37b6ed); follows the same-session ultraperf precedent (perf_audit/core/APPROVAL.md).
- Authorization trail written BEFORE signing: UNTANGLE_APPROVAL.md (worktree root) — instruction, scope, pre-read hashes, command, result, semantics note.
- Pre-read: stale meta attested pre-untangle manifest 1b67e7d2… (06:07:02Z); locally regenerated unsigned manifest 3e0f4132…; check-worktree rc=0; WARDEN_NON_INTERACTIVE + WARDEN_HUMAN_SIGNING_KEY unset.
- Anomaly: `env -u … uv run --frozen warden generate-manifest --human-approve-manifest` returned rc=0 WITHOUT executing (no stdout/audit/meta change). Cause isolated: any `env -u`-prefixed invocation silently no-ops in this terminal (probe: `env -u … warden status` also rc=0, no output/audit); plain invocations work. Signed via plain direct dispatch `main(['generate-manifest','--human-approve-manifest'])` — same code path, audited.
- Result: rc=0, mode=interactive, approver=human-operator, approved_at=2026-11-19T09:27:03+00:00, meta.manifest_sha256 == sha256sum(.warden_manifest.sha256) = 3e0f4132…; audit entry recorded; check-worktree rc=0 after. Tree remains uncommitted for review; meta.json + UNTANGLE_APPROVAL.md are part of the diff.
