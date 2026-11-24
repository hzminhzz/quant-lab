# Quant-Lab workflow — workflow, steps, stages

A reader's map of how work moves through this repo, aligned to the current `main` codebase (verified **2026-11-24**).

Sources, in order of authority:

| File | Role |
|---|---|
| `AGENTS.md` | The invariants. When anything disagrees with it, `AGENTS.md` wins. |
| `AGENT_RUNBOOK.md` | The procedure — 7 steps, and the rules that exist because they were violated once. |
| `STAGE3_CAMPAIGN_PROMPT.md` | Copy-paste brief for running a whole family through the funnel. |
| `docs/archive/agent_native_quant_research_rfc.md` | Original design narrative (`RFC-2026-Q4-001`, archived). |
| `src/warden/*.py` | The actual gates and execution engine. |

---

## 1. Three words, three layers

| Layer | Vocabulary | Defined by | Granularity |
|---|---|---|---|
| **Workflow** | the falsification funnel | `AGENTS.md` §Falsification funnel | one hypothesis, end to end |
| **Step** | 0–7 | `AGENT_RUNBOOK.md` | one command + one required artifact |
| **Stage** | `STAGE_0`, `STAGE_1_SIGNAL`, `STAGE_2_BACKTEST`, `STAGE_3_STRESS` | `trials.stage_reached` in SQLite | one statistical gate |

The **workflow** is the ordered **steps**, and each step lands at a **stage** stamped into `registry/registry.db`. "Stage-3 campaign" means batching many hypotheses through steps 5–7.

---

## 2. The pipeline at a glance

```
            ┌─────────────────────────────────────────────────────────────┐
 preflight  │ scripts/preflight_thesis.py → state card, next legal command │
            │ exit 0 proceed · 2 human must re-sign manifest · 3 stop      │
            └─────────────────────────────────────────────────────────────┘
                                        │
 0  check-worktree   ── rc≠0 → STOP (only the human re-signs the manifest)
 1  resolve contract ── contracts/<ID>.yaml → pinned data path, sha, span, costs, horizon
 2  source trace     ── the original clipping, not a plan about it; [MISSING] for gaps
 3  pre-register     ── warden register-hypothesis      → STAGE_0 economics at trial time
 4  verify           ── warden verify (prefix invariance) → cache/features/*.parquet
 5  screen           ── warden screen                    → STAGE_1_SIGNAL  (cheap, falsifies early)
 6  backtest         ── warden backtest                  → STAGE_3_STRESS  (2+3 in one record)
    verify-causality ── 3 cut points on the run's own code/config/panel
 7  audit-check      ── 6 checks                         → AUDIT_<RUN_ID>.md
    qualify          ── HUMAN ONLY, one holdout read per lineage
```

Terminal outcomes: a hypothesis ends either **falsified** (a gate said no; that is evidence), **blocked** (data/contract/engineering gap — *not* a verdict), or **qualified** (holdout passed).

---

## 3. The steps in detail

### Step 0 — Gate check

```bash
uv run warden check-worktree                 # rc 0 required
uv run python scripts/preflight_thesis.py --contract <CONTRACT_ID>
```

`warden check-worktree` hashes every file listed in `.warden_manifest.sha256` and runs repo-wide (`ml4t.live`) and `strategies/` (`sqlite3`, `subprocess`, HTTP/socket, `ml4t.backtest.Engine`) AST import boundary checks (`warden_ops.integrity.check_ast_boundaries`). Every experiment command runs it and fails closed if tampered (`verify`/`screen`/`audit-check` exit 2; `backtest` prints `WARDEN_RUN_FAILED`).

If `check-worktree` fails: **stop**, output `uv run warden generate-manifest --human-approve-manifest` for the human principal, and never run it yourself without explicit in-session principal delegation.

### Step 1 — Resolve the contract

Read `contracts/<contract_id>.yaml`. Record:

