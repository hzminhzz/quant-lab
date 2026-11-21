# Quant-Lab Autonomous Execution Prompts (Tickets 01–06)

## Prompt for Ticket 01

    Ticket: Urgent Qualification Binding (code_closure), Pre-Reservation target_symbols Guard & ml4t-* Capability Probe

Text

Apply the `implement` skill to Ticket 01 (fetch the issue from GitHub, state its title, verify no blockers).

ALTITUDE (The Real Goal):
Make it structurally impossible for `quant-lab` to run a trial that is born unqualifiable (missing `strategy_code_sha256`), impossible for a symbol typo or unrequested symbol to burn one of a hypothesis's 3 trial slots or distort benchmarks, and impossible for an economically dead 1m/5m scalper to clear Stage 0 on a fake hardcoded volatility constant.

DEPTH (Raw Material, Specific Methods & Outside Falsifiers):
- Inspect the raw code first—do not trust docs/changelogs: read `src/warden/runner.py`, `src/warden/qualify.py`, `src/warden/codeid.py`, `src/warden/cli.py`, `src/warden/registry.py`, `src/warden/gates.py`, `src/warden/diagnostics.py`, `scripts/xs_panel_diagnostics.py`, `scripts/inspect_env.py`, and `uv.lock`.
- Probe the live `uv` environment directly in Python using `inspect.signature` and `__version__` across `ml4t.specs`, `ml4t.data`, `ml4t.engineer`, `ml4t.diagnostic`, `ml4t.backtest`, and `ml4t.models` (record exact FeedSpec v1 vs v2 fields, `DataFeed` options, `compute_ic_hac_stats`, `deflated_sharpe_ratio_from_statistics`, `WalkForwardCV`) into `.workspace/env_manifest.json` (or probe artifact) so Tickets 03–10 never guess APIs.
- Replace the hardcoded `0.0085 * sqrt(expected_holding_bars)` Stage-0 proxy with empirical holding-period realized volatility computed across the exploratory dataset's active `target_symbols` (e.g. lower-quartile/median horizon return std), cached/fast (< 100ms).
- Fix `runner.run_spec` so every trial records `strategy_code_sha256 = code_closure(...)[0]`, fix `cmd_verify` and `causal_proof_for_run` to bind `code_closure` instead of single-file `read_bytes()`, restore `target_symbols` filtering/validation before `register_trial_start` (and restrict `compute_trade_metrics` equal-weight benchmark to active/filtered assets), and fix `scripts/xs_panel_diagnostics.py` imports.
- OUTSIDE FALSIFIERS: On an isolated SQLite registry snapshot (SQLite `.backup()` API—never `cp` a WAL DB, never mutate live `registry/registry.db`, never touch `data/holdout/`):
  1. Run a synthetic strategy through `register-hypothesis -> verify -> backtest -> audit-check -> qualify` (using a `tmp_path` holdout override), then edit a helper in `CODE_DEPENDENCIES` and prove both `verify-causality` and `qualify` fail closed.
  2. Replay a `RUN-HYP068-TYPO-PROBE` (`target_symbols=["NOT_REAL"]`) and prove `trials_used` stays 0.
  3. Run a `1m` low-volatility fixture against Stage 0 and prove it fails `GateFalsificationError` even though `0.0085 * sqrt(H)` would have passed.

RANGE (Cross-Disciplinary Checks — Verbs, Not Roleplay):
- Security/Red-Team: Attempt to bypass `spec_causality` by mutating only a sibling helper or `CODE_DEPENDENCIES` module without touching the main strategy file; verify detection.
- Execution Microstructure: Check that filtering `target_symbols` removes union-calendar dead bars before computing benchmark IR and turnover.

WORKFLOW:
Call `tdd` at the Warden CLI/funnel seam before editing. Run `uv run --frozen ruff check` / available typechecks and targeted `pytest` files on every change, regenerate dev integrity manifest if testing `check-worktree` (recording note if human signing is required before live timer use), run `uv run --frozen pytest -q` once at the end, run `code-review`, and commit to your branch.

---

## Prompt for Ticket 02

    Ticket: Retire Directory LOC Cap for AST Boundary Rules & Prune Dead Execution Surfaces

Text

Apply the `implement` skill to Ticket 02 (fetch the issue, state its title, verify Ticket 01 is merged).

