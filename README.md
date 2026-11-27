# Quant-Lab: Agent-Native Quantitative Trading Research Environment

**Quant-Lab** is a falsification-first quantitative trading research harness built around immutable evaluation contracts, causal strategy inversion, `ml4t.backtest.Engine` execution physics, and an append-only SQLite + Parquet evidence ledger.

---

## New Coding Agent Quickstart

1. **Read the operating contract, then follow the task map:**
   - [`AGENTS.md`](./AGENTS.md) — Current ownership, invariants and authority.
   - [`CONTEXT-MAP.md`](./CONTEXT-MAP.md) — Task-specific routes to procedures, domain owners and evidence.
   - [`AGENT_RUNBOOK.md`](./AGENT_RUNBOOK.md) — Current thesis intake and approval procedure.
   - For system design or implementation, read [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md), then its linked contracts and plan. [`Agent control operations`](./docs/AGENT-CONTROL-OPERATIONS.md) separates implemented source from installed capabilities.
2. **Inspect the workspace & environment before editing:**
   ```bash
   uv run --frozen warden check-worktree
   uv run --frozen warden status --json
   uv run --frozen python scripts/preflight_thesis.py --contract <CONTRACT_ID>
   ```
3. **Start from the current templates and causal baselines:**
   - Templates: `.workspace/templates/hypothesis.md` and `.workspace/templates/experiment.yaml`
   - `CausalModelStrategy` baselines: `strategies/baselines/causal_ridge_1h.py`, `strategies/baselines/causal_linear_portfolio_1h.py`, `strategies/baselines/causal_ipca_1h.py`
   - `CausalRulesStrategy` template: `strategies/candidates/causal_rules_drawdown_template.py`
4. **Progress eligible work through Warden after prospective approval:**
   - Follow #109/RG-05–07 in [`AGENT_RUNBOOK.md`](./AGENT_RUNBOOK.md): resolve data, register and approve complete intent before candidate implementation, then record independent conformance/novelty for the exact candidate.
   - Put the hypothesis at `.workspace/hypotheses/<ID>.md` and experiment spec at `experiments/active/<spec>.yaml` (both stay in place permanently; scientific lifecycle state lives in `registry/registry.db`).
   ```bash
   uv run --frozen warden advance experiments/active/<spec>.yaml --json
   # Batch all pending specs (cron / unattended runner):
   uv run --frozen warden advance --pending --json
   ```
   `warden advance` checks lifecycle/outcome memory and progresses eligible work through `verify` → `screen` → `backtest` → `audit-check`. It does not approve intent or conformance, replay a rejected screen, refund an interrupted attempt, or unseal a holdout. Inspect existing receipts before retrying an uncertain result. Individual native commands remain available; the principal receives decisions through chat, while the agent handles authorized execution.

---

## ML4T lake store

`warden data build` with a reviewed `ml4t_lake` source reads Hive Parquet from
`ML4T_DATA_STORE`. Point it at the **store directory itself**, e.g.
`export ML4T_DATA_STORE=/path/to/ml4t-data/.data/store`, and use the same value in
`ml4t-data`. Use an absolute path for shells, scheduled runs, and worktrees
that may start in different directories. If the variable is unset, Warden
uses `../ml4t-data/.data/store` relative to the quant-lab checkout — that is, the
sibling must literally be named `ml4t-data` and sit next to the checkout. On a
machine whose data repository lives deeper (for example `dev/quant/ml4t-data`)
the default is not the right store, so set `ML4T_DATA_STORE` explicitly; a
default that is missing or empty fails closed at the read, not with a fallback.
A blank, missing, or non-directory override is an error, not a fallback. Existing
pinned Warden artifacts and contracts are unchanged.

## Repository Architecture & Layout

| Path | Role |
| :--- | :--- |
| `src/warden/` | Deterministic evaluation harness: CLI (`warden.cli`), contracts, empirical Stage-0 feasibility & family Stage-1/Stage-3 gates (`gates.py`), inverted causal protocols (`protocols.py`), `ml4t.backtest.Engine` simulation (`engine_sim.py`), prefix-invariance & purged walk-forward checks (`leakage.py`), `code_closure` identity (`codeid.py`), append-only registry (`registry.py`), and one-shot holdout qualification (`qualify.py`). |
| `src/warden_evidence/` | Data admission (`data_admission.py`), feature caching (`feature_cache.py`), and `FeedSpec` / `OHLCVValidator` / `SessionAssigner` market validation (`market_validation.py`). |
| `src/warden_ops/` | Manifest integrity & repo-wide / `strategies/` AST import boundary enforcement (`integrity.py`), checkpointing (`checkpoint.py`), and read-only projections (`readonly.py`). |
| `contracts/` | Human-owned YAML evaluation contracts and pinned dataset provenance manifests (`contracts/datasets/*.json`). |
| `strategies/baselines/` | Causal baseline templates: `causal_ridge_1h.py`, `causal_linear_portfolio_1h.py`, `causal_ipca_1h.py` (plus frozen legacy `cs_ridge_1h.py`). |
| `strategies/candidates/` | Candidate strategy modules (including `causal_rules_drawdown_template.py`; legacy `generate_positions()` modules are frozen for historical replay only). |
| `registry/` | Authoritative SQLite ledger (`registry/registry.db`), immutable return streams (`registry/returns/`), and `ml4t.backtest.Engine` backtest artifacts (`registry/backtest_results/<simulation_epoch>/<contract>/<run_id>`). |
| `scripts/` | Operational tooling: thin hourly cron wrapper (`run_hourly_pipeline.sh` → `warden advance --pending`), reviewed stdio MCP adapter (`mcp_warden/adapter.py`), environment probe (`inspect_env.py`), thesis preflight (`preflight_thesis.py`), and shadow engine parity harness (`shadow_engine_parity.py`). |

