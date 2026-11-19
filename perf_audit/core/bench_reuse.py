"""Timing for screen->backtest position reuse (fix 3c) and the bare child import (fix 3a).

Each rep: fresh registry snapshot root, screen + spec in one runner (reuse), then a spec-only run
on its own fresh root for the no-reuse comparison. Prints medians plus child subprocess wall time.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

PERF = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PERF))
import bench_warden as BW  # noqa: E402

import warden  # noqa: E402
import warden.runner as R  # noqa: E402


def import_cost(module: str, reps: int = 7) -> float:
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, check=False)
        times.append(time.perf_counter() - t0)
    return round(sorted(times)[len(times) // 2], 3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=4)
    args = ap.parse_args()
    BW.instrument("backtest")
    rows = []
    for rep in range(args.reps):
        root = BW.fresh_root(f"reuse{rep}")
        BW.TIMES.clear()
        runner = R.ExperimentRunner(repo_root=root)
        t0 = time.perf_counter()
        runner.run_stage1_screen(root / "spec.yaml")
        screen_s = time.perf_counter() - t0
        BW.TIMES.clear()
        t0 = time.perf_counter()
        reused = runner.run_spec(root / "spec.yaml", override_run_id=f"RUN-REUSE-{rep}")
        spec_reuse_s = time.perf_counter() - t0
        reuse_child = BW.TIMES.get("child_subprocess_wall", [0.0, 0])[0]

        root2 = BW.fresh_root(f"noreuse{rep}")
        BW.TIMES.clear()
        runner2 = R.ExperimentRunner(repo_root=root2)
        t0 = time.perf_counter()
        plain = runner2.run_spec(root2 / "spec.yaml", override_run_id=f"RUN-REUSE-{rep}")
        spec_alone_s = time.perf_counter() - t0
        plain_child = BW.TIMES.get("child_subprocess_wall", [0.0, 0])[0]
        rows.append({"rep": rep, "core": warden.__file__, "screen_s": round(screen_s, 3),
                     "spec_after_screen_s": round(spec_reuse_s, 3),
                     "spec_alone_s": round(spec_alone_s, 3),
                     "reuse_child_subprocess_wall_s": round(reuse_child, 3),
                     "alone_child_subprocess_wall_s": round(plain_child, 3),
                     "verdict": [reused.verdict, plain.verdict],
                     "net_returns_equal": reused.net_sharpe == plain.net_sharpe})
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(root2, ignore_errors=True)
    measured = rows[1:] or rows

    def med(key):
        return sorted(r[key] for r in measured)[len(measured) // 2]

    print(json.dumps({
        "env": BW.env_record(), "core": warden.__file__, "reps": rows,
        "median_screen_s": med("screen_s"),
        "median_spec_after_screen_s": med("spec_after_screen_s"),
        "median_spec_alone_s": med("spec_alone_s"),
        "median_reuse_child_wall_s": med("reuse_child_subprocess_wall_s"),
        "median_alone_child_wall_s": med("alone_child_subprocess_wall_s"),
        "child_import_warden_runner_s": import_cost("warden.runner"),
        "child_import_strategy_loader_s": import_cost("warden.strategy_loader"),
        "all_net_sharpe_equal": all(r["net_returns_equal"] for r in rows),
    }, indent=1))


if __name__ == "__main__":
    main()
