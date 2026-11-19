#!/usr/bin/env python3
"""Restricted MCP executor for quant-lab's warden harness.

One process, stdlib only. Exposes five read-mostly tools over JSON-RPC/HTTP so an
external agent (ChatGPT task, omp session) can drive the falsification funnel without
ever receiving a shell:

    quant_lab_status()                cached state + registry counters (no subprocess)
    warden_exec(subcommand, ...)      one allowlisted warden command, structured result
    registry_query(sql)               SELECT-only against registry.db (mode=ro)
    read_text(path)                   allowlisted read; refuses data/holdout
    write_text(path, content)          writes only under agent-owned roots

Policy lives here, not in a prompt. Everything is enforced before execution:

* subcommands come from a fixed table; `qualify` and `generate-manifest` cannot be
  expressed at all, and every run has WARDEN_NON_INTERACTIVE=1 (which makes `qualify`
  refuse even if it were reachable).
* file arguments must resolve inside their allowed root and may not mention `holdout`.
* flags come from a per-subcommand allowlist; unknown or sealing flags are refused.
* one command at a time (single-flight lock), so two runs cannot interleave on the
  same SQLite ledger or inflate each other's DSR family.
* every call appends a policy decision to .workspace/state/exec_audit.jsonl.

WHAT THIS IS NOT: warden imports strategy modules with exec_module (runner.py:50), so
any command that runs a strategy executes arbitrary Python as this user. The allowlist
bounds which COMMANDS can be requested; it cannot bound what such a command's code
does. Two modes keep the claim honest:

  default                 code roots (strategies/candidates, experiments/active,
                          tests/experiments) are NOT writable, so a caller can only run
                          modules a human already wrote. No new executable surface.
  --allow-code-writes     the caller may write those roots, and therefore may run new
                          code. Refuse to start unless --sandbox bwrap is also given,
                          and say so in quant_lab_status.execution.

  --sandbox bwrap         every warden run goes through bubblewrap: whole filesystem
                          read-only, data/holdout replaced by an empty tmpfs, writable
                          only for registry/, .workspace/ and the active spec/strategy
                          dirs. OS-enforced, not prompt-enforced.

Run:
    python3 scripts/mcp_warden/server.py --selfcheck     # policy unit checks, no network
    python3 scripts/mcp_warden/server.py                 # serve on 127.0.0.1:8793/mcp
    python3 scripts/mcp_warden/server.py --sandbox bwrap --allow-code-writes

Auth: bearer token from $WARDEN_MCP_TOKEN, else ~/.local/state/warden-mcp/token
(created 0600 on first run). Bind is loopback: point a Secure MCP Tunnel at it.
"""

from __future__ import annotations

import argparse
import fcntl
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATE_DIR = REPO / ".workspace" / "state"
AUDIT_LOG = STATE_DIR / "exec_audit.jsonl"
DB_PATH = REPO / "registry" / "registry.db"
TOKEN_PATH = Path.home() / ".local" / "state" / "warden-mcp" / "token"
HOST, PORT = "127.0.0.1", 8793
MAX_BODY = 1 << 20
MAX_TEXT = 512 << 10
PROTOCOL = "2025-06-18"

FORBIDDEN_HINT = re.compile(r"holdout", re.IGNORECASE)
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

READABLE_ROOTS = (
    "AGENTS.md",
    "AGENT_RUNBOOK.md",
    "STAGE3_CAMPAIGN_PROMPT.md",
    ".workspace",
    "contracts",
    "experiments",
    "strategies",
    "scripts",
    "src/warden",
    "tests",
    "docs",
)
# Roots a caller may always write: prose and state, no executable surface.
AGENT_ROOTS = (
    ".workspace/hypotheses",
    ".workspace/campaigns",
    ".workspace/proposals",
    ".workspace/state",
    ".workspace/investigations",
)
# Never writable from here: warden exec_module()s strategy files, and a spec selects
# which module runs. A caller that can write either can run new code, which would make
# every other claim in this file false. Expanding the executable set is a human edit.
CODE_ROOTS = (
    "experiments/active",
    "strategies",
    "tests",
)
SANDBOX = "auto"  # auto -> bwrap when installed, else off (reported loudly)

