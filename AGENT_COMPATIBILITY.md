# Agent Compatibility & Harness-Independent Integration Design (`AGENT_COMPATIBILITY.md`)

**Document ID:** PLAN-2026-Q4-COMPAT

**Authoritative Architecture Reference:** `agent_native_quant_research_rfc.md` (RFC-2026-Q4-001)

**Target Harnesses:** Oh My Pi (`omp`), OpenCode (`opencode`), Claude Code (`claude`), Codex CLI (`codex`), and future CLI/RPC agents

**Process Multiplexer:** Herdr (`herdr`) or standard POSIX shell

## 1. Design Principle: Zero Harness Coupling in the Core Research System

In the original RFC (`agent_native_quant_research_rfc.md`, Section 4.3), scientific governance and path isolation were partially coupled to `opencode.json` permission globs (`permission.edit`, `permission.bash`, `permission.task`). Relying on a single coding agent's configuration format creates two structural liabilities:

1. Switching harnesses (e.g., running **Oh My Pi (`omp`)**, **Claude Code (`claude`)**, or **Codex CLI (`codex`)**) would bypass `opencode.json` rules unless enforced by the underlying operating system and deterministic Python tools.

2. Different harnesses excel at different stages of quantitative work (e.g., `omp` provides hash-anchored edits, persistent Python/Bun evaluation kernels, `skill://` internal URIs, and typed subagent worktree fan-out; `claude` and `codex` provide complementary model reasoning; `opencode` provides lightweight TUI sessions).

To make `quant-lab` 100% coding-agent independent **without changing a single line of the core research architecture, ML4T library integrations, staged falsification funnel, or SQLite trial registry**, we separate the system into:

1. **The Portable Core (Mandatory, Harness-Neutral):** Standard files and deterministic CLI entrypoints that every coding agent can read and execute out of the box (`AGENTS.md`, `.agents/skills/ml4t-*/SKILL.md`, `uv run warden ...`, `registry/registry.db`, `.workspace/`, and POSIX + Git pre-commit write-boundary enforcement).

2. **Optional Harness Adapters (Strictly Additive, Non-Load-Bearing):** Thin configuration files in `.omp/`, `opencode.json`, and `CLAUDE.md` that map each harness's UX conveniences (subagents, model roles, tool permissions) onto the Portable Core. Deleting every optional adapter file leaves `quant-lab`'s scientific safety, data isolation, and experiment reproducibility 100% intact.

## 2. Cross-Harness Capability Mapping (Verified Source Behavior)

All four target coding agents natively converge on the exact portable primitives already used in `quant-lab`:

