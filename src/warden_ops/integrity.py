"""Warden integrity manifest, protected administrative modules, and approval checks."""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
from pathlib import Path

MANIFEST_FILE = ".warden_manifest.sha256"
MANIFEST_SHA256_LEN = 64
_PROTECTED_GLOBS = (
    "src/warden/**/*.py",
    "src/warden_ops/**/*.py",
    "src/warden_evidence/**/*.py",
    "contracts/*.yaml",
    "contracts/*.yml",
    "contracts/datasets/*.json",
)
_PROTECTED_FILES = (
    "AGENTS.md",
    "pyproject.toml",
    "uv.lock",
    "scripts/registry_evidence_snapshot.py",
    "scripts/rehearse_state_machine_migration.py",
    "scripts/mcp_warden/adapter.py",
    "scripts/campaign_projection.py",
    "scripts/run_carver_daily_campaign.py",
    "scripts/research_outcome_memory.py",
    "scripts/run_hourly_pipeline.sh",
    "scripts/setup_agent_adapter.sh",
    "scripts/lock_holdout_permissions.sh",
    "scripts/lock_governance_permissions.py",
    "scripts/preflight_trust_boundary.py",
    "scripts/apply_trust_boundary.sh",
)
_AST_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", "build", "dist", "node_modules",
})
_BANNED_REPO_MODULES = ("ml4t.live", "ml4t_live")
_BANNED_STRATEGY_ROOTS = frozenset(
    {
        "sqlite3", "aiosqlite", "subprocess", "httpx", "requests", "urllib", "urllib3", "socket", "aiohttp",
        "duckdb", "importlib",
    }
)
_BANNED_STRATEGY_MODULES = ("http.client", "ml4t.backtest.engine", "ml4t.backtest.Engine")
_BANNED_STRATEGY_CALLS = frozenset({"__import__", "eval", "exec"})
# Registered strategy modules that predate the dynamic-primitive ban. Pinned by exact
# (path, sha256): editing a pinned file removes its exemption, and a copy of the same
# bytes at another path is never exempt. Both are frozen baseline code recorded in
# registry evidence, so they must not be edited to satisfy a scan.
_LEGACY_DYNAMIC_PRIMITIVE_EXEMPT: dict[str, str] = {
    "strategies/candidates/s183_reference.py": (
        "ef0dd455567df35908ba918d959e44daeb8db097b49dd10930081db535a63f0d"
    ),
    "strategies/candidates/social_stoic_xsmom_10d21d.py": (
        "538d27cc4437a7c8d5c6be81830d0241c7c4e1d5bcaef666280ce032ce0dd6df"
    ),
}
_LEGACY_EXEMPT_STRATEGY_ROOTS = frozenset({"importlib"})
_STRATEGY_ROOTS_ACTIVE = _BANNED_STRATEGY_ROOTS - _LEGACY_EXEMPT_STRATEGY_ROOTS
_BANNED_FROM_CHILDREN: dict[str, dict[str, tuple[str, frozenset[str]]]] = {
    "repo-wide": {"ml4t": ("ml4t.live", frozenset({"live", "*"}))},
    "strategy": {
        "http": ("http.client", frozenset({"client", "*"})),
        "ml4t.backtest": ("ml4t.backtest.Engine", frozenset({"Engine", "engine", "*"})),
    },
}


def _matches_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def _call_target_name(func: ast.expr) -> str | None:
    """Return the called builtin's bare name for `name(...)` or `builtins.name(...)`."""
    if isinstance(func, ast.Name):
        return func.id
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "builtins"
    ):
        return func.attr
    return None


