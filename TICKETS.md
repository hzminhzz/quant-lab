# Quant-Lab Implementation Tickets (`TICKETS.md`)

**Document ID:** PLAN-2026-Q4-TICKETS  
**Authoritative Design Reference:** `agent_native_quant_research_rfc.md` (RFC-2026-Q4-001)  
**Compatibility Reference:** `AGENT_COMPATIBILITY.md`  
**Execution Contract:** `AGENT_EXECUTION.md`  

Each ticket below is bounded to a single cohesive module or milestone (30–90 minutes of agent work), preserves 100% of the approved RFC architecture, and is executable by **Oh My Pi (`omp`)**, **OpenCode (`opencode`)**, **Claude Code (`claude`)**, or **Codex CLI (`codex`)**.

---

## Ticket `TKT-01`: VPS Environment Audit & Clean-Room Repository Bootstrap (Day 1A)

- **Objective & Reason:** Inspect the live Linux VPS environment (OS, CPU/RAM, user/group accounts, Python/uv versions, installed coding-agent CLIs, and existing data paths) and initialize the clean-room `quant-lab` repository with a deterministic `pyproject.toml`, `uv.lock`, directory skeleton, and runtime ML4T API signature manifest (`.workspace/env_manifest.json`).
- **Dependencies & Prerequisites:** None (Root ticket).
- **Exact Implementation Scope:**
  1. Run read-only VPS hardware, user/group, CLI, and filesystem inspection commands.
  2. Create the canonical `quant-lab` directory tree (`contracts/`, `src/warden/`, `strategies/{baselines,candidates}/`, `experiments/active/`, `registry/{returns,predictions}/`, `cache/features/`, `tests/{acceptance,experiments}/`, `scripts/`, `adapters/{omp,opencode,claude,codex}/`, `.workspace/{memory,hypotheses,literature,plans,transitions}/`).
  3. Author `pyproject.toml` (Python `>=3.12,<3.15`, `polars`, `duckdb`, `pyarrow`, `numpy`, `scipy`, `scikit-learn`, `lightgbm`, `pyyaml`, `pydantic`, `ml4t-specs`, `ml4t-data`, `ml4t-engineer`, `ml4t-diagnostic`, `ml4t-backtest`, `ml4t-live`, dev: `pytest`, `ruff`) and resolve `uv.lock`.
  4. Author `src/warden/__init__.py` and minimal `src/warden/cli.py` (`--version`, `--help`).
  5. Author `scripts/inspect_env.py` to record exact package versions and inspect `ml4t.engineer.compute_features` (`calendar` parameter presence), `ml4t.diagnostic.evaluation.stats.deflated_sharpe_ratio`, and `ml4t.backtest.Engine` signatures into `.workspace/env_manifest.json`.
  6. Author `.gitignore` and `tests/test_bootstrap.py`.
- **Expected Files / Modules Created or Modified:**
  - `pyproject.toml`, `uv.lock`, `.gitignore`
  - `src/warden/__init__.py`, `src/warden/cli.py`
  - `scripts/inspect_env.py`
  - `.workspace/env_manifest.json`
  - `tests/test_bootstrap.py`
- **Explicit Out-of-Scope Work:**
  - Do NOT implement Warden contracts, registry, leakage prover, or backtest runner logic (`TKT-03` through `TKT-07`).
  - Do NOT install `ml4t/skills` or create agent adapter configs (`TKT-02`).
  - Do NOT modify or move any files inside `~/quant-research` or `/data/`.
- **Step-by-Step Implementation Instructions:**
  1. Execute read-only VPS checks: `uname -a`, `id`, `groups`, `python3 --version`, `uv --version`, `which omp opencode claude codex herdr duckdb`, and `ls -ld ~/quant-research /data /data/exploratory /data/holdout 2>/dev/null || true`.
  2. Initialize Git repo (`git init` if not already initialized) and create all directories with `.gitkeep` where empty.
  3. Write `pyproject.toml` with `[project.scripts] warden = "warden.cli:main"` and `[build-system] requires = ["hatchling"]`.
  4. Run `uv sync --dev`. *(Blocker handling: If system Python is 3.11 or 3.15, run `uv python install 3.12 && uv python pin 3.12` before `uv sync --dev`. If any `ml4t-*` package requires a specific version constraint on the VPS architecture, record the resolved version in `.workspace/env_manifest.json`.)*
  5. Write `scripts/inspect_env.py` using `importlib.metadata` and `inspect.signature` to check all core and `ml4t-*` modules and write `.workspace/env_manifest.json`.
  6. Write `tests/test_bootstrap.py` asserting that `.workspace/env_manifest.json` exists, all required directories exist, and `uv run warden --version` exits `0`.
- **Acceptance Criteria:**
  - `uv sync --dev --locked` succeeds with exit code `0`.
  - `uv run python scripts/inspect_env.py` exits `0` and writes valid JSON to `.workspace/env_manifest.json` documenting exact installed versions and whether `ml4t.engineer.compute_features` includes `calendar` in its signature.
  - `uv run warden --version` prints `warden 0.1.0`.
- **Required Automated Tests:** `uv run pytest tests/test_bootstrap.py -q` and `uv run ruff check src/ scripts/ tests/`.
- **Required Evidence & Artifacts:** Commit on `feat/tkt-01-bootstrap`, `.workspace/env_manifest.json` content pasted in completion report, and `pytest` output.
- **Definition of Done:** All acceptance tests pass, zero untracked temporary files remain, branch committed, agent stops.
- **Estimated Complexity & Important Risks:** **Low (30–45 mins).** Main risk: Python version mismatch (`3.11` or `3.15` vs. `ml4t-*` requirement of `3.12–3.14`), mitigated by `uv python pin 3.12`.

---

## Ticket `TKT-02`: Portable `AGENTS.md`, 61 `ml4t/skills` Installation & Optional Harness Adapters (Day 1B)

