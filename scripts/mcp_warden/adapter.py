"""Minimal reviewed MCP stdio adapter for Warden (no HTTP, shell, SQL, or holdout).

Run via `uv run --frozen python -m scripts.mcp_warden.adapter` only from an
approved local client. Mutations call the real CLI or shared typed Control;
both acquire the existing registry lease at scientific admission. Never import the locally untracked legacy HTTP server.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from warden_ops.control_contracts import REQUEST

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# (minimum and maximum path args, permitted flag/value types)
COMMANDS = {
    "status": (0, 0, {}), "check-worktree": (0, 0, {}),
    "spec-status": (1, 1, {}),
    "register-hypothesis": (1, 1, {}), "verify": (1, 1, {}),
    "screen": (1, 1, {}), "backtest": (1, 1, {}),
    "advance": (0, 1, {"--hypothesis": "hyp_path"}),
    "audit-check": (0, 0, {"--run-id": "run_id"}),
    "verify-causality": (0, 0, {"--run-id": "run_id"}),
    "attempt-status": (0, 0, {"--run-id": "run_id"}),
    "handoff": (0, 0, {"--notes": "text", "--ticket": "text"}),
    "continue": (0, 1, {}),
}
MUTATING = frozenset({"register-hypothesis", "verify", "screen", "backtest", "advance", "audit-check", "handoff"})


class RestrictedCommandError(ValueError):
    """Command rejected by MCP policy before subprocess invocation."""


def _tracked_clean(root: Path, relative: str) -> bool:
    """Only code already reviewed in Git HEAD may be executed by this adapter."""
    for args in (["ls-files", "--error-unmatch", "--", relative],
                 ["diff", "--quiet", "HEAD", "--", relative]):
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True,
                              check=False, timeout=5)
        if proc.returncode != 0:
            return False
    return True


def _resolve_safe_relative(root: Path, value: str, *, allow_flag_prefix: bool = False) -> tuple[Path, Path]:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RestrictedCommandError("invalid or protected path")
    if not allow_flag_prefix and value.startswith("-"):
        raise RestrictedCommandError("invalid, flag-like or protected path")
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts or "holdout" in value.lower():
        raise RestrictedCommandError("absolute/traversal or protected holdout path refused")
    target = (root / rel).resolve()
    if "holdout" in str(target).lower() or not target.is_relative_to(root.resolve()):
        raise RestrictedCommandError("protected holdout or external path refused")
    return rel, target


def _trusted_spec(root: Path, relative: str) -> None:
    if not _tracked_clean(root, relative):
        raise RestrictedCommandError("evaluation spec is not tracked and unchanged in Git HEAD")
    try:
        obj = yaml.safe_load((root / relative).read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise RestrictedCommandError("invalid reviewed experiment specification") from exc
    module = obj.get("strategy_module") if isinstance(obj, dict) else None
    if not isinstance(module, str) or not module.startswith(("strategies/candidates/", "strategies/baselines/")):
        raise RestrictedCommandError("missing reviewed strategy-module identity")
    _resolve_safe_relative(root, module, allow_flag_prefix=True)
    if not _tracked_clean(root, module):
        raise RestrictedCommandError("strategy module is not tracked and unchanged in Git HEAD")


def _path_arg(root: Path, name: str, value: str) -> str:
    rel, target = _resolve_safe_relative(root, value)
    allowed = {
        "register-hypothesis": (".workspace/hypotheses",),
        "verify": ("experiments/active",),
        "screen": ("experiments/active",),
        "backtest": ("experiments/active",),
        "advance": ("experiments/active",),
        "spec-status": ("experiments/active",),
        "continue": (".workspace/state", ".workspace/transitions"),
    }[name]
    if not target.is_file() or not any(target.is_relative_to((root / p).resolve()) for p in allowed):
        raise RestrictedCommandError("argument outside allowlisted existing files")
    if target.suffix not in ({".md", ".yaml", ".yml"} if name == "register-hypothesis" else
                             {".json", ".md"} if name == "continue" else {".yaml", ".yml"}):
        raise RestrictedCommandError("unsupported file extension")
    return str(rel)


def parse_json_document(raw: str) -> dict[str, Any] | None:
    """Parse the last complete top-level JSON object from mixed stdout (ignoring sentinels)."""
    if not raw:
        return None
    decoder = json.JSONDecoder()
    matched: dict[str, Any] | None = None
    idx = 0
    length = len(raw)
    while idx < length:
        pos = raw.find("{", idx)
        if pos == -1:
            break
        try:
            obj, end = decoder.raw_decode(raw, pos)
        except ValueError:
            idx = pos + 1
            continue
        if isinstance(obj, dict):
            matched = obj
        idx = end
    return matched


def build_argv(command: str, positional: list[str] | None = None,
               flags: dict[str, str | bool] | None = None, root: Path | None = None) -> list[str]:
    """Render only supported CLI argument shapes. No shell or SQL passthrough."""
    effective_root = ROOT if root is None else root
    if command not in COMMANDS:
        raise RestrictedCommandError("command not allowlisted; no holdout, manifest or arbitrary shell")
    minimum, maximum, typed = COMMANDS[command]
    args = [] if positional is None else positional
    raw_flags = {} if flags is None else flags
    if not isinstance(args, list) or not minimum <= len(args) <= maximum:
        raise RestrictedCommandError("invalid positional argument count")
    if not isinstance(raw_flags, dict):
        raise RestrictedCommandError("flags must be a JSON object")
    rendered = [_path_arg(effective_root, command, p) for p in args]
    if command in {"verify", "screen", "backtest", "advance"}:
        if not rendered:
            raise RestrictedCommandError(f"{command} requires a reviewed spec path")
        _trusted_spec(effective_root, rendered[0])
    result = [sys.executable, "-m", "warden.cli", command, *rendered, "--json"]
    for key, val in raw_flags.items():
        if not isinstance(key, str) or not key:
            raise RestrictedCommandError("unsupported CLI flag or value")
        flag = key if key.startswith("--") else "--" + key
        if flag == "--json":
            if not isinstance(val, bool):
                raise RestrictedCommandError("--json must be boolean")
            continue  # Always enabled in the adapter's typed result envelope.
        kind = typed.get(flag)
        if kind is None or not isinstance(val, str) or not val:
            raise RestrictedCommandError("unsupported CLI flag or value")
        if kind == "run_id" and not RUN_ID.fullmatch(val):
            raise RestrictedCommandError("invalid run-id")
        if kind == "hyp_path":
            val = _path_arg(effective_root, "register-hypothesis", val)
        if kind == "text" and (len(val) > 2000 or "\x00" in val):
            raise RestrictedCommandError("invalid note or ticket")
        result.extend([flag, val])
    if {"--run-id"}.issubset(typed) and "--run-id" not in result:
        raise RestrictedCommandError("required --run-id missing")
    return result


def execute(command: str, positional: list[str] | None = None, flags: dict | None = None,
            root: Path | None = None, timeout: int | None = None) -> dict:
    effective_root = ROOT if root is None else root
    argv = build_argv(command, positional, flags, effective_root)
    env = {**os.environ, "WARDEN_NON_INTERACTIVE": "1"}
    effective_timeout = (600 if command in {"backtest", "advance"} else 90) if timeout is None else timeout
    try:
        proc = subprocess.run(argv, cwd=effective_root, env=env, capture_output=True, text=True,
                              check=False, timeout=min(max(effective_timeout, 1), 1800))
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "status": "TIMEOUT_REVIEW", "reason": str(exc),
                "no_auto_retry": True}
    payload = parse_json_document(proc.stdout)
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
            "status": "BUSY" if proc.returncode == 75 else "COMPLETE" if proc.returncode == 0 else "FAILED",
            "result": payload, "stdout_tail": proc.stdout[-1500:],
            "stderr_tail": proc.stderr[-1500:], "no_auto_retry": True}


def execute_control(payload, root=None):
    """Typed transport retains the older reviewed-Git execution boundary."""
    from warden_ops.control import Control
    effective_root = ROOT if root is None else root
    control = Control(effective_root)
    try:
        request = REQUEST.validate_python(payload)
        if request.operation in {"act", "enqueue"}:
            action = request.plan.action
            relative = _path_arg(effective_root, action.kind, action.spec_path)
            _trusted_spec(effective_root, relative)
    except ValidationError:
        return control.handle(payload)  # Shared typed error envelope.
    except RestrictedCommandError as exc:
        return control.refuse("AUTHORIZATION_REQUIRED", str(exc), scope="MCP reviewed Git execution",
                              remedy="review and commit the exact spec and strategy before execution")
    return control.handle(payload)


TOOLS = [
    {"name": "warden_control", "description": "Typed Warden situation/action/receipt interface",
     "inputSchema": {"type": "object", **REQUEST.json_schema()}},
    {"name": "warden_status", "description": "Read-only Warden registry status",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "warden_exec", "description": "Allowlisted native Warden CLI; no trading/holdout/shell/SQL",
     "inputSchema": {"type": "object", "properties": {
         "command": {"type": "string", "enum": sorted(COMMANDS)},
         "positional": {"type": "array", "items": {"type": "string"}, "maxItems": 1},
         "flags": {"type": "object", "additionalProperties": {"type": ["string", "boolean"]}},
     }, "required": ["command"], "additionalProperties": False}},
]


def _handle(request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "quant-lab-restricted-warden", "version": "1.0.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params") or {}
        try:
            if params.get("name") == "warden_control":
                value = execute_control(params.get("arguments") or {})
            elif params.get("name") == "warden_status":
                value = execute("status")
            elif params.get("name") == "warden_exec":
                args = params.get("arguments") or {}
                value = execute(args["command"], args.get("positional"), args.get("flags"))
            else:
                raise RestrictedCommandError("unknown MCP tool")
        except (RestrictedCommandError, KeyError, TypeError) as exc:
            value = {"ok": False, "status": "REFUSED", "reason": str(exc), "no_auto_retry": True}
        result = {"content": [{"type": "text", "text": json.dumps(value)}], "isError": not value["ok"]}
    else:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    for line in sys.stdin:
        try:
            message = json.loads(line)
            reply = _handle(message)
        except (ValueError, KeyError, TypeError) as exc:
            reply = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32600, "message": str(exc)}}
        if reply is not None:
            print(json.dumps(reply), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
