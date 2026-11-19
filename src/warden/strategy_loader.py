"""Lean strategy loader used by the isolated child process: stdlib only, no harness imports."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_module_from_path(module_path: Path):
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load strategy module from {module_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_path.stem] = mod
    # Sibling helpers import by bare name; the loader owns the path bootstrap once.
    module_dir = str(module_path.resolve().parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    spec.loader.exec_module(mod)
    return mod