---

## Key Documentation

| Document | Purpose |
| :--- | :--- |
| [`AGENTS.md`](./AGENTS.md) | Operating contract, four-way ownership, falsification funnel, CLI contracts, and agent-maintained status. |
| [`AGENT_RUNBOOK.md`](./AGENT_RUNBOOK.md) | Thesis intake procedure, verification table, and ledger rules. |
| [`CONTEXT-MAP.md`](./CONTEXT-MAP.md) | Task routing, domain ownership and current-vs-target truth rules. |
| [`CONTEXT.md`](./CONTEXT.md) | Canonical domain meanings; candidate, lineage, selection debt, exposure and authority stay distinct. |
| [`docs/WORKFLOW.md`](./docs/WORKFLOW.md) | Current #109/RG-01–09 progression and pointers to exact evaluator rules. |
| [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) | Target evidence-driven control system and immutable release authority. |
| [`docs/AGENT-CONTROL-CONTRACTS.md`](./docs/AGENT-CONTROL-CONTRACTS.md) | Approved situation/action/receipt, authority, recovery and knowledge contracts. |
| [`docs/AGENT-CONTROL-PLAN.md`](./docs/AGENT-CONTROL-PLAN.md) | Dependency-ordered delivery, behavioral acceptance, resource measurements and cutover gates. |
| [`docs/AGENT-CONTROL-OPERATIONS.md`](./docs/AGENT-CONTROL-OPERATIONS.md) | Implemented typed control, durable jobs, findings and release protocols; production boundaries and recovery. |
| [`docs/AGENT-CONTROL-ACCEPTANCE-20261127.md`](./docs/AGENT-CONTROL-ACCEPTANCE-20261127.md) | Exact frozen source, A01–A25 evidence, full-suite omissions, measurements and preservation. |
| [`docs/AGENT-CONTROL-CUTOVER-20261127.md`](./docs/AGENT-CONTROL-CUTOVER-20261127.md) | One consolidated blocked AC-10 decision; exact subjects and independent authority prerequisites. |
| [`docs/DATA-REQUIREMENT.md`](./docs/DATA-REQUIREMENT.md) | `warden resolve-data` / `data compile`: decisions, the superset opt-in (`--allow-superset`), and what each answer carries. |
| [`docs/EXIT-CODES.md`](./docs/EXIT-CODES.md) | The one exit-code vocabulary (`0` success, `1` evidence refused, `2` request refused, `3` not satisfiable, `75` busy) and the documented `data build` exception. |
| [`STAGE3_CAMPAIGN_PROMPT.md`](./STAGE3_CAMPAIGN_PROMPT.md) | Objective brief for direct autonomous execution, early stopping and evidence-based continuation. |
| [`CHANGELOG.md`](./CHANGELOG.md) | Dated summary of architectural changes and verification runs. |
| [`docs/archive/README.md`](./docs/archive/README.md) | Historical RFC, bootstrap tickets, compatibility spec, handbook, and audit/approval trails. |

---

## Core Protocols & Falsification Funnel

- **Inverted Causal Protocols (`src/warden/protocols.py`):** New `ACTIVE` hypotheses must implement `CausalModelStrategy` (`pipeline` + `build_estimator`) or `CausalRulesStrategy` (`required_symbols` + `configure_strategy`). Legacy `generate_positions()` modules are frozen for read-only historical replay only.
- **All Four Strategy Families Wired:** `rules_ts`, `cross_sectional_ml`, `carry_rv`, and `meta_overlay` each enforce dedicated Stage-1 and Stage-3 gates, plus `ml4t-models` linear/IPCA baseline hurdles before `model_tier == "deep"` strategies.
- **Execution & Stress:** Stage 2, Stage 3, and `qualify` execute via `ml4t.backtest.Engine` (`src/warden/engine_sim.py`) under `simulation_epoch` isolation, computing analytical `1.5x` cost stress (`stress_1p5_cost_sharpe`) and full `2.0x` Engine re-simulation (`cost_2x`), `+1` bar latency (`latency_plus1`), trimmed Sharpe (`trimmed_sharpe`), effective-rank DSR, and optional `param_grid` neighbor stability.

---

## Verified Commands

```bash
# Inspect CLI & worktree integrity (manifest checksums + AST import boundaries)
uv run --frozen warden --help
uv run --frozen warden check-worktree
uv run --frozen warden status --json

# Run test suite (parallel via pytest-xdist: -n 4 --dist worksteal) and lint
uv run --frozen pytest -q
uv run --frozen ruff check src/ tests/
```
