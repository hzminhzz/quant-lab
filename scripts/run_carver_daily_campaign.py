#!/usr/bin/env python3
"""Run the 17 carver_daily Stage-1 screens and write the campaign artifacts.

Each spec is verified for prefix invariance and then screened, unless a
standalone_screen row already exists for its hypothesis (recorded screens are never
re-run: a screen consumes a selection attempt and permanently enlarges the DSR family).

A rejecting screen exits 1 by design; that verdict is recorded, never retried or re-tuned.
No `warden backtest` is executed and no holdout data is read.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

SPEC_DIR = Path("experiments/active/carver_daily")
OUT_DIR = Path(".workspace/af30_campaign")
DB = Path("registry/registry.db")
REQUIRED = ("hypothesis_id", "strategy", "module", "passed", "verdict", "exit_code", "metrics", "notes", "raw_payload")


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def recorded(hypothesis_id: str) -> tuple[str, dict] | None:
    with sqlite3.connect(DB) as conn:
        row = conn.execute(
            "SELECT outcome, diagnostics_json FROM selection_attempts "
            "WHERE hypothesis_id=? AND kind='standalone_screen'",
            (hypothesis_id,),
        ).fetchone()
    if not row:
        return None
    return row[0], json.loads(row[1] or "{}")


def strategy_label(module: Path) -> str:
    return module.stem.split("_", 1)[0]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    specs = sorted(SPEC_DIR.glob("*_screen.yaml"))
    if len(specs) != 17:
        print(f"expected 17 screen specs, found {len(specs)}", file=sys.stderr)
        return 1

    records = []
    for spec in specs:
        cfg = yaml.safe_load(spec.read_text(encoding="utf-8"))
        hyp_id, module = cfg["hypothesis_id"], Path(cfg["strategy_module"])
        label, name = strategy_label(module), cfg.get("strategy_name", spec.stem)
        print(f"== {hyp_id} {label} ({spec.name})")
        sys.stdout.flush()

        ver = run(["uv", "run", "warden", "verify", str(spec), "--json"])
        if ver.returncode != 0 or '"status": "PASS"' not in ver.stdout:
            print(f"VERIFY FAILED for {spec.name}:\n{ver.stdout}\n{ver.stderr}", file=sys.stderr)
            return 1
        print("   verify PASS")

        prior = recorded(hyp_id)
        if prior:
            outcome, diagnostics = prior
            passed = outcome == "PASS"
            metrics = {k: v for k, v in diagnostics.items() if k != "dataset"}
            payload = {"passed": passed, "metrics": metrics, "notes": "from existing screen record"}
            exit_code = 0 if passed else 1
            print(f"   screen already recorded ({outcome}), not re-run")
        else:
            scr = run(["uv", "run", "warden", "screen", str(spec), "--json"])
            try:
                payload = json.loads(scr.stdout.strip())
            except json.JSONDecodeError as exc:
                print(f"screen output was not JSON for {spec.name}: {exc}\n{scr.stdout}\n{scr.stderr}", file=sys.stderr)
                return 1
            metrics = payload.get("metrics", {})
            passed, exit_code = bool(payload.get("passed")), scr.returncode
            print(f"   screen {payload.get('passed')} (exit={exit_code}) notes={payload.get('notes', '')}")

        records.append({
            "hypothesis_id": hyp_id,
            "strategy": label,
            "module": str(module),
            "strategy_name": name,
            "passed": passed,
            "verdict": "PASS" if passed else "REJECT",
            "exit_code": exit_code,
            "metrics": metrics,
            "notes": payload.get("notes", ""),
            "raw_payload": payload,
        })
        sys.stdout.flush()

    screens_path = OUT_DIR / "carver_daily_screens.json"
    screens_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    report_path = OUT_DIR / "carver_daily_screens_report.md"
    report_path.write_text(render_report(records), encoding="utf-8")
    print(f"\nwrote {screens_path} and {report_path}")

    problems = validate(records)
    if problems:
        for problem in problems:
            print(f"SELF-CHECK FAILED: {problem}", file=sys.stderr)
        return 1
    print("self-check OK: 17 records, each with a raw payload")
    return 0


def validate(records: list[dict]) -> list[str]:
    problems = []
    if len(records) != 17:
        problems.append(f"{len(records)} records, expected 17")
    for rec in records:
        missing = [key for key in REQUIRED if key not in rec]
        if missing:
            problems.append(f"{rec.get('hypothesis_id')} missing {missing}")
        if not rec.get("raw_payload"):
            problems.append(f"{rec.get('hypothesis_id')} has an empty raw payload")
    return problems


def render_report(records: list[dict]) -> str:
    lines = [
        "# Carver AF30 Daily Suite - Stage-1 Screen Report",
        "",
        "- **Contract**: `MULTI_ASSET_1D_V1` (17 instruments, 312 bars/year, 2015-2025 exploratory)",
        "- **Universe**: 6 equity indices, 8 FX pairs, 2 metals, 1 energy",
        f"- **Strategies**: {len(records)} (`strategies/candidates/carver_daily/`)",
        "- **Method**: `warden verify` (prefix invariance) then `warden screen` (Stage-1 signal gate)",
        "- **Disclosure**: no `warden backtest` ran in this work unit and no holdout data was",
        "  read. Every screen writes one `selection_attempt` row, which enlarges the",
        "  multiple-testing family for later DSR evaluation on this contract.",
        "",
        "| Hypothesis | Strategy | Module | Profit Factor | Cond. Edge (bps) | Trade Edge (bps) | Time in Mkt | Avg Hold (bars) | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for rec in records:
        m = rec["metrics"]
        pf = f"{m['profit_factor']:.2f}" if m.get("profit_factor") is not None else "n/a"
        ce = f"{m['conditional_edge'] * 1e4:.2f}" if m.get("conditional_edge") is not None else "n/a"
        te = f"{m['trade_edge'] * 1e4:.2f}" if m.get("trade_edge") is not None else "n/a"
        tim = f"{m['time_in_market'] * 100:.1f}%" if m.get("time_in_market") is not None else "n/a"
        hold = f"{m['avg_holding_bars']:.1f}" if m.get("avg_holding_bars") is not None else "n/a"
        lines.append(
            f"| `{rec['hypothesis_id']}` | {rec['strategy']} | `{Path(rec['module']).name}` | {pf} | {ce} | {te} | {tim} | {hold} | **{rec['verdict']}** |"
        )
    passed = sum(1 for rec in records if rec["passed"])
    lines += [
        "",
        (f"**Result**: {passed} of {len(records)} cleared the Stage-1 gate "
         "(PF >= 1.15, trade edge > 1.5x one-way cost, time in market >= 5%, bootstrap p < 0.05)."),
        "",
        "## Notes per strategy",
        "",
    ]
    for rec in records:
        lines.append(f"- `{rec['hypothesis_id']}` {rec['strategy']}: {rec['notes'] or 'no notes'}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
