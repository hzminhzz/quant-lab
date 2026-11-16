#!/usr/bin/env python3
"""T1 grader: five required facts, regex-matched over the agent's prose.

Deliberately literal. It rewards naming the right symbols and constants, not the quality of
the reasoning between them — that limitation is stated in REGISTRATION.md.

Reads <worktree>/answer.md, falling back to the captured stdout.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


CHECKS = [
    ("names the function", r"compute_selection_report"),
    ("locates the module", r"src/warden/registry\.py|warden/registry\.py|registry\.py"),
    ("states the effective-trial formula",
     r"(0\.50|0\.5).*(unvector|attempt)|(unvector|attempt).*(0\.50|0\.5)"),
    ("uses the prior floor", r"max\s*\(|prior_max_keff|prior floor"),
    ("audit 0.80 vs gate 0.90", r"(0\.80|80\s?%).*(0\.90|90\s?%)|(0\.90|90\s?%).*(0\.80|80\s?%)"),
]

CONSEQUENCE = r"(pass|passes|clear|clears)[^.]{0,60}(audit)[^.]{0,80}(reject|fail)|" \
              r"(audit)[^.]{0,60}(looser|laxer|weaker|below)[^.]{0,60}(gate|threshold)"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--worktree", required=True)
    p.add_argument("--answer", default=None)
    a = p.parse_args(argv)

    wt = Path(a.worktree)
    candidates = [wt / "answer.md", wt / "answer.txt"]
    if a.answer:
        candidates.append(Path(a.answer))
    text, used = "", None
    for c in candidates:
        if c.is_file() and c.read_text(encoding="utf-8", errors="replace").strip():
            text, used = c.read_text(encoding="utf-8", errors="replace"), c
            break

    body = norm(text)
    checks = [{"name": name, "ok": bool(re.search(pat, body)), "detail": pat}
              for name, pat in CHECKS]
    bonus = {"name": "states the consequence", "ok": bool(re.search(CONSEQUENCE, body)),
             "detail": "passes audit while the runner gate rejects"}
    checks.append(bonus)

    words = len(text.split())
    checks.append({"name": "under 300 words", "ok": 0 < words <= 300, "detail": f"{words} words"})

    score = sum(c["ok"] for c in checks) / len(checks)
    verdict = {
        "pass": score >= 0.8,
        "score": round(score, 3),
        "checks": checks,
        "notes": f"source={used} words={words}",
    }
    print(json.dumps(verdict))
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
