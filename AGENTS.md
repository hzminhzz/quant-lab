# Quant-Lab agent operating contract

## Start here

- Activate OptMem: run `~/.optmem/memo wake` at session start and follow its output.
  Record durable discoveries with `~/.optmem/memo note "<one line, max 280 bytes>"`;
  complete requested compression before the next action. Never edit its store directly.
  Use `memo recall`/ `memo zoom` for older context. Subagents do not write OptMem.
- Inspect actual Git, checkout, active runtime, ledger, authority and pending receipts.
  Preserve unrelated dirty/untracked work. Dated reports and memory are not live state.
- Read [CONTEXT-MAP.md](CONTEXT-MAP.md) for the task-specific authoritative sources.
- Recover ordinary blockers autonomously via [RECOVER](docs/BLOCKER-RECOVERY.md):
  diagnose, repair, verify, then resume the authorized objective. Preserve failures.

## Ownership and effects

- ML4T owns schemas, features, labeling, diagnostics, CV and simulation. Inspect
  installed APIs; use matching `.agents/skills/ml4t-*/SKILL.md` before domain work.
- Warden owns immutable contracts, causal execution, cost floors, validation-label
  stripping, budgets, append-only SQLite and Parquet evidence. Never bypass it.
- Agents prepare hypotheses, candidates, tests and workspace artifacts. The principal
  owns scientific intent, independent conformance, semantic policy changes, protected
  effects and capital allocation. A missing independent judge is JUDGE_UNAVAILABLE.
- [Trusted appliance ADR](docs/adr/2026-11-27-trusted-research-appliance.md) delegates
  routine maintenance, unsigned checksums, verified deployment, additive migration and
  recovery. It supersedes older independent software-release authority requirements.
  A maintenance receipt is not scientific approval, signing or qualification.
- Never place live orders, run sudo, modify `~/quant-research`, bypass host permissions,
  fabricate approval/conformance, lower gates, or erase/rewrite scientific history.
- Never open, hash, sample, stat or permission-probe sealed holdout files. Only a
  separately authorized exact scientific operation under RG-08 and the existing
  one-shot exposure gates may unseal. Noninteractive qualification remains refused.

## Engineering vs operations

- Use isolated development worktrees and the frozen uv environment. No canonical
  registry, exploratory data or cache symlinks in verification. Tests use disposable
  fixtures, never live state. Migrations rehearse on an independent SQLite backup.
- Use the installed appliance launcher for operational state/effects, not a mutable
  checkout's CLI, MCP or legacy scheduler. Report caller checkout and runtime separately.
  See [operations](docs/AGENT-CONTROL-OPERATIONS.md) for exact commands, lifecycle,
  release receipts, writer quiescence, halt/recovery and known legacy limitations.
- Refresh the unsigned integrity inventory via `warden generate-manifest --maintenance`;
  never invent a scientific signature or alter existing approval metadata.
  Check changed paths, protected inventory/AST, targeted regressions and exact full
  release verification. A passing development test does not authorize activation.
- Scientific scheduling stays paused until separately eligible. No ledger rewind.
  An uncertain effect requires its existing receipt, never a blind retry.

## Research (only within an authorized objective)

- Follow [AGENT_RUNBOOK.md](AGENT_RUNBOOK.md), [WORKFLOW](docs/WORKFLOW.md) and
  [Research Governance](docs/RESEARCH-GOVERNANCE.md). Complete the canonical term sheet,
  resolve the exact DataRequirement, register and approve frozen intent before candidate
  implementation; independently review the exact candidate binding before scoring.
- New ACTIVE candidates use native CausalModelStrategy/CausalRulesStrategy. Legacy
  generate_positions is historical replay only. Preserve full code closure and data
  identities, point-in-time inputs, mandatory execution lag, fold-local preprocessing,
  immutable baselines, selection debt and candidate/lineage limits. No synthetic
  quotes, silent data substitution or choosing contracts to dodge thresholds.
- Follow the family-specific falsification funnel, linear-baseline requirement,
  fixed robustness/cost gates, six-check audit and prior-evidence/novelty rules.
  Details and exact owner references are in the runbook and workflow.
- The approved Dukascopy proxy policy is in [DATA-PROXY](docs/DATA-PROXY.md):
  labeled proxy discovery/robustness is not execution-venue qualification.
- The SQLite ledger is the scientific authority. Inspect spec-status, attempt-status,
  run-bound causal proof and matching finalized receipts before advancing. Keep failed,
  interrupted and historical attempts, exposure and selection debt. No retries/refunds
  on exhausted or uncertain admissions. Narrative reports are projections, not authority.
- Distinguish verified/inferred/hypothesis/unknown and engineering implementation,
  verification, deployment, scientific qualification and trading authorization.
