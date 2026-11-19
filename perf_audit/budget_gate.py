"""Regression-budget gate for the quant-lab one-process funnel (ultraperf fix loop).

Wraps perf_audit/bench_funnel.py (session arm, full funnel forced so arms are comparable).
Metric: IN-SESSION funnel wall (envelope wall_ms: verify+screen+backtest incl. per-stage
worktree/policy/audit, excluding the one uv/interpreter startup) — the same basis as the
7.4s in-process composite baseline. Ceiling 8.1s = baseline x 1.10 (principal-approved
2026-11-19). Process wall incl. startup is reported alongside for production context.
Exit 0 PASS, 1 FAIL, 2 error/no driver.

Run: cd <worktree> && uv run --frozen --project /home/quant/dev/quant-lab python perf_audit/budget_gate.py [--reps 4] [--budget 8.1]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

W = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(W / "perf_audit"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=4)
    ap.add_argument("--budget", type=float, default=8.1)
    args = ap.parse_args()
    if not (W / "scripts" / "run_spec_session.py").is_file():
        print(json.dumps({"gate": "ERROR", "reason": "scripts/run_spec_session.py absent — nothing to gate"}))
        return 2
    import bench_funnel as BF
    import reset_registry as R

    rows = []
    for rep in range(args.reps):
        R.reset(keep_cache=rep > 0)
        row = BF.one_session(rep)
        rows.append(row)
        print(json.dumps(row), flush=True)
    ok = [r for r in rows[1:] if r.get("exit") in (0, 1) and r.get("verdict")
          and r.get("in_session_wall_s")]
    if not ok:
        print(json.dumps({"gate": "ERROR", "reason": "no successful measured reps", "rows": rows}))
        return 2
    walls = sorted(r["in_session_wall_s"] for r in ok)
    proc = sorted(r["wall_s"] for r in ok)
    median = walls[len(walls) // 2]
    passed = median <= args.budget
    verdicts = {r.get("verdict") for r in ok}
    print(json.dumps({"gate": "PASS" if passed else "FAIL",
                      "metric": "in-session funnel wall (same basis as 7.4s composite baseline)",
                      "warmup": 1, "measured_reps": len(ok),
                      "median_s": median, "min_s": walls[0], "max_s": walls[-1],
                      "process_wall_median_s": proc[len(proc) // 2],
                      "budget_s": args.budget,
                      "budget_basis": "7.4s composite baseline x 1.10 (principal-approved 2026-11-19)",
                      "verdicts": sorted(verdicts),
                      "verdict_unchanged": verdicts == {"REVISE"}}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
