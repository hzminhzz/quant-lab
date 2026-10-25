# Codex CLI Adapter for quant-lab

Codex CLI natively discovers repository configuration and domain skills without requiring a dedicated harness adapter or configuration directory.

## Native Discovery

1. **Root Instructions (`AGENTS.md`):** Codex CLI reads `AGENTS.md` automatically at session startup.
2. **Domain Skills (`.agents/skills/`):** Codex CLI scans `.agents/skills/*/SKILL.md` (61 procedural ML4T skills) directly from the repository.
3. **Session Handoffs (`.workspace/transitions/`):** Replayed and validated via `uv run warden continue` and generated via `uv run warden handoff`.

## Execution Patterns

- **Interactive Primary Session:** Run `codex` in the repository root.
- **Background Worker Pane (Herdr):**
  ```bash
  herdr agent start researcher --kind codex --pane w1:p1
  ```
- **Adversarial Audit:** Execute the 6-check audit protocol directly:
  ```bash
  uv run warden audit-check --run-id <RUN_ID> --json
  ```

## Security & Isolation

Security boundaries (read-only `contracts/`, `src/warden/`, and sealed `/data/holdout/`) are enforced by POSIX file permissions and `warden check-worktree` rather than harness configuration.
