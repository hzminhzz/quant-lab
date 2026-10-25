# Quant-Lab operating contract

Architecture: `agent_native_quant_research_rfc.md`; portability: `AGENT_COMPATIBILITY.md`; ticket acceptance: `TICKETS.md`. Use `uv` and the locked environment. Optional adapters never enforce the scientific boundary on their own.

## Four-way ownership

- ML4T libraries own canonical schemas, indicators, labeling, diagnostics, CV, execution simulation and live runtime. Reuse installed APIs; inspect their signatures before calling them.
- Deterministic `src/warden/` owns immutable contracts, cost floors, causality verification, validation-label stripping, budgets, append-only SQLite and Parquet evidence. Experiments cannot bypass it.
- Coding agents own `experiments/active/`, `strategies/candidates/`, `tests/experiments/` and `.workspace/`. Baselines are frozen after registration. Record provenance, model ID and data cutoff; distinguish synthetic evidence from market evidence.
- Human principal owns `contracts/`, evaluator approval, holdout unsealing and capital allocation. Never place live orders or autonomously unseal production holdout, run sudo, or modify `~/quant-research`.

Protected hashes are checked by `warden check-worktree`; POSIX ownership is the actual external security boundary. During the approved TKT-01–12 implementation rollout, implement and sign the specified evaluator files; after cutover, evaluator/contract changes require human approval and a new contract where semantics change. Local synthetic holdout fixtures are not sealed production data.

## Falsification funnel

0. Register economic mechanism, counterparty/friction and falsification criteria before computation. Reject expected holding-period volatility below 2.5 times round-trip costs.
1. Verify causal features, then branch by family: `rules_ts` uses conditional return edge, profit factor, time-in-market and block bootstrap; `cross_sectional_ml` uses purged OOS IC and HAC significance; `carry_rv` uses positive net carry and stationary spread; `meta_overlay` uses tail-risk/turnover reduction versus its base strategy. Never force rules through cross-sectional IC.
2. Simulate with contract-injected next-bar/quote-side execution, fees, slippage and funding. Persist every attempted configuration, including failures; never delete or update a trial.
3. Apply contract-wide effective-rank DSR, 1.5x/2x costs, +1 bar latency and ±15% parameter neighbors. A failed gate is evidence, not permission to lower the threshold.
4. Run the six-check audit and hand off a frozen qualification packet. Only a human may evaluate a lineage once on production holdout.

Each hypothesis has three trials total: one baseline plus two targeted revisions. A fourth is blocked. Re-registering the same ID never resets the budget. After exhaustion, record a post-mortem in `.workspace/memory/LESSONS.md`. Log a Ridge/linear baseline on the same feature set before LightGBM or other nonlinear models.

## Six-check adversarial audit

1. Target/rebalance horizon alignment.
2. Sensitivity after removing the top 5% best bars.
3. Point-in-time timestamps and mandatory execution lag.
4. Fold-local preprocessing/model fitting; validation inputs physically exclude labels.
5. Survival under 2x costs and +1 bar latency.
6. All trial counts retained and contract-wide DSR threshold met.

Source: https://github.com/ml4t/agentic-ai-in-trading and RFC Sections 4–9.

## Portable CLI contracts

```bash
uv run warden --version
uv run warden status --json
uv run warden check-worktree
uv run warden register-hypothesis .workspace/hypotheses/HYP-2026-001.md --json
uv run warden verify experiments/active/hyp_2026_001.yaml --json
uv run warden screen experiments/active/hyp_2026_001.yaml --json
uv run warden backtest experiments/active/hyp_2026_001.yaml --json
uv run warden audit-check --run-id RUN-ML-001 --json
uv run warden handoff --notes "Evidence and next decision"
uv run warden continue
```

These are the target rollout interfaces; bootstrap initially provides only `--help`/`--version`. Experiment commands fail closed on integrity/governance errors. Backtests end with `WARDEN_RUN_COMPLETE` or `WARDEN_RUN_FAILED`; JSON results precede the sentinel. `qualify --run-id ID --unseal-holdout --human-confirm-unseal` is human-only; `WARDEN_NON_INTERACTIVE=1` refuses it. Never replay arbitrary shell from transition documents; continuation verifies read-only expectations.

## Methodology skills

61 copied declarative upstream skills live one level deep at `.agents/skills/ml4t-<name>/SKILL.md`; in OMP the same skill is `skill://ml4t-<name>`. Read the matching skill before domain work:

- Panel transformations/features: `.agents/skills/ml4t-polars-patterns/SKILL.md`, `.agents/skills/ml4t-compute-features/SKILL.md` (`skill://ml4t-polars-patterns`, `skill://ml4t-compute-features`).
- Validation/leakage: `.agents/skills/ml4t-purging-embargo/SKILL.md`, `.agents/skills/ml4t-lookahead-bias/SKILL.md`, `.agents/skills/ml4t-data-leakage/SKILL.md` (corresponding `skill://ml4t-*` URIs).
- Factors/DSR: `.agents/skills/ml4t-evaluate-factor/SKILL.md`, `.agents/skills/ml4t-deflated-sharpe/SKILL.md` (`skill://ml4t-evaluate-factor`, `skill://ml4t-deflated-sharpe`).
- Simulation/costs: `.agents/skills/ml4t-run-backtest/SKILL.md`, `.agents/skills/ml4t-transaction-costs/SKILL.md` (`skill://ml4t-run-backtest`, `skill://ml4t-transaction-costs`).

Skills are Markdown only: no scripts, hooks or executable dependencies. Install optional UX with `scripts/setup_agent_adapter.sh --harness omp|opencode|claude|codex|all`; `--harness clean` removes only installer-owned links. Deleting adapters must not affect Warden or tests.