- **Objective & Reason:** Establish the coding-agent-independent instruction layer (`AGENTS.md`, `CLAUDE.md`, and the 61 declarative [`ml4t/skills`](https://github.com/ml4t/skills) in `.agents/skills/`) alongside optional, non-load-bearing adapter configurations in `adapters/{omp,opencode,claude,codex}/` and an idempotent installer `scripts/setup_agent_adapter.sh`.
- **Dependencies & Prerequisites:** `TKT-01`.
- **Exact Implementation Scope:**
  1. Clone or fetch [`https://github.com/ml4t/skills`](https://github.com/ml4t/skills) into `~/.ml4t-skills` (or local cache) and run `./scripts/install.sh .agents/skills --copy` to populate all 61 `ml4t-*` skills at `.agents/skills/ml4t-<name>/SKILL.md`.
  2. Author the root `AGENTS.md` encoding the harness-neutral operating rules: Four-Way Ownership Partition, Five-Stage Falsification Funnel, 3-Strike Hypothesis Budget, Linear-First Model Gate, the 6-check audit checklist from [`ml4t/agentic-ai-in-trading/CHECKLIST.md`](https://github.com/ml4t/agentic-ai-in-trading/blob/main/README.md), and exact `uv run warden` CLI contracts.
  3. Author the one-line `CLAUDE.md` (`@AGENTS.md`).
  4. Author optional adapter templates under `adapters/`:
     - `adapters/omp/.omp/config.yml`, `adapters/omp/.omp/agents/literature-scout.md`, `adapters/omp/.omp/agents/adversarial-auditor.md` (using verified Oh My Pi frontmatter: `name`, `description`, `tools`, `spawns: ""`, `blocking: true`, `structured: true`, `autoloadSkills`).
     - `adapters/opencode/opencode.json` and `adapters/opencode/.opencode/prompts/adversarial_auditor.txt` (from RFC Section 4.3).
     - `adapters/claude/settings.local.json.example`.
     - `adapters/codex/README.md`.
  5. Author `scripts/setup_agent_adapter.sh` (`--harness omp|opencode|claude|codex|all|clean`) and `tests/test_agent_compatibility.py`.
- **Expected Files / Modules Created or Modified:**
  - `AGENTS.md`, `CLAUDE.md`
  - `.agents/skills/ml4t-*/SKILL.md` (61 skill directories)
  - `adapters/omp/.omp/config.yml`, `adapters/omp/.omp/agents/literature-scout.md`, `adapters/omp/.omp/agents/adversarial-auditor.md`
  - `adapters/opencode/opencode.json`, `adapters/opencode/.opencode/prompts/adversarial_auditor.txt`
  - `adapters/claude/settings.local.json.example`, `adapters/codex/README.md`
  - `scripts/setup_agent_adapter.sh`
  - `tests/test_agent_compatibility.py`
- **Explicit Out-of-Scope Work:**
  - Do NOT make any Python module in `src/warden/` import or depend on `adapters/*`, `.omp/`, or `opencode.json`.
  - Do NOT install any skill containing executable scripts or hooks (verify that `.agents/skills/` contains only Markdown files and `.ml4t-installed` markers).
- **Step-by-Step Implementation Instructions:**
  1. Clone `https://github.com/ml4t/skills.git` to `/tmp/ml4t-skills` (or `~/.ml4t-skills` if outbound GitHub access is active on the VPS) and execute `./scripts/install.sh <repo_root>/.agents/skills --copy`. Verify 61 `ml4t-*` directories exist and zero `.sh`/`.py` executables exist inside `.agents/skills/`.
  2. Write `AGENTS.md` and `CLAUDE.md`. Ensure `AGENTS.md` uses standard Markdown and references skills by both file path (`.agents/skills/ml4t-<name>/SKILL.md`) and OMP `skill://ml4t-<name>` URI.
  3. Create the optional adapter files under `adapters/{omp,opencode,claude,codex}/` exactly as specified in `AGENT_COMPATIBILITY.md`.
  4. Write `scripts/setup_agent_adapter.sh` so `--harness omp` copies/symlinks `adapters/omp/.omp` to `.omp`, `--harness opencode` copies `adapters/opencode/opencode.json` to `./opencode.json`, and `--harness clean` removes active adapter symlinks without touching `AGENTS.md` or `.agents/skills/`.
  5. Write `tests/test_agent_compatibility.py` to validate: (a) `AGENTS.md` and `CLAUDE.md` exist; (b) `.agents/skills/` contains `ml4t-*` skills with valid YAML frontmatter (`name` and `description`) and zero executable scripts; (c) `.omp/agents/*.md` frontmatter contains valid `name`, `description`, `tools`, and `autoloadSkills`; (d) `opencode.json` is valid JSON; and (e) running `--harness clean` leaves `pytest` passing.
- **Acceptance Criteria:**
  - All 61 `ml4t-*` skills are present at `.agents/skills/ml4t-<name>/SKILL.md` (one level deep, matching OMP, Codex, and OpenCode discovery rules).
  - `find .agents/skills -type f ! -name "SKILL.md" ! -name ".ml4t-installed"` returns empty (zero executable scripts).
  - `./scripts/setup_agent_adapter.sh --harness omp` and `--harness clean` execute idempotently with exit code `0`.
- **Required Automated Tests:** `uv run pytest tests/test_agent_compatibility.py -q`.
- **Required Evidence & Artifacts:** Count of installed skills (`ls -1d .agents/skills/ml4t-* | wc -l`), output of `pytest`, and git commit on `feat/tkt-02-agent-compat`.
- **Definition of Done:** Tests pass, skills and adapters verified, committed, agent stops.
- **Estimated Complexity & Important Risks:** **Low (30–45 mins).** Risk: Nested skill directories if `ml4t/skills` is cloned directly instead of running `scripts/install.sh`; prevented by explicit depth check in `test_agent_compatibility.py`.

---

## Ticket `TKT-03`: Immutable Contracts Schema & Initial Market Contract (Day 2A)

- **Objective & Reason:** Implement the core data structures and YAML loader/validator for `EvaluationContract`, `HypothesisSpec`, `Stage1Report`, and `ExperimentResult` in `src/warden/contracts.py` (preserving Section 1 of `run_vertical_slice.py` and Section 4.4 of the RFC), and author the first frozen market contract `contracts/crypto_perps_1h_v1.yaml`.
- **Dependencies & Prerequisites:** `TKT-01`.
- **Exact Implementation Scope:**
  1. Implement `src/warden/contracts.py` containing frozen dataclasses / Pydantic models:
     - `EvaluationContract`: `contract_id`, `asset_class` (`crypto_perps`, `fx`, `indices`, `metals`), `frequency` (`5m`, `15m`, `1h`, `4h`, `1d`), `bars_per_year`, `exploratory_data_path`, `holdout_data_path`, `train_start`, `val_start`, `val_end`, `holdout_start`, `holdout_end`, `n_folds`, `label_horizon_bars`, `embargo_bars`, `execution_delay_bars` (enforced `>= 1`), `execution_mode` (`NEXT_BAR` or `QUOTE_SIDE`), `taker_fee_bps` (enforced `> 0`), `slippage_bps` (enforced `> 0`), `funding_bps_per_8h`, `min_expected_move_to_cost_ratio` (default `2.5`), `data_snapshot_sha256`, and `spec_hash()`.
     - `HypothesisSpec`: `hypothesis_id`, `contract_id`, `title`, `strategy_family` (`rules_ts`, `cross_sectional_ml`, `carry_rv`, `meta_overlay`), `economic_mechanism`, `counterparty_or_friction`, `expected_holding_bars`, `falsification_criteria`, `parent_hypothesis_id`, `source_provenance`, `llm_model_id`, and `from_markdown()` / `from_yaml()` loader.
     - `Stage1Report` and `ExperimentResult` (with JSON/dict serialization).
  2. Enforce invariant validation in `EvaluationContract.__post_init__`: reject any contract where `execution_delay_bars < 1`, `taker_fee_bps + slippage_bps <= 0`, `embargo_bars < 1`, or `val_end > holdout_start` (chronological overlap between validation and holdout).
  3. Create `contracts/crypto_perps_1h_v1.yaml` and `contracts/fx_majors_15m_v1.yaml`.
  4. Write unit tests in `tests/test_contracts.py`.
- **Expected Files / Modules Created or Modified:**
  - `src/warden/contracts.py`
  - `contracts/crypto_perps_1h_v1.yaml`
  - `contracts/fx_majors_15m_v1.yaml`
  - `tests/test_contracts.py`
- **Explicit Out-of-Scope Work:**
  - Do NOT implement SQLite storage (`TKT-05`) or backtest execution (`TKT-07`).
- **Step-by-Step Implementation Instructions:**
  1. Port and extend `EvaluationContract`, `HypothesisSpec`, and `ExperimentResult` from `run_vertical_slice.py` into `src/warden/contracts.py`.
  2. Add YAML/Markdown frontmatter parsing (`load_contract(path: Path) -> EvaluationContract` and `load_hypothesis(path: Path) -> HypothesisSpec`).
  3. Add strict domain validation raising `ContractValidationError` if: (a) costs are zero or negative, (b) `execution_delay_bars < 1`, (c) `val_end > holdout_start`, or (d) `economic_mechanism` / `counterparty_or_friction` in `HypothesisSpec` is shorter than 20 characters (preventing empty placeholder hypotheses).
  4. Write `contracts/crypto_perps_1h_v1.yaml` (`taker_fee_bps: 4.0`, `slippage_bps: 2.0`, `funding_bps_per_8h: 1.0`, `execution_delay_bars: 1`, `n_folds: 4`, `label_horizon_bars: 4`, `embargo_bars: 2`).
  5. Write `tests/test_contracts.py` testing valid loading, `spec_hash()` determinism, and rejection of zero-cost, zero-delay, or overlapping holdout contracts.
- **Acceptance Criteria:**
  - `EvaluationContract` deterministically computes a 16-char SHA-256 `spec_hash()`.
  - Attempting to instantiate `EvaluationContract` with `taker_fee_bps=0, slippage_bps=0`, `execution_delay_bars=0`, or `val_end > holdout_start` raises `ContractValidationError`.
- **Required Automated Tests:** `uv run pytest tests/test_contracts.py -q` and `uv run ruff check src/warden/contracts.py tests/test_contracts.py`.
- **Required Evidence & Artifacts:** `pytest` output, `spec_hash()` printed for `contracts/crypto_perps_1h_v1.yaml`, commit on `feat/tkt-03-contracts`.
- **Definition of Done:** All tests pass, zero lint errors, committed, agent stops.
- **Estimated Complexity & Important Risks:** **Low (30–45 mins).**

---

## Ticket `TKT-04`: Read-Only Dataset Salvage & POSIX Holdout Split (Day 2B)

- **Objective & Reason:** Inspect the existing `quant-research` data store (or generate a canonical fallback dataset via `ml4t.data.providers.SyntheticProvider` / `generate_canonical_market_panel` if `quant-research` data is on an external mount), normalize 1-hour crypto perpetuals OHLCV + funding rates into the canonical `ml4t-specs` Parquet schema, split chronologically into exploratory vs. holdout sets, and prepare the human-approved POSIX permission lock on `/data/holdout/`.
- **Dependencies & Prerequisites:** `TKT-03`.
- **Exact Implementation Scope:**
  1. Inspect read-only data paths in `~/quant-research` (or `$ML4T_DATA_PATH`).
  2. Implement `scripts/salvage_crypto_perps.py` which:
     - Reads source 1h crypto perpetuals bars (or generates the deterministic 12-asset, 2,400-bar synthetic panel if `--synthetic-fallback` is passed for CI/offline testing).
     - Enforces the canonical `ml4t-specs` schema: `timestamp` (`Datetime("us", "UTC")`), `asset` (`Utf8`), `open`, `high`, `low`, `close`, `volume`, `funding_rate` (`Float64`), sorted strictly by `(timestamp, asset)` with zero duplicate keys.
     - Splits strictly at the contract's `holdout_start` boundary: writes pre-cutoff bars (`< holdout_start`) to `exploratory_data_path` and post-cutoff bars (`>= holdout_start`) to `holdout_data_path`.
     - Computes and prints the SHA-256 checksum of both Parquet files and updates `data_snapshot_sha256` in `contracts/crypto_perps_1h_v1.yaml`.
  3. Implement `scripts/lock_holdout_permissions.sh` (for human execution with `sudo`) to set `chown -R root:trader /data/holdout && chmod -R 0640 /data/holdout`.
  4. Write `tests/test_data_salvage.py`.
- **Expected Files / Modules Created or Modified:**
  - `scripts/salvage_crypto_perps.py`
  - `scripts/lock_holdout_permissions.sh`
  - `contracts/crypto_perps_1h_v1.yaml` (updated `data_snapshot_sha256`)
  - `tests/test_data_salvage.py`
- **Explicit Out-of-Scope Work:**
  - **HUMAN APPROVAL GATE:** Do NOT run `sudo` or modify any file inside `~/quant-research`. `~/quant-research` must be accessed strictly read-only.
- **Step-by-Step Implementation Instructions:**
  1. Run a read-only discovery check for existing Parquet/DuckDB files in `~/quant-research` (`find ~/quant-research -maxdepth 3 -name "*.parquet" -o -name "*.duckdb" 2>/dev/null | head -n 30`).
  2. Write `scripts/salvage_crypto_perps.py` supporting `--source-dir <path>` (real salvage) and `--synthetic-fallback` (deterministic 12-asset panel from `run_vertical_slice.py`).
  3. Validate that `exploratory.parquet` max timestamp `< holdout.parquet` min timestamp, that all required columns are non-null, and that prices are strictly positive (`> 0`).
  4. Write `scripts/lock_holdout_permissions.sh` and print clear instructions for the human operator to run `sudo ./scripts/lock_holdout_permissions.sh` if `/data/holdout` requires root ownership.
  5. Write `tests/test_data_salvage.py` verifying schema conformance, chronological disjointness (`max(exp.timestamp) < min(holdout.timestamp)`), and SHA-256 reproducibility.
- **Acceptance Criteria:**
  - `uv run python scripts/salvage_crypto_perps.py --synthetic-fallback --out-dir ./data_test` produces `exploratory.parquet` and `holdout.parquet` with zero timestamp overlap and deterministic SHA-256 hashes.
  - If real `quant-research` crypto perps data is found on the VPS, `salvage_crypto_perps.py` exports it cleanly without modifying a single byte in `~/quant-research`.
- **Required Automated Tests:** `uv run pytest tests/test_data_salvage.py -q`.
- **Required Evidence & Artifacts:** Schema printout, row counts, min/max timestamps, and SHA-256 hashes of `exploratory.parquet` and `holdout.parquet`; commit on `feat/tkt-04-data-salvage`.
- **Definition of Done:** Tests pass, data split verified, human permission script ready, committed, agent stops.
- **Estimated Complexity & Important Risks:** **Low-Medium (45 mins).** Risk: Legacy timestamps missing UTC timezone or containing duplicate bars; handled by explicit Polars `.unique(subset=["timestamp", "asset"])` check and UTC cast.

---

## Ticket `TKT-05`: Append-Only SQLite Trial Registry & Effective-Rank DSR Calculator (Day 3A)

- **Objective & Reason:** Implement `src/warden/registry.py` providing the durable, append-only SQLite experiment registry (`registry/registry.db`) paired with Parquet trial return storage (`registry/returns/{contract_id}/{run_id}.parquet`), the 3-strike hypothesis budget gate, and the Deflated Sharpe Ratio (`compute_effective_trials_and_dsr`) using eigenvalue effective rank ($K_{	ext{eff}}$) and `ml4t.diagnostic.evaluation.stats.deflated_sharpe_ratio` integration.
- **Dependencies & Prerequisites:** `TKT-03`.
- **Exact Implementation Scope:**
  1. Port and productionize `ResearchRegistry` and `compute_effective_trials_and_dsr` from `run_vertical_slice.py` into `src/warden/registry.py`.
  2. Implement the 5 SQLite tables from RFC Section 4.4: `contracts`, `hypotheses`, `feature_sets`, `trials`, and `qualifications`.
  3. Add SQLite triggers (`BEFORE DELETE ON trials`, `BEFORE UPDATE ON trials`) that raise `ABORT 'Append-only violation: trials table is immutable'` so even direct SQL calls cannot delete or overwrite past trials.
  4. Enforce the **3-Strike Hypothesis Budget**: `register_trial_start(hypothesis_id)` checks `trials_used` in `hypotheses`; if `trials_used >= 3`, it raises `HypothesisBudgetExhaustedError` and marks `status = 'REJECTED'`.
  5. Save each trial's out-of-sample net return vector to `registry/returns/{contract_id}/{run_id}.parquet` (as well as `returns_json` in SQLite for redundancy) and compute $(N, K_{	ext{eff}}, \mathbb{E}[\max SR_0], 	ext{DSR})$ across all prior trials registered under `contract_id`.
  6. Write comprehensive unit tests in `tests/test_registry.py`.
- **Expected Files / Modules Created or Modified:**
  - `src/warden/registry.py`
  - `tests/test_registry.py`
- **Explicit Out-of-Scope Work:**
  - Do NOT implement feature leakage testing (`TKT-06`) or backtest execution (`TKT-07`).
- **Step-by-Step Implementation Instructions:**
  1. Implement `ResearchRegistry` in `src/warden/registry.py` with automatic WAL/MEMORY pragma fallback (`_connect()` tries `PRAGMA journal_mode=WAL;`, falling back to `PRAGMA journal_mode=MEMORY;` if the filesystem raises an `OperationalError`).
  2. Create tables `contracts`, `hypotheses`, `feature_sets`, `trials`, and `qualifications`, plus SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers on `trials` to enforce immutability at the database engine level.
  3. Implement `compute_effective_trials_and_dsr()` calling `ml4t.diagnostic.evaluation.stats.deflated_sharpe_ratio` when multi-trial return matrices are passed, cross-checked against the eigenvalue entropy effective rank ($K_{	ext{eff}} = \exp(-\sum p_i \ln p_i)$) from `run_vertical_slice.py`.
  4. Implement `check_hypothesis_budget(hypothesis_id)` raising `HypothesisBudgetExhaustedError` on the 4th attempt.
  5. Write `tests/test_registry.py` testing: (a) hypothesis registration; (b) monotonic growth of $N$ and $K_{	ext{eff}}$ across 4 trials (reproducing the `1.00 -> 1.39 -> 1.65 -> 2.55` progression from `run_vertical_slice.py`); (c) SQLite trigger rejection of `DELETE FROM trials` and `UPDATE trials`; and (d) rejection of a 4th trial on the same `hypothesis_id`.
- **Acceptance Criteria:**
  - Executing `DELETE FROM trials` or `UPDATE trials` on `registry.db` raises `sqlite3.IntegrityError` / `OperationalError` (`Append-only violation`).
  - Attempting a 4th trial on a single `hypothesis_id` raises `HypothesisBudgetExhaustedError`.
  - Adding identical (collinear) return streams keeps $K_{	ext{eff}} pprox 1.0$, whereas adding orthogonal return streams increases $K_{	ext{eff}} 	o N$ and increases `expected_max_sharpe_noise`.
- **Required Automated Tests:** `uv run pytest tests/test_registry.py -q`.
- **Required Evidence & Artifacts:** `pytest` output showing trigger immutability, 3-strike budget enforcement, and $K_{	ext{eff}}$ calculation; commit on `feat/tkt-05-registry`.
- **Definition of Done:** All tests pass, committed, agent stops.
- **Estimated Complexity & Important Risks:** **Medium (45–60 mins).** Risk: Filesystem lock quirks with SQLite WAL mode, solved by the tested `_connect()` fallback.

---

## Ticket `TKT-06`: Prefix-Invariance Leakage Prover & Content-Addressed Feature Cache (Day 3B)

- **Objective & Reason:** Implement `src/warden/leakage.py` providing `FeaturePipelineProtocol`, the deterministic **Prefix-Invariance (No-Lookahead) Prover**, fold-boundary purging/embargo verification (`purged_walk_forward_splits` & `ml4t.diagnostic.WalkForwardCV`), the `ml4t-engineer` version-safe adapter (`calendar=None` check), and content-addressed Parquet feature caching (`cache/features/{feature_hash}.parquet`).
- **Dependencies & Prerequisites:** `TKT-03`, `TKT-05` (and `TKT-04` synthetic data fixture).
- **Exact Implementation Scope:**
  1. Implement `FeaturePipelineProtocol` and `WalkForwardModelProtocol` in `src/warden/leakage.py` (matching RFC Section 5).
  2. Implement `verify_prefix_invariance(pipeline, bars_df, contract)`:
     - Evaluates `pipeline.compute(bars_df_truncated, contract)` on bars up to cut point $t_0$.
     - Constructs a perturbed copy `bars_df_perturbed` of the full dataset where all bars at $t > t_0$ receive a $+50\%$ price shock, $3	imes$ volume shock, and $+100	ext{ bps}$ funding shock.
     - Evaluates `pipeline.compute(bars_df_perturbed, contract)` and asserts that for all $t \le t_0$, every engineered feature column matches the truncated run within `atol=1e-10, rtol=1e-10` (and identical null masks). Any mismatch raises `LookaheadLeakageError` naming the exact leaking column and first diverging timestamp!
  3. Implement `verify_fold_isolation(WalkForwardModelProtocol)` which strips the target label column from `val_features_df` before calling `fit_predict_fold` and verifies that `max(train_timestamps) + label_horizon + embargo <= min(val_timestamps)`.
  4. Implement `compute_and_cache_features(pipeline, bars_df, contract, cache_dir, registry)` which hashes `(contract.spec_hash(), pipeline_source_sha256, ml4t_engineer_version)` and caches verified features to `cache/features/{feature_hash}.parquet` and logs to `feature_sets` in `registry.db`.
  5. Write `tests/test_warden_leakage.py` planting 4 classic lookahead bugs (`shift(-1)`, full-sample z-score standardization before split, ungrouped rolling window bleeding across assets, and forward-filled future nulls) and proving 100% detection.
- **Expected Files / Modules Created or Modified:**
  - `src/warden/leakage.py`
  - `tests/test_warden_leakage.py`
- **Explicit Out-of-Scope Work:**
  - Do NOT implement Stage 1/2/3 statistical gates or `ml4t-backtest` execution (`TKT-07`).
- **Step-by-Step Implementation Instructions:**
  1. Implement `LookaheadLeakageError` and `CrossAssetLeakageError` exceptions in `src/warden/leakage.py`.
  2. Implement `verify_prefix_invariance()` (checking future timestamp perturbation) and `verify_asset_independence()` (perturbing a single asset's prices and verifying that per-asset time-series features for *other* assets do not change unless the feature is explicitly declared `cross_sectional=True`, and even then only at the same timestamp $t$, never from $t+1$).
  3. Add the 5-line `ml4t.engineer.compute_features` signature adapter (checking `inspect.signature(compute_features).parameters` for `"calendar"` per the `TKT-01` manifest).
  4. Implement content-addressed Parquet caching in `cache/features/{feature_hash}.parquet`.
  5. Write `tests/test_warden_leakage.py` with 4 planted leakage traps and 2 valid causal pipelines (one time-series, one cross-sectional demeaned at $t-1$).
- **Acceptance Criteria:**
  - All 4 planted leakage bugs (`pl.col("close").shift(-1)`, global `(x - x.mean()) / x.std()`, ungrouped `.rolling_mean(10)` without `.over("asset")`, and `.backward_fill()`) deterministically raise `LookaheadLeakageError` with the offending column name.
  - Valid causal pipelines pass verification and hit the Parquet cache on the second call in `< 50 ms`.
- **Required Automated Tests:** `uv run pytest tests/test_warden_leakage.py -q`.
- **Required Evidence & Artifacts:** `pytest` output showing all 4 planted leakage traps caught and cached hit timing; commit on `feat/tkt-06-leakage-prover`.
- **Definition of Done:** All tests pass, committed, agent stops.
- **Estimated Complexity & Important Risks:** **Medium (45–60 mins).**


---

## Ticket `TKT-07`: Family-Specific Stage 1 Gates, Stage 2/3 Backtest Runner & `warden` CLI (Day 3C)

- **Objective & Reason:** Implement `src/warden/gates.py`, `src/warden/runner.py`, and the complete `src/warden/cli.py` (plus worktree integrity & LOC budget guards) to connect the Five-Stage Falsification Funnel (`Stage 0 -> Stage 1 family-specific branching -> Stage 2 ml4t-backtest simulation -> Stage 3 DSR/cost/delay/neighborhood stress -> Stage 4 audit/handoff`) into a single deterministic CLI callable by any coding agent.
- **Dependencies & Prerequisites:** `TKT-02`, `TKT-03`, `TKT-05`, `TKT-06`.
- **Exact Implementation Scope:**
  1. Implement `src/warden/gates.py`:
     - **Stage 0 Feasibility Gate:** Verifies registered hypothesis exists, `trials_used < 3`, and expected holding-period return volatility exceeds `2.5x` round-trip cost floor (`taker_fee_bps + slippage_bps`).
     - **Stage 1 Family-Specific Gates:**
       - `cross_sectional_ml` ($N \ge 10$): Purged Walk-Forward OOS Rank/Pearson IC (`ml4t.diagnostic.analyze_signal` + `compute_hac_tstat`), requiring `mean_ic >= 0.015` and `ic_hac_tstat >= 2.0`.
       - `rules_ts` ($N = 1..9$): Conditional return edge $\mathbb{E}[R \mid S 
eq 0] > 1.5	imes$ one-way cost, `profit_factor >= 1.15`, time-in-market $\ge 5\%$, block-bootstrap $p < 0.05$ (*never* forced through cross-sectional IC).
       - `carry_rv`: Net carry capture after funding/roll $> 0$ and spread ADF $p < 0.05$.
       - `meta_overlay`: $\ge 15\%$ reduction in 99% CVaR / Max Drawdown or $\ge 20\%$ turnover reduction with Sharpe delta $\ge -0.05$.
  2. Implement `src/warden/runner.py`:
     - Bridges validated signals/weights into `ml4t.backtest.Engine` (`BacktestConfig` injected from `EvaluationContract` with `ExecutionMode.NEXT_BAR` / `ExecutionPrice.QUOTE_SIDE`, mandatory commission/slippage/funding rates) with fallback to the exact vectorized/rebalance accounting in `run_vertical_slice.py`.
     - Executes Stage 3 stress tests automatically: `1.0x` baseline cost, `1.5x` cost, `2.0x` cost (`stress_2x_cost_sharpe > 0.50`), `+1 bar` additional latency, and $\pm 15\%$ parameter neighborhood stability.
     - Appends the trial and its OOS return vector to `ResearchRegistry` and computes `deflated_sharpe_prob` across all historical trials on the contract.
  3. Implement `src/warden/cli.py` subcommands (all supporting `--json` for machine parsing across OMP, OpenCode, Claude, and Codex):
     - `warden status [--json]`
     - `warden check-worktree` (verifies `.warden_manifest.sha256` and runs `scripts/check_loc_budget.py` enforcing `src/warden/ < 1,200 LOC` and `experiments/active/*.py < 250 LOC`)
     - `warden register-hypothesis <path> [--json]`
     - `warden verify <spec_yaml> [--json]`
     - `warden screen <spec_yaml> [--json]`
     - `warden backtest <spec_yaml> [--json]`
     - `warden audit-check --run-id <ID> [--json]`
     - `warden handoff --notes "..."` and `warden continue [<transition_path>]`
  4. Generate `.warden_manifest.sha256` (checksums of `src/warden/*.py`, `contracts/*.yaml`, and `AGENTS.md`) and `scripts/check_loc_budget.py`.
- **Expected Files / Modules Created or Modified:**
  - `src/warden/gates.py`
  - `src/warden/runner.py`
  - `src/warden/cli.py`
  - `scripts/check_loc_budget.py`
  - `.warden_manifest.sha256`
  - `tests/test_gates_and_runner.py`
- **Explicit Out-of-Scope Work:**
  - Do NOT implement the specific production strategy files (`TKT-08`, `TKT-09`) or the holdout unsealing command (`TKT-11`).
- **Step-by-Step Implementation Instructions:**
  1. Implement `src/warden/gates.py` with the 4 family-specific Stage 1 evaluators and Stage 0 feasibility screen.
  2. Implement `src/warden/runner.py` integrating `ml4t.backtest` (`Engine`, `BacktestConfig`, `DataFeed`) and `ResearchRegistry`. Ensure every run emits `WARDEN_RUN_COMPLETE` (or `WARDEN_RUN_FAILED`) on its final stdout line so `herdr pane wait-output` triggers deterministically.
  3. Implement `src/warden/cli.py` with `argparse`, wiring all subcommands and calling `check_worktree()` before any experiment command.
  4. Write `scripts/check_loc_budget.py` and generate `.warden_manifest.sha256`.
  5. Write `tests/test_gates_and_runner.py` verifying: (a) `rules_ts` strategies bypass IC and use conditional edge / Profit Factor; (b) `cross_sectional_ml` strategies are gated by HAC IC $t$-stat; (c) modifying a byte in `contracts/crypto_perps_1h_v1.yaml` without updating `.warden_manifest.sha256` causes `warden check-worktree` to abort with exit code `2`; and (d) `scripts/check_loc_budget.py` passes.
- **Acceptance Criteria:**
  - `uv run warden status --json` and `uv run warden check-worktree` exit `0`.
  - `uv run python scripts/check_loc_budget.py` confirms `src/warden/` total LOC `< 1,200`.
  - Tampering with a protected file in `contracts/` or `src/warden/` is immediately caught and blocked by `warden check-worktree`.
- **Required Automated Tests:** `uv run pytest tests/test_gates_and_runner.py -q`.
- **Required Evidence & Artifacts:** `pytest` output, `warden --help` output, LOC count report from `scripts/check_loc_budget.py`, and commit on `feat/tkt-07-warden-runner`.
- **Definition of Done:** All tests pass, `.warden_manifest.sha256` committed, agent stops.
- **Estimated Complexity & Important Risks:** **Medium-High (60–75 mins).**

---

## Ticket `TKT-08`: Vertical Slice 1 — Rules-Based Volatility Breakout Strategy (`HYP-2026-001`) (Day 4A)

- **Objective & Reason:** Implement and execute the first production vertical slice (`HYP-2026-001`: 1-Hour Intraday Volatility-Breakout Trend Continuation on Major Crypto Perpetuals, `strategy_family: rules_ts`) through the full `warden` pipeline (`register-hypothesis -> verify -> screen -> backtest`), proving that rules-based time-series strategies execute end-to-end with family-appropriate gates and portable `ml4t.backtest.Strategy` code.
- **Dependencies & Prerequisites:** `TKT-04`, `TKT-07`.
- **Exact Implementation Scope:**
  1. Author `.workspace/hypotheses/HYP-2026-001.md` (with YAML frontmatter matching `HypothesisSpec` for `rules_ts`).
  2. Author `strategies/candidates/vol_breakout_1h.py` implementing both `FeaturePipelineProtocol` (causal lookback return and realized volatility z-score at $t - 1$) and a lifecycle-v1 portable `VolBreakoutStrategy(Strategy)` subclass (`on_prepare`, `on_data`, `on_end`).
  3. Author `experiments/active/hyp_2026_001.yaml` referencing `CRYPTO_PERPS_1H_V1`, `HYP-2026-001`, and parameter grid neighbors (`z_threshold: [2.4, 2.2, 2.0]`).
  4. Execute `warden register-hypothesis`, `warden verify`, `warden screen`, and `warden backtest` across the 3 parameter configurations (consuming the 3-trial budget for `HYP-2026-001`) and write `tests/experiments/test_hyp_2026_001.py`.
- **Expected Files / Modules Created or Modified:**
  - `.workspace/hypotheses/HYP-2026-001.md`
  - `strategies/candidates/vol_breakout_1h.py`
  - `experiments/active/hyp_2026_001.yaml`
  - `tests/experiments/test_hyp_2026_001.py`
  - Updated `registry/registry.db` and `registry/returns/CRYPTO_PERPS_1H_V1/RUN-RULES-*.parquet`
- **Explicit Out-of-Scope Work:**
  - Do NOT implement the cross-sectional ML strategy (`TKT-09`) or touch `/data/holdout/`.
- **Step-by-Step Implementation Instructions:**
  1. Write `.workspace/hypotheses/HYP-2026-001.md` documenting the dealer gamma / retail liquidation cascade mechanism, counterparty friction, 8-bar holding period, and falsification criteria (`Net Sharpe < 1.0`, `Profit Factor < 1.15`, `Breakeven Cost < 12 bps`, `2x-Cost Stress Sharpe < 0.5`).
  2. Implement `strategies/candidates/vol_breakout_1h.py` (`< 200 LOC`).
  3. Run `uv run warden register-hypothesis .workspace/hypotheses/HYP-2026-001.md --json`.
  4. Run `uv run warden verify experiments/active/hyp_2026_001.yaml --json` (confirming prefix-invariance passes and features are cached).
  5. Run `uv run warden screen experiments/active/hyp_2026_001.yaml --json` and `uv run warden backtest experiments/active/hyp_2026_001.yaml --json` across the 3 configurations (`RUN-RULES-001`, `RUN-RULES-002`, `RUN-RULES-003`).
  6. Update `.workspace/hypotheses/HYP-2026-001.md` with the logged SQLite results and write `tests/experiments/test_hyp_2026_001.py` verifying that a 4th trial on `HYP-2026-001` is blocked by the 3-strike budget gate.
- **Acceptance Criteria:**
  - `warden verify` passes prefix-invariance with zero lookahead leakage.
  - `RUN-RULES-001`, `RUN-RULES-002`, and `RUN-RULES-003` are permanently recorded in `registry/registry.db` with `mean_ic IS NULL` (confirming no forced IC gate) and populated `profit_factor`, `hit_rate`, `breakeven_cost_bps`, `stress_2x_cost_sharpe`, and `effective_trials_keff` (`1.00 -> 1.39 -> 1.65` on synthetic benchmark).
  - Attempting a 4th run on `HYP-2026-001` raises `HypothesisBudgetExhaustedError`.
- **Required Automated Tests:** `uv run pytest tests/experiments/test_hyp_2026_001.py -q`.
- **Required Evidence & Artifacts:** SQLite query output for `RUN-RULES-001..003`, `pytest` output, commit on `feat/tkt-08-vertical-slice-rules`.
- **Definition of Done:** All 3 trials logged, 4th trial blocked by budget test, committed, agent stops.
- **Estimated Complexity & Important Risks:** **Medium (45 mins).**

---

## Ticket `TKT-09`: Vertical Slice 2 — Cross-Sectional Funding & Reversal ML Strategy (`HYP-2026-002`) (Day 4B)

- **Objective & Reason:** Implement and execute the second production vertical slice (`HYP-2026-002`: Cross-Sectional Funding-Rate & Short-Horizon Reversal Model, `strategy_family: cross_sectional_ml`) using Purged Walk-Forward Cross-Validation (`4 folds, horizon=4h, embargo=2h`), Stage 1 Newey-West HAC IC gating, and Stage 2/3 market-neutral portfolio simulation, proving that the Linear-First Model Gate (Ridge baseline before LightGBM) and cross-hypothesis $K_{	ext{eff}}$ trial accumulation (`N=4, K_eff=2.55`) work end-to-end.
- **Dependencies & Prerequisites:** `TKT-08`.
- **Exact Implementation Scope:**
  1. Author `.workspace/hypotheses/HYP-2026-002.md` (`strategy_family: cross_sectional_ml`).
  2. Author `strategies/baselines/cs_ridge_1h.py` implementing `FeaturePipelineProtocol`, `WalkForwardModelProtocol` (closed-form / `sklearn.linear_model.Ridge` fitted strictly inside purged training folds with validation labels stripped), and a market-neutral top-3 / bottom-3 `TargetWeightExecutor` `Strategy`.
  3. Author `strategies/candidates/cs_lgbm_1h.py` implementing a regularized `LightGBM` walk-forward model that `warden` only allows to run *after* the linear Ridge baseline (`RUN-ML-001`) is logged under `HYP-2026-002`.
  4. Author `experiments/active/hyp_2026_002_ridge.yaml` and `experiments/active/hyp_2026_002_lgbm.yaml`.
  5. Execute `warden register-hypothesis`, `warden verify`, `warden screen`, and `warden backtest` for `RUN-ML-001` (Ridge baseline) and `RUN-ML-002` (LightGBM ablation), and write `tests/experiments/test_hyp_2026_002.py`.
- **Expected Files / Modules Created or Modified:**
  - `.workspace/hypotheses/HYP-2026-002.md`
  - `strategies/baselines/cs_ridge_1h.py`
  - `strategies/candidates/cs_lgbm_1h.py`
  - `experiments/active/hyp_2026_002_ridge.yaml`, `experiments/active/hyp_2026_002_lgbm.yaml`
  - `tests/experiments/test_hyp_2026_002.py`
  - Updated `registry/registry.db` (`RUN-ML-001`, `RUN-ML-002`)
- **Explicit Out-of-Scope Work:**
  - Do NOT touch `/data/holdout/`.
- **Step-by-Step Implementation Instructions:**
  1. Write `.workspace/hypotheses/HYP-2026-002.md` specifying the crowded perpetual funding + short-term reversal mechanism and Stage 1 (`Mean IC >= 0.02`, `HAC t-stat >= 2.0`) & Stage 3 gates.
  2. Implement `strategies/baselines/cs_ridge_1h.py` and `strategies/candidates/cs_lgbm_1h.py` (`< 220 LOC` each). Enforce the Linear-First Model Gate check in `warden`: running `hyp_2026_002_lgbm.yaml` before `hyp_2026_002_ridge.yaml` raises `LinearBaselineRequiredError`.
  3. Register `HYP-2026-002`, verify prefix-invariance, and run `hyp_2026_002_ridge.yaml` (`RUN-ML-001`) followed by `hyp_2026_002_lgbm.yaml` (`RUN-ML-002`).
  4. Verify in `registry/registry.db` that `RUN-ML-001` includes `RUN-RULES-001..003` in its contract-wide trial return matrix ($N=4, K_{	ext{eff}} pprox 2.55$ on the synthetic panel) and `RUN-ML-002` increments $N=5$.
  5. Document the Ridge vs. LightGBM comparison in `.workspace/hypotheses/HYP-2026-002.md` and `.workspace/memory/LESSONS.md`.
- **Acceptance Criteria:**
  - Attempting to run `cs_lgbm_1h.py` on a hypothesis without a logged linear baseline raises `LinearBaselineRequiredError`.
  - `val_features_df` passed to `fit_predict_fold` physically lacks the target label column.
  - Both `RUN-ML-001` and `RUN-ML-002` record purged walk-forward OOS `mean_ic`, `ic_hac_tstat`, `gross_sharpe`, `net_sharpe`, `deflated_sharpe_prob`, and `effective_trials_keff` in `registry/registry.db`.
- **Required Automated Tests:** `uv run pytest tests/experiments/test_hyp_2026_002.py -q`.
- **Required Evidence & Artifacts:** SQLite query showing all 5 trials (`RUN-RULES-001..003`, `RUN-ML-001..002`) with $N$ and $K_{	ext{eff}}$ progression; `pytest` output; commit on `feat/tkt-09-vertical-slice-ml`.
- **Definition of Done:** Tests pass, both models logged and compared in `.workspace/`, committed, agent stops.
- **Estimated Complexity & Important Risks:** **Medium (45–60 mins).**

---

## Ticket `TKT-10`: Herdr Worker Pane Integration, Session `handoff`/`continue` & 6-Check Adversarial Audit (Day 4C)

- **Objective & Reason:** Implement and test the harness-independent `scripts/herdr_dispatch.sh` wrapper (which dispatches long deterministic `warden` runs to a `Herdr` worker pane when `HERDR_ENV=1` is present and falls back cleanly to synchronous execution when absent), verify `uv run warden handoff` and `uv run warden continue` session transitions with inline `# expect:` assertions, and run `uv run warden audit-check` (the 6-check adversarial audit from [`ml4t/agentic-ai-in-trading/CHECKLIST.md`](https://github.com/ml4t/agentic-ai-in-trading/blob/main/README.md)) on `RUN-ML-001`.
- **Dependencies & Prerequisites:** `TKT-08`, `TKT-09`.
- **Exact Implementation Scope:**
  1. Author `scripts/herdr_dispatch.sh` supporting `--pane <pane_id>` (defaulting to creating/reusing a worker pane via `herdr pane split` / `herdr pane run` + `herdr pane wait-output --regex "WARDEN_RUN_COMPLETE|WARDEN_RUN_FAILED"` when `HERDR_ENV=1` and `herdr` binary exist, or running directly in subshell otherwise).
  2. Verify `uv run warden audit-check --run-id RUN-ML-001 --json`, which deterministically evaluates the 6 ML4T audit checks (1. Target-rebalance alignment; 2. Top-5% outlier bar removal sensitivity; 3. Lagged timestamp causality; 4. Fold-local fitting verification; 5. 2x-cost & +1-bar latency survival; 6. Trial count & DSR threshold verification) and writes `.workspace/hypotheses/AUDIT_RUN-ML-001.md`.
  3. Execute `uv run warden handoff --ticket TKT-10` to generate `.workspace/transitions/YYYY-MM-DD/HHMMSS.md` containing read-only bash commands with inline `# expect:` values (`git rev-parse --short HEAD`, `sqlite3 registry/registry.db "SELECT COUNT(*) FROM trials"`, `warden check-worktree`), then execute `uv run warden continue` to verify 100% pass and test drift detection.
  4. Write `tests/test_herdr_and_handoff.py`.
- **Expected Files / Modules Created or Modified:**
  - `scripts/herdr_dispatch.sh`
  - `.workspace/hypotheses/AUDIT_RUN-ML-001.md`
  - `.workspace/transitions/YYYY-MM-DD/HHMMSS.md`
  - `tests/test_herdr_and_handoff.py`
- **Explicit Out-of-Scope Work:**
  - Do NOT require a particular coding agent (`omp`, `opencode`, `claude`, or `codex`) to be running inside `Herdr` for `herdr_dispatch.sh` or `warden handoff`/`continue` to pass.
- **Step-by-Step Implementation Instructions:**
  1. Write `scripts/herdr_dispatch.sh` with `set -euo pipefail`. Check `if [[ "${HERDR_ENV:-0}" == "1" ]] && command -v herdr >/dev/null 2>&1; then ... else ... fi` per `herdr.dev/docs/agent-automation/`.
  2. Run `uv run warden audit-check --run-id RUN-ML-001 --json` and save the human-readable audit report to `.workspace/hypotheses/AUDIT_RUN-ML-001.md`.
  3. Run `uv run warden handoff --notes "Completed vertical slices and audit"` and then `uv run warden continue` to verify that all `# expect:` assertions match.
  4. Write `tests/test_herdr_and_handoff.py` testing: (a) `herdr_dispatch.sh` fallback execution and mock `herdr` CLI invocation; (b) `warden audit-check` output schema (all 6 checks present and scored); and (c) `warden continue` flagging drift when a dummy trial is added after `handoff`.
- **Acceptance Criteria:**
  - `uv run warden audit-check --run-id RUN-ML-001 --json` returns a valid 6-check JSON verdict and writes `.workspace/hypotheses/AUDIT_RUN-ML-001.md`.
  - `uv run warden continue` exits `0` on an unchanged state and reports explicit drift if `registry.db` or `HEAD` changes.
- **Required Automated Tests:** `uv run pytest tests/test_herdr_and_handoff.py -q`.
- **Required Evidence & Artifacts:** Contents of `.workspace/hypotheses/AUDIT_RUN-ML-001.md` and the generated `.workspace/transitions/` file; `pytest` output; commit on `feat/tkt-10-herdr-handoff-audit`.
- **Definition of Done:** All tests pass, audit and transition artifacts verified, committed, agent stops.
- **Estimated Complexity & Important Risks:** **Low-Medium (45 mins).**

---

## Ticket `TKT-11`: Human-Only Holdout Qualification Gate (`warden qualify`) & 6-Part Acceptance Suite (Day 5A)

- **Objective & Reason:** Implement `src/warden/qualify.py` (`warden qualify --run-id <ID> --unseal-holdout`, which requires interactive human confirmation / `trader` group permissions and locks the hypothesis lineage permanently after a single evaluation on `holdout_data_path`) and implement the full 6-part automated Acceptance Test Suite from Section 9 of the RFC (`tests/acceptance/test_rfc_acceptance.py`).
- **Dependencies & Prerequisites:** `TKT-10`.
- **Exact Implementation Scope:**
  1. Implement `src/warden/qualify.py`:
     - Refuses to run if `WARDEN_NON_INTERACTIVE=1` or if `--human-confirm-unseal` token is absent.
     - Checks `qualifications` table in `registry.db`: if *any* run from the same `hypothesis_id` (or `parent_hypothesis_id` lineage) has already been evaluated on the holdout set, raises `HoldoutAlreadyUnsealedError` (preventing repeated holdout probing).
     - Evaluates the frozen strategy/model artifact from `run_id` once on `holdout_data_path`, records `holdout_gross_sharpe`, `holdout_net_sharpe`, `holdout_max_dd_pct`, and `val_to_holdout_sharpe_ratio` in `qualifications`, and updates the hypothesis status to `PROMOTED` (if holdout Net Sharpe $> 0.80$ and ratio $\ge 0.50$) or `REJECTED_ON_HOLDOUT`.
  2. Implement `tests/acceptance/test_rfc_acceptance.py` executing all 6 objective acceptance tests defined in Section 9 of `agent_native_quant_research_rfc.md`:
     - Test 1: Bit-for-Bit Reproducibility across two isolated subprocess runs (`max |diff| == 0.0`).
     - Test 2: Causal Prefix-Invariance (`100%` detection of planted lookahead bugs).
     - Test 3: Purged & Embargoed Fold Disjointness (`max(train_idx) + h + e <= min(val_idx)`).
     - Test 4: Holdout & Governance Isolation (tampering with contracts, deleting trials, or re-unsealing a holdout lineage all fail closed).
     - Test 5: Backtest Accounting & Cost Floor Fidelity (analytical vs. simulated fill price & fee check to `1e-8` precision).
     - Test 6: Research Ergonomics & Throughput Funnel Timing (Stage 0 `< 100 ms`, Stage 1 cached feature screen `< 2.0 s`, full 4-fold vertical slice `< 60 s`).
- **Expected Files / Modules Created or Modified:**
  - `src/warden/qualify.py`
  - `src/warden/cli.py` (wire `qualify` subcommand)
  - `.warden_manifest.sha256` (updated for `qualify.py` and `cli.py`)
  - `tests/acceptance/test_rfc_acceptance.py`
- **Explicit Out-of-Scope Work:**
  - **HUMAN APPROVAL GATE:** Do NOT run `warden qualify` against real `/data/holdout/` production data during automated tests; `test_rfc_acceptance.py` must run against an isolated `tmp_path` synthetic holdout fixture.
- **Step-by-Step Implementation Instructions:**
  1. Implement `src/warden/qualify.py` with one-shot lineage lock in `qualifications` table.
  2. Wire `warden qualify` into `src/warden/cli.py` and refresh `.warden_manifest.sha256`.
  3. Write `tests/acceptance/test_rfc_acceptance.py` covering all 6 acceptance dimensions from RFC Section 9.
  4. Run the full test suite (`uv run pytest -q`) and verify `scripts/check_loc_budget.py`.
- **Acceptance Criteria:**
  - All 6 acceptance tests in `tests/acceptance/test_rfc_acceptance.py` pass (`100%` pass rate).
  - Attempting to call `qualify_candidate()` a second time on the same hypothesis lineage raises `HoldoutAlreadyUnsealedError`.
- **Required Automated Tests:** `uv run pytest tests/acceptance/test_rfc_acceptance.py -v` and `uv run pytest -q`.
- **Required Evidence & Artifacts:** Verbatim `pytest -v` output for all 6 RFC acceptance tests, wall-clock timing measurements, and commit on `feat/tkt-11-qualification-acceptance`.
- **Definition of Done:** All unit, experiment, and acceptance tests pass; LOC budget verified; committed; agent stops.
- **Estimated Complexity & Important Risks:** **Medium (45–60 mins).**

---

## Ticket `TKT-12`: Ergonomics Benchmark Report & Human-Approved Legacy `quant-research` Freeze (Day 5B)

- **Objective & Reason:** Produce the final quantitative ergonomics and verification report (`.workspace/memory/ERGONOMICS_BENCHMARK.md`) comparing the completed `quant-lab` environment against the RFC targets, prepare the non-destructive `scripts/freeze_legacy_repo.sh` script for human execution to mark `~/quant-research` read-only (`chmod -R a-w`), and write the final session transition snapshot.
- **Dependencies & Prerequisites:** `TKT-11`.
- **Exact Implementation Scope:**
  1. Run a full benchmark pass measuring wall-clock time, peak RSS memory, total LOC in `src/warden/`, number of installed `ml4t/skills`, and SQLite registry state.
  2. Author `.workspace/memory/ERGONOMICS_BENCHMARK.md` and update `.workspace/memory/LESSONS.md` with initial lessons from `HYP-2026-001` and `HYP-2026-002`.
  3. Author `scripts/freeze_legacy_repo.sh` (which prints the exact `chmod -R a-w ~/quant-research` command and requires interactive human `YES_FREEZE_LEGACY` confirmation before modifying permissions on `~/quant-research`).
  4. Run `uv run warden handoff --notes "Phase 1-5 rollout complete"` and verify `uv run warden continue`.
- **Expected Files / Modules Created or Modified:**
  - `.workspace/memory/ERGONOMICS_BENCHMARK.md`
  - `.workspace/memory/LESSONS.md`
  - `scripts/freeze_legacy_repo.sh`
  - `.workspace/transitions/YYYY-MM-DD/HHMMSS.md`
- **Explicit Out-of-Scope Work:**
  - **HUMAN APPROVAL GATE:** Do NOT execute `scripts/freeze_legacy_repo.sh --apply` autonomously. Leave the final permission freeze of `~/quant-research` to the human trader.
- **Step-by-Step Implementation Instructions:**
  1. Collect timing, LOC (`uv run python scripts/check_loc_budget.py`), and test metrics (`uv run pytest -q`) and write `.workspace/memory/ERGONOMICS_BENCHMARK.md`.
  2. Write `scripts/freeze_legacy_repo.sh` with `--dry-run` (default) and `--apply` (interactive confirmation).
  3. Execute `uv run warden handoff` and `uv run warden continue`.
- **Acceptance Criteria:**
  - `uv run pytest -q` passes 100% of tests across the repository.
  - `uv run python scripts/check_loc_budget.py` confirms `src/warden/` is `< 1,200 LOC`.
  - `./scripts/freeze_legacy_repo.sh --dry-run` exits `0` without modifying `~/quant-research`.
  - `uv run warden continue` exits `0` with zero drift warnings.
- **Required Automated Tests:** `uv run pytest -q` and `uv run warden continue`.
- **Required Evidence & Artifacts:** `.workspace/memory/ERGONOMICS_BENCHMARK.md` summary table, `warden continue` output, commit on `feat/tkt-12-rollout-complete`.
- **Definition of Done:** All 12 tickets complete, repository clean, transition snapshot verified, agent stops.
- **Estimated Complexity & Important Risks:** **Low (30 mins).**
