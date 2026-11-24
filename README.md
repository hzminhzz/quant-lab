# Quant-Lab: Agent-Native Quantitative Trading Research Environment

**Quant-Lab** is a falsification-first quantitative trading research harness built around immutable evaluation contracts, causal strategy inversion, `ml4t.backtest.Engine` execution physics, and an append-only SQLite + Parquet evidence ledger.

---

## Repository Architecture & Layout

| Path | Role |
| :--- | :--- |
| `src/warden/` | Deterministic evaluation harness: CLI (`warden.cli`), contracts, empirical Stage-0 feasibility & family Stage-1/Stage-3 gates (`gates.py`), inverted causal protocols (`protocols.py`), `ml4t.backtest.Engine` simulation (`engine_sim.py`), prefix-invariance & purged walk-forward checks (`leakage.py`), `code_closure` identity (`codeid.py`), append-only registry (`registry.py`), and one-shot holdout qualification (`qualify.py`). |
| `src/warden_evidence/` | Data admission (`data_admission.py`), feature caching (`feature_cache.py`), and `FeedSpec` / `OHLCVValidator` / `SessionAssigner` market validation (`market_validation.py`). |
| `src/warden_ops/` | Manifest integrity & repo-wide / `strategies/` AST import boundary enforcement (`integrity.py`), checkpointing (`checkpoint.py`), and read-only projections (`readonly.py`). |
| `contracts/` | Human-owned YAML evaluation contracts and pinned dataset provenance manifests (`contracts/datasets/*.json`). |
| `strategies/baselines/` | Causal baseline templates: `causal_ridge_1h.py`, `causal_linear_portfolio_1h.py`, `causal_ipca_1h.py` (plus frozen legacy `cs_ridge_1h.py`). |
| `strategies/candidates/` | Candidate strategy modules (including `causal_rules_drawdown_template.py`). |
| `registry/` | Authoritative SQLite ledger (`registry/registry.db`), immutable return streams (`registry/returns/`), and `ml4t.backtest.Engine` backtest artifacts (`registry/backtest_results/<simulation_epoch>/<contract>/<run_id>`). |
| `scripts/` | Operational tooling: typed hourly queue coordinator (`hourly_queue_coordinator.py`, invoked by `run_hourly_pipeline.sh`), reviewed stdio MCP adapter (`mcp_warden/adapter.py`), environment probe (`inspect_env.py`), thesis preflight (`preflight_thesis.py`), and shadow engine parity harness (`shadow_engine_parity.py`). |

---

## Key Documentation

| Document | Purpose |
| :--- | :--- |
| [`AGENTS.md`](./AGENTS.md) | Operating contract, four-way ownership, falsification funnel, CLI contracts, and agent-maintained status. |
| [`AGENT_RUNBOOK.md`](./AGENT_RUNBOOK.md) | Thesis intake procedure, verification table, and ledger rules. |
| [`CHANGELOG.md`](./CHANGELOG.md) | Dated summary of architectural changes and verification runs. |
| [`agent_native_quant_research_rfc.md`](./agent_native_quant_research_rfc.md) | Architecture RFC (`RFC-2026-Q4-001`). |
| [`AGENT_COMPATIBILITY.md`](./AGENT_COMPATIBILITY.md) | Multi-harness compatibility contract (`omp`, `opencode`, `claude`, `codex`, `herdr`). |
| [`TICKETS.md`](./TICKETS.md) | Bounded implementation ticket specifications. |

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

# Run thesis workflow
uv run --frozen warden verify experiments/active/<spec>.yaml --json
uv run --frozen warden screen experiments/active/<spec>.yaml --json
uv run --frozen warden backtest experiments/active/<spec>.yaml --json
uv run --frozen warden audit-check --run-id <RUN_ID> --json

# Run test suite (parallel via pytest-xdist: -n 4 --dist worksteal) and lint
uv run --frozen pytest -q
uv run --frozen ruff check <paths>
```
