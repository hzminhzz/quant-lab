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
VENUE_POLICY_FILE = "contracts/policy/venue_policy.yaml"
_PROTECTED_GLOBS = (
    "src/warden/**/*.py",
    "src/warden_ops/**/*.py",
    "src/warden_evidence/**/*.py",
    "contracts/*.yaml",
    "contracts/*.yml",
    # A dataset manifest (``*.json``) *and* the spec that produced it are trusted inputs:
    # a build publishes both, and either one can silently re-point a contract's data.
    "contracts/datasets/*.json",
    "contracts/datasets/*.yaml",
    "contracts/datasets/*.yml",
    # Read-only contract archive (#114).  These bytes are the historical replay authority
    # for closed contracts, so editing one must invalidate the worktree gate exactly like
    # editing a live contract does; the mapping document is protected with them.
    "contracts/archive/*.yaml",
    "contracts/archive/*.yml",
    "contracts/archive/*.json",
    "contracts/archive/datasets/*.json",
)
_PROTECTED_FILES = (
    "AGENTS.md",
    "pyproject.toml",
    "uv.lock",
    # Contract lifecycle decisions (#106): deleting or editing the document invalidates the
    # worktree gate, which every agent runs before registering a hypothesis.  It is listed
    # explicitly because ``contracts/*.yaml`` deliberately does not reach its directory.
    "contracts/lifecycle/contract_lifecycle.json",
    VENUE_POLICY_FILE,
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
_BANNED_FROM_CHILDREN: dict[str, dict[str, tuple[str, frozenset[str]]]] = {
    "repo-wide": {"ml4t": ("ml4t.live", frozenset({"live", "*"}))},
    "strategy": {
        "http": ("http.client", frozenset({"client", "*"})),
        "ml4t.backtest": ("ml4t.backtest.Engine", frozenset({"Engine", "engine", "*"})),
    },
}
#: Issue #95: market bars are read through ``warden.market_data.MarketDataGateway`` only.
#: Every evaluator module is checked; the two permitted readers hold Warden-owned bytes
#: (feature cache, trial return artifacts), never market data.
_EVALUATOR_ROOTS = ("src/warden", "src/warden_evidence")
_PARQUET_READER_ALLOWLIST = frozenset({
    "src/warden/market_data.py",
    "src/warden_evidence/feature_cache.py",
    "src/warden_evidence/return_artifacts.py",
})
_PARQUET_READ_CALLS = frozenset({"read_parquet", "scan_parquet"})
_POLARS_MODULES = frozenset({"polars", "polars.io", "polars.lazyframe.frame"})


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


def _is_evaluator(rel: str) -> bool:
    return any(rel == root or rel.startswith(root + "/") for root in _EVALUATOR_ROOTS)


def _dotted(node: ast.expr) -> str:
    """``polars.io`` for the attribute chain rooted at a simple name, else ``""``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    return ".".join([node.id, *reversed(parts)])


def _polars_aliases(tree: ast.Module) -> tuple[dict[str, str], dict[str, str], int | None]:
    """Resolve every alias a module can read Parquet through.

    Returns ``(module_names, reader_names, star_import_line)``:

    * ``module_names`` maps a locally bound name to the polars module it refers to —
      ``import polars as pl``, ``import polars`` and ``import polars.io`` all bind one;
    * ``reader_names`` maps a locally bound name to ``read_parquet``/``scan_parquet`` —
      ``from polars import read_parquet as rp``;
    * ``star_import_line`` is set for ``from polars import *``, which cannot be resolved
      and is therefore rejected outright rather than assumed safe.
    """
    module_names: dict[str, str] = {}
    reader_names: dict[str, str] = {}
    star_import_line: int | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _POLARS_MODULES or alias.name.startswith("polars."):
                    module_names[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module not in _POLARS_MODULES:
                continue
            for alias in node.names:
                if alias.name == "*":
                    star_import_line = node.lineno
                elif alias.name in _PARQUET_READ_CALLS:
                    reader_names[alias.asname or alias.name] = alias.name
    return module_names, reader_names, star_import_line


def _parquet_read_target(
    node: ast.Call, module_names: dict[str, str], reader_names: dict[str, str]
) -> str | None:
    """The banned Parquet read this call performs, if any — alias-aware.

    Covers ``pl.read_parquet``, ``polars.scan_parquet``, ``pl.io.read_parquet``,
    ``from polars import read_parquet as rp; rp(...)`` and
    ``getattr(polars, "read_parquet")``.  A dotted chain rooted at a polars alias is
    rejected even when the middle attribute is unfamiliar, so a renamed submodule cannot
    slip past the rule.
    """
    func = node.func
    if isinstance(func, ast.Name) and func.id in reader_names:
        return f"{func.id} (alias of polars.{reader_names[func.id]})"
    if isinstance(func, ast.Attribute) and func.attr in _PARQUET_READ_CALLS:
        dotted = _dotted(func.value)
        root = dotted.split(".")[0] if dotted else ""
        if root in module_names:
            return f"{_dotted(func)}(...) via {root}={module_names[root]}"
    if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
        target, attr = node.args[0], node.args[1]
        if (
            isinstance(attr, ast.Constant)
            and attr.value in _PARQUET_READ_CALLS
            and isinstance(target, ast.Name)
            and target.id in module_names
        ):
            return f"getattr({target.id}, {attr.value!r})"
    return None


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
        checks_parquet = _is_evaluator(rel) and rel not in _PARQUET_READER_ALLOWLIST
        polars_names, parquet_readers, parquet_star_line = (
            _polars_aliases(tree) if checks_parquet else ({}, {}, None)
        )
        if parquet_star_line is not None:
            violations.append(
                f"{rel}:{parquet_star_line}: banned star import from 'polars' "
                "(a Parquet reader must not be imported under an unresolvable name)"
            )
        scopes: list[tuple[str, tuple[str, ...], frozenset[str]]] = [
            ("repo-wide", _BANNED_REPO_MODULES, frozenset()),
        ]
        if is_strategy:
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
            elif isinstance(node, ast.Call) and is_strategy:
                called = _call_target_name(node.func)
                if called in _BANNED_STRATEGY_CALLS:
                    violations.append(f"{rel}:{node.lineno}: banned strategy call '{called}()'")
            if checks_parquet and isinstance(node, ast.Call):
                target = _parquet_read_target(node, polars_names, parquet_readers)
                if target is not None:
                    violations.append(
                        f"{rel}:{node.lineno}: banned market-data read '{target}'; "
                        "market bars are readable only through MarketDataGateway"
                    )
    return violations

def compute_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_manifest_entries(repo_root: Path = Path(".")) -> dict[str, Path]:
    """The exact protected set: evaluator packages, contracts, dataset manifests, trust files.

    This is the single source of truth shared by manifest generation and verification, so a
    newly added file under a protected directory can never be silently absent from the set.
    """
    from warden.codeid import source_path

    repo = Path(repo_root)
    entries: dict[str, Path] = {}
    for pattern in _PROTECTED_GLOBS:
        # Validate the fixed directory prefix before glob can enter it.
        prefix = pattern.split("*", 1)[0].rstrip("/")
        if prefix:
            source_path(repo, prefix)
        for path in sorted(repo.glob(pattern)):
            rel = path.relative_to(repo).as_posix()
            if source_path(repo, rel).is_file():
                entries[rel] = path
    for rel in _PROTECTED_FILES:
        path = source_path(repo, rel)
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
    try:
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return _manifest_entries(text)


def _manifest_entries(text: str) -> dict[str, str] | None:
    """Parse `<sha256>  <relpath>` lines; malformed, empty or duplicate entries fail closed."""
    entries: dict[str, str] = {}
    lines = text.splitlines()
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


def read_signed_manifest_entry(repo_root: Path, relpath: str) -> bytes:
    """Read an exact manifest-covered input and verify the HMAC in a trusted runtime.

    Unlike the general worktree integrity check, absent or unverified signatures fail here.
    Verify and parse the same manifest bytes, and return only the input bytes hashed against it.
    Callers still need the general worktree gate for the other protected inputs and AST checks.
    """
    repo = Path(repo_root).resolve()
    path = repo / relpath
    if path.resolve() != path or relpath not in protected_manifest_entries(repo):
        raise ValueError(f"required protected input is missing or not a regular repository file: {relpath}")
    signature = repo / (MANIFEST_FILE + ".sig")
    key = os.environ.get("WARDEN_HUMAN_SIGNING_KEY", "")
    if not key or not signature.is_file():
        raise ValueError("a verified human manifest HMAC is required")
    manifest = (repo / MANIFEST_FILE).read_bytes()
    recorded = signature.read_bytes().strip()
    expected = hmac.new(key.encode(), manifest, hashlib.sha256).hexdigest().encode("ascii")
    if not hmac.compare_digest(expected, recorded):
        raise ValueError("invalid human manifest HMAC")
    listed = _manifest_entries(manifest.decode("utf-8"))
    if listed is None or relpath not in listed:
        raise ValueError(f"protected input is not covered by a valid signed manifest: {relpath}")
    data = path.read_bytes()
    if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), listed[relpath]):
        raise ValueError(f"protected input digest mismatch: {relpath}")
    return data


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
    try:
        protected = protected_manifest_entries(repo)
    except (OSError, ValueError):
        return False
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
