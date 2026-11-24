"""Warden integrity manifest, protected administrative modules, and approval checks."""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
from pathlib import Path

MANIFEST_FILE = ".warden_manifest.sha256"
_AST_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", "build", "dist", "node_modules",
})
_BANNED_REPO_MODULES = ("ml4t.live", "ml4t_live")
_BANNED_STRATEGY_ROOTS = frozenset(
    {"sqlite3", "aiosqlite", "subprocess", "httpx", "requests", "urllib", "urllib3", "socket", "aiohttp"}
)
_BANNED_STRATEGY_MODULES = ("http.client", "ml4t.backtest.engine", "ml4t.backtest.Engine")
_BANNED_FROM_CHILDREN: dict[str, dict[str, tuple[str, frozenset[str]]]] = {
    "repo-wide": {"ml4t": ("ml4t.live", frozenset({"live", "*"}))},
    "strategy": {
        "http": ("http.client", frozenset({"client", "*"})),
        "ml4t.backtest": ("ml4t.backtest.Engine", frozenset({"Engine", "engine", "*"})),
    },
}


def _matches_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def _iter_repo_py_files(repo: Path) -> list[Path]:
    py_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _AST_SKIP_DIRS and not d.endswith(".egg-info")
        )
        for fname in sorted(filenames):
            if fname.endswith(".py"):
                path = Path(dirpath) / fname
                if path.is_file():
                    py_files.append(path)
    py_files.sort(key=lambda p: p.relative_to(repo).as_posix())
    return py_files


def check_ast_boundaries(repo_root: Path = Path(".")) -> list[str]:
    """Enforce repo-wide and strategies/ AST import boundaries; fail closed on syntax errors."""
    repo = Path(repo_root)
    violations: list[str] = []
    for path in _iter_repo_py_files(repo):
        rel_path = path.relative_to(repo)
        rel = rel_path.as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, OSError, UnicodeDecodeError) as exc:
            lineno = getattr(exc, "lineno", None) or 1
            msg = getattr(exc, "msg", str(exc))
            violations.append(f"{rel}:{lineno}: {type(exc).__name__}: {msg}")
            continue

        scopes: list[tuple[str, tuple[str, ...], frozenset[str]]] = [
            ("repo-wide", _BANNED_REPO_MODULES, frozenset()),
        ]
        if rel_path.parts[0] == "strategies":
            scopes.append(("strategy", _BANNED_STRATEGY_MODULES, _BANNED_STRATEGY_ROOTS))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    root = name.split(".")[0]
                    for scope, prefixes, roots in scopes:
                        if root in roots or _matches_prefix(name, prefixes):
                            violations.append(f"{rel}:{node.lineno}: banned {scope} import '{name}'")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mod = node.module or ""
                mod_root = mod.split(".")[0]
                imported = [alias.name for alias in node.names]
                for scope, prefixes, roots in scopes:
                    from_children = _BANNED_FROM_CHILDREN[scope]
                    if mod_root in roots or _matches_prefix(mod, prefixes):
                        violations.append(f"{rel}:{node.lineno}: banned {scope} import from '{mod}'")
                    elif (
                        (rule := from_children.get(mod)) is not None
                        and any(n in rule[1] for n in imported)
                    ):
                        violations.append(
                            f"{rel}:{node.lineno}: banned {scope} import '{rule[0]}' "
                            f"(from {mod} import {', '.join(imported)})"
                        )
    return violations

def compute_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_manifest(repo_root: Path = Path(".")) -> str:
    repo = Path(repo_root)
    protected = (
        sorted(repo.glob("src/warden/**/*.py"))
        + sorted(repo.glob("src/warden_ops/**/*.py"))
        + sorted(repo.glob("src/warden_evidence/**/*.py"))
        + sorted(repo.glob("contracts/*.yaml"))
        + sorted(repo.glob("contracts/*.yml"))
        + sorted(repo.glob("contracts/datasets/*.json"))
    )
    protected += [
        p for p in (
            repo / "AGENTS.md",
            repo / "pyproject.toml",
            repo / "uv.lock",
            repo / "scripts" / "registry_evidence_snapshot.py",
            repo / "scripts" / "rehearse_state_machine_migration.py",
            repo / "scripts" / "mcp_warden" / "adapter.py",
            repo / "scripts" / "campaign_projection.py",
            repo / "scripts" / "run_carver_daily_campaign.py",
            repo / "scripts" / "research_outcome_memory.py",
            repo / "scripts" / "run_hourly_pipeline.sh",
            repo / "scripts" / "setup_agent_adapter.sh",
            repo / "scripts" / "lock_holdout_permissions.sh",
            repo / "scripts" / "lock_governance_permissions.sh",
            repo / "scripts" / "preflight_trust_boundary.py",
            repo / "scripts" / "apply_trust_boundary.sh",
        ) if p.is_file()
    ]
    protected = list(dict.fromkeys(protected))
    content = "\n".join(f"{compute_file_sha256(p)}  {p.relative_to(repo)}" for p in protected) + "\n"
    (repo / MANIFEST_FILE).write_text(content, encoding="utf-8")
    return content


def manifest_approval_state(repo_root: Path = Path(".")) -> dict:
    """Independent approval state. A self-written checksum is integrity, never authority."""
    repo = Path(repo_root)
    manifest, sig = repo / MANIFEST_FILE, repo / (MANIFEST_FILE + ".sig")
    meta = repo / (MANIFEST_FILE + ".meta.json")
    state = {"signed": False, "verified": False, "mode": "unsigned", "approver": ""}
    if meta.is_file():
        state.update({k: v for k, v in json.loads(meta.read_text(encoding="utf-8")).items()
                      if k in {"mode", "approver", "approved_at"}})
    if not sig.is_file():
        return state
    state["signed"] = True
    key = os.environ.get("WARDEN_HUMAN_SIGNING_KEY", "")
    if not key:
        state["verified"] = None  # cannot verify without the human-held key
        return state
    digest = hmac.new(key.encode(), manifest.read_bytes(), hashlib.sha256).hexdigest()
    state["verified"] = hmac.compare_digest(digest, sig.read_text(encoding="utf-8").strip())
    return state


def check_worktree(repo_root: Path = Path(".")) -> bool:
    repo = Path(repo_root)
    manifest = repo / MANIFEST_FILE
    if not manifest.is_file():
        return False
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, rel = line.strip().split(maxsplit=1)
        target = repo / rel
        if not target.is_file() or compute_file_sha256(target) != expected:
            return False
    return not check_ast_boundaries(repo)
