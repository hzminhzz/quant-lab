#!/usr/bin/env python3
"""Strict data/registry integration checks: PASS (0), FAIL (1) or BLOCKED (3). No silent skips."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REQUIRED = ("data/exploratory", "registry/registry.db")
SUITE = Path("tests") / "integration"


def missing_preconditions(root: Path) -> list[str]:
    return [rel for rel in REQUIRED if not (root / rel).exists()]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    missing = missing_preconditions(root)
    if missing:
        print(f"BLOCKED: live integration preconditions missing: {missing}")
        print("Qualification evidence needs the shipped datasets and the migrated registry; "
              "this runner never reports success without running the checks.")
        return 3
    proc = subprocess.run([sys.executable, "-m", "pytest", str(SUITE), "-q", "-p", "no:cacheprovider"],
                          cwd=root, check=False)
    if proc.returncode != 0:
        print("FAIL: live integration checks failed")
        return 1
    print("PASS: live integration checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
