# Quant-Lab Automatic Skill Routing: Architecture & Jev Evaluation Research

**Status**: Completed Research & Implementation Reference  
**PR**: https://github.com/hzminhzz/quant-lab/pull/19  
**Target Harness**: Oh My Pi (`omp`) via optional adapter (`adapters/omp/.omp/extensions/skill-router.ts`)

---

## 1. Architectural Summary & Problem Definition

When coding agents develop quantitative trading strategies, they frequently fail to load relevant domain skills (e.g. `ml4t-compute-features`, `ml4t-lookahead-bias`, `ml4t-cost-model`) due to LLM overconfidence and the token penalty of voluntary tool invocation. 

Hardcoding 61 fragile domain keyword rules or regexes in Python is anti-pattern bloat that requires constant manual updates whenever new skills are introduced. Conversely, building a complex multi-agent bureaucracy (CIO agent, Risk agent, Reviewer agent) violates the solo-trader operating contract and introduces prompt degradation and latency.

The adopted solution is a **minimal, metadata-driven automatic skill router**:
1. **Catalog & Frontmatter Discovery**: Discovers all 61 skills from `.agents/skills/ml4t-*/SKILL.md` frontmatter (`name`, `description`, `when_to_use`, `dependencies`) at runtime.
2. **Multi-Label Decision Primitives**: Constructs independent Boolean questions evaluated concurrently against a System One decision model (**TypeSafe Jev** via `modelRoles.judge`).
3. **Recursive Transitive DAG Closure**: Automatically resolves declared prerequisites in topological order (e.g. `ml4t-run-backtest` $\to$ `ml4t-cost-model` $\to$ `ml4t-transaction-costs`).
4. **Pre-Turn Injection & Generic Action Gate**: Injects verified skill bodies into the session context via `before_agent_start`. In `tool_call`, blocks unguided strategy modifications or evaluation runs if active coverage contains zero research skills or if routing encounters errors, while leaving read-only diagnostic tools (`read`, `grep`, `glob`) operational.

---

## 2. Live Evaluation Record (61 Skills on Trend Thesis)

### Test Prompt
*"Implement a trend continuation strategy: long EMA crossover (12/26 EMA) confirmed by upward ATR expansion on hourly Polars bars, with next-bar execution under Warden transaction costs."*

### Live Model Response
Evaluated against `typesafe/jev-1.14` concurrently across all 61 skills:

| Probability ($p$) | Skill Name | Routing Disposition | Primary Reason |
|:---:|---|---|---|
| **0.92** | `ml4t-polars-patterns` | **DIRECT ($\ge 0.70$)** | Grouped rolling window indicators with `.over()` without Python loops |
| **0.81** | `ml4t-run-backtest` | **DIRECT ($\ge 0.70$)** | Event-driven next-bar order execution semantics (bar $t$ signal fills bar $t+1$) |
| **0.76** | `ml4t-lookahead-bias` | **DIRECT ($\ge 0.70$)** | Enforcing strict causal time-series boundaries; eliminating forward leaks |
| **0.70** | `ml4t-cost-model` | **DIRECT ($\ge 0.70$)** | Commissions, spreads, and square-root market impact modeling |
| **0.53** | `ml4t-transaction-costs` | **AMBIGUOUS ($0.30 \le p < 0.70$)** | Feasibility screen; also auto-pulled as a formal prerequisite of `cost-model` |
| $\le 0.28$ | *(All other 56 skills)* | **REJECTED ($p < 0.30$)** | ML models, purged CV, futures rollovers, live trading correctly rejected |

### Transitive DAG Closure (Topological Order)
```
1. ml4t-transaction-costs  (prerequisite of cost-model)
2. ml4t-cost-model         (direct selection, p = 0.70)
3. ml4t-lookahead-bias     (direct selection, p = 0.76)
4. ml4t-polars-patterns    (direct selection, p = 0.92)
5. ml4t-run-backtest       (direct selection, p = 0.81)
```
- **Total Injected Size**: 20,068 characters (~5,000 tokens / 19.6 KiB).
- **Execution Proof**: Tested end-to-end in real `omp` process. The coding model received the 5 skills before answering, explicitly acknowledged the injected rules, and generated vectorized next-bar execution strategy code.

---

## 3. Flat 1-Stage Selection vs. 2-Stage Category Pruning

Why 1-stage flat parallel evaluation is superior to category-first routing for 61 skills:
1. **Network Latency**: Jev evaluates all 61 questions in 1 single HTTP request ($O(1)$). Adding a preliminary category stage requires 2 sequential round-trips, doubling network latency.
2. **False-Negative Elimination**: Essential correctness skills cross category boundaries (e.g. `polars-patterns` is under Infrastructure, `lookahead-bias` and `transaction-costs` are under Concepts, while `run-backtest` is under Backtest). Pruning at the category level would drop cross-cutting guardrails.
3. **No Frontmatter Categories**: Skill frontmatters do not carry an internal category field; categories are directory conventions. Flat discovery avoids hardcoded category taxonomies.

---

## 4. Test Verification Summary

- **36 Bun tests passing** (`bun test adapters/omp/tests`): covers 3-hop DAG dependency resolution, cycle detection, ambiguity handling, size limits, and a stratified 5-family benchmark with 0% missing-essential-skill rate.
- **17 Python compatibility tests passing** (`uv run pytest tests/test_agent_compatibility.py`): verifies template validity, symlink installation idempotence, and clean uninstallation.
- **Warden Worktree Integrity verified**: `uv run warden check-worktree` exits 0 (protected contracts, LOC budgets, and manifests intact).