- `exploratory_data_path` (and `holdout_data_path` — which you may never read)
- `data_snapshot_sha256` + `data_provenance` (`verified_market` / `synthetic` / `unknown`)
- `train_start < val_start < val_end <= holdout_start < holdout_end`
- `bars_per_year`, `asset_class`, `frequency`, `execution_mode` (`NEXT_BAR` / `QUOTE_SIDE`)
- `taker_fee_bps`, `slippage_bps`, `funding_bps_per_8h` → `total_one_way_cost_decimal()`
- `label_horizon_bars`, `embargo_bars`, `execution_delay_bars` (contract validation requires ≥ 1)
- `n_folds`, `min_expected_move_to_cost_ratio` (default 2.5)

Resolution goes through `resolve_dataset(contract, repo_root)`, which verifies the SHA-256 digest and `FeedSpec` / `OHLCVValidator` / `SessionAssigner` market validation. Never infer a data file from a contract name: `CRYPTO_PERPS_1H_V1` and `_V2` pin different parquets.

### Step 2 — Source trace

Read the **original clipping**, not a plan or a summary of it. Every rule you implement carries `file:line`; every rule the source does not define is written `[MISSING]` — never invented.

### Step 3 — Pre-register

```bash
uv run warden register-hypothesis .workspace/hypotheses/HYP-2026-NNN.md --json
```

The markdown front-matter must carry: `hypothesis_id`, `contract_id`, `title`, `strategy_family` (`rules_ts`, `cross_sectional_ml`, `carry_rv`, or `meta_overlay`), `economic_mechanism` and `counterparty_or_friction` (≥ 20 chars each), `expected_holding_bars`, `falsification_criteria`, plus optional `parent_hypothesis_id`, `source_provenance`, `source_published_at`, and `llm_model_id`. Templates live at `.workspace/templates/hypothesis.md` and `.workspace/templates/experiment.yaml`.

Registration is immutable once a trial exists. Re-registering the same ID never resets the budget.

### Step 4 — Strategy protocol & causality verification

```bash
uv run warden verify experiments/active/<spec>.yaml --json
```

New `ACTIVE` hypotheses must implement one of the two inverted causal protocols in `src/warden/protocols.py` (legacy `generate_positions()` is frozen for read-only historical replay only and fails closed on `ACTIVE` hypotheses):
- `CausalModelStrategy`: `pipeline` + `build_estimator(params) -> WalkForwardModelProtocol` (reference baselines: `strategies/baselines/causal_ridge_1h.py`, `causal_linear_portfolio_1h.py`, `causal_ipca_1h.py`).
- `CausalRulesStrategy`: `required_symbols(params)` + `configure_strategy(contract, params) -> Strategy` (reference template: `strategies/candidates/causal_rules_drawdown_template.py`).

`warden verify` runs prefix-invariance sampling at 3 cut points (`1/4`, `1/2`, `3/4` via `src/warden/leakage.py::verify_prefix_invariance` / `verify_rules_strategy_prefix_invariance` — adversarial sampling, not a formal proof) and writes `cache/features/<hash>.parquet` for pipeline strategies.

### Step 5 — Screen (Stage 1)

```bash
uv run warden screen experiments/active/<spec>.yaml --json     # exit 0 = PASS, 1 = REJECT
```

Runs the strategy over the **validation** slice and applies the family gate (§4, Stage 1). It persists a `selection_attempts` row with `kind=standalone_screen`, `stage=STAGE_1_SIGNAL`, `outcome=PASS|REJECT` — screening consumes validation evidence even when it rejects.

### Step 6 — Trial (Stage 2 + Stage 3)

```bash
uv run warden backtest experiments/active/<spec>.yaml --json
uv run warden verify-causality --run-id <RUN_ID> --json
```