ALTITUDE (The Real Goal):
Eliminate Goodhart's Law from governance: stop engineers and agents from splitting files into uncounted sibling packages or cramming 400-character lines to dodge a single-directory line counter, while ensuring zero unintegrated, orphaned, or divergent execution surfaces remain reachable.

DEPTH (Raw Material, Specific Methods & Outside Falsifiers):
- Inspect `scripts/check_loc_budget.py`, `src/warden_ops/integrity.py`, `src/warden/causality.py`, `scripts/mcp_warden/server.py`, `src/warden/cli.py`, `src/warden/runner.py`, `src/warden/leakage.py`, `src/warden/gates.py`, `src/warden_evidence/feature_cache.py`, and ALL callers across `tests/` and `scripts/` via repo-wide search before deleting any symbol.
- Keep existing stable `src/warden_ops/` and `src/warden_evidence/` paths (avoid churn), replace the `WARDEN_MAX_LOC` directory cap with an AST-based architectural boundary gate inside `check-worktree` / integrity checks:
  1. Ban `ml4t.live` / `ml4t-live` imports anywhere in the repository.
  2. Ban `sqlite3`, `subprocess`, network clients (`httpx`, `requests`, `urllib`, `socket`), and direct `ml4t.backtest.Engine` imports inside `strategies/`.
- Centralize all Stage 0–4 threshold literals (`MIN_SR`, `SR2_FLOOR`, `SRD_FLOOR`, `DSR_FLOOR`, `AUDIT_DSR_FLOOR`, `HOLDOUT_SR_FLOOR`, `HOLDOUT_RATIO_FLOOR`, `REVISE_SR_FLOOR`) into `gates.py` and import them everywhere (`runner.py`, `cli.py`, `qualify.py`).
- Delete dead surfaces: `src/warden/causality.py` (calls nonexistent `registry.record_causality`), `scripts/mcp_warden/server.py` (drifted/unintegrated HTTP server), duplicate `load_module_from_path` in `runner.py`, unused checkpoint constants in `cli.py`, dead `call_ml4t_compute_features` and `executable_label_span` in `leakage.py`, and the broken `FeaturePipeline` class fallback at `cli.py:138`. Update `feature_cache.py` to read `ml4t.engineer.__version__` dynamically.
- OUTSIDE FALSIFIERS: Plant temporary AST-violation test fixtures (including function-local `import sqlite3` and `from ml4t.live import ...` in a temp strategy module) and prove `check-worktree` fails closed; verify zero remaining occurrences of deleted symbols or duplicate gate literals repo-wide.

RANGE (Cross-Disciplinary Checks):
- Compiler/Static-Analysis Engineering: Test `import x`, `from x import y`, aliased imports, and function-local imports inside AST traversal so trivial syntax tricks cannot evade boundary rules.

WORKFLOW:
Call `tdd` at the `check-worktree` / CLI integrity seam first. Run `uv run --frozen ruff check` / typechecks and targeted `pytest` continuously, run `uv run --frozen pytest -q` once at the end (updating any tests pinned solely to deleted dead code or the old LOC cap with explicit rationale), call `code-review`, and commit to your branch.

---

## Prompt for Ticket 03

    Ticket: Enforce FeedSpec, OHLCVValidator & SessionAssigner Contracts at Data Salvage and Preflight

Text

Apply the `implement` skill to Ticket 03 (fetch the issue, state its title, verify Ticket 01 is merged and check `env_manifest.json` for installed `ml4t-specs` & `ml4t-data` APIs).

ALTITUDE (The Real Goal):
Turn the market-data boundary between `ml4t-data` and `quant-lab` into a fail-closed type/invariant compiler so corrupt OHLC bars, missing `bid`/`ask` quotes on `QUOTE_SIDE` contracts, mixed exchange calendars, and silent `funding_rate=0.0` financing placeholders can never enter research panels or burn trial slots.