| Portable Primitive | Canonical Path in `quant-lab` | Oh My Pi (`omp`) ([can1357/oh-my-pi](https://github.com/can1357/oh-my-pi)) | OpenCode (`opencode`) ([opencode.ai/docs/agents](https://opencode.ai/docs/agents/)) | Claude Code (`claude`) ([coding-agent-toolkit](https://github.com/stefan-jansen/coding-agent-toolkit)) | Codex CLI (`codex`) ([coding-agent-toolkit](https://github.com/stefan-jansen/coding-agent-toolkit)) | 
| ----- | ----- | ----- | ----- | ----- | ----- | 
| **Root Project Instructions** | `AGENTS.md` (with 1-line `CLAUDE.md` containing `@AGENTS.md`) | Discovers ancestor `AGENTS.md` natively via `src/discovery/agents-md.ts` and `.omp/AGENTS.md` ([oh-my-pi/docs/skills.md](https://github.com/can1357/oh-my-pi/blob/main/docs/skills.md)). | Reads `AGENTS.md` natively at startup. | Reads `CLAUDE.md` (`@AGENTS.md` include directive). | Reads `AGENTS.md` natively at startup. | 
| **61 ML4T Domain Skills** | `.agents/skills/ml4t-*/SKILL.md` (installed via `ml4t/skills/scripts/install.sh`) | Native `agents` skill provider (priority 70) scans `.agents/skills/*/SKILL.md` automatically (`enableAgentsProject: true` by default); exposes `skill://<name>` URIs and `/skill:<name>` commands ([oh-my-pi/docs/skills.md](https://github.com/can1357/oh-my-pi/blob/main/docs/skills.md)). | Discovers project skills or reads `.agents/skills/ml4t-*/SKILL.md` via standard file/skill tools. | Reads `.claude/skills/` (can be symlinked to `.agents/skills/` or read directly from `.agents/skills/`). | Native project skill discovery in `.agents/skills/` ([ml4t/skills](https://github.com/ml4t/skills)). | 
| **Deterministic Tool Execution** | `uv run warden <subcommand> --json` | Executes via built-in in-process `brush` shell or persistent Python `eval` kernel ([can1357/oh-my-pi](https://github.com/can1357/oh-my-pi)). | Executes via `bash` tool. | Executes via `Bash` tool. | Executes via shell tool. | 
| **Subagent / Auditor Delegation** | CLI fallback: `uv run warden audit-check <run_id>` (plus optional native subagents) | First-class `task` tool spawns isolated worktree workers defined in `.omp/agents/*.md` (`structured: true`, `autoloadSkills`, `blocking: true`) ([oh-my-pi/docs/task-agent-discovery.md](https://github.com/can1357/oh-my-pi/blob/main/docs/task-agent-discovery.md)). | Invokes `.opencode/agents/*.md` or `opencode.json` subagents via `@mention` or `Task` tool. | Invokes `.claude/agents/*.md` subagents via `Task` tool. | Executes single-session or spawns `codex exec` worker panes via `Herdr`. | 
| **Session Continuity & Handoffs** | `.workspace/transitions/YYYY-MM-DD/HHMMSS.md` (`uv run warden handoff` / `continue`) | Compatible with `.workspace/transitions/` and supports `--resume` / `/branch` / `/fork` ([oh-my-pi/docs/tree.md](https://github.com/can1357/oh-my-pi/blob/main/docs/tree.md)). | Reads/writes `.workspace/transitions/` via `warden`. | Reads/writes `.workspace/transitions/` via `coding-agent-toolkit` or `warden`. | Reads/writes `.workspace/transitions/` via `coding-agent-toolkit` or `warden`. | 
| **Herdr Terminal Orchestration** | `herdr agent start <name> --kind <kind> --pane <pane>` | First-class supported kind `--kind omp` (installs extension in `~/.omp/agent`, reports live state, supports `omp --resume=<session>`) ([herdr.dev/docs/integrations](https://herdr.dev/docs/integrations/)). | First-class supported kind `--kind opencode` ([herdr.dev/docs/agents](https://herdr.dev/docs/agents/)). | First-class supported kind `--kind claude`. | First-class supported kind `--kind codex`. | 

## 3. Deep Dive: Oh My Pi (`omp`) Architecture & Optional Adapter Design

Based on direct verification of [`can1357/oh-my-pi`](https://github.com/can1357/oh-my-pi) (`v18.12.6`, October 2026) and its official documentation ([`docs/config-usage.md`](https://github.com/can1357/oh-my-pi/blob/main/docs/config-usage.md), [`docs/skills.md`](https://github.com/can1357/oh-my-pi/blob/main/docs/skills.md), [`docs/task-agent-discovery.md`](https://github.com/can1357/oh-my-pi/blob/main/docs/task-agent-discovery.md), [`docs/rpc.md`](https://github.com/can1357/oh-my-pi/blob/main/docs/rpc.md)), `omp` provides several capabilities that map onto `quant-lab`:

1. **Zero-Migration Skill & Context Discovery:**

   * `omp` scans eight configuration roots on disk in priority order: `.omp` (100), `omp-plugins` (90), `.claude` (80), `agent-plugins` (75), `.codex` / `.agents` / `claude-plugins` (70), `.gemini` (60), `opencode` (55), and `AGENTS.md` / `CLAUDE.md` (10) ([oh-my-pi/docs/config-usage.md](https://github.com/can1357/oh-my-pi/blob/main/docs/config-usage.md)).

   * Because `ml4t/skills/scripts/install.sh .agents/skills --copy` installs the 61 ML4T skills into `.agents/skills/ml4t-<name>/SKILL.md` (one level deep with YAML frontmatter `name` and `description`), `omp`'s built-in `agents` skill provider discovers all 61 skills automatically without a single symlink or migration script ([oh-my-pi/docs/skills.md](https://github.com/can1357/oh-my-pi/blob/main/docs/skills.md)).

   * Inside an `omp` session, the agent can read any ML4T skill via `skill://ml4t-purging-embargo` or the user can inject it via `/skill:ml4t-purging-embargo`.

2. **Role-Based Model Routing (`--plan`, `--slow`, `--smol`, `advisor`):**

   * `omp` supports model roles configured in `.omp/config.yml` or via CLI flags (`omp --plan <model> --slow <model> --smol <model>`) ([oh-my-pi/docs/cli-reference.md](https://github.com/can1357/oh-my-pi/blob/main/docs/cli-reference.md)).

   * It also supports an inline `advisor` model role that watches every turn the primary agent takes and injects concerns or hard blockers before the turn completes ([can1357/oh-my-pi](https://github.com/can1357/oh-my-pi)).

3. **First-Class Subagents (`.omp/agents/*.md`):**

   * Unlike skills and rules, `omp`'s `task` subagent discovery (`src/task/discovery.ts`) intentionally scans only `.omp/agents/*.md` (`TASK_AGENT_CONFIG_SOURCE = ".omp"`) and skips `.claude/agents` or `.codex/agents` because `omp` subagents support specialized frontmatter fields: `tools`, `spawns`, `model`, `blocking: true`, `structured: true`, `output` (JSON schema), `autoloadSkills`, `read-summarize: false`, and `advisor` ([oh-my-pi/docs/task-agent-discovery.md](https://github.com/can1357/oh-my-pi/blob/main/docs/task-agent-discovery.md)).

   * When `omp` enters Plan Mode, `resolveEffectiveSubagentPolicy()` automatically restricts all spawned subagents to read-only tools (`read`, `grep`, `glob`, `web_search`, `ast_grep`) and disables worktree mutations ([oh-my-pi/docs/task-agent-discovery.md](https://github.com/can1357/oh-my-pi/blob/main/docs/task-agent-discovery.md)).

4. **Programmatic Integration (`omp --mode rpc` & SDK):**

   * For headless automation outside interactive TUI panes, `omp` provides NDJSON RPC over stdio (`omp --mode rpc --no-ui`) and the `@oh-my-pi/pi-coding-agent` TypeScript SDK (`createAgentSession`, `SessionManager`) ([oh-my-pi/docs/rpc.md](https://github.com/can1357/oh-my-pi/blob/main/docs/rpc.md)). However, in `quant-lab`, interactive or automated panes inside `Herdr` (`herdr agent start researcher --kind omp --pane w1:p1`) remain the primary workflow so no custom RPC wrapper is needed.

### Optional `.omp/` Adapter Files (`adapters/omp/` -> `.omp/`)

To give `omp` users native access to `@literature-scout` and `@adversarial-auditor` with automatic ML4T skill preloading (`autoloadSkills`), we provide three optional files under `.omp/`:

#### `.omp/config.yml` (Optional Project Settings)

```
# Optional Oh My Pi project settings for quant-lab
skills:
  enableAgentsProject: true
  enableSkillCommands: true
task:
  maxRecursionDepth: 2
  structured: true

```

#### `.omp/agents/literature-scout.md` (Optional OMP Subagent)

```
---
name: literature-scout
description: Searches academic papers, SSRN preprints, and documentation to extract economic mechanisms, counterparty frictions, and parameter priors.
tools: [read, grep, glob, web_search]
spawns: ""
blocking: true
read-summarize: false
autoloadSkills:
  - ml4t-causal-identification
  - ml4t-factor-research
---
You are a read-only quantitative literature scout. Extract:
1. Economic mechanism and market microstructure friction.
2. Counterparty (who is forced or willing to take the other side).
3. Exact data requirements, holding horizon, and realistic transaction-cost sensitivity.
Return a structured summary that can be written to `.workspace/literature/`. Do not modify any repository files.

```

#### `.omp/agents/adversarial-auditor.md` (Optional OMP Subagent)

```
---
name: adversarial-auditor
description: Audits candidate strategies against the 6-check ML4T leakage and overfitting checklist before qualification.
tools: [read, grep, glob, bash]
spawns: ""
blocking: true
structured: true
read-summarize: false
autoloadSkills:
  - ml4t-lookahead-bias
  - ml4t-data-leakage
  - ml4t-purging-embargo
  - ml4t-deflated-sharpe
  - ml4t-transaction-costs
---
You are a read-only adversarial quantitative auditor. Do not edit any files.
Run `uv run warden audit-check --run-id <RUN_ID> --json` and inspect `git diff` to verify all six ML4T audit checks:
1. Target alignment: Does the label horizon match the trading rebalance horizon?
2. Outlier sensitivity: Do results survive removing the top 5% best bars/days?
3. Point-in-time causality: Are all features strictly lagged by >= execution_delay_bars?
4. Fold-local fitting: Are all scalers, imputers, and models fitted strictly inside train_idx?
5. Cost & turnover realism: Does Net Sharpe remain > 0.50 under 2x transaction costs and +1 bar delay?
6. Selection-bias accounting: Were all attempted runs logged in registry.db and does DSR clear the contract gate?

```

## 4. Moving Enforcement from `opencode.json` to Deterministic Core (`warden` + OS/Git)

Because harness-specific configuration files (`opencode.json`, `.omp/config.yml`, `.claude/settings.json`) are now optional adapters, **all security and scientific boundaries previously enforced in `opencode.json` are enforced in the harness-independent core**:

| Security / Scientific Boundary | Previous RFC Mechanism (OpenCode-Coupled) | New Harness-Independent Mechanism (Works for OMP, OpenCode, Claude, Codex, or Manual Shell) | 
| ----- | ----- | ----- | 
| **1. Protecting `contracts/`, `src/warden/`, and `AGENTS.md` from Agent Edits** | `opencode.json` `"edit": {"*": "deny", "experiments/active/**": "allow"}` | **Dual OS + Git Enforcement:** (a) On the VPS, `contracts/`, `src/warden/`, and `AGENTS.md` are owned by `root:trader` with mode `0644` (read-only to the `agent` Linux user running the coding agent); (b) `warden check-worktree` (also installed as a `.git/hooks/pre-commit` hook and executed at the start of every \`warden verify | 
| **2. Blocking Agent Access to Sealed Holdout Data (`/data/holdout/`)** | `opencode.json` `"external_directory": "deny"` + `"uv run warden qualify*": "deny"` | **POSIX Permission + Group Gate:** `/data/holdout/` is owned by `root:trader` with mode `0640`. The coding agent runs as user `agent` (not in group `trader`). Even if an agent executes `cat /data/holdout/...` or `uv run warden qualify` in bash, the Linux kernel returns `PermissionError: [Errno 13] Permission denied`. | 
| **3. Session Handoffs (`handoff` / `continue`)** | Relied on `coding-agent-toolkit` slash commands (`/handoff`, `/continue`) | **Built into `warden` CLI:** `uv run warden handoff --notes "..."` writes `.workspace/transitions/YYYY-MM-DD/HHMMSS.md` with `# expect:` assertions (`git rev-parse HEAD`, `trials` count, contract hashes), and `uv run warden continue` replays and verifies those assertions from any shell or coding agent. | 
| **4. Adversarial Pre-Promotion Audit** | Relied on OpenCode `@adversarial-auditor` subagent | **Deterministic CLI + Optional Subagent:** `uv run warden audit-check --run-id <ID> --json` runs deterministic checks on the trial record, diff, and LOC budgets, and prints the structured 6-check audit prompt so any agent (with or without subagents) can execute or delegate the audit. | 

## 5. Summary of Minimal Changes from the Original RFC

In strict compliance with Requirement 1 and Requirement 6, **zero changes** were made to the research pipeline, ML4T library integrations, Stage 0–4 falsification funnel, SQLite + Parquet registry schema, or vertical-slice algorithms. The only changes from `agent_native_quant_research_rfc.md` are:

1. **Demoted `opencode.json` from a root requirement to an optional adapter** (`adapters/opencode/opencode.json`), alongside optional adapters for **Oh My Pi** (`adapters/omp/.omp/`), **Claude Code** (`adapters/claude/`), and **Codex CLI** (`adapters/codex/`), activated via a single non-destructive helper (`scripts/setup_agent_adapter.sh --harness omp|opencode|claude|codex|all`).

2. **Moved write-boundary and holdout-isolation enforcement into `warden` (`warden check-worktree` + `.warden_manifest.sha256`) and POSIX user permissions** (`agent` user vs. `trader` user), ensuring identical safety regardless of which coding agent is used.

3. **Added three small, portable CLI subcommands to `warden`** (`warden status --json`, `warden handoff`, `warden continue`, and `warden audit-check --run-id <ID>`) so agents that do not use `coding-agent-toolkit` slash commands or native subagents can execute session transitions and adversarial audits via standard shell commands.

4. **Documented one genuine upstream version blocker (`ml4t-engineer` calendar API in v0.1.6 vs. v0.2):** As discovered during RFC research (`github.com/ml4t/engineer`), `ml4t-engineer` v0.1.6 on PyPI does not yet accept the `calendar=None` keyword argument documented in the `main`-branch v0.2 quickstart. Ticket `TKT-01` and `TKT-06` explicitly include a 5-line version-inspection adapter so `warden` works seamlessly with both PyPI `0.1.6` and source `0.2.0`.