One `backtest` call runs `ml4t.backtest.Engine` (`1x`), analytical `1.5x` cost stress (`stress_1p5_cost_sharpe`), full `2.0x` Engine re-simulation (`cost_2x`), `+1`-bar latency Engine re-simulation (`latency_plus1`), optional `param_grid` re-simulations (each recorded as its own `selection_attempts` row), `trimmed_sharpe`, DSR selection report, exports Engine artifacts under `registry/backtest_results/<simulation_epoch>/<contract>/<run_id>`, and writes one `trials` row. Output is JSON followed by `WARDEN_RUN_COMPLETE` (or `WARDEN_RUN_FAILED` on error).

The trial slot is registered before the strategy executes, so a failure after that point consumes one of the three slots (`verdict=FAILED`). Run `verify`, `verify-causality`, and `scripts/preflight_trial.py` before spending trial slots.

### Step 7 — Audit, then qualify

```bash
uv run warden audit-check --run-id <RUN_ID> --json     # writes .workspace/hypotheses/AUDIT_<RUN_ID>.md
uv run warden qualify --run-id <RUN_ID> --unseal-holdout --human-confirm-unseal
```

`audit-check` is agent-runnable and exits 0 only when all six checks pass and the run is market-eligible. `qualify` is **human-only**: it refuses under `WARDEN_NON_INTERACTIVE=1`, requires an `APPROVED` six-check audit, verified run-bound causal proof, matching `strategy_code_sha256` from `code_closure`, literal confirmation token `YES_UNSEAL_HOLDOUT`, and refuses a second read of any lineage.

---

## 4. Stage gates, as coded

### Stage 0 — economics feasibility (`src/warden/gates.py::evaluate_stage0_feasibility`)

```
hurdle = total_one_way_cost_decimal() * 2 * min_expected_move_to_cost_ratio   # default 2.5
reject if holding_volatility < hurdle
also raises HypothesisBudgetExhaustedError if trials_used >= PER_HYPOTHESIS_CAP (3)
```

`holding_volatility` is computed empirically from the panel over `expected_holding_bars` via `gates.empirical_holding_volatility` inside `runner._prepare_and_check_stage0` (at screen/backtest time). Rejection records `selection_attempts.stage='STAGE_0'`, `kind='failed_reservation'`.

### Stage 1 — family-specific signal triage (`src/warden/gates.py`)

All four strategy families are wired across Stage 1 and Stage 3:

| Family | Stage-1 Gate & Pass Condition |
|---|---|
| `rules_ts` | `evaluate_stage1_rules_ts`: `trade_edge > 1.5 × one_way_cost` **and** trade-level `profit_factor >= 1.15` **and** `time_in_market >= 0.05` **and** block-bootstrap `p < 0.05` **and** `trimmed_sharpe > -0.001` |
| `cross_sectional_ml` | `evaluate_stage1_cross_sectional_ml`: `mean_ic >= 0.015` **and** Newey-West HAC `t >= 2.0` (`>= 10` IC observations) |
| `carry_rv` | `evaluate_stage1_carry_rv`: positive net carry (`> 0.0`) **and** stationary spread (KPSS/ADF stationarity gate via `ml4t-diagnostic`) |
| `meta_overlay` | `evaluate_stage1_meta_overlay`: evaluated via `apply_causal_meta_overlay` against the registered parent return stream (`tail_risk` / drawdown / turnover improvement vs. parent) |

### Stage 2 — causal simulation (`src/warden/engine_sim.py`)

Stage 2, Stage 3, and `qualify` simulate via `ml4t.backtest.Engine` under `simulation_epoch` isolation (`ENGINE_SIMULATION_EPOCH`), enforcing contract `execution_mode`, `execution_delay_bars >= 1` (`signal_delay_bars > 0` raises `DoubleLatencyError`), taker fee + slippage per side, and funding settlement.

### Stage 3 — stress and selection bias (`src/warden/runner.py::run_spec`)

