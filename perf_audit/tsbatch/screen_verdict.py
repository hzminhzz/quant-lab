"""Gate 4: screen/backtest verdict parity for a vectorised ts module on a fixture registry snapshot.

Copies the registry with a fresh-budget fixture hypothesis (HYP-BENCH-ULWPERF), symlinks the
read-only data/contracts trees, copies this worktree's strategies/ into a scratch root, and runs
warden's stage-1 screen twice: once with the vectorised module, once with the HEAD copy. The live
registry is only ever opened read-only via sqlite backup; no qualify, no manifest, no holdout.

Usage: uv run --frozen --project /home/quant/dev/quant-lab python perf_audit/tsbatch/screen_verdict.py [module ...] [--mode screen|backtest]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path

import yaml

WT = Path(__file__).resolve().parents[2]
LIVE = Path("/home/quant/dev/quant-lab")
SCRATCH = WT / "perf_audit" / "tsbatch"
OLD = SCRATCH / "old"
BENCH_HYP = "HYP-BENCH-ULWPERF"

MODULES = {
    "ts_s06_slow_trend_long_short_1h": "experiments/active/af30/06_slow_trend_long_short_screen.yaml",
    "ts_s19_xs_momentum_1h": "experiments/active/af30/19_xs_momentum_screen.yaml",
    "rsi_ema_vwap_pullback_1h": "experiments/active/hyp_2026_003.yaml",
    "ts_london_orb_1h": "experiments/active/failed/hyp_2026_008_t1.yaml",
    "carver_breakout_daily": "experiments/active/hyp_2026_094_t1.yaml",
}


def fresh_root(tag: str, module: str, source: Path) -> Path:
    """Scratch repo root: snapshot registry + fixture hypothesis + copied strategies."""
    root = SCRATCH / f"tmp_{tag}_{int(time.time() * 1000)}"
    (root / "registry").mkdir(parents=True)
    src = sqlite3.connect(f"file:{LIVE}/registry/registry.db?mode=ro", uri=True)
    dst = sqlite3.connect(root / "registry" / "registry.db")
    src.backup(dst)
    dst.close()
    src.close()
    for name in ("contracts", "data"):
        os.symlink(LIVE / name, root / name)
    shutil.copytree(WT / "strategies", root / "strategies", ignore=shutil.ignore_patterns("__pycache__"))

    cfg = yaml.safe_load((WT / MODULES[module]).read_text(encoding="utf-8"))
    conn = sqlite3.connect(root / "registry" / "registry.db")
    row = conn.execute(
        "SELECT title, economic_mechanism, counterparty_or_friction, expected_holding_bars, "
        "falsification_criteria, strategy_family FROM hypotheses WHERE hypothesis_id=?",
        (cfg["hypothesis_id"],),
    ).fetchone()
    if row is None:
        raise SystemExit(f"hypothesis {cfg['hypothesis_id']} missing from registry snapshot")
    conn.execute("DELETE FROM hypotheses WHERE hypothesis_id=?", (BENCH_HYP,))
    conn.execute(
        "INSERT INTO hypotheses (hypothesis_id, contract_id, title, strategy_family, economic_mechanism, "
        "counterparty_or_friction, expected_holding_bars, falsification_criteria, parent_hypothesis_id, "
        "source_provenance, llm_model_id, strategy_module, status, trials_used, created_at, attempts_json) "
        "VALUES (?,?,?,?,?,?,?,?,NULL,'internal_research','vec-batch','', 'ACTIVE',0,?,'{}')",
        (BENCH_HYP, cfg["contract_id"], row[0], row[5], row[1], row[2], row[3], row[4], time.time()),
    )
    conn.commit()
    conn.close()

    # install the module version under test
    target = root / "strategies" / "candidates" / f"{module}.py"
    target.write_bytes(source.read_bytes())
    cfg["hypothesis_id"] = BENCH_HYP
    cfg.pop("run_id", None)
    cfg["strategy_module"] = f"strategies/candidates/{module}.py"
    (root / "spec.yaml").write_text(yaml.safe_dump(cfg))
    return root


def run(mode: str, tag: str, module: str, source: Path) -> dict:
    from warden.runner import ExperimentRunner

    root = fresh_root(tag, module, source)
    try:
        runner = ExperimentRunner(repo_root=root)
        t0 = time.perf_counter()
        if mode == "screen":
            rep = runner.run_stage1_screen(root / "spec.yaml")
            out = {"passed": rep.passed, "metrics": {k: (float(v) if isinstance(v, (int, float)) else v)
                                                     for k, v in rep.metrics.items()}}
        else:
            res = runner.run_spec(root / "spec.yaml", override_run_id=f"RUN-BENCH-ULWPERF-{mode}-PARITY")
            payload = asdict(res)
            payload.pop("strategy_code_sha256", None)  # expected to differ: the point is returns/verdict parity
            out = {"verdict": res.verdict, "fields": payload}
        out["wall_s"] = round(time.perf_counter() - t0, 3)
        return out
    except Exception as exc:  # noqa: BLE001 - verdict parity includes failure parity
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("modules", nargs="*")
    ap.add_argument("--mode", choices=["screen", "backtest"], default="screen")
    a = ap.parse_args()
    mods = a.modules or list(MODULES)
    results = []
    for m in mods:
        new = run(a.mode, f"{a.mode}new", m, WT / "strategies" / "candidates" / f"{m}.py")
        old = run(a.mode, f"{a.mode}old", m, OLD / f"{m}.py")
        same = {k: v for k, v in new.items() if k != "wall_s"} == {k: v for k, v in old.items() if k != "wall_s"}
        rec = {"module": m, "mode": a.mode, "new": new, "old": old, "verdict_identical": same}
        results.append(rec)
        print(json.dumps(rec), flush=True)
    (SCRATCH / f"verdict_{a.mode}.json").write_text(json.dumps(results, indent=1))
    print("ALL_IDENTICAL", all(r["verdict_identical"] for r in results))


if __name__ == "__main__":
    sys.path.insert(0, str(WT / "src"))
    main()
