"""Digest of a strategy's full executable code closure.

A trial records which strategy file it ran, but not which bytes: helpers imported from a
sibling module are invisible. `s02`/`s03`/`s04` all import `carver_common`, so editing that
helper leaves a per-file digest unchanged while changing what ran. This hashes the module
together with every local file reachable through its imports (module-level AND function-local)
plus any runtime-loaded code the module declares in `CODE_DEPENDENCIES` (e.g. the book's
constituents, s183's subdir helper) — so the identity covers all code that actually runs.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

_EXTERNAL_PREFIXES = {"warden", "ml4t", "numpy", "polars", "scipy", "pandas", "yaml", "math", "json"}


def _candidate_paths(name: str, importer_dir: Path) -> list[Path]:
    first = name.split(".")[0]
    return [importer_dir / f"{first}.py", importer_dir / first / "__init__.py"]


def _imported_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):  # every import, including function-local: all of them change what runs
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                names.append("." * node.level + (node.module or ""))
            elif node.module:
                names.append(node.module)
    return names


def _declared_dependencies(tree: ast.Module) -> list[str]:
    """Runtime-loaded code a module declares as part of its identity (CODE_DEPENDENCIES)."""
    consts: dict[str, ast.AST] = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else ([node.target] if isinstance(node, ast.AnnAssign) else [])
        val = getattr(node, "value", None)
        if val is None:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                consts[t.id] = val

    def strings(n):
        if isinstance(n, ast.Name):
            n = consts.get(n.id)
        if isinstance(n, (ast.Tuple, ast.List)):
            return [e.value for e in n.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        return []

    return strings(consts.get("CODE_DEPENDENCIES"))


def source_path(root: Path, path: str | Path) -> Path:
    """Validate a source reference before inspecting its target.

    Do not resolve untrusted links: even stat'ing a sealed target is forbidden.
    Source is local and unlinked; mutable dataset bindings are not source.
    """
    root = Path(root)
    target = root / path
    if not target.is_relative_to(root) or ".." in target.parts:
        raise ValueError("protected or external input refused")
    parts = target.relative_to(root).parts
    # Python source can legitimately be named e.g. aaa_holdout.py. Dataset
    # trees (including holdout staging) are never code or preparation inputs.
    if (not parts or parts[0].lower() in {"data", "registry", "cache"}
            or any("holdout" in part.lower() for part in parts[:-1])
            or ("holdout" in parts[-1].lower() and target.suffix != ".py"
                and parts != ("scripts", "lock_holdout_permissions.sh"))):
        raise ValueError("protected or external input refused")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlinked source input refused")
    return target


def code_inventory(repo_root: Path, module_path: str) -> dict[str, str]:
    """Discover and hash allowed source once, using the same bytes for AST and identity."""
    root = Path(repo_root).resolve()
    entry = source_path(root, module_path)
    if not entry.is_file():
        raise FileNotFoundError(f"strategy module not found: {module_path}")
    inventory = {}
    queue = [entry]

    def include(candidate):
        candidate = source_path(root, candidate)
        if candidate.is_file():
            queue.append(candidate)
            return True
        return False

    while queue:
        path = queue.pop()
        rel = str(path.relative_to(root))
        if rel in inventory:
            continue
        raw = path.read_bytes()
        inventory[rel] = hashlib.sha256(raw).hexdigest()
        try:
            tree = ast.parse(raw.decode("utf-8"))
        except SyntaxError:
            continue
        for name in _imported_names(tree):
            if name.startswith("."):
                base = path.parent / name.lstrip(".")
                for cand in (base.with_suffix(".py"), base / "__init__.py"):
                    include(cand)
                continue
            if name.split(".")[0] in _EXTERNAL_PREFIXES:
                continue
            for cand in _candidate_paths(name, path.parent):
                if include(cand):
                    break
        for dep in _declared_dependencies(tree):
            include(root / dep)
    return dict(sorted(inventory.items()))


def code_closure(repo_root: Path, module_path: str) -> tuple[str, list[str]]:
    """Preserve the historical closure identity for allowed source bytes."""
    inventory = code_inventory(repo_root, module_path)
    lines = [f"{rel} {sha}" for rel, sha in inventory.items()]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), list(inventory)