- `stress_1p5_cost_sharpe`: computed analytically from the 1x Engine result (`analytical_1p5x_sharpe`)
- `sr2` (`cost_2x`): full `ml4t.backtest.Engine` simulation with `cost_multiplier=2.0` (`> 0.50`)
- `srd` (`latency_plus1`): full `ml4t.backtest.Engine` simulation with `delay_offset=1` (`> 0.0`)
- `stability` (`neighbor_stability`): `gates.evaluate_neighbor_stability` — returns `neighbor_tested=False`, `stability=None` when `param_grid` is empty; sign-safe stability ratio when `param_grid` is supplied
- `trimmed_sharpe`: `gates.compute_trimmed_sharpe` (`> -0.001`)
- `dsr`: non-ratchet effective-rank DSR (`>= 0.90`) isolated by `simulation_epoch`
- Baseline guardrails: nonlinear ML requires a logged linear baseline (`CausalModelStrategy` Ridge / `ml4t-models` linear / IPCA); `model_tier == "deep"` requires the linear baseline to clear the contract cost-breakeven hurdle.

When `verdict == "REVISE"`, `diagnostics_json["failed_gates"]` lists every failing gate key (`net_sharpe`, `dsr`, `cost_2x`, `latency_plus1`, `neighbor_stability`, `trimmed_sharpe`, `stage1_rules`, `stage1_carry_rv`, `stage1_meta_overlay`, `ic_hac`). Retrospective `source_published_at` data yields `RETROSPECTIVE_REPLICATION_ONLY`; synthetic or ineligible market data yields `NON_PROMOTABLE`.

### Six-check audit (`src/warden/cli.py::cmd_audit_check`)

| # | Check | Coded condition |
|---|---|---|
| 1 | target/rebalance alignment | `stage_reached ∈ {STAGE_2_BACKTEST, STAGE_3_STRESS}` and `label_horizon_bars >= 1` |
| 2 | outlier sensitivity | `trimmed_sharpe > TRIMMED_SHARPE_FLOOR`, `net_sharpe > 0`, and `verdict == CANDIDATE_FOR_QUALIFICATION` |
| 3 | point-in-time causality | run-bound causal proof verified (`causal_proof_for_run`) and `execution_delay_bars >= 1` |
| 4 | fold-local fitting | (`n_folds >= 2` and `embargo_bars >= 1`) or `strategy_family == "rules_ts"` |
| 5 | cost + latency survival | `stress_2x_cost_sharpe > SR2_FLOOR (0.50)` and `delay_plus1_sharpe > SRD_FLOOR (0.0)` |
| 6 | selection-bias accounting | `total_trials_n >= 1` and `deflated_sharpe_prob >= AUDIT_DSR_FLOOR (0.80)` |

### Holdout gate (`src/warden/qualify.py`)

Requires `verdict == CANDIDATE_FOR_QUALIFICATION`, positive validation Net Sharpe, non-synthetic market-eligible dataset, non-quarantined trial, `APPROVED` six-check audit, verified run-bound causal proof, un-unsealed lineage, and exact `strategy_code_sha256` match from `code_closure`. Promotion requires `holdout_net_sharpe > 0.80` and `holdout / validation ratio >= 0.50`.

---

## 5. Vocabulary in the registry

- `trials.stage_reached`: `STAGE_1_SIGNAL`, `STAGE_3_STRESS` (covers Stage 2 + Stage 3 in one record), `FAILED`. (`STAGE_0`, `DATA` appear in `selection_attempts`.)
- `trials.verdict`: `CANDIDATE_FOR_QUALIFICATION`, `REVISE` (see `diagnostics_json["failed_gates"]`), `REJECTED_EARLY`, `FAILED`, `NON_PROMOTABLE`, `RETROSPECTIVE_REPLICATION_ONLY`.
- `hypotheses.status`: `ACTIVE`, `REJECTED`, `REJECTED_ON_HOLDOUT`, `PROMOTED` (only `ACTIVE` accepts new trials).
- `audit_evidence.verdict`: `APPROVED`, `REJECTED`, `REVOKED_INELIGIBLE`.

---

## 6. Budgets & evidence accounting