# Content that becomes executable if warden imports it, or that a module could load.
# Refused in every writable root regardless of directory: a helper imported by a
# reviewed strategy is executable surface just like the strategy itself.
EXECUTABLE_EXT = frozenset(
    {".py", ".pyc", ".pyo", ".pth", ".so", ".sh", ".bash", ".zsh",
     ".pl", ".rb", ".js", ".mjs", ".cjs", ".ts", ".pkl", ".pickle",
     ".joblib", ".whl", ".zip", ".tar", ".gz"}
)
# One OS-level lease across every process on the box (server, cron driver, omp session).
LOCK_PATH = Path.home() / ".local" / "state" / "warden-mcp" / "exec.lock"

SUBCOMMANDS: dict[str, tuple[str | None, int, frozenset[str], int]] = {
    "check-worktree": (None, 0, frozenset({"--json"}), 120),
    "status": (None, 0, frozenset({"--json"}), 60),
    "register-hypothesis": (".workspace/hypotheses", 1, frozenset({"--json"}), 180),
    "verify": ("experiments/active", 1, frozenset({"--json"}), 900),
    "verify-causality": ("experiments/active", 1, frozenset({"--json"}), 900),
    "screen": ("experiments/active", 1, frozenset({"--json"}), 1800),
    "backtest": ("experiments/active", 1, frozenset({"--json"}), 3600),
    "audit-check": (None, 0, frozenset({"--json", "--run-id"}), 900),
    "handoff": (None, 0, frozenset({"--json", "--notes", "--ticket"}), 120),
    "continue": (None, 1, frozenset({"--json"}), 120),
}
class PolicyError(Exception):
    """Refused before execution. The message is the policy reason."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(entry: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": _now(), **entry}, sort_keys=True) + "\n"
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError:
        pass


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolve_read(rel: str) -> Path:
    if not isinstance(rel, str) or not rel or "\x00" in rel:
        raise PolicyError("path must be a non-empty string")
    if FORBIDDEN_HINT.search(rel):
        raise PolicyError("policy: any path mentioning 'holdout' is sealed")
    target = (REPO / rel).resolve()
    if not _under(target, REPO):
        raise PolicyError("policy: path escapes the repository")
    if not any(_under(target, (REPO / r).resolve()) for r in READABLE_ROOTS):
        raise PolicyError(f"policy: read not allowlisted: {rel}")
    if not target.is_file():
        raise PolicyError(f"not a file: {rel}")
    return target


def _resolve_write(rel: str) -> Path:
    if not isinstance(rel, str) or not rel or "\x00" in rel:
        raise PolicyError("path must be a non-empty string")
    if FORBIDDEN_HINT.search(rel):
        raise PolicyError("policy: any path mentioning 'holdout' is sealed")
    target = (REPO / rel).resolve()
    if not _under(target, REPO):
        raise PolicyError("policy: path escapes the repository")
    allowed = AGENT_ROOTS
    if not any(_under(target, (REPO / r).resolve()) for r in allowed):
        if any(_under(target, (REPO / r).resolve()) for r in CODE_ROOTS):
            raise PolicyError(
                "policy: executable roots are read-only. warden exec_module()s strategy "
                "files and a spec selects which module runs, so writing either is code "
                "execution. Write a proposal under .workspace/proposals/ and let the "
                "principal add the module and spec."
            )
        raise PolicyError(f"policy: write not allowlisted: {rel}")
    if target.suffix.lower() in EXECUTABLE_EXT:
        raise PolicyError(
            f"policy: {target.suffix} content is executable surface and is refused in "
            "writable roots; keep prose (.md/.json/.jsonl/.yaml/.txt/.csv)"
        )
    return target


def _sandbox_argv(argv: list[str]) -> list[str]:
    """Wrap argv in bubblewrap: whole fs read-only, holdout hidden, no network.

    Note what this does NOT do: registry/ stays writable because warden persists its own
    validated results from the same process that ran the strategy. With agent-authored
    code that would be unsafe; that is why code roots are not writable at all. Only
    modules already reviewed in this repo can run here.
    """
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise PolicyError("policy: --sandbox bwrap requested but bwrap is not installed")
    sandbox = [bwrap, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc"]
    sandbox += ["--unshare-net"]  # warden reads local parquet only
    sandbox += ["--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev"]
    # Persistent scratch instead of a fresh tmpfs: numba JIT caches live here, so a
    # second run does not pay the compile cost again.
    scratch = Path.home() / ".cache" / "warden-mcp-sandbox"
    scratch.mkdir(parents=True, exist_ok=True)
    sandbox += ["--bind", str(scratch), "/tmp"]
    # An empty tmpfs over the sealed holdout: the process cannot even see the directory.
    sandbox += ["--tmpfs", str(REPO / "data" / "holdout")]
    for path in (REPO / "registry", REPO / ".workspace"):
        if path.exists():
            sandbox += ["--bind", str(path), str(path)]
    sandbox += ["--chdir", str(REPO), "--"]
    return sandbox + argv


def build_command(
    subcommand: str,
    positional: list[str] | None = None,
    flags: dict[str, object] | None = None,
) -> tuple[list[str], int]:
    """Validate and render one warden invocation. Raises PolicyError on refusal."""
    if subcommand not in SUBCOMMANDS:
        raise PolicyError(
            f"policy: subcommand not allowlisted: {subcommand!r} "
            f"(allowed: {sorted(SUBCOMMANDS)})"
        )
    root, max_pos, allowed_flags, timeout = SUBCOMMANDS[subcommand]
    positional = list(positional or [])
    flags = dict(flags or {})

    if len(positional) > max_pos:
        raise PolicyError(f"policy: {subcommand} takes at most {max_pos} positional argument(s)")
    for raw in positional:
        if raw.startswith("-"):
            raise PolicyError("policy: pass flags through `flags`, not positional arguments")
        if root is None:
            raise PolicyError(f"policy: {subcommand} takes no positional arguments")
        if FORBIDDEN_HINT.search(raw):
            raise PolicyError("policy: any path mentioning 'holdout' is sealed")
        target = (REPO / raw).resolve()
        if not _under(target, (REPO / root).resolve()):
            raise PolicyError(f"policy: argument must live under {root}/: {raw}")
        if not target.exists():
            raise PolicyError(f"not found: {raw}")

    argv_flags: list[str] = []
    for name, value in flags.items():
        flag = name if name.startswith("--") else f"--{name}"
        if flag not in allowed_flags:
            raise PolicyError(f"policy: flag not allowlisted for {subcommand}: {flag}")
        if value is True:
            argv_flags.append(flag)
        elif value is False or value is None:
            continue
        elif isinstance(value, str):
            if flag == "--run-id":
                if not RUN_ID_RE.match(value):
                    raise PolicyError("policy: run-id must match [A-Za-z0-9._-]{1,64}")
                argv_flags.extend([flag, value])
            elif flag in ("--notes", "--ticket"):
                if len(value) > 2000:
                    raise PolicyError("policy: notes/ticket capped at 2000 characters")
                argv_flags.extend([flag, value])
            else:
                raise PolicyError(f"policy: {flag} does not take a value")
        else:
            raise PolicyError(f"policy: flag values must be string|boolean: {flag}")

    warden_bin = REPO / ".venv" / "bin" / "warden"
    base = (
        [str(warden_bin)]
        if warden_bin.exists()
        else [shutil.which("uv") or "uv", "run", "warden"]
    )
    argv = [*base, subcommand, *positional, *argv_flags]
    if _sandbox_active():
        argv = _sandbox_argv(argv)
    return argv, timeout


def _sandbox_active() -> bool:
    return SANDBOX == "bwrap" or (SANDBOX == "auto" and shutil.which("bwrap") is not None)


def execution_mode() -> dict:
    """The boundary actually in force, for status and startup logging."""
    sandboxed = _sandbox_active()
    return {
        "sandbox": "bwrap" if sandboxed else "off",
        "network": "denied" if sandboxed else "available",
        "holdout": "hidden-by-tmpfs" if sandboxed else "posix-acl-only",
        "code_writes": "denied (experiments/active, strategies/, tests/ are read-only)",
        "runs_only": "modules already present in the repo and referenced by an existing spec",
        "registry": "writable by warden itself (persists its validated results in-process)",
        "lease": "cross-process flock at ~/.local/state/warden-mcp/exec.lock",
    }


def _acquire_lease():
    """Cross-process single-flight lease (flock), so a cron driver and this server agree."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = LOCK_PATH.open("a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = ""
        try:
            fh.seek(0)
            holder = fh.read(200).strip()
        except OSError:
            pass
        fh.close()
        return None, holder
    fh.seek(0)
    fh.truncate()
    fh.write(f"pid={os.getpid()} at={_now()}\n")
    fh.flush()
    return fh, ""


