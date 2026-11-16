#!/usr/bin/env python3
"""Aggregate runs.jsonl into the report table: pass rate, median TTA, IQR.

Medians and IQR only — one slow run should not define a harness, and a mean would let it.

    python .workspace/bench/report.py
    python .workspace/bench/report.py --tag all --task T2
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

LEDGER = Path(__file__).resolve().parent / "runs" / "runs.jsonl"


def iqr(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    q = st.quantiles(xs, n=4)
    return q[2] - q[0]


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", default="measured", help="'measured' excludes smoke/diagnostic rows")
    p.add_argument("--task", default=None)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    rows = [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
    rows = [r for r in rows if a.tag == "all" or r.get("tag") == a.tag]
    corrections = [r for r in rows if "graded_from" in r]
    rows = [r for r in rows if "graded_from" not in r]  # correction rows never double-count
    if a.task:
        rows = [r for r in rows if r["task"] == a.task]
    if not rows:
        print(f"no rows (tag={a.tag})")
        return 1

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["task"], r["harness"]), []).append(r)

    table = []
    for (task, harness), rs in sorted(groups.items()):
        done = [r for r in rs if r.get("wall_s") is not None]
        walls = [r["wall_s"] for r in done]
        passed = sum(1 for r in rs if r.get("pass"))
        table.append({
            "task": task, "harness": harness, "n": len(rs), "pass": passed,
            "pass_rate": round(passed / len(rs), 2),
            "dnf": sum(1 for r in rs if r.get("notes", "").startswith(("refused", "timed out"))),
            "dq": sum(1 for r in rs if r.get("protected_touched")),
            "median_wall_s": round(st.median(walls), 1) if walls else None,
            "iqr_wall_s": round(iqr(walls), 1) if walls else None,
            "forced_load": sum(1 for r in rs if r.get("idle_gate_forced")),
        })

    if a.json:
        print(json.dumps(table, indent=2))
        return 0

    head = f"{'task':<5}{'harness':<9}{'n':<4}{'pass':<7}{'rate':<7}{'DNF':<5}{'DQ':<4}" \
           f"{'median_s':<10}{'IQR_s':<8}{'forced':<7}"
    print(head)
    print("-" * len(head))
    for t in table:
        print(f"{t['task']:<5}{t['harness']:<9}{t['n']:<4}{t['pass']:<7}{t['pass_rate']:<7}"
              f"{t['dnf']:<5}{t['dq']:<4}{str(t['median_wall_s']):<10}"
              f"{str(t['iqr_wall_s']):<8}{t['forced_load']:<7}")
    print(f"\nrows: {len(rows)}  excluded by tag filter: {a.tag}")
    if corrections:
        for c in corrections:
            print(f"correction: {c['graded_from']} regraded -> pass={c['pass']} "
                  f"({c['notes']})")

    # paired view: only defined when runs were interleaved with --interleave
    pairs: dict[tuple[str, int], dict[str, dict]] = {}
    for r in rows:
        if "rep" in r:
            pairs.setdefault((r["task"], r["rep"]), {})[r["harness"]] = r
    deltas = []
    for (task, rep), by in sorted(pairs.items()):
        h, o = by.get("hermes"), by.get("omp")
        if not h or not o or h.get("wall_s") is None or o.get("wall_s") is None:
            continue
        if h.get("timed_out") or o.get("timed_out"):
            continue
        deltas.append((task, rep, h["wall_s"] - o["wall_s"], h["pass"], o["pass"]))
    if deltas:
        diffs = [d[2] for d in deltas]
        print("\npaired (hermes - omp), negative = hermes faster:")
        for task, rep, delta, hp, op in deltas:
            print(f"  {task} rep{rep}: {delta:+.1f}s   pass hermes={hp} omp={op}")
        print(f"  median delta {st.median(diffs):+.1f}s over {len(diffs)} pair(s); "
              f"hermes faster in {sum(1 for d in diffs if d < 0)}/{len(diffs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
