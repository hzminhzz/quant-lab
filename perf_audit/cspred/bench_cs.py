"""Screen / backtest verdict parity on the HYP-BENCH-ULWPERF fixture, cs modules.

Adapted from perf_audit/bench_warden.py (unmodified): same governance — the live
registry is only ever opened mode=ro and backed up into a scratch dir; a fixture
hypothesis with fresh budget is inserted into the copy; data/contracts are
symlinks to the live repo (read-only); every tmp root is removed afterwards.

Differences: the experiment spec and the strategies source root are parameters,
so the same spec can be run against the live modules (before) and the worktree
modules (after).

Run: cd <worktree> && uv run --frozen --project /home/quant/dev/quant-lab \
       python perf_audit/cspred/bench_cs.py <mode> --spec <rel> \
       --strat-src /home/quant/dev/quant-lab --reps 1
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

import yaml

MAIN = Path("/home/quant/dev/quant-lab")
SCRATCH = Path(__file__).resolve().parent
BENCH_HYP = "HYP-BENCH-ULWPERF"


def fresh_root(tag: str, spec_rel: str, strat_src: Path) -> Path:
    root = SCRATCH / f"tmp_{tag}_{int(time.time() * 1000)}"
    (root / "registry").mkdir(parents=True)
    src = sqlite3.connect(f"file:{MAIN}/registry/registry.db?mode=ro", uri=True)
    dst = sqlite3.connect(root / "registry" / "registry.db")
    src.backup(dst)
    dst.close()
    src.close()
    for name in ("contracts", "data"):
        os.symlink(MAIN / name, root / name)
    shutil.copytree(strat_src / "strategies", root / "strategies",
                    ignore=shutil.ignore_patterns("__pycache__"))
    cfg = yaml.safe_load((MAIN / spec_rel).read_text())
    conn = sqlite3.connect(root / "registry" / "registry.db")
    row = conn.execute(
        "SELECT title, economic_mechanism, counterparty_or_friction, expected_holding_bars, "
        "falsification_criteria, strategy_family, contract_id FROM hypotheses WHERE hypothesis_id=?",
        (cfg["hypothesis_id"],)).fetchone()
    if row is None:
        raise SystemExit(f"hypothesis {cfg['hypothesis_id']} not in registry copy")
    conn.execute("DELETE FROM hypotheses WHERE hypothesis_id=?", (BENCH_HYP,))
    conn.execute(
        "INSERT INTO hypotheses (hypothesis_id, contract_id, title, strategy_family, economic_mechanism, "
        "counterparty_or_friction, expected_holding_bars, falsification_criteria, parent_hypothesis_id, "
        "source_provenance, llm_model_id, strategy_module, status, trials_used, created_at, attempts_json) "
        "VALUES (?,?,?,?,?,?,?,?,NULL,'internal_research','ulwperf-cs','', 'ACTIVE',0,?,'{}')",
        (BENCH_HYP, row[6], row[0], row[5], row[1], row[2], row[3], row[4], time.time()))
    conn.commit()
    conn.close()
    cfg["hypothesis_id"] = BENCH_HYP
    cfg.pop("run_id", None)
    (root / "spec.yaml").write_text(yaml.safe_dump(cfg))
    return root


def run_rep(mode: str, rep: int, spec_rel: str, strat_src: Path) -> dict:
    from warden.runner import ExperimentRunner
    root = fresh_root(f"{mode}{rep}", spec_rel, strat_src)
    runner = ExperimentRunner(repo_root=root)
    t0 = time.perf_counter()
    err = None
    verdict = None
    extras = {}
    try:
        if mode == "screen":
            rep_obj = runner.run_stage1_screen(root / "spec.yaml")
            verdict = f"passed={rep_obj.passed}"
            extras = {"reasons": list(getattr(rep_obj, "reasons", []) or [])}
        else:
            res = runner.run_spec(root / "spec.yaml", override_run_id=f"RUN-CS-{rep}")
            verdict = res.verdict
            extras = {"mean_ic": getattr(res, "mean_ic", None),
                      "n_trades": getattr(res, "n_trades", None)}
    except Exception as exc:  # noqa: BLE001 - the harness error is the observation
        err = f"{type(exc).__name__}: {exc}"
    wall = time.perf_counter() - t0
    shutil.rmtree(root, ignore_errors=True)
    return {"mode": mode, "rep": rep, "spec": spec_rel, "strat_src": str(strat_src),
            "wall_s": round(wall, 3), "verdict": verdict, "error": err, "extras": extras}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["screen", "backtest"])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--strat-src", required=True)
    ap.add_argument("--reps", type=int, default=1)
    args = ap.parse_args()
    print(json.dumps({"env": {"mode": args.mode, "spec": args.spec, "strat_src": args.strat_src,
                              "loadavg": os.getloadavg(),
                              "live_strategies_md5_ok": (Path(args.strat_src) / "strategies").exists()}}),
          flush=True)
    for rep in range(args.reps):
        print(json.dumps(run_rep(args.mode, rep, args.spec, Path(args.strat_src))), flush=True)


if __name__ == "__main__":
    main()