DEPTH (Raw Material, Specific Methods & Outside Falsifiers):
- Inspect `src/warden/contracts.py`, `src/warden/provenance.py`, `scripts/preflight_thesis.py`, `scripts/salvage_crypto.py`, `scripts/salvage_multi_asset.py`, `scripts/salvage_crypto_perps.py`, `scripts/build_fx_majors_1d_v2.py`, `scripts/prepare_syncster_cme.py`, `contracts/*.yaml`, and `contracts/datasets/*.json`.
- Inspect installed `ml4t.specs.FeedSpec`, `ml4t.data.validation` (`OHLCVValidator`, `ValidationRulePresets`, `AnomalyManager`), and `ml4t.data.sessions` (`SessionAssigner`, `SessionCompleter`) signatures in the `uv` environment; use only fields supported by the installed version (checking `time_contract_version=2` availability first).
- Wire `FeedSpec` validation into `EvaluationContract` and `preflight_thesis.py` (failing closed before trial start if `QUOTE_SIDE` lacks `bid`/`ask` or timestamps/columns fail `FeedSpec`).
- Consolidate duplicate `normalize_panel` checks in salvage scripts onto `OHLCVValidator` (with `crypto_rules()`, `forex_rules()`, `futures_rules()`, etc.) + `AnomalyManager` + `SessionAssigner`, enforce synchronized contract YAML & dataset manifest writes (`data_provenance` explicit), and store unknown CFD/FX financing/funding as explicit `null` rather than `0.0` (never forward-filling prices on gaps).
- OUTSIDE FALSIFIERS: Build synthetic Parquet fixtures reproducing (a) the `fx_majors_1d` V1 LSE level-shift anomaly, (b) `FX_MAJORS_1D_V1` `QUOTE_SIDE` missing `bid`/`ask`, and (c) multi-exchange `INDICES_1H_V2` timestamps (`ES`, `DAX`, `HK33`). Prove salvage/preflight rejects (a) and (b) before touching `registry.db` (`trials_used == 0`), assigns exact `session_date` on (c), and leaves unknown financing `null`.

RANGE (Cross-Disciplinary Checks):
- Compiler/Invariant Engineering: Represent missing quotes/financing and malformed bars as unrepresentable inputs rather than runtime warnings.
- Financial Data Forensics: Verify `SessionAssigner` preserves UTC/exchange boundaries across DST transitions and holidays without fabricating missing bars.

WORKFLOW:
Call `tdd` at the data salvage & contract preflight seam first. Run `uv run --frozen ruff check` / typechecks and targeted tests continuously, run `uv run --frozen pytest -q` at the end, invoke `code-review`, and commit to your branch.

---

## Prompt for Ticket 04

    Ticket: Fix Outlier Sensitivity (trimmed_sharpe), Frequency-Aware Fold Purging, Sign-Safe Neighbor Stability & REVISE Attribution

Text

Apply the `implement` skill to Ticket 04 (fetch the issue, state its title, verify Ticket 02 is merged).

ALTITUDE (The Real Goal):
Close every statistical escape hatch in Stage 3 and Stage 4 so outlier-driven strategies cannot sneak past audit checks, `1d`/`1m` splits never mis-purge, losing parameter grids never report passing stability, and agents stop guessing why a run got `REVISE`.