- **3 trials per hypothesis** (`PER_HYPOTHESIS_CAP = 3`): baseline + up to 2 targeted revisions.
- **`param_grid` entries** are recorded as `selection_attempts` (`kind="param_grid"`), count toward DSR, and populate `neighbor_stability` (`neighbor_tested=False`, `stability=None` when omitted).
- **Lineage cap is six** trials across a parent/child chain.
- **DSR floor is inherited** across contracts sharing `asset_class:frequency` (`contract_search_state`) and isolated per `simulation_epoch`.

---

## 7. What the manifest covers

`.warden_manifest.sha256` is generated by `warden_ops.integrity.generate_manifest` and verified by `warden check-worktree`. It covers `src/warden/**/*.py`, `src/warden_ops/**/*.py`, `src/warden_evidence/**/*.py`, `contracts/*.yaml|yml`, `contracts/datasets/*.json`, `AGENTS.md`, `pyproject.toml`, `uv.lock`, and protected operational scripts (including `scripts/hourly_queue_coordinator.py`, `scripts/run_hourly_pipeline.sh`, `scripts/mcp_warden/adapter.py`, and trust-boundary/snapshot scripts). Docs (`README.md`, `AGENT_RUNBOOK.md`, `CHANGELOG.md`, `docs/`), `experiments/`, `strategies/`, and `.workspace/` are outside the manifest.

---

## 8. Where things live

| Path | Contents |
|---|---|
| `contracts/<id>.yaml` | Frozen evaluation contracts (human-owned) |
| `contracts/datasets/` | Dataset pins / manifests |
| `experiments/active/<spec>.yaml` | Active experiment spec (`contract_id`, `hypothesis_id`, `strategy_module`, `parameters`, `param_grid`, `run_id`, `model_type`) |
| `strategies/baselines/` | Causal baseline templates (`causal_ridge_1h.py`, `causal_linear_portfolio_1h.py`, `causal_ipca_1h.py`) |
| `strategies/candidates/` | Candidate strategies (`CausalRulesStrategy` / `CausalModelStrategy`; legacy `generate_positions` frozen for replay) |
| `.workspace/hypotheses/HYP-*.md` | Pre-registration + logged results table |
| `.workspace/hypotheses/AUDIT_*.md` | Six-check audit output |
| `.workspace/templates/` | `hypothesis.md`, `experiment.yaml` |
| `.workspace/memory/LESSONS.md` | Post-mortems and durable lessons |
| `registry/registry.db` | Authoritative append-only SQLite ledger |
| `registry/returns/<contract>/<run>.parquet` | Per-run net return vectors |
| `registry/backtest_results/<simulation_epoch>/<contract>/<run_id>` | `ml4t.backtest.Engine` exported artifacts |
| `cache/features/*.parquet` | Verified feature matrices |
| `scripts/hourly_queue_coordinator.py` | Typed queue coordinator invoked by `scripts/run_hourly_pipeline.sh` |
| `scripts/mcp_warden/adapter.py` | Reviewed stdio MCP adapter |
| `data/` | Panels; `data/holdout/` is sealed (human-owned) |

---

## 9. Live registry status & family checklist

Inspect live state directly rather than trusting static snapshots:

```bash
uv run --frozen warden status --json
uv run --frozen python scripts/preflight_thesis.py --contract <CONTRACT_ID>
```

Checklist for a new family campaign:
1. Run `uv run --frozen warden check-worktree`, identify `<FAMILY>_*` contracts with both YAML and panel, inspect `contract_search_state` and panel schema (`funding_rate` non-zero check for carry).
2. Report preflight findings before writing specs.
3. One mechanism per hypothesis (`CausalModelStrategy` or `CausalRulesStrategy`; linear/IPCA baseline before nonlinear/deep).
4. Register → `verify` → `screen` / `backtest` (with `>= 2`-point `param_grid` where neighbor stability is claimed) → `verify-causality` → `audit-check`.
5. Close campaign ledgers by script (`scripts/build_*_stage3_closure.py`), never manual transcription.
