# Oh My Pi (OMP) Adapter for quant-lab

This directory provides the optional Oh My Pi (OMP) harness adapter for `quant-lab`, including subagent definitions and automatic skill routing.

## Architecture & Dynamic Skill Routing

### 1. Main-Session Auto-Loader
The skill router extension (`.omp/extensions/skill-router.ts`) intercepts user turns in the primary OMP interactive session. Rather than requiring manual skill invocation, the auto-loader:
1. Evaluates user intent and context against frontmatter metadata of all 61 procedural skills in `.agents/skills/`.
2. Selects relevant skills ($p \ge 0.7$) and resolves transitive dependencies deterministically.
3. Injects selected skill bodies and provenance headers directly into session context.

### 2. Per-Child Extension Routing
- **Subagent-Only Frontmatter**: The `autoloadSkills` list in `.omp/agents/*.md` (e.g., `literature-scout.md`, `adversarial-auditor.md`) is static and applies exclusively to designated subagent configurations at spawn time.
- **Dynamic Per-Child Rebinding**: When child sessions spawn via the `task` tool, the extension factory re-executes per child (`ctx.agent.kind === "sub"`). The extension dynamically resolves active skills based on the subtask's specific intent and context rather than inheriting static parent lists.

### 3. Native OMP Judgment Reuse & Bun Runtime
- **Runtime**: Native Bun runtime (bundled with OMP).
- **Minimum Version**: OMP 18.13.1.
- **Zero Third-Party SDK**: Judgment evaluation reuses OMP's native public judgment exports:
  - `resolveJudge` from `@oh-my-pi/pi-coding-agent/judgment`
  - `findScopedSettings` from `@oh-my-pi/pi-coding-agent/config/settings`
  - Root re-export: `getActiveSkills` for runtime inspection.
- The native judgment runner produces typed decisions (`{ type: "noul", noul: p }`), which the adapter translates to internal `{ type: "bool", bool: p }` probabilities without third-party SDK dependencies or custom client libraries.

### 4. Judge Model Configuration (`modelRoles.judge`)
- Standardizes on `modelRoles.judge` (introduced in OMP 18.13.1, replacing legacy 18.12.x inline `advisor` role conventions) in project or user OMP settings:
  ```yaml
  modelRoles:
    judge: "jev" # or provider-qualified identifier configured via standard OMP model registry
  ```
- Uses standard OMP authentication (environment variables or OMP auth credentials). No standalone API clients, third-party SDK packages, or additional SDK credentials are required.
### 5. Scope Changes, Ambiguity Handling & Action Gate
- **Scope Changes & Fresh Coverage**: When user intent or tool actions transition to a new task scope (e.g., shifting from exploratory data reads to model fitting or code modifications), the router evaluates the updated state to determine fresh skill coverage.
- **Conservative Inclusion**: Skills scoring $p \ge 0.7$ are selected. Ambiguous skills ($0.3 \le p < 0.7$) are conservatively included in active coverage and tracked in metadata (`ambiguous`) without prompting manual user interrupts.
- **Mutating Action Gate**: If newly required skill coverage cannot be resolved, or if routing encounters errors, cycles, or missing dependencies during mutating operations (file edits, git commits, code execution), the action gate pauses execution to prevent unguided mutations.
- **Read-Only Inspection During Failures**: Read-only tools (`read`, `grep`, `glob`, inspection commands) remain fully functional during routing failures to allow diagnostics and manual investigation.
## Operational Boundaries & Safety

- **Raw Shell, Not Sandbox**: OMP operates directly within a raw host shell without container or OS-level sandbox isolation. The action gate protects against unguided destructive edits.
- **No Warden Coupling**: The OMP adapter and skill router have zero dependency on `src/warden/`, contracts, or governance manifests. If the router fails, is removed, or is bypassed via raw shell or Codex CLI, Warden verification, leakage checks, and qualification gates remain fully intact.
- **Body Guidance vs. Correctness**: Injected skills provide soft procedural guidance and domain heuristics; they do **not** provide formal mathematical correctness or safety proofs. Hard guarantees derive exclusively from deterministic Warden gates (`uv run warden audit-check`), contract hashes, and test suites.
- **All-61 Accuracy Not Established**: Empirical routing accuracy across all 61 ML4T skills is unbenchmarked and not guaranteed. Heuristic matching operates on metadata keywords and phase intent.

## Installation & Lifecycle

Install adapter symlinks into the repo root:
```bash
./scripts/setup_agent_adapter.sh --harness omp
```
This symlinks `.omp -> adapters/omp/.omp`, exposing `.omp/extensions/skill-router.ts` and subagent definitions.

Remove adapter symlinks cleanly:
```bash
./scripts/setup_agent_adapter.sh --harness clean
```
This removes `.omp` while leaving canonical files in `adapters/omp/` and `.agents/skills/` intact.

## Verification & Tests

Run Bun test suite (OMP core tests):
```bash
bun test adapters/omp/tests
```

Run Python adapter compatibility and installer lifecycle suite:
```bash
uv run pytest tests/test_agent_compatibility.py
```
