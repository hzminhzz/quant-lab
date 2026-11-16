#!/usr/bin/env python3
"""T2 grader: mutation testing over the agent's new tests.

A test only counts if it is green on the clean tree AND red under the deadband mutant
(`<=` -> `<` in apply_buffer). Green-green is a test that proves nothing; red-red is a test
that is broken or environment-dependent.

Also enforces the task boundary: implementation and protected paths must be untouched.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

IMPL = Path("strategies/candidates/carver_daily/carver_common.py")
GUARD = "<= buffer_fraction * weight"
MUTANT = "< buffer_fraction * weight"
PROTECTED = ("src/warden/", "contracts/", "AGENTS.md", ".warden_manifest.sha256")


def changed(wt: Path) -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain"], cwd=wt, capture_output=True,
                         text=True, check=False).stdout
    paths = []
    for line in out.splitlines():
        p = line[3:].strip()
        p = p.split(" -> ", 1)[1] if " -> " in p else p
        # scratch paths created by setup.sh are not agent edits
        if p.split("/")[0] in {"registry", "data", ".venv"} or p == ".bench_baseline.json":
            continue
        paths.append(p)
    return paths


def pytest(wt: Path, targets: list[str]) -> tuple[int, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets],
        cwd=wt, capture_output=True, text=True, check=False,
    )
    return r.returncode, (r.stdout + r.stderr)[-1500:]


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--worktree", required=True)
    p.add_argument("--answer", default=None)
    a = p.parse_args(argv)
    wt = Path(a.worktree)

    paths = changed(wt)
    test_files = [q for q in paths if q.startswith("tests/") and q.endswith(".py")]
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("a test file was added or changed under tests/", bool(test_files), ", ".join(test_files))
    touched_protected = [q for q in paths if q.startswith(PROTECTED)]
    add("no protected paths touched", not touched_protected, ", ".join(touched_protected))
    impl_touched = IMPL.as_posix() in paths
    add("implementation left alone", not impl_touched, IMPL.as_posix())

    if not test_files:
        print(json.dumps({"pass": False, "score": 0.0, "checks": checks,
                          "notes": "no test file to grade"}))
        return 1

    rc0, log0 = pytest(wt, test_files)
    add("agent tests pass on the clean tree", rc0 == 0, log0[-400:])

    impl = wt / IMPL
    original = impl.read_text(encoding="utf-8")
    killed = False
    log1 = "mutant anchor not found exactly once; mutant could not be applied"
    if original.count(GUARD) == 1:
        impl.write_text(original.replace(GUARD, MUTANT), encoding="utf-8")
        try:
            rc1, log1 = pytest(wt, test_files)
            killed = rc1 != 0
        finally:
            impl.write_text(original, encoding="utf-8")
    add("agent tests kill the deadband mutant", killed, log1[-400:])

    rc2, log2 = pytest(wt, test_files)
    add("tests pass again after the revert", rc2 == 0, log2[-400:])

    score = sum(c["ok"] for c in checks) / len(checks)
    verdict = {"pass": score == 1.0, "score": round(score, 3), "checks": checks,
               "notes": f"graded {len(test_files)} test file(s)"}
    print(json.dumps(verdict))
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
