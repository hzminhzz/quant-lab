# Quant-Lab: Agent-Native Quantitative Trading Research Environment

**Quant-Lab** is a lean, agent-independent quantitative trading research environment designed from first principles for systematic hypothesis discovery, falsification-first statistical testing, and transaction-cost-aware native backtesting across crypto perpetuals, equity indices, FX, and metals (`5m`, `15m`, `1h`, and `1d` swing horizons).

---

## Repository Architecture & Specifications

This repository is initialized with the complete technical architecture, agent-independent execution contracts, bounded implementation tickets, and standalone reference vertical slice:

| Document / Artifact | Purpose |
| :--- | :--- |
| [`agent_native_quant_research_rfc.md`](./agent_native_quant_research_rfc.md) | **Authoritative Architecture RFC (`RFC-2026-Q4-001`)**: Four-stratum system topology, deterministic `Warden` firewall, tiered statistical gates (`G0`–`G4`), `ResearchRegistry` SQLite schema, and ML4T integration boundaries. |
| [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md) | **5-Day VPS Implementation Roadmap**: Dependency DAG, critical path, milestone breakdown (`M1`–`M5`), and epistemic verification boundaries (verified research vs. VPS inspection requirements). |
| [`TICKETS.md`](./TICKETS.md) | **12 Bounded Implementation Tickets (`TKT-01` to `TKT-12`)**: Single-session, independently testable tickets with exact scopes, out-of-scope guards, automated pytest suites, and Definitions of Done. |
| [`AGENT_COMPATIBILITY.md`](./AGENT_COMPATIBILITY.md) | **Coding-Agent Independence Specification**: Zero-lock-in filesystem, CLI (`quantlab`), and JSON Schema protocol supporting **Oh My Pi (`omp`)**, **OpenCode (`opencode`)**, **Claude Code (`claude`)**, **Codex CLI (`codex`)**, and **Herdr (`herdr`)**. |
| [`AGENT_EXECUTION.md`](./AGENT_EXECUTION.md) | **Universal Agent Execution Contract**: Reusable 10-rule prompt template for executing exactly one ticket per session, plus the ready-to-paste `TKT-01` prompt. |
| [`run_vertical_slice.py`](./run_vertical_slice.py) | **Reference Vertical Slice**: Self-contained executable reference demonstrating `rules_ts` (Donchian Breakout) and `cross_sectional_ml` (LightGBM Ranker) through `PurgedWalkForwardCV`, Newey-West HAC $t$-statistics, and Effective-Rank Deflated Sharpe Ratio ($N_{\text{eff}}$ DSR). |

---

## Core Architectural Principles

1. **Four-Stratum Separation of Concerns:**
   - **Stratum 1 — Immutable Data Lakehouse (`data/`):** Read-only Parquet storage queried via DuckDB and Polars lazy frames with strict `available_at_utc <= decision_timestamp_utc` point-in-time enforcement.
   - **Stratum 2 — Deterministic `Warden` & ML4T Engine (`src/quantlab/`):** Pure Python (`<= 2,500` LOC target) owning temporal splitting (`PurgedWalkForwardCV`), execution physics (`Broker` next-bar fills, funding rates, slippage), and statistical gates (`N_eff` DSR, PBO, Block Bootstrap).
   - **Stratum 3 — Hybrid Research Memory (`registry/experiments.db` + `RESEARCH_STATE.md`):** Append-only SQLite registry logging *every* trial (including failed and killed runs) so $N_{\text{eff}}$ accounts for the full cluster of correlated trials, paired with a `<150`-line narrative state summary.
   - **Stratum 4 — Pluggable Coding-Agent Harnesses:** Any CLI-capable coding agent operates strictly as an unprivileged caller of `quantlab` CLI subcommands and JSON Schema contracts.

2. **Falsification-First Funnel (`G0` $\rightarrow$ `G4`):**
   - **`G0` (Schema & AST Gate, `< 1s`):** Blocks forbidden imports (`os`, `subprocess`, `socket`), future shifts (`shift(-k)`), and unstructured YAML/JSON specs.
   - **`G1` (Signal Screening Gate, `< 5s`):** Strategy-family-aware screening (`rules_ts` Block-Bootstrap Expectancy vs. `cross_sectional_ml` Newey-West HAC Rank IC) before running full broker simulation.
   - **`G2` (Temporal OOS Gate, `< 30s`):** Purged Walk-Forward CV with mandatory purge (`t1`) and embargo buffers.
   - **`G3` (Execution Friction Gate, `< 60s`):** Next-bar open fills, $1.5\times$ fee/slippage stress test, and historical funding-rate deduction.
   - **`G4` (Multiple-Testing Gate, `< 5s`):** Eigenvalue Effective-Rank Deflated Sharpe Ratio ($\text{DSR}(N_{\text{eff}}) \ge 0.95$) and Probability of Backtest Overfitting ($\text{PBO} \le 0.25$).

---

## Quickstart: Executing the Implementation Plan

Once this initial architecture PR is merged into `main`, start implementation with **Ticket `TKT-01`** (`Repository Scaffold, pyproject.toml, and Core Domain Schemas`) using any supported coding agent:

```bash
# Option A: Oh My Pi (OMP)
omp "Read AGENT_EXECUTION.md and execute Ticket TKT-01 from TICKETS.md."

# Option B: OpenCode
opencode run "Read AGENT_EXECUTION.md and execute Ticket TKT-01 from TICKETS.md."

# Option C: Claude Code
claude "Read AGENT_EXECUTION.md and execute Ticket TKT-01 from TICKETS.md."

# Option D: Codex CLI
codex "Read AGENT_EXECUTION.md and execute Ticket TKT-01 from TICKETS.md."
```

To run the standalone reference vertical slice directly:

```bash
python3 run_vertical_slice.py
```
