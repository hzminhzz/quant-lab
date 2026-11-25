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
import sys
from pathlib import Path

from warden_ops.readonly import ReadOnlyResearchRegistry

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


def _stage3(ledger, hyp):
    trial = ledger.read_latest_trial(hyp, by_created_at=True)
    if trial is None:
        return None
    trial["audit"] = ledger.read_trial_audit_verdict(trial["run_id"])
    return trial


def _screen(ledger, hyp):
    return ledger.read_standalone_screen(hyp)


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
    ledger = ReadOnlyResearchRegistry(db)
    rows, counts = [], {"candidate": 0, "revise": 0, "blocked": 0, "stage1": 0, "unknown": 0}
    detail = []
    for hyp in STRATEGIES:
        found = ledger.read_hypothesis(hyp)
        module = found["strategy_module"] if found else ""
        src = STAGE1_SOURCE.get(hyp, hyp)
        s3 = _stage3(ledger, hyp)
        scr = _screen(ledger, hyp) or _screen(ledger, src)
        via = f" (via `{src}`)" if src != hyp else ""

        if s3 and s3["verdict"] == "CANDIDATE_FOR_QUALIFICATION":
            counts["candidate"] += 1
            disp, metric = f"CANDIDATE (`{s3['run_id']}`)", f"{s3['net_sharpe']:.3f} | {s3['deflated_sharpe_prob']:.3f}"
        elif s3:
            counts["revise"] += 1
            disp, metric = f"REVISE (`{s3['run_id']}`)", f"{s3['net_sharpe']:.3f} | {s3['deflated_sharpe_prob']:.3f}"
        elif why:
            counts["blocked"] += 1
            disp, metric = "BLOCKED_EXECUTION — no Stage-3 row", "— | —"
        elif scr and scr["outcome"] == "REJECT":
            counts["stage1"] += 1
            disp, metric = "Falsified at Stage 1", "— | —"
        else:
            counts["unknown"] += 1
            disp, metric = "UNSCREENED — no recorded Stage-1 outcome", "— | —"

        stage1 = "—"
        if scr:
            try:
                diag = json.loads(scr["diagnostics_json"])
            except Exception:
                diag = {}
            parts = []
            for label, key, fmt in (("PF", "profit_factor", "{:.3f}"),
                                    ("p", "bootstrap_p_value", "{:.2f}")):
                if isinstance(diag.get(key), (int, float)):
                    parts.append(f"{label} " + fmt.format(diag[key]))
            stage1 = (" · ".join(parts) or scr["outcome"]) + via
        rows.append(f"| `{hyp}` | `{Path(module).stem}` | {stage1} | {metric} | {disp} |")

        if s3:
            nb = "n/a" if s3["param_neighbor_stability"] in (None, 1.0) else f"{s3['param_neighbor_stability']:.3f}"
            s15 = "NULL" if s3["stress_1p5_cost_sharpe"] is None else f"{s3['stress_1p5_cost_sharpe']:.3f}"
            detail.append(f"| `{s3['run_id']}` | `{hyp}` | {s3['net_sharpe']:.3f} | {s15} | "
                          f"{s3['stress_2x_cost_sharpe']:.3f} | {s3['delay_plus1_sharpe']:.3f} | "
                          f"{s3['annualized_turnover']:.1f}x | {s3['breakeven_cost_bps']:.0f} bps | "
                          f"{s3['max_drawdown_pct']:.1f}% | {nb} | {s3['deflated_sharpe_prob']:.3f} | "
                          f"{s3['audit'] or 'not audited'} |")

    cover, cov = [], {"fx": 0, "stage1": 0, "excluded": 0, "unclassified": 0}
    for hyp in AF30_SET:
        found = ledger.read_hypothesis(hyp)
        module = Path(found["strategy_module"]).stem if found and found["strategy_module"] else "—"
        scr = _screen(ledger, hyp)
        stage1 = "—"
        if scr:
            try:
                diag = json.loads(scr["diagnostics_json"])
            except Exception:
                diag = {}
            pf, pv = diag.get("profit_factor"), diag.get("bootstrap_p_value")
            bits = []
            if isinstance(pf, (int, float)):
                bits.append(f"PF {pf:.3f}")
            if isinstance(pv, (int, float)):
                bits.append(f"p {pv:.2f}")
            stage1 = f"{scr['outcome']} · " + " · ".join(bits) if bits else scr["outcome"]
        else:
            stage1 = "— (never screened)"

        own = _stage3(ledger, hyp) or _stage3(ledger, STAGE3_ELSEWHERE.get(hyp, ""))
        elsewhere = f"`{own['run_id']}` {own['verdict'].replace('_FOR_QUALIFICATION', '')} {own['net_sharpe']:.3f}" if own else "—"

        fx_hyp = FX_OF.get(hyp)
        fx_row = _stage3(ledger, fx_hyp) if fx_hyp else None
        if fx_row:
            cov["fx"] += 1
            fx_cell = f"`{fx_row['run_id']}` · netSR {fx_row['net_sharpe']:.3f}"
        elif hyp in MECHANISM_REJECTED:
            cov["excluded"] += 1
            fx_cell = "not run — " + MECHANISM_REJECTED[hyp]
        elif scr and scr["outcome"] == "REJECT":
            cov["stage1"] += 1
            fx_cell = "not run — REJECT is terminal on the 17-instrument book"
        else:
            cov["unclassified"] += 1
            fx_cell = "not run — no recorded reason"
        cover.append(f"| `{hyp}` | `{module}` | {stage1} | {elsewhere} | {fx_cell} |")

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
