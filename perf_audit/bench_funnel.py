"""Measure the hourly-pipeline funnel (verify+screen+backtest) end to end.

Modes:
  cli      three `uv run warden <cmd> --json` processes (the pre-fix pipeline steps A-C)
  session  `uv run python -m scripts.run_spec_session <spec> --json` (one process)

Both arms run from the worktree with `--frozen --project /home/quant/dev/quant-lab`, so
the interpreter/venv is identical and only the process/funnel structure differs.
Each rep resets the worktree registry snapshot (fixture hypothesis, fresh budget);
rep 0 starts with a cold feature cache, later reps reuse the cache.
All three stages are forced to run so the full funnel is comparable, even when the
screen fails (the production script halts there on purpose).

Run: python perf_audit/bench_funnel.py cli --reps 3
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import reset_registry as R

WT = Path(__file__).resolve().parent.parent
SPEC = "perf_audit/fixtures/bench_ultraperf.yaml"
PROJECT = "/home/quant/dev/quant-lab"
UV = ["uv", "run", "--frozen", "--project", PROJECT]


def run(cmd: list[str], env: dict | None = None) -> tuple[float, int, str, str]:
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=WT, capture_output=True, text=True, env=env, check=False)
    return time.perf_counter() - t0, p.returncode, p.stdout, p.stderr


def payload(out: str):
    try:
        data = json.loads(out[out.index("{"): out.rindex("}") + 1])
    except ValueError:
        return {}
    return data.get("value", data) if isinstance(data, dict) else {}


def parse(out: str) -> dict:
    data = payload(out)
    return {k: data[k] for k in ("run_id", "verdict", "passed", "status") if k in data}


def last_line(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def one_cli(rep: int) -> dict:
    env = dict(os.environ, WARDEN_NON_INTERACTIVE="1")
    steps = []
    t_total0 = time.perf_counter()
    for name in ("verify", "screen", "backtest"):
        wall, code, out, err = run(UV + ["warden", name, SPEC, "--json"], env)
        steps.append({"step": name, "wall_s": round(wall, 3), "exit": code, **parse(out),
                      "err": last_line(err)})
    total = time.perf_counter() - t_total0
    bt = steps[2]
    return {"rep": rep, "mode": "cli", "wall_s": round(total, 3), "steps": steps,
            "screen_passed": steps[1].get("passed"), "verdict": bt.get("verdict"),
            "run_id": bt.get("run_id"), "backtest_exit": bt["exit"]}


def one_session(rep: int) -> dict:
    env = dict(os.environ, WARDEN_NON_INTERACTIVE="1")
    wall, code, out, err = run(
        UV + ["python", "-m", "scripts.run_spec_session", SPEC, "--json", "--force-all"], env)
    info = payload(out)
    return {"rep": rep, "mode": "session", "wall_s": round(wall, 3), "exit": code,
            "screen_passed": info.get("screen_passed"), "verdict": info.get("verdict"),
            "run_id": info.get("run_id"), "err": last_line(err),
            "in_session_wall_s": round(info["wall_ms"] / 1000.0, 3) if info.get("wall_ms") else None,
            "stages": [{"stage": s["stage"], "wall_ms": s["wall_ms"], "exit": s["exit"]}
                       for s in info.get("stages", [])]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["cli", "session", "ab"])
    ap.add_argument("--reps", type=int, default=3)
    a = ap.parse_args()
    rows: list[dict] = []
    if a.mode == "ab":
        # interleaved so machine-load drift hits both arms equally
        for rep in range(a.reps):
            for arm in ("cli", "session"):
                R.reset(keep_cache=rep > 0)
                row = one_cli(rep) if arm == "cli" else one_session(rep)
                row["loadavg"] = [round(v, 2) for v in os.getloadavg()]
                rows.append(row)
                print(json.dumps(row), flush=True)
        for arm in ("cli", "session"):
            walls = sorted(r["wall_s"] for r in rows if r["mode"] == arm)
            print(json.dumps({"summary": {"mode": arm, "cold_s": walls[0],
                                          "median_s": walls[len(walls) // 2],
                                          "min_s": walls[0], "max_s": walls[-1],
                                          "reps": walls}}))
        return
    for rep in range(a.reps):
        R.reset(keep_cache=rep > 0)
        row = one_cli(rep) if a.mode == "cli" else one_session(rep)
        rows.append(row)
        print(json.dumps(row), flush=True)
    measured = sorted(r["wall_s"] for r in rows[1:]) or [rows[0]["wall_s"]]
    print(json.dumps({"summary": {"mode": a.mode, "cold_s": rows[0]["wall_s"],
                                  "median_s": measured[len(measured) // 2],
                                  "min_s": measured[0], "max_s": measured[-1]}}))


if __name__ == "__main__":
    main()
