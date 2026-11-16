#!/usr/bin/env python3
"""quant-lab harness benchmark runner (pilot: T1-T3).

Runs one graded task in an isolated worktree against a harness (hermes|omp),
times it, grades it, and appends one row to runs/runs.jsonl. Rows are
append-only: never edit a row, add a correction row instead.

    uv run python .workspace/bench/runner.py --task T1 --harness both
    uv run python .workspace/bench/runner.py --task T2 --harness omp --repeat 3
    uv run python .workspace/bench/runner.py --task T3 --harness hermes --dry-run
    uv run python .workspace/bench/runner.py --regrade <run_id>

Design notes that matter:
  * A run is DNF unless the grader passes. No self-reported success is read.
  * The idle gate refuses to start above --max-load; --force records the violation.
  * Worktrees live outside the repo so `git status` stays clean for the other agents
    editing this checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parents[1]
RUNS = BENCH / "runs"
LEDGER = RUNS / "runs.jsonl"
WORKTREE_HOME = Path.home() / ".cache" / "quant-lab-bench" / "wt"
PY = ROOT / ".venv" / "bin" / "python"
MAX_LOAD = 1.5
MANIFEST_FILE = ".warden_manifest.sha256"

TASKS = {
    "T1": BENCH / "tasks" / "T1_dsr_hurdle",
    "T2": BENCH / "tasks" / "T2_deadband_test",
    "T3": BENCH / "tasks" / "T3_partition_order",
    "T4": BENCH / "tasks" / "T4_funnel_screen",
    "T6": BENCH / "tasks" / "T6_holdout_refusal",
}

# {prompt}, {usage}, {model} are substituted. Keep these explicit: an implicit
# default is how a benchmark silently measures the wrong thing.
HARNESS = {
    "hermes": ["hermes", "--yolo", "--usage-file", "{usage}", "{flags}", "-z", "{prompt}"],
    "omp": ["omp", "-p", "{flags}", "{prompt}", "--no-session", "--approval-mode=yolo"],
}
MODEL_FLAG = {"hermes": ["-m", "{model}"], "omp": ["--model={model}"]}
# same reasoning budget on both sides: hermes spells it --reasoning, omp --thinking
REASONING_FLAG = {"hermes": ["--reasoning", "{level}"], "omp": ["--thinking={level}"]}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load1() -> float:
    return float(Path("/proc/loadavg").read_text().split()[0])


def sh(cmd, cwd=None, env=None, timeout=None, capture=True):
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        timeout=timeout,
        capture_output=capture,
        text=True,
        check=False,
    )


def git(worktree: Path, *args: str) -> str:
    return sh(["git", *args], cwd=worktree).stdout


def seed_manifest_paths(worktree: Path) -> None:
    """Overlay the working tree's manifest-covered paths onto the checkout.

    A worktree from HEAD cannot pass `warden check-worktree` here: the manifest covers
    untracked contract files and uncommitted edits, so a clean checkout fails integrity and
    every warden command exits 2. The benchmark therefore measures the working tree, not a
    commit — say so when reporting, because it is not reproducible from a hash.
    """
    manifest = ROOT / MANIFEST_FILE
    if not manifest.is_file():
        return
    paths = {line.split(maxsplit=1)[1].strip() for line in manifest.read_text().splitlines() if line.strip()}
    paths = {p for p in paths if not any(q.startswith(p + "/") for q in paths)}  # keep top-most
    for rel in sorted(paths):
        src, dest = ROOT / rel, worktree / rel
        if not src.exists():
            continue
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            sh(["rsync", "-a", "--delete", f"{src}/", f"{dest}/"])
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
    shutil.copyfile(manifest, worktree / MANIFEST_FILE)


def make_worktree(run_id: str, rev: str, task_dir: Path) -> Path:
    WORKTREE_HOME.mkdir(parents=True, exist_ok=True)
    wt = WORKTREE_HOME / run_id
    if wt.exists():
        sh(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT)
        shutil.rmtree(wt, ignore_errors=True)
    r = sh(["git", "worktree", "add", "--detach", str(wt), rev], cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"git worktree add failed:\n{r.stderr}")
    seed_manifest_paths(wt)
    # Baseline the seeded state as a commit, so `git status` afterwards shows only what the
    # agent did. Without this, the manifest overlay looks like the agent edited contracts.
    sh(["git", "add", "-A", "--", ".", ":(exclude)registry", ":(exclude)data"], cwd=wt)
    sh(["git", "-c", "user.email=bench@local", "-c", "user.name=bench",
        "commit", "-q", "--no-verify", "-m", "bench baseline"], cwd=wt)
    setup = task_dir / "setup.sh"
    if setup.is_file():
        s = sh(["bash", str(setup), str(wt)], cwd=wt, timeout=300)
        if s.returncode != 0:
            raise SystemExit(f"setup.sh failed:\n{s.stdout}\n{s.stderr}")
    return wt


def drop_worktree(wt: Path) -> None:
    sh(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT)
    shutil.rmtree(wt, ignore_errors=True)


def changed_paths(worktree: Path) -> list[str]:
    out = git(worktree, "status", "--porcelain")
    paths = []
    for line in out.splitlines():
        p = line[3:].strip()
        if " -> " in p:
            p = p.split(" -> ", 1)[1]
        paths.append(p)
    return paths


def protected_touched(paths: list[str]) -> list[str]:
    guards = ("src/warden/", "contracts/", "AGENTS.md", ".warden_manifest.sha256")
    return [p for p in paths if p.startswith(guards)]


def task_env(worktree: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(worktree / "src"), str(worktree), str(ROOT / "src")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("VIRTUAL_ENV", None)
    return env


def grade(task: str, worktree: Path, run_dir: Path) -> dict:
    cmd = [str(PY), str(TASKS[task] / "grade.py"), "--worktree", str(worktree)]
    answer = run_dir / "answer.txt"
    if answer.is_file():
        cmd += ["--answer", str(answer)]
    r = sh(cmd, cwd=worktree, env=task_env(worktree), timeout=900)
    if r.returncode not in (0, 1):
        return {"pass": False, "score": 0.0, "checks": [],
                "notes": f"grader crashed rc={r.returncode}: {r.stderr[-800:]}"}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"pass": False, "score": 0.0, "checks": [],
                "notes": f"grader emitted no JSON: {r.stdout[-500:]}"}


def harness_cmd(harness: str, prompt: str, usage: Path, model: str | None,
                reasoning: str | None = None) -> list[str]:
    flags: list[str] = []
    if model:
        flags += [part.format(model=model) for part in MODEL_FLAG[harness]]
    if reasoning:
        flags += [part.format(level=reasoning) for part in REASONING_FLAG[harness]]
    cmd: list[str] = []
    for part in HARNESS[harness]:
        if part == "{flags}":
            cmd += flags
        else:
            cmd.append(part.format(prompt=prompt, usage=str(usage)))
    return cmd


def run_one(task: str, harness: str, rev: str, model: str | None, timeout: int,
            force: bool, dry: bool, keep: bool, tag: str = "measured",
            reasoning: str | None = None, rep: int = 0) -> dict:
    task_dir = TASKS[task]
    prompt = (task_dir / "prompt.md").read_text(encoding="utf-8").strip()
    run_id = f"{task}-{harness}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    run_dir = RUNS / run_id
    usage = run_dir / "usage.json"

    row = {
        "run_id": run_id, "task": task, "harness": harness, "mode": "B" if (model or reasoning) else "A", "tag": tag,
        "rep": rep,
        "rev": rev, "model": model, "ts_start": now(),
        "load_1m_before": load1(), "idle_gate_forced": False,
        "wall_s": None, "exit_code": None, "timed_out": False,
        "token_usage": None, "files_changed": [], "protected_touched": [],
        "verdict": None, "pass": False, "notes": "",
    }
    if row["load_1m_before"] > MAX_LOAD and not force:
        row["notes"] = f"refused: load1 {row['load_1m_before']:.2f} > {MAX_LOAD}"
        row["ts_end"] = now()
        return row
    row["idle_gate_forced"] = row["load_1m_before"] > MAX_LOAD

    cmd = harness_cmd(harness, prompt, usage, model, reasoning)
    if dry:
        row["notes"] = "dry-run: would run: " + " ".join(shlex.join(cmd).split())[:150]
        row["ts_end"] = now()
        return row
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "command.json").write_text(json.dumps({"cmd": cmd, "cwd": str(WORKTREE_HOME / run_id)}, indent=2))

    wt = make_worktree(run_id, rev, task_dir)
    started = time.monotonic()
    try:
        proc = sh(cmd, cwd=wt, env=task_env(wt), timeout=timeout)
        row["exit_code"] = proc.returncode
        (run_dir / "stdout.txt").write_text(proc.stdout or "")
        (run_dir / "stderr.txt").write_text(proc.stderr or "")
        (run_dir / "answer.txt").write_text(proc.stdout or "")
    except subprocess.TimeoutExpired:
        row["timed_out"] = True
        (run_dir / "stdout.txt").write_text("")
    finally:
        row["wall_s"] = round(time.monotonic() - started, 2)

    row["load_1m_after"] = load1()
    row["files_changed"] = changed_paths(wt)
    row["protected_touched"] = protected_touched(row["files_changed"])
    row["token_usage"] = json.loads(usage.read_text()) if usage.is_file() else None

    if not row["timed_out"] and not row["protected_touched"]:
        row["verdict"] = grade(task, wt, run_dir)
        row["pass"] = bool(row["verdict"].get("pass"))
    else:
        row["notes"] = "timed out" if row["timed_out"] else \
            f"disqualified: protected paths touched {row['protected_touched']}"

    diff = git(wt, "diff", "HEAD")
    if diff:
        (run_dir / "diff.patch").write_text(diff)
    status = git(wt, "status", "--porcelain")
    (run_dir / "status.txt").write_text(status)
    # deliverable files an agent creates are usually untracked; keep a copy for review
    for line in status.splitlines():
        if not line.startswith("??"):
            continue
        rel = line[3:].strip()
        if rel.startswith(("registry/", "data/", ".venv")):
            continue  # scratch copies, not deliverables
        src = wt / rel
        if src.is_file() and src.stat().st_size < 200_000:
            dest = run_dir / "artifacts" / src.relative_to(wt)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
    if not keep:
        drop_worktree(wt)
    row["ts_end"] = now()
    return row


def load_rows() -> list[dict]:
    if not LEDGER.is_file():
        return []
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]


def regrade(run_id: str) -> dict:
    """Grade a finished attempt from what it left in runs/<run_id>/.

    A timeout is a wall-clock cut, not a verdict: the deliverable may be complete. This
    reapplies the captured diff, restores captured untracked files, grades, and appends a
    correction row. The original row is never touched.
    """
    rows = [r for r in load_rows() if r["run_id"] == run_id]
    if not rows:
        raise SystemExit(f"no such run: {run_id}")
    row = rows[0]
    run_dir = RUNS / run_id
    wt = make_worktree(f"{run_id}-regrade", row["rev"], TASKS[row["task"]])
    try:
        patch = run_dir / "diff.patch"
        if patch.is_file():
            sh(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=wt)
        art = run_dir / "artifacts"
        if art.is_dir():
            for src in art.rglob("*"):
                if src.is_file():
                    dest = wt / src.relative_to(art)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dest)
        verdict = grade(row["task"], wt, run_dir)
    finally:
        drop_worktree(wt)
    correction = {
        "run_id": f"{run_id}:regrade", "graded_from": run_id, "task": row["task"],
        "harness": row["harness"], "mode": row["mode"], "tag": row["tag"],
        "ts_start": now(), "ts_end": now(), "wall_s": row["wall_s"],
        "pass": bool(verdict.get("pass")), "verdict": verdict,
        "notes": f"regrade of original attempt ({row['notes'] or 'completed'})",
    }
    append(correction)
    return correction


def append(row: dict) -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def summary(rows: list[dict]) -> str:
    out = [f"{'task':<4}{'harness':<8}{'pass':<6}{'wall_s':<9}{'exit':<6}notes"]
    for r in rows:
        out.append(f"{r['task']:<4}{r['harness']:<8}{str(r['pass']):<6}"
                   f"{str(r['wall_s']):<9}{str(r['exit_code']):<6}{r['notes']}")
    return "\n".join(out)


def main(argv=None) -> int:
    global MAX_LOAD
    p = argparse.ArgumentParser()
    p.add_argument("--task", default="T1", choices=[*TASKS, "all"])
    p.add_argument("--harness", default="both", choices=["hermes", "omp", "both"])
    p.add_argument("--rev", default="HEAD")
    p.add_argument("--model", default=None, help="pin the same model on both sides (mode B)")
    p.add_argument("--reasoning", default=None, help="pin the same reasoning level on both sides")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--interleave", action="store_true",
                   help="paired design: alternate harnesses within each repetition, so a busy "
                        "box hits both sides equally; report per-rep differences, not raw medians")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--max-load", type=float, default=MAX_LOAD)
    p.add_argument("--force", action="store_true", help="run anyway above the idle gate; recorded")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--keep", action="store_true")
    p.add_argument("--regrade", default=None, metavar="RUN_ID",
                   help="re-grade a stored attempt from runs/<run_id>/ and append a correction row")
    p.add_argument("--tag", default="measured", help="label rows; only 'measured' counts as evidence")
    a = p.parse_args(argv)

    MAX_LOAD = a.max_load
    if a.regrade:
        c = regrade(a.regrade)
        print(json.dumps({"run_id": c["run_id"], "pass": c["pass"],
                          "score": c["verdict"].get("score"),
                          "checks": [[x["name"], x["ok"]] for x in c["verdict"].get("checks", [])]},
                         indent=2))
        return 0 if c["pass"] else 1

    tasks = list(TASKS) if a.task == "all" else [a.task]
    harnesses = ["hermes", "omp"] if a.harness == "both" else [a.harness]
    rows = []
    if a.interleave:
        # Paired design: both harnesses see the same machine conditions within a repetition.
        # On a box that never idles, per-rep differences are the only defensible signal.
        for rep in range(a.repeat):
            for task in tasks:
                for harness in harnesses:
                    row = run_one(task, harness, a.rev, a.model, a.timeout, a.force,
                                  a.dry_run, a.keep, a.tag, a.reasoning, rep)
                    if not a.dry_run:
                        append(row)
                    rows.append(row)
    else:
        for _ in range(a.repeat):
            for task in tasks:
                for harness in harnesses:
                    row = run_one(task, harness, a.rev, a.model, a.timeout, a.force,
                                  a.dry_run, a.keep, a.tag, a.reasoning)
                    if not a.dry_run:
                        append(row)
                    rows.append(row)
    print(summary(rows))
    if any(r.get("notes", "").startswith("refused") for r in rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
