#!/usr/bin/env python3
"""Regenerate the AF30 Step-3 status tables from the registry.

The ledger embeds these tables between BEGIN/END markers so the prose cannot drift from
`registry/registry.db`. `--check` exits 1 when the file no longer matches what the
registry says; the test suite runs it so a stale table fails CI.

S02/S03/S04 (HYP-2026-096/097/098) have no trial of their own: they passed Stage 1 on
MULTI_ASSET_1D_V1 and were re-homed to INDICES_1D_V1 as HYP-2026-112/113/114.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from warden_ops.readonly import ReadOnlyResearchRegistry

DB = Path("registry/registry.db")
LEDGER = Path(".workspace/af30_campaign/step3_closure.md")
BEGIN = "<!-- BEGIN GENERATED -->"
END = "<!-- END GENERATED -->"

STRATEGIES = [f"HYP-2026-{i:03d}" for i in range(94, 112)]
INDICES_COVERAGE = {"HYP-2026-096": "HYP-2026-112",
                    "HYP-2026-097": "HYP-2026-113",
                    "HYP-2026-098": "HYP-2026-114"}


def _stage3(ledger, hyp):
    """The hypothesis's own newest Stage-3 row plus its recorded audit verdict."""
    trial = ledger.read_latest_trial(hyp, by_created_at=True)
    if trial is None:
        return None
    trial["audit"] = ledger.read_trial_audit_verdict(trial["run_id"])
    return trial


def _screen(ledger, hyp):
    return ledger.read_standalone_screen(hyp)


def _module(ledger, hyp):
    identity = ledger.read_hypothesis(hyp)
    if identity is None:
        raise LookupError(f"{hyp} has no registered hypothesis row in this registry")
    return identity["strategy_module"]


def build(db: Path = DB) -> str:
    ledger = ReadOnlyResearchRegistry(db)
    rows, counts = [], {"candidate": 0, "revise": 0, "stage1": 0, "passed": 0, "unknown": 0}
    for hyp in STRATEGIES:
        module = _module(ledger, hyp)
        s3 = _stage3(ledger, hyp)
        if s3 is None and hyp in INDICES_COVERAGE:
            s3 = _stage3(ledger, INDICES_COVERAGE[hyp])
            run_from = INDICES_COVERAGE[hyp]
        else:
            run_from = hyp
        scr = _screen(ledger, hyp)
        via = "" if run_from == hyp else f" (via `{run_from}`)"
        if s3 and s3["verdict"] == "CANDIDATE_FOR_QUALIFICATION":
            counts["candidate"] += 1
            disp = f"CANDIDATE{via}"
            metric = f"{s3['net_sharpe']:.3f}{via} | {s3['deflated_sharpe_prob']:.3f}"
        elif s3:
            counts["revise"] += 1
            disp = f"REVISE (`{s3['run_id']}`)"
            metric = f"{s3['net_sharpe']:.3f} | {s3['deflated_sharpe_prob']:.3f}"
        elif scr and scr["outcome"] == "REJECT":
            counts["stage1"] += 1
            disp = "Falsified at Stage 1"
            metric = "— | —"
        elif scr and scr["outcome"] == "PASS":
            counts["passed"] += 1
            disp = "Stage-1 PASS — no Stage-3 row"
            metric = "— | —"
        else:
            counts["unknown"] += 1
            disp = "UNSCREENED — no recorded Stage-1 outcome"
            metric = "— | —"
        stage1 = "—"
        if scr:
            import json
            try:
                diag = json.loads(scr["diagnostics_json"])
            except Exception:
                diag = {}
            pf = diag.get("profit_factor")
            pval = diag.get("bootstrap_p_value")
            tim = diag.get("time_in_market")
            failed = []
            if isinstance(pf, (int, float)) and pf < 1.15:
                failed.append("PF")
            if isinstance(pval, (int, float)) and pval >= 0.05:
                failed.append("p")
            if isinstance(tim, (int, float)) and tim < 0.05:
                failed.append("TiM")
            parts = []
            if isinstance(pf, (int, float)):
                parts.append(f"PF {pf:.3f}")
            if isinstance(pval, (int, float)):
                parts.append(f"p {pval:.2f}")
            stage1 = " · ".join(parts) or scr["outcome"]
            if failed:
                stage1 += f" (fails {', '.join(failed)})"
        rows.append(f"| `{hyp}` | `{Path(module).stem}` | {stage1} | {metric} | {disp} |")

    detail = []
    for hyp in STRATEGIES:
        s3 = _stage3(ledger, hyp)
        src = hyp
        if s3 is None and hyp in INDICES_COVERAGE:
            s3 = _stage3(ledger, INDICES_COVERAGE[hyp])
            src = INDICES_COVERAGE[hyp]
        if not s3:
            continue
        nb = "n/a" if s3["param_neighbor_stability"] in (None, 1.0) else f"{s3['param_neighbor_stability']:.3f}"
        s15 = "NULL" if s3["stress_1p5_cost_sharpe"] is None else f"{s3['stress_1p5_cost_sharpe']:.3f}"
        audit = s3["audit"] or "not audited"
        detail.append(f"| `{s3['run_id']}` | `{src}` | {s3['net_sharpe']:.3f} | {s15} | "
                      f"{s3['stress_2x_cost_sharpe']:.3f} | {s3['delay_plus1_sharpe']:.3f} | "
                      f"{s3['annualized_turnover']:.1f}x | {s3['breakeven_cost_bps']:.0f} bps | "
                      f"{s3['max_drawdown_pct']:.1f}% | {nb} | {s3['deflated_sharpe_prob']:.3f} | {audit} |")

    return "\n".join([
        BEGIN,
        "| Strategy | Module | Stage-1 (vs PF 1.15 / p 0.05) | netSR | DSR | Disposition |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        f"Counts: {counts['candidate']} CANDIDATE, {counts['revise']} REVISE with a Stage-3 row, "
        f"{counts['stage1']} falsified at Stage 1, {counts['passed']} Stage-1 PASS without a "
        f"Stage-3 row, {counts['unknown']} unscreened — {len(STRATEGIES)} total.",
        "",
        "| Stage-3 run | Source hyp | netSR | 1.5x | 2x | +1 bar | turnover | breakeven | maxDD | stability | DSR | audit |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
        *detail,
        END,
    ])


def render(text: str, block: str) -> str:
    before, rest = text.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    return before + block + after


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    p.add_argument("--db", type=Path, default=DB)
    p.add_argument("--ledger", type=Path, default=LEDGER)
    a = p.parse_args(argv)
    block = build(a.db)
    current = a.ledger.read_text()
    if a.check:
        if render(current, block) != current:
            print("STALE: ledger tables do not match the registry", file=sys.stderr)
            return 1
        print("ledger tables match the registry")
        return 0
    a.ledger.write_text(render(current, block))
    print(f"wrote {a.ledger}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
