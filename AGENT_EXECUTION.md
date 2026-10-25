# Coding-Agent Execution Contract & Prompt Templates (`AGENT_EXECUTION.md`)

**Document ID:** PLAN-2026-Q4-EXEC  
**Supported Harnesses:** Oh My Pi (`omp`), OpenCode (`opencode`), Claude Code (`claude`), Codex CLI (`codex`), and any CLI/RPC coding agent  
**Process Multiplexer:** Herdr (`herdr`) or standard POSIX terminal  

---

## 1. Universal Single-Ticket Execution Contract (Reusable Prompt Template)

Copy and paste the block below into **Oh My Pi (`omp`)**, **OpenCode (`opencode`)**, **Claude Code (`claude`)**, or **Codex CLI (`codex`)** whenever assigning a ticket from `TICKETS.md`. Replace only `<TICKET_ID>` (e.g., `TKT-01`).

```markdown
# TASK ASSIGNMENT: Execute Exactly One Implementation Ticket (<TICKET_ID>)

You are operating as a disciplined systems and quantitative engineering agent inside the `quant-lab` repository.

## Mandatory Execution Contract (10 Rules)
1. **Read Authoritative Context First:** Before running any write command, read:
   - `AGENTS.md` (if it exists yet; created in TKT-02)
   - `agent_native_quant_research_rfc.md` (the authoritative architecture design)
   - `AGENT_COMPATIBILITY.md` (the harness-independent interface rules)
   - Your assigned ticket `<TICKET_ID>` in `TICKETS.md`.
2. **Inspect the Live VPS Environment:** Run the read-only preflight inspection commands listed in `<TICKET_ID>` (checking OS user/groups, Python/uv versions, installed `ml4t-*` package versions, disk paths, and `git status --porcelain`). Do not assume paths or package versions without inspecting them.
3. **Single-Ticket Boundary:** Execute **ONLY** `<TICKET_ID>`. Do not start, scaffold, or partially implement `<TICKET_ID + 1>` or any other ticket.
4. **Preserve Unrelated State:** Do not delete, overwrite, or reformat untracked data files, existing SQLite databases (`registry/registry.db`), `.workspace/` memory notes, or uncommitted changes outside the ticket's file scope.
5. **Strict Scope & Architecture Fidelity:** Implement only the exact files and interfaces specified in `<TICKET_ID>`. Do not redesign the Warden architecture, alter scientific gates, add unrequested dependencies, or introduce agent-framework abstractions.
6. **Human Approval Gate for Destructive / Privileged Operations:** If a command requires `sudo`, modifies POSIX ownership/permissions outside the repo, deletes existing files, or touches the legacy `quant-research` repository, **STOP and ask for explicit human confirmation** (or output the exact command for the human to run) unless already pre-provisioned.
7. **Execute Automated Tests & Verification Commands:** Run every pytest suite, linter check, and CLI verification command listed in `<TICKET_ID>`. All tests must pass. Fix any failures within scope before proceeding.
8. **Review Diff & Report Deviations:** Run `git status` and `git diff` (or `omp` git overview). Verify that no protected files outside `<TICKET_ID>` scope were modified and that LOC budgets are respected. Explicitly document any environment-driven deviation (e.g., an upstream `ml4t-*` API signature difference).
9. **Commit & Record Transition State:** Commit the completed work on branch `feat/<TICKET_ID-lowercase>` with a commit message referencing `<TICKET_ID>`. If `warden handoff` is available (TKT-07+), run `uv run warden handoff --ticket <TICKET_ID>`.
10. **Emit Evidence Report & STOP:** Output a concise Completion Report containing:
    - Environment inspection findings
    - Files created/modified (with line counts)
    - Exact test commands run and their verbatim pass/fail output
    - Checksums / artifact paths produced
    - Any blockers or deviations noticed
    Then **STOP IMMEDIATELY** and wait for human review.
```

---

## 2. Exact Prompt for Implementing Ticket 1 (`TKT-01`)

Send the following exact prompt to your chosen coding agent (`omp`, `opencode`, `claude`, or `codex`) to execute **Ticket 1 (`TKT-01: VPS Environment Audit & Clean-Room Repository Bootstrap`)**:

