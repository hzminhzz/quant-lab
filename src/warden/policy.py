"""Policy control plane for warden subcommands (the agent-facing tool surface).

Policy is enforced here, in code, never by the prompt. Unknown subcommands are
denied; high-impact ones are flagged for human approval rather than executed.
"""

from __future__ import annotations

from dataclasses import dataclass

ALLOWED = frozenset({
    "status", "check-worktree", "verify", "screen", "backtest",
    "audit-check", "handoff", "continue", "register-hypothesis",
})
APPROVAL = frozenset({"qualify"})


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str
    approval_required: bool = False


def authorize(subcommand: str | None) -> Decision:
    if not subcommand:
        return Decision(False, "no subcommand")
    if subcommand in APPROVAL:
        return Decision(True, f"{subcommand} is a human-only gate", approval_required=True)
    if subcommand in ALLOWED:
        return Decision(True, "allowed")
    return Decision(False, f"{subcommand} is not on the warden allowlist")