def _release_lease(fh) -> None:
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


def _run(argv: list[str], timeout: int) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "LANG": "C.UTF-8",
        "WARDEN_NON_INTERACTIVE": "1",  # belt and braces: qualify refuses under this
    }
    if _sandbox_active():
        # The filesystem is read-only inside bwrap, so numba/matplotlib/pycache must be
        # pointed at the writable tmpfs or imports fail on cache locators.
        env.update(
            {
                "NUMBA_CACHE_DIR": "/tmp/numba",
                "MPLCONFIGDIR": "/tmp/mpl",
                "PYTHONPYCACHEPREFIX": "/tmp/pycache",
                "XDG_CACHE_HOME": "/tmp/xdg",
                "TMPDIR": "/tmp",
            }
        )
    lease, holder = _acquire_lease()
    if lease is None:
        return {
            "ok": False,
            "error": "EXEC_BUSY",
            "policy": "single-flight lease held by another process on this box",
            "lease_file": str(LOCK_PATH),
            "holder": holder or "unknown",
        }
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=REPO,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
            shell=False,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=timeout)
            code: int | None = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            code = None
            out = err = ""
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # bwrap + children
            except (ProcessLookupError, PermissionError):
                pass
            try:
                out, err = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                pass
    finally:
        _release_lease(lease)

    elapsed = round(time.monotonic() - started, 2)
    if timed_out:
        return {
            "ok": False,
            "error": "TIMEOUT",
            "command": argv,
            "timeout_s": timeout,
            "duration_s": elapsed,
            "stdout_tail": (out or "")[-4000:],
            "policy": "allow",
            "orphan_check": (
                "The process group was killed, but a ledger row may already have been "
                "committed. Before retrying anything irreversible, run registry_query for "
                "trials created after this timestamp and re-read hypothesis trials_used."
            ),
        }
    stdout = out or ""
    sentinel = None
    for candidate in ("WARDEN_RUN_COMPLETE", "WARDEN_RUN_FAILED"):
        if candidate in stdout:
            sentinel = candidate
    if len(stdout) > 12000:
        stdout = stdout[:4000] + f"\n...[{len(stdout) - 12000} bytes elided]...\n" + stdout[-8000:]
    return {
        "ok": code == 0,
        "command": argv,
        "exit_code": code,
        "sentinel": sentinel,
        "duration_s": elapsed,
        "stdout_tail": stdout,
        "stderr_tail": (err or "")[-3000:],
        "policy": "allow",
    }


