#!/usr/bin/env python3
"""Generate one decision record per AF30 daily strategy from the registry.

The records are derived, never hand-written: every number is read from `registry.db`
(or the audit report the harness wrote next to it), so a reader can re-run this script
and get the same files. Nothing here computes a new metric, and nothing reads holdout.

Fail-closed: every hypothesis is resolved and rendered in memory before a single file is
written, so a hypothesis with no evidence aborts the run instead of leaving a partial set
of records behind.

Usage:
    python scripts/build_carver_daily_decisions.py
    python scripts/build_carver_daily_decisions.py --out /tmp/decisions   # dry inspection
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from scipy import stats

DEFAULT_DB = Path("registry/registry.db")
DEFAULT_OUT = Path(".workspace/af30_campaign/decision")
DEFAULT_AUDIT = Path(".workspace/hypotheses")
DEFAULT_REVISIONS = Path(".workspace/af30_campaign/revisions")
CONTRACT_ID = "MULTI_ASSET_1D_V1"
HYPOTHESES = tuple(f"HYP-2026-{n:03d}" for n in range(95, 112))

# Contract terms, from contracts/multi_asset_1d_v1.yaml.
ONE_WAY_COST = (0.5 + 0.5) / 1e4
BARS_PER_YEAR = 312
VAL_BARS = 806
MIN_PROFIT_FACTOR = 1.15
MIN_TIME_IN_MARKET = 0.05
EDGE_COST_MULTIPLIER = 1.5
MAX_BOOTSTRAP_P = 0.05

# Runner gates (src/warden/runner.py), restated here because the records quote them.
NET_SR_HURDLE = 1.0
STRESS_SR_HURDLE = 0.50
DELAY_SR_HURDLE = 0.0
DSR_HURDLE = 0.90


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def audit_report(run_id: str, audit_dir: Path) -> dict[str, Any] | None:
    path = audit_dir / f"AUDIT_{run_id}.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    verdict = re.search(r"^Verdict: (.+)$", text, re.MULTILINE)
    checks = re.findall(r"^- \[(PASS|FAIL)\] (.+)$", text, re.MULTILINE)
    return {"path": path, "verdict": verdict.group(1).strip() if verdict else "UNKNOWN",
            "checks": [(name, status == "PASS") for status, name in checks]}


def stage1_failures(metrics: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    pf = float(metrics.get("profit_factor", 0.0))
    tim = float(metrics.get("time_in_market", 0.0))
    edge = float(metrics.get("trade_edge", 0.0))
    p = float(metrics.get("bootstrap_p_value", 1.0))
    if pf < MIN_PROFIT_FACTOR:
        fails.append(f"profit factor {pf:.2f} < {MIN_PROFIT_FACTOR}")
    if tim < MIN_TIME_IN_MARKET:
        fails.append(f"time in market {tim:.1%} < {MIN_TIME_IN_MARKET:.0%}")
    if edge <= EDGE_COST_MULTIPLIER * ONE_WAY_COST:
        fails.append(f"trade edge {edge * 1e4:.2f} bps <= {EDGE_COST_MULTIPLIER} x one-way cost "
                     f"({EDGE_COST_MULTIPLIER * ONE_WAY_COST * 1e4:.2f} bps)")
    if p >= MAX_BOOTSTRAP_P:
        fails.append(f"bootstrap p {p:.2f} >= {MAX_BOOTSTRAP_P}")
    return fails


def stage3_failures(trial: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    if float(trial["net_sharpe"]) <= NET_SR_HURDLE:
        fails.append(f"net Sharpe {trial['net_sharpe']:.3f} <= {NET_SR_HURDLE}")
    if float(trial["stress_2x_cost_sharpe"]) <= STRESS_SR_HURDLE:
        fails.append(f"2x-cost Sharpe {trial['stress_2x_cost_sharpe']:.3f} <= {STRESS_SR_HURDLE}")
    if float(trial["delay_plus1_sharpe"]) <= DELAY_SR_HURDLE:
        fails.append(f"+1-bar Sharpe {trial['delay_plus1_sharpe']:.3f} <= {DELAY_SR_HURDLE}")
    if float(trial["deflated_sharpe_prob"]) < DSR_HURDLE:
        fails.append(f"deflated Sharpe probability {trial['deflated_sharpe_prob']:.4f} < {DSR_HURDLE}")
    return fails


def implied_net_sharpe(noise_ann: float, bars: int = VAL_BARS) -> float:
    """Annualised net Sharpe at which DSR reaches 0.90, holding variance ~1. An estimate."""
    lo, hi = 0.0, 10.0
    for _ in range(200):
        sr = (lo + hi) / 2
        prob = stats.norm.cdf((sr - noise_ann / math.sqrt(BARS_PER_YEAR)) / math.sqrt(1.0 / (bars - 1)))
        lo, hi = (sr, hi) if prob < DSR_HURDLE else (lo, sr)
    return sr * math.sqrt(BARS_PER_YEAR)


def spend_line(hyp: dict[str, Any], refusals: str | None) -> str:
    used = int(hyp["trials_used"])
    if used == 0:
        return "0 of 3 — no trial was run; the decision rests on the Stage-1 screen"
    if used >= 3:
        return f"{used} of 3 — budget exhausted"
    if refusals:
        return (f"{used} of 3 — terminal, {3 - used} unspent: both sealed revisions were refused "
                f"by the admissibility gate (see `{refusals}`)")
    return (f"{used} of 3 — terminal, {3 - used} unspent: no revision was preregistered and no "
            f"admissible axis can be triggered by this strategy's parameters")


def render(hyp: dict[str, Any], trial: dict[str, Any] | None, screen: dict[str, Any] | None,
           audit: dict[str, Any] | None, contract: dict[str, Any], refusals: str | None) -> str:
    title = hyp["title"]
    lines = [f"# Decision — {hyp['hypothesis_id']} · {title}", ""]
    if trial is not None:
        fails = stage3_failures(trial)
        verdict = "NOT QUALIFIED AT STAGE 3"
        gate = "; ".join(fails) if fails else "no runner gate failed"
        lines += [f"- **Contract**: `{CONTRACT_ID}`",
                  f"- **Verdict**: {verdict} — runner verdict `{trial['verdict']}`",
                  f"- **Binding gate**: {gate}",
                  f"- **Trial spend**: {spend_line(hyp, refusals)}",
                  f"- **Registry status**: `{hyp['status']}` (the status column is not the verdict)", ""]
        lines += ["## Evidence", "",
                  (f"Source: `registry/registry.db`, `trials` row `{trial['run_id']}` "
                  f"(trial {trial['trial_number_in_hyp']} of 3)."), "",
                  "| field | value |", "|---|---|"]
        for key in ("net_sharpe", "stress_2x_cost_sharpe", "delay_plus1_sharpe",
                    "deflated_sharpe_prob", "total_trials_n", "effective_trials_keff",
                    "breakeven_cost_bps", "annualized_turnover", "param_neighbor_stability",
                    "stage_reached", "verdict"):
            value = trial[key]
            shown = f"{value:.4f}" if isinstance(value, float) else str(value)
            lines.append(f"| `{key}` | {shown} |")
        lines.append("")
        if audit is None:
            lines += ["## Audit", "", "No audit report exists for this run.", ""]
        else:
            failed = [name for name, ok in audit["checks"] if not ok]
            lines += ["## Audit", "",
                      f"Status: `{audit['verdict']}` — "
                      + (f"failed {', '.join(f'`{n}`' for n in failed)}" if failed else "all six checks passed"),
                      f"Source: `{audit['path']}`.", "",
                      ("The audit verdict is an audit status, not the strategy verdict. Check 2 requires the "
                      "runner verdict to be `CANDIDATE_FOR_QUALIFICATION`, so it cannot pass for a `REVISE` "
                      "trial regardless of the strategy's merit."), ""]
    else:
        assert screen is not None, "collect() refuses a hypothesis with neither trial nor screen"
        metrics = json.loads(screen["diagnostics_json"] or "{}")
        fails = stage1_failures(metrics)
        verdict = ("REJECTED AT STAGE 1 — no Stage-3 trial was run" if fails
                   else "STAGE-1 PASS — NOT ADVANCED TO STAGE 3")
        gate = ("; ".join(fails) if fails
                else "no Stage-1 criterion failed; this work unit advanced only S01 and S05 to Stage 3")
        spend = (spend_line(hyp, refusals) if fails
                 else "0 of 3 — not advanced to Stage 3 in this work unit")
        lines += [f"- **Contract**: `{CONTRACT_ID}`",
                  f"- **Verdict**: {verdict}",
                  f"- **Binding gate**: {gate}",
                  f"- **Trial spend**: {spend}",
                  f"- **Registry status**: `{hyp['status']}` (the status column is not the verdict)", "",
                  "## Evidence", "",
                  (f"Source: `registry/registry.db`, `selection_attempts` row `{screen['attempt_id']}` "
                  f"(`{screen['kind']}`, outcome `{screen['outcome']}`). No new computation was performed "
                  "for this record."), "",
                  "| field | value |", "|---|---|",
                  f"| `profit_factor` | {float(metrics.get('profit_factor', 0.0)):.4f} |",
                  f"| `conditional_edge` | {float(metrics.get('conditional_edge', 0.0)):.6f} |",
                  f"| `trade_edge` (bps) | {float(metrics.get('trade_edge', 0.0)) * 1e4:.2f} |",
                  f"| `time_in_market` | {float(metrics.get('time_in_market', 0.0)):.3f} |",
                  f"| `bootstrap_p_value` | {float(metrics.get('bootstrap_p_value', 1.0)):.3f} |",
                  f"| `avg_holding_bars` | {float(metrics.get('avg_holding_bars', 0.0)):.1f} |", ""]

    lines += ["## Disclosure", ""]
    criteria = " ".join(str(hyp["falsification_criteria"]).split())
    if trial is not None:
        lines += [
            f"- Registered falsification criteria (Stage 1 only): {criteria}",
            ("- Those criteria were **met**; the mechanism was not falsified at Stage 1. The Stage-3 "
            "outcome is `REVISE` / NOT QUALIFIED, which is a gate verdict, not a falsification."),
            "- The audit status is recorded separately and is not evidence that the mechanism has no edge.",
        ]
    else:
        assert screen is not None, "collect() refuses a hypothesis with neither trial nor screen"
        passed_stage1 = not stage1_failures(json.loads(screen["diagnostics_json"] or "{}"))
        lines += [f"- Registered falsification criteria (Stage 1): {criteria}"]
        if passed_stage1:
            lines += [
                ("- Those criteria were **met**. No Stage-3 trial was run because this work unit advanced "
                 "only S01 and S05, so no Stage-3 verdict exists for this strategy and none is claimed here."),
            ]
        else:
            lines += [
                ("- Those criteria were **not** met, so the mechanism is falsified at Stage 1 under its own "
                 "registered rule. Revisiting it requires a **new** preregistered hypothesis with a different "
                 "mechanism, never a re-run of this ID."),
            ]
    if trial is not None:
        selection = json.loads(trial["diagnostics_json"] or "{}").get("selection", {})
        k_eff = float(trial["effective_trials_keff"])
        noise = float(trial["expected_max_sharpe_noise"])
        unvec = int(selection.get("unvectorized_attempts", contract["unvectorized_screens"]))
        state_label = "this trial was judged under"
    else:
        k_eff, noise = float(contract["max_keff_gate"]), float(contract["max_noise"])
        unvec = int(contract["unvectorized_screens"])
        state_label = "last stored on the contract"
    if trial is None:
        lines += [
            (f"- A trial started now would compute `max(k_dedup, {k_eff:.4f}) + 0.50 x {unvec} = "
             f"{k_eff + 0.5 * unvec:.4f}` as its gate, because the unvectorized penalty is added on top "
             f"of the stored floor rather than replacing it. That is an estimate of the next run's gate, "
             f"not the stored value."),
        ]
    lines += [
        (f"- Selection state {state_label} on `{CONTRACT_ID}`: `k_eff_gate` {k_eff:.4f}, "
        f"expected-max-Sharpe noise floor {noise:.4f} annualised, {unvec} unvectorized attempts. "
        f"The unvectorized penalty is re-applied on top of the previous floor on every trial, so the "
        f"gate ratchets."),
        (f"- Implied net Sharpe for DSR >= {DSR_HURDLE} under that floor: about "
        f"{implied_net_sharpe(noise):.2f} annualised (estimate, variance ~1, {VAL_BARS} bars)."),
        (f"- Evidence window: the trial path evaluates `val_start`..`val_end` only "
        f"(2023-01-01..2025-07-01, {VAL_BARS} bars). No production holdout was read."),
        "",
    ]
    return "\n".join(lines)


def collect(db_path: Path, audit_dir: Path, revisions_dir: Path,
            hyp_ids: tuple[str, ...] = HYPOTHESES) -> list[tuple[Path, str]]:
    """Resolve and render every record, or raise before anything is written."""
    con = connect(db_path)
    try:
        state = con.execute("SELECT * FROM contract_search_state WHERE contract_id=?", (CONTRACT_ID,)).fetchone()
        if state is None:
            raise ValueError(f"no contract_search_state row for {CONTRACT_ID}")
        contract = dict(state)
        contract["unvectorized_screens"] = con.execute(
            "SELECT COUNT(*) FROM selection_attempts WHERE contract_id=? AND kind='standalone_screen'",
            (CONTRACT_ID,)).fetchone()[0]
        rendered: list[tuple[Path, str]] = []
        for hyp_id in hyp_ids:
            hyp = con.execute("SELECT * FROM hypotheses WHERE hypothesis_id=?", (hyp_id,)).fetchone()
            if hyp is None:
                raise ValueError(f"{hyp_id} is not registered; refusing to emit a partial set")
            trial = con.execute("SELECT * FROM trials WHERE hypothesis_id=? ORDER BY trial_number_in_hyp DESC LIMIT 1",
                                (hyp_id,)).fetchone()
            screen = con.execute("SELECT * FROM selection_attempts WHERE hypothesis_id=? AND kind='standalone_screen'",
                                 (hyp_id,)).fetchone()
            if trial is None and screen is None:
                raise ValueError(f"{hyp_id} has neither a trial nor a Stage-1 screen row; no evidence to cite")
            refusals_path = revisions_dir / f"{hyp_id}.refusals.md"
            refusals = str(refusals_path) if refusals_path.is_file() else None
            audit = audit_report(trial["run_id"], audit_dir) if trial is not None else None
            if trial is not None and audit is None:
                raise ValueError(f"{hyp_id}: no audit report for {trial['run_id']}")
            text = render(dict(hyp), dict(trial) if trial else None, dict(screen) if screen else None,
                          audit, contract, refusals)
            rendered.append((Path(hyp_id) / "DECISION.md", text))
    finally:
        con.close()
    return rendered


def write_all(records: list[tuple[Path, str]], out_dir: Path) -> list[Path]:
    written: list[Path] = []
    for rel, text in records:
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate AF30 daily decision records.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--revisions-dir", type=Path, default=DEFAULT_REVISIONS)
    args = parser.parse_args(argv)
    try:
        records = collect(args.db, args.audit_dir, args.revisions_dir)
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f"DECISIONS_FAILED: {exc}", file=sys.stderr)
        return 1
    written = write_all(records, args.out)
    print(json.dumps({"records": len(written), "out": str(args.out)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
