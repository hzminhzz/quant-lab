#!/usr/bin/env python3
"""Regenerate the FX_MAJORS Stage-3 status tables from the registry.

Same contract as `build_af30_step3_closure.py`: the ledger embeds these tables between
BEGIN/END markers so the prose cannot drift from `registry/registry.db`. `--check` exits 1
when the file no longer matches what the registry says.

Two differences from the AF30 generator, both forced by this family:

* The Stage-1 evidence is not on this contract. S01-S05 passed Stage 1 on
  MULTI_ASSET_1D_V1 (HYP-2026-095..099) and are re-homed here as HYP-2026-115..119, so the
  screen row is read from the source hypothesis and labelled `via`.
* The blocker column is *derived*, by calling the same `require_execution_columns` the
  runner calls against the contract's own panel. It is not hard-coded, so when the contract
  becomes executable the ledger says so on the next regeneration instead of lying.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB = Path("registry/registry.db")
LEDGER = Path(".workspace/fx_stage3/stage3_closure.md")
BEGIN = "<!-- BEGIN GENERATED -->"
END = "<!-- END GENERATED -->"

CONTRACT = "FX_MAJORS_1D_V2"
STRATEGIES = [f"HYP-2026-{i}" for i in range(120, 125)]
STAGE1_SOURCE = {
    "HYP-2026-120": "HYP-2026-095",
    "HYP-2026-121": "HYP-2026-096",
    "HYP-2026-122": "HYP-2026-097",
    "HYP-2026-123": "HYP-2026-098",
    "HYP-2026-124": "HYP-2026-099",
}

# The AF30 Step-3 set, for coverage accounting only: 094 (breakout 80) + 095..111.
AF30_SET = [f"HYP-2026-{i:03d}" for i in range(94, 112)]
# S02/S03/S04 reached Stage 3 by re-homing to INDICES_1D_V1 (HYP-2026-112/113/114).
STAGE3_ELSEWHERE = {"HYP-2026-096": "HYP-2026-112",
                    "HYP-2026-097": "HYP-2026-113",
                    "HYP-2026-098": "HYP-2026-114"}
FX_OF = {v: k for k, v in STAGE1_SOURCE.items()}
# 094 has no Stage-1 screen of its own, but the same 80-bar breakout was Stage-1 REJECTed as
# HYP-2026-109 and failed Stage 3 twice (093 REVISE, 094 REVISE). Excluded on the mechanism, not the id.
MECHANISM_REJECTED = {"HYP-2026-094": "breakout: Stage-1 REJECT as `HYP-2026-109`, Stage-3 REVISE as `HYP-2026-093`"}


def _stage3(con, hyp):
    return con.execute(
        "SELECT run_id, net_sharpe, stress_2x_cost_sharpe, delay_plus1_sharpe, "
        "annualized_turnover, breakeven_cost_bps, max_drawdown_pct, param_neighbor_stability, "
        "deflated_sharpe_prob, verdict, stress_1p5_cost_sharpe, "
        "(SELECT verdict FROM audit_evidence a WHERE a.run_id = trials.run_id) AS audit FROM trials "
        "WHERE hypothesis_id=? ORDER BY created_at DESC LIMIT 1", (hyp,)).fetchone()


def _screen(con, hyp):
    return con.execute(
        "SELECT outcome, diagnostics_json FROM selection_attempts "
        "WHERE hypothesis_id=? AND kind='standalone_screen' LIMIT 1", (hyp,)).fetchone()


def blocker(repo_root: Path = Path(".")) -> str:
    """Why this contract cannot produce a trial, or '' when it can. Derived, never assumed."""
    try:
        from warden.contracts import load_contract
        from warden.runner import require_execution_columns

        contract = load_contract(repo_root / "contracts" / f"{CONTRACT.lower()}.yaml")
        panel = repo_root / contract.exploratory_data_path
        if not panel.is_file():
            return "missing panel"
        import polars as pl

        require_execution_columns(pl.read_parquet(panel, n_rows=1), contract)
        return ""
    except Exception as exc:  # any refusal is the blocker, verbatim
        return f"{type(exc).__name__}: {exc}"


def build(db: Path = DB, why: str | None = None) -> str:
    why = blocker() if why is None else why
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows, counts = [], {"candidate": 0, "revise": 0, "blocked": 0, "stage1": 0, "unknown": 0}
        detail = []
        for hyp in STRATEGIES:
            found = con.execute("SELECT strategy_module FROM hypotheses WHERE hypothesis_id=?",
                                (hyp,)).fetchone()
            module = found[0] if found else ""
            src = STAGE1_SOURCE.get(hyp, hyp)
            s3 = _stage3(con, hyp)
            scr = _screen(con, hyp) or _screen(con, src)
            via = f" (via `{src}`)" if src != hyp else ""

            if s3 and s3[9] == "CANDIDATE_FOR_QUALIFICATION":
                counts["candidate"] += 1
                disp, metric = f"CANDIDATE (`{s3[0]}`)", f"{s3[1]:.3f} | {s3[8]:.3f}"
            elif s3:
                counts["revise"] += 1
                disp, metric = f"REVISE (`{s3[0]}`)", f"{s3[1]:.3f} | {s3[8]:.3f}"
            elif why:
                counts["blocked"] += 1
                disp, metric = "BLOCKED_EXECUTION — no Stage-3 row", "— | —"
            elif scr and scr[0] == "REJECT":
                counts["stage1"] += 1
                disp, metric = "Falsified at Stage 1", "— | —"
            else:
                counts["unknown"] += 1
                disp, metric = "UNSCREENED — no recorded Stage-1 outcome", "— | —"

            stage1 = "—"
            if scr:
                try:
                    diag = json.loads(scr[1])
                except Exception:
                    diag = {}
                parts = []
                for label, key, fmt in (("PF", "profit_factor", "{:.3f}"),
                                        ("p", "bootstrap_p_value", "{:.2f}")):
                    if isinstance(diag.get(key), (int, float)):
                        parts.append(f"{label} " + fmt.format(diag[key]))
                stage1 = (" · ".join(parts) or scr[0]) + via
            rows.append(f"| `{hyp}` | `{Path(module).stem}` | {stage1} | {metric} | {disp} |")

            if s3:
                nb = "n/a" if s3[7] in (None, 1.0) else f"{s3[7]:.3f}"
                s15 = "NULL" if s3[10] is None else f"{s3[10]:.3f}"
                detail.append(f"| `{s3[0]}` | `{hyp}` | {s3[1]:.3f} | {s15} | {s3[2]:.3f} | {s3[3]:.3f} | "
                              f"{s3[4]:.1f}x | {s3[5]:.0f} bps | {s3[6]:.1f}% | {nb} | {s3[8]:.3f} | "
                              f"{s3[11] or 'not audited'} |")
    finally:
        con.close()

    cover, cov = [], {"fx": 0, "stage1": 0, "excluded": 0, "unclassified": 0}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for hyp in AF30_SET:
            found = con.execute("SELECT strategy_module FROM hypotheses WHERE hypothesis_id=?",
                                (hyp,)).fetchone()
            module = Path(found[0]).stem if found else "—"
            scr = _screen(con, hyp)
            stage1 = "—"
            if scr:
                try:
                    diag = json.loads(scr[1])
                except Exception:
                    diag = {}
                pf, pv = diag.get("profit_factor"), diag.get("bootstrap_p_value")
                bits = []
                if isinstance(pf, (int, float)):
                    bits.append(f"PF {pf:.3f}")
                if isinstance(pv, (int, float)):
                    bits.append(f"p {pv:.2f}")
                stage1 = f"{scr[0]} · " + " · ".join(bits) if bits else scr[0]
            else:
                stage1 = "— (never screened)"

            own = _stage3(con, hyp) or _stage3(con, STAGE3_ELSEWHERE.get(hyp, ""))
            elsewhere = f"`{own[0]}` {own[9].replace('_FOR_QUALIFICATION', '')} {own[1]:.3f}" if own else "—"

            fx_hyp = FX_OF.get(hyp)
            fx_row = _stage3(con, fx_hyp) if fx_hyp else None
            if fx_row:
                cov["fx"] += 1
                fx_cell = f"`{fx_row[0]}` · netSR {fx_row[1]:.3f}"
            elif hyp in MECHANISM_REJECTED:
                cov["excluded"] += 1
                fx_cell = "not run — " + MECHANISM_REJECTED[hyp]
            elif scr and scr[0] == "REJECT":
                cov["stage1"] += 1
                fx_cell = "not run — REJECT is terminal on the 17-instrument book"
            else:
                cov["unclassified"] += 1
                fx_cell = "not run — no recorded reason"
            cover.append(f"| `{hyp}` | `{module}` | {stage1} | {elsewhere} | {fx_cell} |")
    finally:
        con.close()

    head = [
        BEGIN,
        f"Blocker for `{CONTRACT}`: {why or 'none — contract is executable'}",
        "",
        "| Strategy | Module | Stage-1 (PF 1.15 / p 0.05) | netSR | DSR | Disposition |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        f"Counts: {counts['candidate']} CANDIDATE, {counts['revise']} REVISE with a Stage-3 row, "
        f"{counts['blocked']} blocked before any trial, {counts['stage1']} falsified at Stage 1, "
        f"{counts['unknown']} unscreened — {len(STRATEGIES)} total.",
        "",
        "| Stage-3 run | Source hyp | netSR | 1.5x | 2x | +1 bar | turnover | breakeven | maxDD | stability | DSR | audit |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
        *detail,
        "",
        "### AF30 coverage: all 18, and why only five were re-tested on FX",
        "",
        "| Hypothesis | Module | Stage-1 on `MULTI_ASSET_1D_V1` | Stage-3 (anywhere) | FX Stage-3 |",
        "|---|---|---|---|---|",
        *cover,
        "",
        f"Coverage: {cov['fx']} measured on FX (three of them also reached Stage 3 on "
        f"`INDICES_1D_V1` via `HYP-2026-112/113/114`), {cov['stage1']} Stage-1 REJECT carried as "
        f"terminal, {cov['excluded']} excluded on the mechanism, {cov['unclassified']} unclassified — "
        f"{len(AF30_SET)} total.",
        END,
    ]
    return "\n".join(head)


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