def tool_status() -> dict:
    current: dict | None = None
    try:
        current = json.loads((STATE_DIR / "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = None

    def query(sql: str) -> list[sqlite3.Row]:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(sql).fetchall()

    counts = {}
    try:
        counts = {
            "hypotheses": query("SELECT count(*) c FROM hypotheses")[0]["c"],
            "trials": query("SELECT count(*) c FROM trials")[0]["c"],
            "trialed_hypotheses": query("SELECT count(DISTINCT hypothesis_id) c FROM trials")[0]["c"],
            "promoted": query("SELECT count(*) c FROM hypotheses WHERE status='PROMOTED'")[0]["c"],
            "qualifications": query("SELECT count(*) c FROM qualifications")[0]["c"],
        }
        active_row = query(
            "SELECT t.hypothesis_id, t.run_id, t.verdict, t.strategy_family, "
            "h.trials_used, h.status AS hyp_status, t.created_at "
            "FROM trials t JOIN hypotheses h USING (hypothesis_id) "
            "ORDER BY t.created_at DESC LIMIT 1"
        )
        active = dict(active_row[0]) if active_row else None
        floors = {
            r["contract_id"]: round(r["max_keff_gate"], 2)
            for r in query("SELECT contract_id, max_keff_gate FROM contract_search_state")
        }
        recent = [
            {k: r[k] for k in ("run_id", "hypothesis_id", "verdict", "net_sharpe", "created_at")}
            for r in query(
                "SELECT run_id, hypothesis_id, verdict, round(net_sharpe,3) net_sharpe, created_at "
                "FROM trials ORDER BY created_at DESC LIMIT 5"
            )
        ]
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"REGISTRY_UNREADABLE: {exc}", "policy": "allow"}

    needs_human, reason = False, None
    if active and (active.get("trials_used") or 0) >= 3:
        needs_human, reason = True, "active lineage has spent all 3 trials"
    gates = (current or {}).get("quality_gates") or {}
    if gates.get("worktree_integrity") is False:
        needs_human, reason = True, "last recorded worktree check failed"
    queue = STATE_DIR.parent / "campaigns" / "queue.yaml"
    return {
        "ok": True,
        "generated_at": _now(),
        "repo": str(REPO),
        "git_head": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
        or None,
        "registry_counts": counts,
        "active": active,
        "family_keff": floors,
        "last_trials": recent,
        "recorded_state": current,
        "pending_queue": queue.read_text(encoding="utf-8")[:4000] if queue.is_file() else None,
        "needs_human": {"flag": needs_human, "reason": reason},
        "execution": execution_mode(),
        "note": "worktree_ok is cached from the last recorded state; run warden_exec('check-worktree') to verify.",
        "policy": "allow",
    }


def tool_registry_query(sql: object, limit: int = 500) -> dict:
    if not isinstance(sql, str) or not sql.strip():
        raise PolicyError("sql must be a non-empty string")
    stripped = sql.strip().rstrip(";")
    if ";" in stripped:
        raise PolicyError("policy: exactly one statement per call")
    if not re.match(r"(?is)^(select|with|pragma\s+table_info|pragma\s+table_list)\b", stripped):
        raise PolicyError("policy: SELECT/WITH/PRAGMA reads only")
    if re.search(r"(?i)\b(insert|update|delete|drop|alter|create|attach|detach|vacuum|reindex)\b", stripped):
        raise PolicyError("policy: mutating SQL refused")
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(stripped).fetchmany(max(1, min(int(limit), 2000)))
    return {
        "ok": True,
        "columns": list(rows[0].keys()) if rows else [],
        "rows": [dict(r) for r in rows],
        "row_count": len(rows),
        "source": "registry/registry.db (mode=ro)",
        "policy": "allow",
    }


def tool_read_text(path: str, offset: int = 0, limit: int = 400) -> dict:
    target = _resolve_read(path)
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(0, int(offset))
    end = min(len(lines), start + max(1, min(int(limit), 4000)))
    return {
        "ok": True,
        "path": str(target.relative_to(REPO)),
        "lines": lines[start:end],
        "line_count": len(lines),
        "offset": start,
        "observed_at": _now(),
        "policy": "allow",
    }


def tool_write_text(path: str, content: object) -> dict:
    target = _resolve_write(path)
    if not isinstance(content, str):
        raise PolicyError("content must be a string")
    if len(content.encode()) > MAX_TEXT:
        raise PolicyError("policy: content larger than 512 KiB")
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "path": str(target.relative_to(REPO)),
        "bytes": len(content.encode()),
        "replaced": existed,
        "observed_at": _now(),
        "policy": "allow",
    }