```markdown
# TASK ASSIGNMENT: Execute Ticket TKT-01 (VPS Environment Audit & Clean-Room Repository Bootstrap)

Read `agent_native_quant_research_rfc.md`, `AGENT_COMPATIBILITY.md`, and `TICKETS.md` (specifically `TKT-01`). You are executing **ONLY `TKT-01`**.

## Step 1: Read-Only VPS Environment Inspection
Run the following read-only inspection commands and record their exact outputs:
1. `uname -a && lscpu | grep "Model name\|CPU(s):" && free -h && df -h .`
2. `id && groups` (verify current user and whether separate `trader` / `agent` users exist)
3. `python3 --version && uv --version && git --version && sqlite3 --version`
4. Check which coding-agent CLIs and multiplexers are installed:
   `for cmd in omp opencode claude codex herdr duckdb; do which $cmd && $cmd --version 2>/dev/null || echo "$cmd: not found"; done`
5. Check whether the legacy `quant-research` repository or existing market data directories exist on disk (read-only check; DO NOT modify them):
   `ls -ld ~/quant-research /data /data/exploratory /data/holdout 2>/dev/null || true`

## Step 2: Bootstrap the `quant-lab` Repository & Dependency Lockfile
Inside the target `quant-lab` repository root:
1. Create the canonical directory tree required by `TKT-01`:
   `contracts/`, `src/warden/`, `strategies/baselines/`, `strategies/candidates/`, `experiments/active/`, `registry/returns/`, `registry/predictions/`, `cache/features/`, `tests/acceptance/`, `tests/experiments/`, `scripts/`, `adapters/omp/`, `adapters/opencode/`, `adapters/claude/`, `adapters/codex/`, `.workspace/memory/`, `.workspace/hypotheses/`, `.workspace/literature/`, `.workspace/plans/`, `.workspace/transitions/`.
2. Create `pyproject.toml` targeting Python `>=3.12,<3.15` with core dependencies (`polars`, `duckdb`, `pyarrow`, `numpy`, `scipy`, `scikit-learn`, `lightgbm`, `pyyaml`, `pydantic`) and the ML4T package suite (`ml4t-specs`, `ml4t-data`, `ml4t-engineer`, `ml4t-diagnostic`, `ml4t-backtest`, `ml4t-live`), plus dev dependencies (`pytest`, `ruff`). Define the console script entrypoint: `warden = "warden.cli:main"`.
3. Create a minimal placeholder `src/warden/__init__.py` (`__version__ = "0.1.0"`) and `src/warden/cli.py` (supporting `--version` and `--help`) so the package installs cleanly in editable mode.
4. Run `uv sync --dev` (if any `ml4t-*` package requires a specific Python version between 3.12 and 3.14 on this host, use `uv python pin 3.12` or `3.13` and document the exact resolved versions from `uv.lock`).
5. Create `scripts/inspect_env.py` as specified in `TKT-01` to programmatically verify imports and print the exact installed versions and API signatures of `polars`, `duckdb`, `pyarrow`, `ml4t.specs`, `ml4t.data`, `ml4t.engineer` (specifically checking whether `compute_features` accepts `calendar=None`), `ml4t.diagnostic`, `ml4t.backtest`, and `ml4t.live`, writing the result to `.workspace/env_manifest.json`.
6. Create `.gitignore` (ignoring `.venv/`, `cache/features/*.parquet`, `registry/returns/*.parquet`, `registry/predictions/*.parquet`, `/data/`, `.omp/logs/`, `.claude/`, `.codex/`, `.opencode/`).

## Step 3: Verify Acceptance Criteria & Commit
1. Run `uv run python scripts/inspect_env.py` and verify `.workspace/env_manifest.json` is generated with zero import errors.
2. Run `uv run warden --version` and `uv run pytest tests/test_bootstrap.py -q`.
3. Review `git status` and `git diff`, commit on branch `feat/tkt-01-bootstrap`, print the concise Completion Report with the contents of `.workspace/env_manifest.json`, and **STOP** without starting `TKT-02`.
```

---

## 3. Minimal Harness-Specific Launch Examples (Interactive & Herdr)

The core research workflow is identical across all harnesses because the agent interacts with the system via `AGENTS.md`, `.agents/skills/ml4t-*/SKILL.md`, and `uv run warden <subcommand>`. Below are the minimal launch commands for each harness:

### A. Oh My Pi (`omp`)
```bash
# Optional: activate OMP project adapter (.omp/config.yml and .omp/agents/*.md)
./scripts/setup_agent_adapter.sh --harness omp

# Launch interactively with explicit plan/slow/smol model roles
omp --plan anthropic/claude-opus-4-6 --slow anthropic/claude-sonnet-4-6 --smol anthropic/claude-haiku-4-5

# Or launch inside a persistent Herdr pane on the Linux VPS:
herdr agent start researcher --kind omp --pane w1:p1
herdr agent prompt researcher "Read AGENT_EXECUTION.md and execute TKT-01 from TICKETS.md" --wait --timeout 300000
```

### B. OpenCode (`opencode`)
```bash
# Optional: activate OpenCode project adapter (opencode.json + .opencode/agents/)
./scripts/setup_agent_adapter.sh --harness opencode

# Launch inside Herdr pane:
herdr agent start researcher --kind opencode --pane w1:p1
```

### C. Claude Code (`claude`)
```bash
# Optional: activate Claude Code adapter (CLAUDE.md + .claude/skills symlink)
./scripts/setup_agent_adapter.sh --harness claude

# Launch inside Herdr pane:
herdr agent start researcher --kind claude --pane w1:p1
```

### D. Codex CLI (`codex`)
```bash
# Codex reads AGENTS.md and .agents/skills/ natively with zero adapter files needed
herdr agent start researcher --kind codex --pane w1:p1
```

### E. Dispatching Long-Running Deterministic Jobs via `Herdr` (Harness-Neutral)
Regardless of whether the primary coding agent in pane `w1:p1` is `omp`, `opencode`, `claude`, or `codex`, long deterministic jobs (> 30 seconds) are dispatched to the raw shell worker pane `w1:p2` using `Herdr`'s agent-neutral `pane` primitive ([herdr.dev/docs/agent-automation](https://herdr.dev/docs/agent-automation/)):

```bash
# If HERDR_ENV=1 is set in the environment, dispatch to worker pane w1:p2:
herdr pane run w1:p2 "uv run warden backtest experiments/active/hyp_2026_002.yaml --json"
herdr pane wait-output w1:p2 --regex "WARDEN_RUN_COMPLETE|WARDEN_RUN_FAILED" --timeout 300000
herdr pane read w1:p2 --lines 60

# Fallback if running without Herdr (direct synchronous execution):
uv run warden backtest experiments/active/hyp_2026_002.yaml --json
```
