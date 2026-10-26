# Plan: Warden Tool-Contract & Governance Control Plane

**Status:** approved (LOC cap raised to 2,000 by human, 2026-10-26)
**Skills applied:** `ml4t-agent-tool-contracts`, `ml4t-agent-governance`, `ml4t-research-operator`
**Budget:** `src/warden/` 1,487 → est. 1,610 / 2,000 LOC. Tests do not count.
**Non-goals:** per-subcommand JSON Schema files, `list_skills`/`read_skill` tools, live trading controls.

---

## Phase 1 — Policy layer (`src/warden/policy.py`, ~55 LOC)

```python
@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str
    approval_required: bool = False

ALLOWED = {"status", "check-worktree", "verify", "screen", "backtest",
           "audit-check", "handoff", "continue", "register-hypothesis"}
APPROVAL = {"qualify"}      # still requires --human-confirm-unseal
```

- `authorize(subcommand, argv) -> Decision`; unknown subcommand → deny.
- Policy lives in code; the prompt never decides. Mirrors the governance skill's `Warden.authorize`.

## Phase 2 — Audit log (`src/warden/audit.py`, ~45 LOC)

Append-only `.workspace/audit/toolcalls.jsonl`, one line per invocation:
`{cmd, argv, decision, reason, observed_at, exit_code, duration_ms, result_bytes}`.
Same fields the tool-contracts skill requires ("arguments, status, duration, result size") and what governance requires ("immutable log of policy decisions").
Gitignore the log; it is operational, not evidence.

## Phase 3 — CLI envelope + wiring (`src/warden/cli.py`, ~45 LOC)

- `main()`: `authorize` → deny ⇒ exit 2 + audit line; approval ⇒ exit 2 unless token present.
- Read-only commands (`status`, `check-worktree`, `continue`) gain a uniform envelope under `--json`:
  `{"ok": bool, "value": <payload>, "source": "warden.<cmd>", "observed_at": iso, "policy": "allow"}`
- Bare-text output stays the default so existing callers and tests keep working.

## Phase 4 — Remove shell from continuation entirely (`cli.py`, ~20 LOC)

**Advisory confirmed empirically:** `sqlite3 registry/registry.db "SELECT writefile('/tmp/warden_probe_adv','x') FROM trials LIMIT 1"` created a 1-byte file. A `SELECT`-shaped regex allowlist is not a read-only guarantee — `writefile()`, `readfile()`, `load_extension()`, and `edit()` all reach the filesystem from a query, and opening the DB read-only does not help. **Do not pattern-match SQL.**

Replace the `sqlite3 ` prefix with typed re-execution — the trace never spawns the `sqlite3` binary:

```python
TRACE_OPS = {"git_head", "registry_count", "worktree_check"}
LEGACY = {
    "git rev-parse --short HEAD": {"op": "git_head"},
    'sqlite3 registry/registry.db "SELECT COUNT(*) FROM trials"': {"op": "registry_count", "table": "trials"},
    'sqlite3 registry/registry.db "SELECT COUNT(*) FROM hypotheses"': {"op": "registry_count", "table": "hypotheses"},
    "uv run warden check-worktree": {"op": "worktree_check"},
}
```

- `cmd_handoff` emits `{"op": ..., "expect": ...}` entries (no shell); `expect` is retained.
- `_replay` dispatches on `op`: `git_head` → `git rev-parse` via `subprocess` (no args from the trace), `registry_count` → `runner.registry._connect()` count with `table ∈ {trials, hypotheses}` asserted in code, `worktree_check` → `check_worktree()` in-process.
- Anything not in `TRACE_OPS` (including every `sqlite3` string except the two exact legacy literals) → `REFUSED`, exit 1.
- `ALLOWED_CMD_PREFIXES` shrinks to `("git rev-parse",)` — and that one is now built by code, not trusted from the file.
- Audit (Phase 2) records the resolved `op`, not the raw string, so the log is typed too.

**Why not `-readonly` / `PRAGMA query_only`:** both still permit `writefile()`. Only removing the SQL surface does.

## Phase 5 — Untrusted-evidence classification (~15 LOC, migration-sensitive)

`HypothesisSpec.source_provenance` is stored but never classified. Do **not** add a dataclass field: `register_hypothesis` compares `asdict(hyp)` against the DB row and any new field breaks re-registration of existing hypotheses.
Instead: `register-hypothesis` prints `UNTRUSTED SOURCE: <provenance>` and records it in the audit log when provenance matches `substack|blog|twitter|medium|http`. Reporting only — no schema change.

---

## Verification per phase

| Phase | Gate |
| :--- | :--- |
| 1 | `uv run pytest tests/test_policy.py -q` |
| 2 | audit line written once per invocation; `wc -l` grows by exactly 1 |
| 3 | `uv run warden status --json` envelope keys present; all existing tests still pass |
| 4 | typed ops only; **rejection tests** (below) all REFUSED with exit 1 and no side effects |
| 5 | `register-hypothesis` on a Substack-provenance file prints UNTRUSTED SOURCE |
| all | `uv run ruff check && uv run pytest -q && uv run python scripts/check_loc_budget.py && uv run warden check-worktree` |

### Phase 4 rejection matrix (all must exit 1, print `REFUSED`, leave no file)

| Trace entry | Expected |
| :--- | :--- |
| `sqlite3 registry/registry.db "SELECT COUNT(*) FROM trials"` | **replays** (42 legacy transitions depend on it) |
| `sqlite3 registry/registry.db "SELECT writefile('/tmp/warden_probe','x') FROM trials"` | REFUSED; `/tmp/warden_probe` must not exist |
| `sqlite3 registry/registry.db "SELECT load_extension('/tmp/evil')"` | REFUSED |
| `sqlite3 registry/registry.db "SELECT * FROM trials"` | REFUSED (only the defined count queries are typed) |
| `sqlite3 registry/registry.db "DROP TABLE trials"` | REFUSED; table count unchanged |
| `sqlite3 registry/registry.db "ATTACH DATABASE '/tmp/x' AS y"` | REFUSED |
| `touch /tmp/pwn` | REFUSED (unchanged red-team test) |


## Judge verdict (2026-10-26)

| Question | Result |
| :--- | :--- |
| Prewalk required? | **No** — `P(prewalk) = 0.28`. Incremental phase-by-phase edit-and-test. |
| Dominant failure mode | **A: breaking callers (0.50)** vs **B: allowlist regression (0.45)** — confidence 0.33, so treat as co-dominant, not decided. |
| Legacy Markdown transitions | **Keep the `_legacy_trace` adapter (0.95)** — they stay read-only audit history. |

**Consequences for the build (binding):**
1. Envelope must be **additive only** — `value` carries the old object, existing keys and bare-text output unchanged.
2. Phase 4's regex must still match the exact legacy command `sqlite3 registry/registry.db "SELECT COUNT(*) FROM trials"`; the allowlist test in `test_redteam_hardening.py` must pass unchanged.
3. No prewalk; but Phase 4 lands last, after Phases 1–3 are green, so a regression is attributable.

## Risks

- **Envelope breaks callers** if `--json` output shape changes. Mitigation: additive keys only; `value` carries the old object.
- **Audit log growth**: one line per call, ~200 B. Rotate at 10 MB; not built now.
- **Allowlist tightening** could refuse a legitimate future query; the predicate is one regex, easy to widen.