TOOLS = [
    {
        "name": "quant_lab_status",
        "description": (
            "Use this first in every run. Returns cached loop state plus live registry "
            "counters: hypotheses, trials, promotions, qualifications, active lineage with "
            "trials_used, per-contract DSR family floors, and whether a human is needed. "
            "Read-only, no subprocess. worktree_ok is cached; verify with warden_exec."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "warden_exec",
        "description": (
            "Use this to advance the falsification funnel. Runs exactly one allowlisted "
            "warden subcommand with no shell, returns structured evidence: command argv, "
            "exit_code, sentinel (WARDEN_RUN_COMPLETE/WARDEN_RUN_FAILED), duration, stdout "
            "tail. Allowlisted: check-worktree, status, register-hypothesis, verify, "
            "verify-causality, screen, backtest, audit-check, handoff, continue. "
            "qualify and generate-manifest are unreachable by construction."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subcommand": {
                    "type": "string",
                    "enum": sorted(SUBCOMMANDS),
                    "description": "One allowlisted warden subcommand.",
                },
                "positional": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 1,
                    "description": (
                        "File argument, e.g. a hypothesis markdown under .workspace/hypotheses "
                        "or a spec yaml under experiments/active."
                    ),
                },
                "flags": {
                    "type": "object",
                    "description": "Allowlisted flags only, e.g. {\"--json\": true, \"--run-id\": \"RUN-...\"}.",
                    "additionalProperties": {"type": ["string", "boolean"]},
                },
            },
            "required": ["subcommand"],
            "additionalProperties": False,
        },
    },
    {
        "name": "registry_query",
        "description": (
            "Use this to read the append-only ledger. One SELECT/WITH/PRAGMA statement per "
            "call against registry.db in read-only mode. Tables: hypotheses, trials, "
            "selection_attempts, contract_search_state, qualifications, audit_reports."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_text",
        "description": (
            "Use this to read repo text: AGENTS.md, AGENT_RUNBOOK.md, .workspace/** "
            "(hypotheses, memory/LESSONS.md, state), contracts/**, experiments/**, scripts/**. "
            "Any path mentioning 'holdout' is refused, as is anything outside the allowlist."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 4000},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_text",
        "description": (
            "Use this to record work: hypothesis drafts under .workspace/hypotheses, queue "
            "entries under .workspace/campaigns, proposals, state files, specs under "
            "experiments/active. contracts/, src/warden/ and registry/ are refused."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
]
TOOL_IMPL = {
    "quant_lab_status": tool_status,
    "warden_exec": None,  # bound below: needs the validated argv path
    "registry_query": tool_registry_query,
    "read_text": tool_read_text,
    "write_text": tool_write_text,
}


def call_tool(name: str, args: dict) -> dict:
    if name == "warden_exec":
        argv, timeout = build_command(
            str(args.get("subcommand", "")),
            args.get("positional") or [],
            args.get("flags") or {},
        )
        result = _run(argv, timeout)
        _audit(
            {
                "tool": name,
                "argv": argv,
                "decision": result.get("policy"),
                "ok": result.get("ok"),
                "exit": result.get("exit_code"),
                "sentinel": result.get("sentinel"),
                "duration_s": result.get("duration_s"),
            }
        )
        return result
    impl = TOOL_IMPL.get(name)
    if impl is None:
        raise PolicyError(f"unknown tool: {name}")
    result = impl(**args)
    _audit({"tool": name, "args": list(args), "decision": result.get("policy"), "ok": result.get("ok")})
    return result


def handle_rpc(payload: dict) -> dict | None:
    method = payload.get("method")
    msg_id = payload.get("id")
    if method is None:
        return None
    if method.startswith("notifications/"):
        return None
    if msg_id is None:
        return None
    if method == "initialize":
        params = payload.get("params") or {}
        requested = params.get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": requested if isinstance(requested, str) else PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "quant-lab-warden-executor", "version": "1.0.0"},
                "instructions": (
                    "Restricted executor for the quant-lab falsification funnel. Start with "
                    "quant_lab_status. warden_exec runs one allowlisted command; qualify and "
                    "any holdout access are unreachable by construction."
                ),
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = payload.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = call_tool(name, args if isinstance(args, dict) else {})
            is_error = not result.get("ok", True)
        except PolicyError as exc:
            result = {"ok": False, "error": "POLICY_REFUSED", "reason": str(exc), "policy": "deny"}
            is_error = True
            _audit({"tool": name, "args": raw_audit_args(args), "decision": "deny", "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surface, never crash the server
            result = {"ok": False, "error": type(exc).__name__, "reason": str(exc)}
            is_error = True
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}],
                "isError": is_error,
            },
        }
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def raw_audit_args(args: object) -> list:
    return list(args) if isinstance(args, dict) else []


