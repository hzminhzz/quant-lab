# Quant-Lab Implementation Plan (`IMPLEMENTATION_PLAN.md`)

**Document ID:** PLAN-2026-Q4-MASTER  
**Authoritative Design Reference:** `agent_native_quant_research_rfc.md` (RFC-2026-Q4-001)  
**Vertical Slice Reference:** `run_vertical_slice.py`  
**Compatibility Specification:** `AGENT_COMPATIBILITY.md`  
**Execution Contract:** `AGENT_EXECUTION.md`  
**Ticket Specifications:** `TICKETS.md` (`TKT-01` through `TKT-12`)  

---

## 1. Executive Summary & Architectural Continuity

This implementation plan converts the approved `quant-lab` Architecture RFC (`agent_native_quant_research_rfc.md`) and its five-day Linux VPS rollout roadmap into **12 dependency-aware, single-session bounded tickets (`TKT-01` through `TKT-12`)** that any coding agent (**Oh My Pi (`omp`)**, **OpenCode (`opencode`)**, **Claude Code (`claude`)**, **Codex CLI (`codex`)**, or future harnesses) can execute deterministically.

### 1.1 Preserved Architectural Invariants (Zero Redesign)
1. **Option 1 Clean-Room Repository (`quant-lab`):** A minimal, flat repository (`< 1,200 LOC` of owned governance code in `src/warden/`) backed directly by the six published [`ml4t-*` libraries](https://ml4trading.io/libraries/) (`ml4t-data`, `ml4t-engineer`, `ml4t-models`, `ml4t-diagnostic`, `ml4t-backtest`, `ml4t-live`, plus `ml4t-specs`).
2. **Four-Way Ownership Partition:**
   - **ML4T Libraries** own canonical data schemas, 120 TA-Lib-validated Polars/Numba indicators, triple-barrier/ATR labeling, Purged Walk-Forward / CPCV splitters, Newey-West HAC IC, Deflated Sharpe Ratio (`deflated_sharpe_ratio` with `effective_rank`), and causal event-driven order simulation (`ExecutionMode.NEXT_BAR` / `ExecutionPrice.QUOTE_SIDE`).
   - **Deterministic Harness (`src/warden/`)** owns `EvaluationContract` enforcement, mandatory cost/slippage/funding floors, validation label stripping, prefix-invariance lookahead verification, the 3-strike trial cap per hypothesis, and append-only writes to `registry/registry.db`.
   - **Coding Agent (`omp` / `opencode` / `claude` / `codex` + 61 `ml4t/skills`)** operates strictly on `experiments/active/`, `strategies/candidates/`, and `.workspace/`.
   - **Human Principal** owns `contracts/*.yaml`, one-shot holdout qualification (`warden qualify`), and `ml4t-live` capital allocation.
3. **Five-Stage Falsification Funnel with Strategy-Family Branching:** Preserves Stage 0 (Feasibility & Cost Floor), Stage 1 (Family-Specific Triage: Cross-Sectional Rank IC & HAC $t$-stat vs. Time-Series Conditional Edge & Profit Factor vs. Carry Capture vs. Meta-Overlay Tail/Turnover Reduction), Stage 2 (`ml4t-backtest` Simulation), Stage 3 (DSR across all contract trials $K_{	ext{eff}}$, `2x` cost stress, `+1 bar` latency stress, $\pm 15\%$ parameter neighborhood stability), and Stage 4 (6-Check Adversarial Audit & Handoff).

---

## 2. Five-Day Milestone Mapping & Ticket Breakdown

The original 5-day VPS rollout from Section 11 of `agent_native_quant_research_rfc.md` is preserved in exact chronological order and decomposed into 12 bounded tickets so each ticket touches a single cohesive module boundary and completes in one coding-agent session (30–90 minutes):

| Rollout Day | Original RFC Milestone | Bounded Tickets | Target Modules / Artifacts |
| :--- | :--- | :--- | :--- |
| **Day 1** | **Bootstrap Clean Room, Install 61 ML4T Skills & Configure Agent Interfaces** | **`TKT-01`:** VPS Environment Audit & Clean-Room Repo Bootstrap<br>**`TKT-02`:** Portable `AGENTS.md`, 61 `ml4t/skills` Installation & Optional Harness Adapters (`omp`, `opencode`, `claude`, `codex`) | `pyproject.toml`, `uv.lock`, `scripts/inspect_env.py`, `.workspace/env_manifest.json`, `AGENTS.md`, `CLAUDE.md`, `.agents/skills/ml4t-*`, `adapters/{omp,opencode,claude,codex}/` |
| **Day 2** | **Salvage One Canonical Dataset, Lock Holdout & Define Contracts** | **`TKT-03`:** Immutable Contracts Schema (`src/warden/contracts.py`) & `crypto_perps_1h_v1.yaml`<br>**`TKT-04`:** Read-Only Dataset Salvage (`quant-research` -> `ml4t-specs` Parquet) & POSIX Holdout Split | `src/warden/contracts.py`, `contracts/crypto_perps_1h_v1.yaml`, `scripts/salvage_crypto_perps.py`, `/data/exploratory/` (`0644`), `/data/holdout/` (`0640 root:trader`) |
| **Day 3** | **Deploy `warden` Core: SQLite Trial Registry, Leakage Prover, Gates & Runner** | **`TKT-05`:** Append-Only SQLite Registry & Effective-Rank DSR Calculator (`registry.py`)<br>**`TKT-06`:** Prefix-Invariance Leakage Prover & Content-Addressed Feature Cache (`leakage.py`)<br>**`TKT-07`:** Family-Specific Stage 1 Gates, Stage 2/3 `ml4t-backtest` Runner & CLI (`gates.py`, `runner.py`, `cli.py`) | `src/warden/registry.py`, `src/warden/leakage.py`, `src/warden/gates.py`, `src/warden/runner.py`, `src/warden/cli.py`, `scripts/check_loc_budget.py`, `.warden_manifest.sha256` |
| **Day 4** | **Execute Both Vertical Slices (`rules_ts` & `cross_sectional_ml`) & Herdr Automation** | **`TKT-08`:** Vertical Slice 1 — Rules-Based Volatility Breakout Strategy (`HYP-2026-001`)<br>**`TKT-09`:** Vertical Slice 2 — Cross-Sectional Funding & Reversal ML Strategy (`HYP-2026-002`)<br>**`TKT-10`:** Herdr Worker Pane Integration, Session `handoff`/`continue` & 6-Check Adversarial Audit | `experiments/active/hyp_2026_001.*`, `strategies/candidates/vol_breakout_1h.py`, `experiments/active/hyp_2026_002.*`, `strategies/candidates/cs_ridge_1h.py`, `scripts/herdr_dispatch.sh` |
| **Day 5** | **Acceptance Suite, Holdout Qualification Gate & Legacy Repo Freeze** | **`TKT-11`:** Human-Only Holdout Qualification Gate (`warden qualify`) & Full Acceptance Test Suite<br>**`TKT-12`:** Ergonomics Benchmark Report & Human-Approved Legacy `quant-research` Read-Only Freeze | `tests/acceptance/test_rfc_acceptance.py`, `src/warden/qualify.py`, `.workspace/memory/ERGONOMICS_BENCHMARK.md`, `scripts/freeze_legacy_repo.sh` |

---

## 3. Ticket Dependency Graph (DAG) & Critical Path

### 3.1 Dependency Graph (ASCII DAG)

```
                         ┌──────────────────────────────┐
                         │ TKT-01: VPS Audit & Bootstrap│
                         └──────────────┬───────────────┘
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
┌────────────────────────────────────┐        ┌────────────────────────────────────┐
│ TKT-02: AGENTS.md, 61 ml4t/skills  │        │ TKT-03: Contracts Schema & YAML    │
│ & Optional OMP/OpenCode Adapters   │        │ (`src/warden/contracts.py`)        │
└────────────────┬───────────────────┘        └────────┬──────────────────┬────────┘
                 │                                     │                  │
                 │            ┌────────────────────────┘                  │
                 │            ▼                                           ▼
                 │   ┌─────────────────────────────────┐      ┌────────────────────────────────────┐
                 │   │ TKT-04: Dataset Salvage &       │      │ TKT-05: SQLite Registry &          │
                 │   │ POSIX Holdout Lock [HUMAN GATE] │      │ Effective-Rank DSR (`registry.py`) │
                 │   └────────┬────────────────────────┘      └───────────┬────────────────────────┘
                 │            │                                           │
                 │            │       ┌───────────────────────────────────┘
                 │            ▼       ▼
                 │   ┌─────────────────────────────────┐
                 │   │ TKT-06: Prefix-Invariance       │
                 │   │ Leakage Prover & Feature Cache  │
                 │   └────────┬────────────────────────┘
                 │            │
                 └───────────►▼
                     ┌─────────────────────────────────┐
                     │ TKT-07: Family Gates, Backtest  │
                     │ Runner & `warden` CLI + Lock    │
                     └────────┬───────────────┬────────┘
                              │               │
              ┌───────────────┘               └───────────────┐
              ▼                                               ▼
┌────────────────────────────────────┐       ┌────────────────────────────────────┐
│ TKT-08: Vertical Slice 1           │       │ TKT-09: Vertical Slice 2           │
│ Rules-Based Strategy (`HYP-001`)   │       │ Cross-Sectional ML (`HYP-002`)     │
└─────────────┬──────────────────────┘       └────────────────┬───────────────────┘
              │                                               │
              └───────────────┐               ┌───────────────┘
                              ▼               ▼
                     ┌─────────────────────────────────┐
                     │ TKT-10: Herdr Worker Dispatch,  │
                     │ Handoff/Continue & 6-Check Audit│
                     └────────────────┬────────────────┘
                                      │
                                      ▼
                     ┌─────────────────────────────────┐
                     │ TKT-11: Holdout Qualification   │
                     │ Gate & 6-Part Acceptance Suite  │
                     └────────────────┬────────────────┘
                                      │
                                      ▼
                     ┌─────────────────────────────────┐
                     │ TKT-12: Benchmark Report &      │
                     │ Freeze Legacy Repo [HUMAN GATE] │
                     └─────────────────────────────────┘
```

### 3.2 Critical Path to the First Working Vertical Slice

The **shortest critical path** to executing the first end-to-end production vertical slice on real/synthetic VPS data is **7 sequential tickets** (`TKT-01 -> TKT-03 -> TKT-05 -> TKT-06 -> TKT-07 -> TKT-08` [or `TKT-09`]):
1. **`TKT-01`** establishes the Python 3.12+ `uv` environment, installs `ml4t-*` wheels, and records exact API signatures in `.workspace/env_manifest.json`.
2. **`TKT-03`** implements `EvaluationContract`, `HypothesisSpec`, and `ExperimentResult` in `src/warden/contracts.py` plus `contracts/crypto_perps_1h_v1.yaml`. *(Note: `TKT-02` and `TKT-04` can run in parallel with `TKT-03`/`TKT-05`; `TKT-06` and `TKT-07` include a deterministic `SyntheticProvider` fallback so the vertical slice can be tested even before human approval of `TKT-04`'s `/data/holdout/` POSIX chown).*
3. **`TKT-05`** implements the append-only SQLite `ResearchRegistry` and `compute_effective_trials_and_dsr` in `src/warden/registry.py`.
4. **`TKT-06`** implements `FeaturePipelineProtocol`, the prefix-invariance lookahead prover, and content-addressed Parquet feature caching in `src/warden/leakage.py`.
5. **`TKT-07`** wires the Stage 1 family-specific gates (`gates.py`), the Stage 2/3 `ml4t-backtest` & `ml4t-diagnostic` runner (`runner.py`), and the `warden` CLI (`cli.py`).
6. **`TKT-08` & `TKT-09`** execute the rules-based (`HYP-2026-001`) and cross-sectional ML (`HYP-2026-002`) vertical slices through `uv run warden`.

---

## 4. Epistemic Verification Matrix: Facts vs. VPS Assumptions vs. Blocker Resolutions

Because the Linux VPS and private `hzminhzz/quant-research` repository cannot be inspected remotely, every ticket separates **Already Verified Facts** from **Assumptions Requiring Live VPS Inspection** and **Human Approval Gates**:

| Category | Item | Epistemic Status & Handling in Tickets |
| :--- | :--- | :--- |
| **Verified Fact** | `run_vertical_slice.py` mathematical & SQLite registry logic | **Verified in Sandbox:** Executed end-to-end on Python 3.11+ with `numpy`, `scipy`, `pyarrow`, and `sqlite3`, confirming that $N$ and $K_{	ext{eff}}$ (`1.00 -> 1.39 -> 1.65 -> 2.55`) accumulate across trials and gate high-turnover strategies via 2x-cost stress and DSR. **Does NOT by itself prove live `ml4t-*` wheel integration on your VPS.** |
| **Verified Fact** | `can1357/oh-my-pi` (`omp`), `opencode`, and `herdr` CLI/config contracts | **Verified from Official Docs:** `omp` natively loads `AGENTS.md` and `.agents/skills/*/SKILL.md` (`priority 70`), uses `.omp/agents/*.md` for `task` subagents, and integrates with `Herdr` via `--kind omp` ([can1357/oh-my-pi](https://github.com/can1357/oh-my-pi); [herdr.dev/docs/integrations](https://herdr.dev/docs/integrations/)). |
| **Known Upstream Blocker & Resolution** | `ml4t-engineer` `compute_features(..., calendar=None)` API difference between PyPI `v0.1.6` and GitHub `main` (`v0.2`) | **Documented Blocker ([github.com/ml4t/engineer](https://github.com/ml4t/engineer)):** Release `0.1.6` on PyPI does not accept the `calendar=None` keyword argument introduced in `0.2`. **Resolution in `TKT-01` & `TKT-06`:** `scripts/inspect_env.py` inspects `inspect.signature(ml4t.engineer.compute_features)` at bootstrap and `src/warden/leakage.py` dispatches `calendar=None` only if present in the signature. |
| **Known Upstream Blocker & Resolution** | SQLite WAL / file-locking errors on virtual or network-mounted filesystems | **Documented Blocker (Observed in Sandbox):** Standard SQLite rollback journals can raise `sqlite3.OperationalError: disk I/O error` on certain virtualized mounts. **Resolution in `TKT-05`:** `ResearchRegistry._connect()` explicitly tests `PRAGMA journal_mode=WAL;` on the VPS filesystem and falls back cleanly to `PRAGMA journal_mode=MEMORY; PRAGMA synchronous=FULL;` if POSIX `fcntl` locks fail. |
| **VPS Assumption Requiring Inspection** | Python version (`3.12`–`3.14`), `uv`, `herdr`, and coding-agent binaries on the VPS | **Checked in `TKT-01` Step 1:** `ml4t-data`, `ml4t-diagnostic`, `ml4t-backtest`, and `ml4t-live` require Python `>=3.12,<3.15` ([github.com/ml4t/data](https://github.com/ml4t/data)). `TKT-01` checks `python3 --version` and uses `uv python install 3.12` if the host default is older (`3.10`/`3.11`) or newer (`3.15`). |
| **VPS Assumption Requiring Inspection** | Location and column schema of existing `quant-research` Parquet files | **Checked in `TKT-04` Step 1:** `TKT-04` runs a read-only schema inspection script on `~/quant-research` before writing a non-destructive export script (`scripts/salvage_crypto_perps.py`) that maps existing columns to `ml4t-specs` (`timestamp`, `asset`, `open`, `high`, `low`, `close`, `volume`, `funding_rate`). |
| **Human Approval Gate (Privileged / Destructive)** | Creating `/data/holdout/` with `root:trader` (`0640`), locking `src/warden/` (`0644`), and freezing `~/quant-research` (`chmod -R a-w`) | **Explicit Human Sign-Off Required in `TKT-04`, `TKT-07`, `TKT-11`, and `TKT-12`:** Coding agents must **never** run `sudo chown`, `chmod -R a-w ~/quant-research`, or `warden qualify --unseal-holdout` autonomously. The agent prepares and tests the script, then pauses for human execution. |

---

## 5. Concise List of Differences Between This Plan and the Original RFC

As required by Section 6 of your instructions, here is the complete, exhaustive list of differences between this implementation plan and `agent_native_quant_research_rfc.md`:

1. **Coding-Agent Independence (Requirement 2):** Replaced the mandatory root `opencode.json` dependency with a harness-neutral core (`AGENTS.md`, `.agents/skills/ml4t-*/SKILL.md`, and `uv run warden <subcommand> --json`) plus optional, non-load-bearing adapters in `adapters/{omp,opencode,claude,codex}/` (documented in `AGENT_COMPATIBILITY.md` and implemented in `TKT-02`).
2. **Defense-in-Depth Write Protection (`TKT-07`):** Because `opencode.json` `"edit": {"*": "deny"}` is no longer assumed to be active when running `omp`, `claude`, or `codex`, `warden` now includes a built-in manifest integrity check (`warden check-worktree` against `.warden_manifest.sha256` + Git pre-commit hook) that aborts any run if `src/warden/**`, `contracts/**`, or `AGENTS.md` have been modified without human re-signing.
3. **Portable Session Handoffs & Audit CLI (`TKT-07` & `TKT-10`):** Added `uv run warden handoff`, `uv run warden continue`, and `uv run warden audit-check --run-id <ID>` to `src/warden/cli.py` so any coding agent can generate `# expect:` transition files and run the 6-check adversarial audit without relying on harness-specific slash commands or subagent plugins.
4. **Upstream `ml4t-engineer` Version Adapter (`TKT-01` & `TKT-06`):** Added runtime signature inspection for `ml4t.engineer.compute_features` to handle the `calendar=None` parameter difference between PyPI `v0.1.6` and GitHub `v0.2`.
