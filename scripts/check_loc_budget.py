#!/usr/bin/env python3
"""Check Lines of Code (LOC) budgets for warden core and active experiments."""

from __future__ import annotations

import sys
from pathlib import Path

WARDEN_MAX_LOC = 1500
EXPERIMENT_MAX_LOC = 250


def count_file_loc(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    warden_dir = repo_root / "src" / "warden"

    warden_files = sorted(warden_dir.glob("*.py"))
    warden_total = sum(count_file_loc(f) for f in warden_files)

    print("=== LOC BUDGET AUDIT ===")
    print(f"src/warden/ total: {warden_total} / {WARDEN_MAX_LOC} LOC")
    for f in warden_files:
        print(f"  - {f.name}: {count_file_loc(f)} LOC")

    failed = False
    if warden_total >= WARDEN_MAX_LOC:
        msg = f"ERROR: src/warden/ exceeds budget ({warden_total} >= {WARDEN_MAX_LOC})"
        print(msg, file=sys.stderr)
        failed = True

    experiments_dir = repo_root / "experiments" / "active"
    if experiments_dir.is_dir():
        for exp in sorted(experiments_dir.glob("*.py")):
            loc = count_file_loc(exp)
            print(f"experiments/active/{exp.name}: {loc} / {EXPERIMENT_MAX_LOC} LOC")
            if loc >= EXPERIMENT_MAX_LOC:
                msg = f"ERROR: {exp.name} exceeds budget ({loc} >= {EXPERIMENT_MAX_LOC})"
                print(msg, file=sys.stderr)
                failed = True

    if failed:
        return 1
    print("PASS: All LOC budgets satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
