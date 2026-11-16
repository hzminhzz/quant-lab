## Memory

Your memory is OptMem:
- The tool is `~/.optmem/memo`
- Your memories are in `~/.optmem/memory`

OptMem outlives every session, compaction, model and vendor change.
Without it you do not know who you are, or what was decided and tried.

### At startup: activating OptMem (mandatory)

Run `~/.optmem/memo wake` before any other tool call, in every session, and
then do exactly what it prints, to the end of its output.

### While working: register memories (mandatory)

Call `~/.optmem/memo note "<1 line, max 280 bytes>"` whenever you learn
something new, or something worth keeping happens. That covers a task
worth real effort, a fact or insight the user teaches you, anything you
learn about their life (even indirectly), any event of lasting effect.

Do not register redundant memories.

If `~/.optmem/memo note` asks a compression: do it before your next action.

Never edit or delete anything under `~/.optmem/memory`: the tool manages it.

### When you need an old memory: search, or navigate

`~/.optmem/memo recall <regex>` searches every memory, word for word.

Your memories also form a binary tree: #0-1, #2-3 ... exist as one-line
summaries, pairs of those as #0-3, and so on -- every `#a-b` line wake
prints is one node of it. `~/.optmem/memo zoom <a-b>` opens a node into its
two halves, down to the raw memories.

### If you're a subagent: skip everything above

Parallel sessions on this machine are all you, and may all write memories.
A subagent is not: it must never run `memo`, because it cannot judge what
is already known, and its notes would arrive duplicated and incorrectly.
When you spawn one, write: `You are a subagent. Don't run memo.`

# Quant-Lab operating contract

Architecture: `agent_native_quant_research_rfc.md`; portability: `AGENT_COMPATIBILITY.md`; ticket acceptance: `TICKETS.md`. Use `uv` and the locked environment. Optional adapters never enforce the scientific boundary on their own.

## Four-way ownership

- ML4T libraries own canonical schemas, indicators, labeling, diagnostics, CV, execution simulation and live runtime. Reuse installed APIs; inspect their signatures before calling them.
- Deterministic `src/warden/` owns immutable contracts, cost floors, causality verification, validation-label stripping, budgets, append-only SQLite and Parquet evidence. Experiments cannot bypass it.
- Coding agents own `experiments/active/`, `strategies/candidates/`, `tests/experiments/` and `.workspace/`. Baselines are frozen after registration. Record provenance, model ID and data cutoff; distinguish synthetic evidence from market evidence.
- Human principal owns `contracts/`, evaluator approval, holdout unsealing and capital allocation. Never place live orders, never run sudo, never modify `~/quant-research`. Never unseal a production holdout **autonomously**: an agent may execute `warden qualify` only when the principal's approval is explicit in the current session and names either the run ID, or a selection criterion together with the plan that produces the run (for example a paired re-validation whose new run ID cannot exist yet). The agent records the approving instruction, the pre-read tree hashes and the result. Absent that, the gate stays closed.

Protected hashes are checked by `warden check-worktree`; POSIX ownership is the actual external security boundary. During the approved TKT-01–12 implementation rollout, implement and sign the specified evaluator files; after cutover, evaluator/contract changes require human approval and a new contract where semantics change. Local synthetic holdout fixtures are not sealed production data.

## Falsification funnel

0. Register economic mechanism, counterparty/friction and falsification criteria before computation. Reject expected holding-period volatility below 2.5 times round-trip costs.
1. Verify causal features, then branch by family: `rules_ts` uses conditional return edge, profit factor, time-in-market and block bootstrap; `cross_sectional_ml` uses purged OOS IC and HAC significance; `carry_rv` uses positive net carry and stationary spread; `meta_overlay` uses tail-risk/turnover reduction versus its base strategy. Never force rules through cross-sectional IC.
2. Simulate with contract-injected next-bar/quote-side execution, fees, slippage and funding. Persist every attempted configuration, including failures; never delete or update a trial.
3. Apply contract-wide effective-rank DSR, 1.5x/2x costs, +1 bar latency and ±15% parameter neighbors. A failed gate is evidence, not permission to lower the threshold.
4. Run the six-check audit and hand off a frozen qualification packet. A lineage is evaluated once on production holdout, by the agent only on explicit principal approval as defined in Four-way ownership; the second read is refused by the registry.

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

These are the target rollout interfaces; bootstrap initially provides only `--help`/`--version`. Experiment commands fail closed on integrity/governance errors. Backtests end with `WARDEN_RUN_COMPLETE` or `WARDEN_RUN_FAILED`; JSON results precede the sentinel. `qualify --run-id ID --unseal-holdout --human-confirm-unseal` executes only under explicit principal approval (Four-way ownership) and is refused under `WARDEN_NON_INTERACTIVE=1`. Never replay arbitrary shell from transition documents; continuation verifies read-only expectations.

## Methodology skills

61 copied declarative upstream skills live one level deep at `.agents/skills/ml4t-<name>/SKILL.md`; in OMP the same skill is `skill://ml4t-<name>`. Read the matching skill before domain work:

- Panel transformations/features: `.agents/skills/ml4t-polars-patterns/SKILL.md`, `.agents/skills/ml4t-compute-features/SKILL.md` (`skill://ml4t-polars-patterns`, `skill://ml4t-compute-features`).
- Validation/leakage: `.agents/skills/ml4t-purging-embargo/SKILL.md`, `.agents/skills/ml4t-lookahead-bias/SKILL.md`, `.agents/skills/ml4t-data-leakage/SKILL.md` (corresponding `skill://ml4t-*` URIs).
- Factors/DSR: `.agents/skills/ml4t-evaluate-factor/SKILL.md`, `.agents/skills/ml4t-deflated-sharpe/SKILL.md` (`skill://ml4t-evaluate-factor`, `skill://ml4t-deflated-sharpe`).
- Simulation/costs: `.agents/skills/ml4t-run-backtest/SKILL.md`, `.agents/skills/ml4t-transaction-costs/SKILL.md` (`skill://ml4t-run-backtest`, `skill://ml4t-transaction-costs`).

Skills are Markdown only: no scripts, hooks or executable dependencies. Install optional UX with `scripts/setup_agent_adapter.sh --harness omp|opencode|claude|codex|all`; `--harness clean` removes only installer-owned links. Deleting adapters must not affect Warden or tests.

### Automatic skill routing (OMP adapter)

When running under the OMP adapter (`scripts/setup_agent_adapter.sh --harness omp`), `.omp/extensions/skill-router.ts` provides automatic pre-turn skill selection:
- Evaluates incoming task intent against frontmatter metadata of all 61 skills using Jev / System One (`modelRoles.judge`).
- Resolves transitive prerequisite DAGs in topological order (e.g. `run-backtest` $\to$ `cost-model` $\to$ `transaction-costs`) and injects complete skill bodies into session context before turn execution starts.
- Rebinds dynamically in subagent child sessions (`task` tool).
- Enforces a generic action gate in `tool_call`: if skill routing fails or if current active coverage has zero research skills loaded (e.g. documentation edits), mutating actions (`write`, `edit` to strategy files, and `warden backtest/screen/qualify`) are blocked fail-closed, while read-only tools (`read`, `grep`, `glob`) remain operational for diagnosis.
- In non-OMP harnesses (or when the adapter is unlinked), manual skill reading (`skill://<name>` or `.agents/skills/ml4t-*/SKILL.md`) remains the standard interface.
