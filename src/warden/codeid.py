"""Digest of a strategy's local import closure.

A trial records which strategy file it ran, but not which bytes: helpers imported from a
sibling module are invisible. `s02`/`s03`/`s04` all import `carver_common`, so editing that
helper leaves a per-file digest unchanged while changing what ran. This hashes the module
together with every local file reachable through its module-level imports.
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
    for node in tree.body:  # module level only: function-local imports do not change the file's code
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                names.append("." * node.level + (node.module or ""))
            elif node.module:
                names.append(node.module)
    return names


def code_closure(repo_root: Path, module_path: str) -> tuple[str, list[str]]:
    """Return (digest, sorted repo-relative paths) for the module and its local imports."""
    repo_root = Path(repo_root).resolve()
    entry = repo_root / module_path
    if not entry.is_file():
        raise FileNotFoundError(f"strategy module not found: {module_path}")

    seen: set[Path] = set()
    queue = [entry]
    while queue:
        path = queue.pop()
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for name in _imported_names(tree):
            if name.startswith("."):
                base = path.parent / name.lstrip(".")
                for cand in (base.with_suffix(".py"), base / "__init__.py"):
                    if cand.is_file():
                        queue.append(cand)
                continue
            if name.split(".")[0] in _EXTERNAL_PREFIXES:
                continue
            for cand in _candidate_paths(name, path.parent):
                if cand.is_file():
                    queue.append(cand)
                    break

    rels = sorted(str(p.relative_to(repo_root)) for p in seen)
    lines = [f"{rel} {hashlib.sha256((repo_root / rel).read_bytes()).hexdigest()}" for rel in rels]
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return digest, rels