DEPTH (Raw Material, Specific Methods & Outside Falsifiers):
- Inspect `src/warden/cli.py` (`cmd_audit_check`), `src/warden/leakage.py` (`verify_fold_isolation`), `src/warden/runner.py` (`run_spec`), `src/warden/gates.py`, and `.workspace/hypotheses/AUDIT_*.md` + `.workspace/memory/LESSONS.md` (`HYP-2026-125`, `HYP-2026-003B`).
- Replace `trimmed_ok = mean(r <= p95) > -0.001` with `trimmed_sharpe > 0` (`annualised_sharpe(r[r <= p95], bars_per_year) > 0.0`) inside `audit-check` check 2 (`2_outlier_sensitivity`), recording `trimmed_sharpe` in the audit JSON and markdown report.
- Replace hardcoded `timedelta(hours=required_gap)` in `verify_fold_isolation` with bar-frequency-aware or `WalkForwardCV`-aligned temporal gap checks across `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, and `1d` contracts.
- Replace `min(n_sr) / (max(n_sr) + 1e-8)` with sign-safe neighbor stability (`max(n_sr) > 0` and `min(n_sr) >= 0.70 * baseline_net_sharpe`), explicitly mark `neighbor_tested = False` (never `1.0`) when `param_grid` is empty, and emit a deterministic `failed_gates` array in diagnostics on every `REVISE` trial.
- OUTSIDE FALSIFIERS:
  1. Feed a synthetic return stream modeled on `HYP-2026-125` (positive headline Sharpe `+1.04`, top-5% bars carrying all PnL so trimmed Sharpe is negative) into `audit-check` and prove `2_outlier_sensitivity` fails even if headline Net Sharpe > 1.0.
  2. Run `verify_fold_isolation` on a `1d` contract with a 1-day gap (`required_gap=6`) and prove it raises `LookaheadLeakageError`; run on a `5m` contract with a valid 60-bar (`5h`) gap and prove it passes.
  3. Run a `param_grid` where all neighbor Sharpes are negative (`-1.15`, `-0.96`) and prove stability fails; run an empty `param_grid` and verify `neighbor_tested=False` / no fake `1.0`.

RANGE (Cross-Disciplinary Checks):
- Statistical Forensics: Verify `trimmed_sharpe` invariance across `1m`, `1h`, and `1d` bar annualizations.
- Autonomous Agent Mechanism Design: Verify `failed_gates` strings cleanly drive `scripts/check_revision_prereg.py` / revision diagnostics without parsing prose notes.

WORKFLOW:
Call `tdd` at the Warden CLI (`audit-check`, `backtest`) seam first. Run `uv run --frozen ruff check` / typechecks and targeted tests continuously, run `uv run --frozen pytest -q` once at the end, invoke `code-review`, and commit to your branch.

---

## Prompt for Ticket 05

    Ticket: Fix DSR Non-Ratchet Accounting, Simulation-Epoch Isolation, source_published_at Cutoffs & Shadow ml4t-diagnostic Calibration

Text

Apply the `implement` skill to Ticket 05 (fetch the issue, state its title, verify Ticket 02 is merged).

ALTITUDE (The Real Goal):
Make the multiple-testing ledger mathematically honest across sessions and simulation epochs—eliminating the infinite `unvectorized` DSR ratchet, preventing `SelectionHistoryError` across matrix-to-`Engine` transitions, and blocking post-publication sources from masquerading as unseen out-of-sample evidence.

DEPTH (Raw Material, Specific Methods & Outside Falsifiers):
- Inspect `src/warden/registry.py` (`compute_selection_report`, `selection_history`, `prior_search_floor`, `update_search_floor`, schema/migrations), `src/warden/contracts.py` (`HypothesisSpec`), `src/warden/runner.py`, `scripts/rehearse_state_machine_migration.py`, and read-only `registry/registry.db` counts/floors (via SQLite `.backup()` snapshot only—never touch live DB).
- Inspect installed `ml4t.diagnostic.evaluation.stats` (`deflated_sharpe_ratio`, `deflated_sharpe_ratio_from_statistics`, `effective_number_of_trials`, `compute_pbo`, `ras_sharpe_adjustment`) and `ml4t.diagnostic.metrics.compute_ic_hac_stats` in the `uv` environment.
- Fix DSR accounting: compute `k_effective = k_dedup + UNVECTORIZED_PENALTY * unvectorized`, then apply `k_gate = max(k_effective, prior_floor)` *once* (stopping repeated `+0.50 * unvectorized` additions on every trial); record `param_grid` attempts before computing `n_attempts` and include aligned vectorized `selection_attempts` (`param_grid` + vectorized `rules_ts` screens) inside `effective_rank`.
- Add `simulation_epoch` (additive migration preserving legacy rows untouched, verified via `rehearse_state_machine_migration.py` on a snapshot!) so `selection_history` aligns raw return vectors strictly within matching `(contract_id, simulation_epoch)` while inheriting `prior_search_floor` across `asset_class:frequency`.
- Add `source_published_at` to `HypothesisSpec`; auto-tag trials where `source_published_at >= contract.val_start` as `RETROSPECTIVE_REPLICATION_ONLY` and block `CANDIDATE_FOR_QUALIFICATION`.
- Build a read-only shadow calibration script running `ml4t.diagnostic` (`compute_ic_hac_stats`, `deflated_sharpe_ratio`, `compute_pbo`, `ras_sharpe_adjustment`) over existing `registry/returns/*.parquet` to measure exact numerical differences vs legacy `gates`/`registry` math.
- OUTSIDE FALSIFIERS: On a snapshot copy of `registry.db`, run 3 consecutive trials with 20 prior screens and assert `k_eff_gate` stays flat rather than ratcheting `+10.0` per trial; insert a new `simulation_epoch` trial with different bar count and verify `prior_search_floor` inherits cleanly with zero `SelectionHistoryError`.

RANGE (Cross-Disciplinary Checks):
- Multiple-Testing Forensics & Database Engineering: Prove additive migration idempotency (`rehearse_state_machine_migration.py`: legacy table row SHA-256 unchanged across repeated runs) and document any historical vs `ml4t-diagnostic` DSR/HAC delta empirically.

WORKFLOW:
Call `tdd` at the `ResearchRegistry` / CLI seam first. Run `uv run --frozen ruff check` / typechecks and targeted tests continuously, run `uv run --frozen pytest -q` once at the end, invoke `code-review`, and commit to your branch.

---

## Prompt for Ticket 06

    Ticket: Shadow-Verify ml4t-backtest.Engine Parity & Runtime Profile Over Analytical and Historical Lineages

Text

Apply the `implement` skill to Ticket 06 (fetch the issue, state its title, verify Tickets 01 and 03 are merged).

ALTITUDE (The Real Goal):
Empirically prove where `quant-lab`'s legacy matrix loop hides cash, margin, stop-loss, funding, or calendar distortions—and measure `ml4t-backtest.Engine`'s exact accounting precision and runtime envelope on real panels—before switching production Stage 2/3 to `Engine`.

DEPTH (Raw Material, Specific Methods & Outside Falsifiers):
- Inspect `ml4t.backtest` (`Engine`, `DataFeed`, `BacktestConfig`, `TargetWeightExecutor`, `RebalanceConfig`, `ContractSpec`, `funding_df`, `StopLoss`, `TrailingStop`, `ExecutionMode.NEXT_BAR`, `ExecutionPrice.QUOTE_SIDE`), `src/warden/runner.py`, `src/warden/qualify.py`, and historical promoted/candidate specs (`RUN-HYP065-003`, `RUN-HYP066-003`, `RUN-HYP069-001`, `RUN-HYP091-001`, `RUN-HYP120-001`, `RUN-XSRR-001`).
- Build a standalone non-gating shadow verification harness (never modifying live `registry.db` or reading `data/holdout/`) with two test axes:
  1. **Analytical Round-Trip Oracles (`1e-8` precision):** Exact synthetic multi-bar ledgers covering `NEXT_BAR`, `QUOTE_SIDE` (spread crossing + bid/ask marking), 8-hour perpetual `funding_df` transfers, `StopLoss`, `TrailingStop`, `allow_leverage=False` order rejections, and `1.5x` analytical fill-derived fee scaling vs full `1.5x` `Engine` rerun.
  2. **Historical Replay & Multi-Exchange Calendar Probe:** Replay the historical runs on `data/exploratory/` (including `INDICES_1H_V2` and `MULTI_ASSET_1H_V1` with `SessionAssigner` `session_date`!). Specifically probe how `DataFeed(session_col=...)` handles partial holiday sessions across mixed US/EU/Asia calendars (`ES` open while `HK33` closed) and measure wall-clock runtime on 100-asset hourly panels (`CRYPTO_PERPS_TOP100_1H_V1`).
- OUTSIDE FALSIFIERS: Generate a structured JSON + Markdown parity report quantifying `|SR_vec - SR_engine|`, `max_gross_leverage`, `cash_deficit_bars`, `funding_delta_bps`, `holiday_session_behavior`, `1.5x_analytical_vs_engine_delta`, and `engine_wall_seconds` per contract; assert synthetic oracles pass to `1e-8`.

RANGE (Cross-Disciplinary Checks):
- Execution Microstructure & Systems Performance: Measure `Engine` peak RSS and runtime scaling vs bar count so Ticket 07's Stage-1 `Engine`-free vs Stage-2/3 `Engine` split guarantees zero 300s timeouts.

WORKFLOW:
Call `tdd` on the analytical round-trip oracle and shadow replay seam first. Run `uv run --frozen ruff check` / typechecks and targeted tests continuously, run `uv run --frozen pytest -q` once at the end, invoke `code-review`, and commit to your branch.