def _is_legacy_dynamic_primitive_module(rel: str, path: Path) -> bool:
    """True only for an unmodified, path-pinned pre-ban strategy module."""
    pinned = _LEGACY_DYNAMIC_PRIMITIVE_EXEMPT.get(rel)
    if pinned is None:
        return False
    return hmac.compare_digest(compute_file_sha256(path), pinned)


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

        is_strategy = rel_path.parts[0] == "strategies"
        legacy_dynamic = is_strategy and _is_legacy_dynamic_primitive_module(rel, path)
        strategy_roots = _STRATEGY_ROOTS_ACTIVE if legacy_dynamic else _BANNED_STRATEGY_ROOTS
        scopes: list[tuple[str, tuple[str, ...], frozenset[str]]] = [
            ("repo-wide", _BANNED_REPO_MODULES, frozenset()),
        ]
        if is_strategy:
            scopes.append(("strategy", _BANNED_STRATEGY_MODULES, strategy_roots))

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
            elif isinstance(node, ast.Call) and is_strategy and not legacy_dynamic:
                called = _call_target_name(node.func)
                if called in _BANNED_STRATEGY_CALLS:
                    violations.append(f"{rel}:{node.lineno}: banned strategy call '{called}()'")
    return violations

def compute_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_manifest_entries(repo_root: Path = Path(".")) -> dict[str, Path]:
    """The exact protected set: evaluator packages, contracts, dataset manifests, trust files.

    This is the single source of truth shared by manifest generation and verification, so a
    newly added file under a protected directory can never be silently absent from the set.
    """
    repo = Path(repo_root)
    entries: dict[str, Path] = {}
    for pattern in _PROTECTED_GLOBS:
        for path in sorted(repo.glob(pattern)):
            if path.is_file():
                entries[path.relative_to(repo).as_posix()] = path
    for rel in _PROTECTED_FILES:
        path = repo / rel
        if path.is_file():
            entries[rel] = path
    return entries


def generate_manifest(repo_root: Path = Path(".")) -> str:
    repo = Path(repo_root)
    protected = list(protected_manifest_entries(repo).values())
    content = "\n".join(f"{compute_file_sha256(p)}  {p.relative_to(repo).as_posix()}" for p in protected) + "\n"
    (repo / MANIFEST_FILE).write_text(content, encoding="utf-8")
    return content


def _parse_manifest(manifest: Path) -> dict[str, str] | None:
    """Parse `<sha256>  <relpath>` lines; malformed, empty or duplicate entries fail closed."""
    entries: dict[str, str] = {}
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not any(line.strip() for line in lines):
        return None
    for line in lines:
        if not line.strip():
            continue
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            return None
        digest, rel = parts
        if len(digest) != MANIFEST_SHA256_LEN or any(c not in "0123456789abcdef" for c in digest):
            return None
        if rel in entries:
            return None
        entries[rel] = digest
    return entries


def _manifest_signature_ok(repo: Path) -> bool:
    """Verify a present human signature whenever the human-held key is available.

    A missing signature is not a hash-integrity failure (approval state reports it); a present
    signature under a supplied key must match the manifest bytes exactly.
    """
    sig = repo / (MANIFEST_FILE + ".sig")
    key = os.environ.get("WARDEN_HUMAN_SIGNING_KEY", "")
    if not sig.is_file() or not key:
        return True
    try:
        recorded = sig.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return False
    digest = hmac.new(key.encode(), (repo / MANIFEST_FILE).read_bytes(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, recorded)


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
    """Fail closed unless the manifest lists exactly the protected set with matching hashes."""
    repo = Path(repo_root)
    manifest = repo / MANIFEST_FILE
    if not manifest.is_file():
        return False
    listed = _parse_manifest(manifest)
    if listed is None:
        return False
    protected = protected_manifest_entries(repo)
    # Exact set equality: an unlisted new file under a protected directory, a manifest entry
    # whose file disappeared, and a stale manifest are all integrity failures.
    if set(listed) != set(protected):
        return False
    for rel, expected in listed.items():
        if compute_file_sha256(protected[rel]) != expected:
            return False
    if not _manifest_signature_ok(repo):
        return False
    return not check_ast_boundaries(repo)
