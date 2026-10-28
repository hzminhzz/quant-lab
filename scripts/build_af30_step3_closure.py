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
import sqlite3
import sys
from pathlib import Path

DB = Path("registry/registry.db")
LEDGER = Path(".workspace/af30_campaign/step3_closure.md")
BEGIN = "<!-- BEGIN GENERATED -->"
END = "<!-- END GENERATED -->"

STRATEGIES = [f"HYP-2026-{i:03d}" for i in range(94, 112)]
INDICES_COVERAGE = {"HYP-2026-096": "HYP-2026-112",
                    "HYP-2026-097": "HYP-2026-113",
                    "HYP-2026-098": "HYP-2026-114"}


def _stage3(con, hyp):
    return con.execute(
        "SELECT run_id, net_sharpe, stress_2x_cost_sharpe, delay_plus1_sharpe, "
        "annualized_turnover, breakeven_cost_bps, max_drawdown_pct, param_neighbor_stability, "
        "deflated_sharpe_prob, verdict FROM trials WHERE hypothesis_id=? "
        "ORDER BY created_at DESC LIMIT 1", (hyp,)).fetchone()


def _screen(con, hyp):
    return con.execute(
        "SELECT outcome, diagnostics_json FROM selection_attempts "
        "WHERE hypothesis_id=? AND kind='standalone_screen' LIMIT 1", (hyp,)).fetchone()


def build(db: Path = DB) -> str:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows, counts = [], {"candidate": 0, "revise": 0, "stage1": 0, "passed": 0, "unknown": 0}
        for hyp in STRATEGIES:
            module = con.execute("SELECT strategy_module FROM hypotheses WHERE hypothesis_id=?",
                                 (hyp,)).fetchone()[0]
            s3 = _stage3(con, hyp)
            if s3 is None and hyp in INDICES_COVERAGE:
                s3 = _stage3(con, INDICES_COVERAGE[hyp])
                run_from = INDICES_COVERAGE[hyp]
            else:
                run_from = hyp
            scr = _screen(con, hyp)
            via = "" if run_from == hyp else f" (via `{run_from}`)"
            if s3 and s3[9] == "CANDIDATE_FOR_QUALIFICATION":
                counts["candidate"] += 1
                disp = f"CANDIDATE{via}"
                metric = f"{s3[1]:.3f}{via} | {s3[8]:.3f}"
            elif s3:
                counts["revise"] += 1
                disp = f"REVISE (`{s3[0]}`)"
                metric = f"{s3[1]:.3f} | {s3[8]:.3f}"
            elif scr and scr[0] == "REJECT":
                counts["stage1"] += 1
                disp = "Falsified at Stage 1"
                metric = "— | —"
            elif scr and scr[0] == "PASS":
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
                    diag = json.loads(scr[1])
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
                stage1 = " · ".join(parts) or scr[0]
                if failed:
                    stage1 += f" (fails {', '.join(failed)})"
            rows.append(f"| `{hyp}` | `{Path(module).stem}` | {stage1} | {metric} | {disp} |")

        detail = []
        for hyp in STRATEGIES:
            s3 = _stage3(con, hyp)
            src = hyp
            if s3 is None and hyp in INDICES_COVERAGE:
                s3 = _stage3(con, INDICES_COVERAGE[hyp])
                src = INDICES_COVERAGE[hyp]
            if not s3:
                continue
            nb = "n/a" if s3[7] in (None, 1.0) else f"{s3[7]:.3f}"
            detail.append(f"| `{s3[0]}` | `{src}` | {s3[1]:.3f} | {s3[2]:.3f} | {s3[3]:.3f} | "
                          f"{s3[4]:.1f}x | {s3[5]:.0f} bps | {s3[6]:.1f}% | {nb} | {s3[8]:.3f} |")
    finally:
        con.close()

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
        "| Stage-3 run | Source hyp | netSR | 2x | +1 bar | turnover | breakeven | maxDD | stability | DSR |",
        "|---|---|---|---|---|---|---|---|---|---|",
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