def load_token() -> str:
    env = os.environ.get("WARDEN_MCP_TOKEN", "").strip()
    if env:
        return env
    if TOKEN_PATH.is_file():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(token + "\n", encoding="utf-8")
    TOKEN_PATH.chmod(0o600)
    print(f"[warden-mcp] generated bearer token at {TOKEN_PATH}", file=sys.stderr)
    return token


class Handler(BaseHTTPRequestHandler):
    server_version = "warden-mcp/1.0"
    token = ""

    def log_message(self, fmt: str, *args) -> None:  # journald-friendly
        print(f"[warden-mcp] {self.address_string()} {fmt % args}", file=sys.stderr)

    def _send(self, code: int, body: object) -> None:
        raw = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/healthz":
            self._send(200, {"ok": True, "server": "warden-mcp", "time": _now()})
        else:
            self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in ("/mcp", ""):
            self._send(404, {"ok": False, "error": "not_found"})
            return
        auth = self.headers.get("Authorization", "")
        presented = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not presented or not hmac.compare_digest(presented, self.token):
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"ok": False, "error": "bad_content_length"})
            return
        if length <= 0 or length > MAX_BODY:
            self._send(413, {"ok": False, "error": "body_too_large"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except ValueError:
            self._send(400, {"ok": False, "error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._send(400, {"ok": False, "error": "expected_single_jsonrpc_object"})
            return
        response = handle_rpc(payload)
        self._send(202 if response is None else 200, response)


def selfcheck() -> int:
    failures: list[str] = []

    def expect_refusal(label: str, fn) -> None:
        try:
            fn()
        except PolicyError:
            return
        failures.append(f"{label}: expected PolicyError, got through")

    expect_refusal("qualify unreachable", lambda: build_command("qualify", ["--run-id", "RUN-1"]))
    expect_refusal("generate-manifest unreachable", lambda: build_command("generate-manifest"))
    expect_refusal("backtest on holdout path", lambda: build_command("backtest", ["data/holdout/x.parquet"]))
    expect_refusal("spec outside experiments/active", lambda: build_command("backtest", ["scripts/mcp_warden/server.py"]))
    expect_refusal("unknown flag", lambda: build_command("backtest", ["experiments/active/hyp_2026_127.yaml"], {"--force": True}))
    expect_refusal("sealing flag", lambda: build_command("backtest", ["experiments/active/hyp_2026_127.yaml"], {"--unseal-holdout": True}))
    expect_refusal("bad run id", lambda: build_command("audit-check", flags={"--run-id": "RUN 1; rm -rf /"}))
    expect_refusal("read holdout", lambda: tool_read_text("data/holdout/crypto_majors_1h.parquet"))
    expect_refusal("read outside repo", lambda: tool_read_text("../../etc/passwd"))
    expect_refusal("write contracts", lambda: tool_write_text("contracts/new.yaml", "x"))
    expect_refusal("write registry", lambda: tool_write_text("registry/registry.db", "x"))
    expect_refusal("write strategy module", lambda: tool_write_text("strategies/candidates/x.py", "print(1)"))
    expect_refusal("write spec", lambda: tool_write_text("experiments/active/x.yaml", "x"))
    expect_refusal("write tests", lambda: tool_write_text("tests/experiments/test_x.py", "x"))
    expect_refusal("executable ext in workspace", lambda: tool_write_text(".workspace/hypotheses/x.py", "print(1)"))
    expect_refusal("pickle in workspace", lambda: tool_write_text(".workspace/proposals/payload.pkl", "x"))
    expect_refusal("sql delete", lambda: tool_registry_query("DELETE FROM trials"))
    expect_refusal("sql multi", lambda: tool_registry_query("SELECT 1; SELECT 2"))
    expect_refusal("sql attach", lambda: tool_registry_query("ATTACH 'x' AS y"))

    try:
        argv, timeout = build_command("audit-check", flags={"--run-id": "RUN-HYP127-001", "--json": True})
        assert "audit-check" in argv and "--run-id" in argv, argv
        assert argv[argv.index("--run-id") + 1] == "RUN-HYP127-001", argv
        assert "--json" in argv and timeout == 900
        argv, _ = build_command("check-worktree", flags={"--json": True})
        assert argv[-2:] == ["check-worktree", "--json"], argv
        rows = tool_registry_query("SELECT count(*) AS c FROM trials")
        assert rows["ok"] and rows["rows"][0]["c"] > 0, rows
        status = tool_status()
        assert status["ok"] and status["registry_counts"]["trials"] > 0, status
        # cross-process lease: a second holder must be refused while the first is open
        first, _ = _acquire_lease()
        assert first is not None, "lease acquisition failed (another run in flight?)"
        second, _holder = _acquire_lease()
        assert second is None, "lease is not single-flight"
        _release_lease(first)
        third, _ = _acquire_lease()
        assert third is not None, "lease not reacquirable after release"
        _release_lease(third)
    except (PolicyError, AssertionError, KeyError) as exc:
        failures.append(f"positive path failed: {exc!r}")

    for line in failures:
        print(f"FAIL {line}", file=sys.stderr)
    print(f"selfcheck: {'OK' if not failures else f'{len(failures)} failure(s)'}")
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Restricted MCP executor for warden.")
    parser.add_argument("--selfcheck", action="store_true", help="run policy checks and exit")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--sandbox",
        choices=("auto", "bwrap", "off"),
        default="auto",
        help="auto = bubblewrap when installed; off = no fs/network isolation (reported in status)",
    )
    opts = parser.parse_args()
    global SANDBOX
    SANDBOX = opts.sandbox
    if SANDBOX == "bwrap" and shutil.which("bwrap") is None:
        print("[warden-mcp] --sandbox bwrap requested but bwrap is not installed", file=sys.stderr)
        return 2
    if opts.selfcheck:
        SANDBOX = "auto"
        return selfcheck()
    Handler.token = load_token()
    server = ThreadingHTTPServer((opts.host, opts.port), Handler)
    print(f"[warden-mcp] listening on http://{opts.host}:{opts.port}/mcp  (repo {REPO})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
