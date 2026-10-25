# Pull Request: Initialize Quant-Lab Architecture RFC, Agent-Independent Implementation Plan & Reference Vertical Slice

- **Repository:** `https://github.com/hzminhzz/quant-lab`
- **Source Branch:** `feat/initial-architecture-and-plan`
- **Target Branch:** `main`
- **PR Title:** `chore(arch): initialize quant-lab architecture RFC, agent-independent implementation plan, and vertical slice`
- **Status:** Ready for Human Architect Review & Merge

---

## 1. Summary

This Pull Request initializes `hzminhzz/quant-lab` with the complete first-principles **Agent-Native Quantitative Trading Research Environment** architecture, multi-harness coding-agent compatibility specifications, a 12-ticket implementation roadmap, and the verified standalone vertical slice.

It establishes the foundational blueprints before any production package code (`TKT-01` through `TKT-12`) is executed by coding agents (`omp`, `opencode`, `claude`, `codex`).

---

## 2. Included Deliverables

| File | Size | Description |
| :--- | :--- | :--- |
| `README.md` | ~4.2 KB | Repository overview, four-stratum architecture summary, `G0`–`G4` falsification funnel, and quickstart CLI commands. |
| `agent_native_quant_research_rfc.md` | ~144 KB | **Authoritative Architecture RFC (`RFC-2026-Q4-001`)**: Full system design, `Warden` deterministic engine, `ResearchRegistry` SQLite schema, ML4T integration, and statistical validation math (`PurgedWalkForwardCV`, Newey-West HAC IC, $N_{\text{eff}}$ DSR). |
| `IMPLEMENTATION_PLAN.md` | ~19 KB | **5-Day VPS Implementation Roadmap**: Milestone structure (`M1`–`M5`), ticket dependency DAG, critical path, and explicit epistemic boundaries between verified facts and VPS-dependent checks. |
| `TICKETS.md` | ~50 KB | **12 Bounded Implementation Tickets (`TKT-01`–`TKT-12`)**: Self-contained, single-session engineering tickets with strict scope boundaries, pytest suites, and Definitions of Done. |
| `AGENT_COMPATIBILITY.md` | ~17 KB | **Coding-Agent Independence Specification**: Portable interface (`AGENTS.md`, `quantlab` CLI JSON stdout contract, `skills/*/SKILL.md`, JSON Schemas) supporting Oh My Pi (`omp`), OpenCode (`opencode`), Claude Code (`claude`), Codex CLI (`codex`), and Herdr (`herdr`). |
| `AGENT_EXECUTION.md` | ~9.8 KB | **Universal Agent Execution Contract**: 10-rule single-ticket execution prompt template and ready-to-run `TKT-01` prompt. |
| `run_vertical_slice.py` | ~33 KB | **Standalone Reference Vertical Slice**: Executable end-to-end validation script demonstrating `rules_ts` and `cross_sectional_ml` flows through the SQLite registry and `G0`–`G4` statistical gates. |

---

## 3. Architectural & Governance Highlights

1. **Strict Separation of Agent Reasoning and Deterministic Validation:**
   - Coding agents cannot modify `src/quantlab/warden/`, `registry/experiments.db`, or `data/` during research runs.
   - All backtesting, temporal purging/embargoing, cost modeling, and multiple-testing adjustments execute inside deterministic Python (`Warden`).
2. **Coding-Agent Harness Independence:**
   - Replaces single-harness coupling with a zero-framework filesystem and CLI boundary (`AGENTS.md` + `skills/` + `quantlab <subcommand> --json`).
   - Supports **Oh My Pi (`omp`)**, **OpenCode (`opencode`)**, **Claude Code (`claude`)**, and **Codex CLI (`codex`)** interchangeably.
3. **Trial Inflation & Overfitting Defense:**
   - Every hypothesis execution—including syntax failures (`G0`), early screening rejections (`G1`), and OOS failures (`G2`/`G3`)—is permanently recorded in `registry/experiments.db` before evaluation completes.
   - Selection bias is penalized via Eigenvalue Effective-Rank Deflated Sharpe Ratio ($N_{\text{eff}}$ DSR) across trial return correlation matrices.

---

## 4. Verification & Pre-Merge Checklist

- [x] `run_vertical_slice.py` executes cleanly with zero lookahead leakage, verifying both `rules_ts` (Donchian Breakout) and `cross_sectional_ml` (LightGBM Ranker) evaluation paths.
- [x] All 12 tickets (`TKT-01` through `TKT-12`) in `TICKETS.md` have explicit dependencies, file scopes, out-of-scope guards, automated test commands, and Definitions of Done.
- [x] `AGENT_COMPATIBILITY.md` and `AGENT_EXECUTION.md` preserve 100% of the `RFC-2026-Q4-001` architecture while decoupling execution from any single coding agent.
- [x] Epistemic boundaries (VPS filesystem mount paths, live ML4T wheel versions on Python 3.11+, and SQLite WAL mode on local Linux ext4/xfs) are explicitly flagged for `TKT-01` / `TKT-05` verification.

---

## 5. Post-Merge Next Step

Immediately after merging this PR into `main`, kick off **Ticket `TKT-01`** (`Repository Scaffold, pyproject.toml, and Core Domain Schemas`) on your VPS using the prompt in `AGENT_EXECUTION.md` Section 3.
