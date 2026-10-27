#!/usr/bin/env python3
"""Check that an S05-style revision trial is admissible before it consumes a trial.

The spec (`.workspace/plans/carver_daily_decision/spec.md`, criteria 6 and 7) says a
revision may run only when two things are true:

1. its configuration was sealed in `.workspace/af30_campaign/revisions/<HYP>.md`
   *before* the baseline trial ran, and
2. the immediately preceding trial row for that hypothesis fails the axis the
   revision names, under a fixed numeric rule.

This script enforces both. It reads the registry and the preregistration; it never
writes either, and it never touches `src/warden/`.

Axis rules (spec criterion 7):

   2x_cost_survival          stress_2x_cost_sharpe <= 0.50
   latency_survival          delay_plus1_sharpe <= 0.0
   breakeven_cost            breakeven_cost_bps < 1.0
   param_neighbour_stability param_neighbor_stability < 0.50, or any param_grid
                             point recorded during that trial with net Sharpe <= 0

Grid points are attributed to a trial by time window: `run_spec` writes every grid
`selection_attempt` before it writes the trial row, and grid rows carry `run_id=''`, so
a trial owns the grid rows created in `(previous trial's created_at, this trial's
created_at]`. A trial run without a `param_grid` (S01) stores the no-grid default of
1.0 and owns no grid rows, so this axis can never justify a revision from it.

A revision is judged only against an **observed** Stage-3 `REVISE` predecessor. A
`FAILED` trial row stores zeroed stress/latency/breakeven metrics, and those zeros
would satisfy the numeric predicates; they are engineering failures, not strategy
failures, so they are refused explicitly.

Usage:
    python scripts/check_revision_prereg.py                     # prereg file only
    python scripts/check_revision_prereg.py --spec <trial.yaml> # admissibility of one run
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PREREG = Path(".workspace/af30_campaign/revisions/HYP-2026-099.md")
DEFAULT_DB = Path("registry/registry.db")
PREREG_KEYS = {"hypothesis_id", "contract_id", "revisions"}
REVISION_KEYS = {"run_id", "axis", "parameters", "rationale"}
AXES = ("2x_cost_survival", "latency_survival", "breakeven_cost", "param_neighbour_stability")


def load_prereg(path: Path) -> dict[str, Any]:
    """Return the first ```yaml block of the preregistration markdown, parsed."""
    text = path.read_text(encoding="utf-8")
    if "```yaml" not in text:
        raise ValueError(f"{path}: no ```yaml block found")
    block = text.split("```yaml", 1)[1].split("```", 1)[0]
    data = yaml.safe_load(block)
    if not isinstance(data, dict):
        raise TypeError(f"{path}: yaml block is not a mapping")
    return data


def validate_prereg(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = PREREG_KEYS - set(data)
    if missing:
        errors.append(f"preregistration is missing keys: {sorted(missing)}")
    extra = set(data) - PREREG_KEYS
    if extra:
        errors.append(f"preregistration has unknown keys: {sorted(extra)}")
    revisions = data.get("revisions")
    if not isinstance(revisions, list) or len(revisions) != 2:
        return errors + ["preregistration must name exactly two revisions"]
    seen_axes, seen_runs = set(), set()
    for rev in revisions:
        if not isinstance(rev, dict) or set(rev) != REVISION_KEYS:
            errors.append(f"revision {rev!r} must have exactly the keys {sorted(REVISION_KEYS)}")
            continue
        if rev["axis"] not in AXES:
            errors.append(f"revision {rev['run_id']}: axis '{rev['axis']}' is not one of {list(AXES)}")
        if rev["axis"] in seen_axes:
            errors.append(f"revision {rev['run_id']}: axis '{rev['axis']}' repeats an earlier revision; "
                          "each revision must address a different failure axis")
        seen_axes.add(rev["axis"])
        if rev["run_id"] in seen_runs:
            errors.append(f"duplicate run_id '{rev['run_id']}' in preregistration")
        seen_runs.add(rev["run_id"])
        if not isinstance(rev["parameters"], dict) or not rev["parameters"]:
            errors.append(f"revision {rev['run_id']}: parameters must be a non-empty mapping")
        if not str(rev["rationale"]).strip():
            errors.append(f"revision {rev['run_id']}: rationale is empty")
    return errors


def axis_fails(axis: str, row: dict[str, Any], grid_sharpes: list[float]) -> tuple[bool, str]:
    """Return (fails, human explanation) for one trial row under one axis rule."""
    def metric(name: str) -> float:
        value = row.get(name)
        if value is None:
            raise ValueError(f"predecessor trial {row.get('run_id')} has no '{name}'; cannot judge '{axis}'")
        return float(value)

    if axis == "2x_cost_survival":
        v = metric("stress_2x_cost_sharpe")
        return v <= 0.50, f"stress_2x_cost_sharpe={v:.4f} (fails at <= 0.50)"
    if axis == "latency_survival":
        v = metric("delay_plus1_sharpe")
        return v <= 0.0, f"delay_plus1_sharpe={v:.4f} (fails at <= 0.0)"
    if axis == "breakeven_cost":
        v = metric("breakeven_cost_bps")
        return v < 1.0, f"breakeven_cost_bps={v:.4f} (fails at < 1.0)"
    if axis == "param_neighbour_stability":
        v = metric("param_neighbor_stability")
        losing = [s for s in grid_sharpes if s <= 0.0]
        if v < 0.50:
            return True, f"param_neighbor_stability={v:.4f} (fails at < 0.50)"
        if losing:
            return True, f"grid point(s) with net Sharpe <= 0: {losing}"
        return False, (f"param_neighbor_stability={v:.4f} and grid Sharpes {grid_sharpes} "
                       f"are all positive (fails at < 0.50 or any point <= 0)")
    raise ValueError(f"unknown axis '{axis}'")


def grid_sharpes_for(con: sqlite3.Connection, hypothesis_id: str,
                     trial_created_at: float, prior_created_at: float) -> list[float]:
    """Net Sharpes of the param_grid points recorded *during* that trial.

    `run_spec` writes every grid `selection_attempt` before it writes the trial row, so a
    trial owns the grid rows created in `(previous trial's created_at, this trial's created_at]`.
    Grid rows carry `run_id=''`, so this window is the only honest attribution available.
    """
    rows = con.execute(
        "SELECT diagnostics_json FROM selection_attempts WHERE hypothesis_id=? AND kind='param_grid' "
        "AND created_at > ? AND created_at <= ? ORDER BY created_at",
        (hypothesis_id, prior_created_at, trial_created_at)).fetchall()
    out: list[float] = []
    for r in rows:
        diag = json.loads(r["diagnostics_json"] or "{}")
        if "net_sharpe" in diag:
            out.append(float(diag["net_sharpe"]))
    return out


def _canonical(mapping: dict[str, Any]) -> str:
    return json.dumps({k: mapping[k] for k in sorted(mapping)}, sort_keys=True, default=str)


def check(prereg_path: Path, spec_path: Path | None = None, db_path: Path | None = None) -> list[str]:
    """Return every reason the preregistration or the trial spec is inadmissible."""
    if not prereg_path.is_file():
        return [f"preregistration file not found: {prereg_path}"]
    try:
        data = load_prereg(prereg_path)
    except (ValueError, yaml.YAMLError) as exc:
        return [str(exc)]
    errors = validate_prereg(data)
    if errors or spec_path is None:
        return errors

    spec_path = Path(spec_path)
    if not spec_path.is_file():
        return [f"trial spec not found: {spec_path}"]
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    for key in ("hypothesis_id", "contract_id", "run_id", "parameters"):
        if key not in spec:
            return [f"trial spec {spec_path} is missing '{key}'"]
    if spec["hypothesis_id"] != data["hypothesis_id"]:
        return [f"spec hypothesis {spec['hypothesis_id']} does not match preregistration {data['hypothesis_id']}"]
    if spec["contract_id"] != data["contract_id"]:
        return [f"spec contract {spec['contract_id']} does not match preregistration {data['contract_id']}"]

    sealed = {rev["run_id"]: rev for rev in data["revisions"]}

    db_path = Path(db_path or DEFAULT_DB)
    if not db_path.is_file():
        return [f"registry not found: {db_path}"]
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        hyp = con.execute("SELECT trials_used, status FROM hypotheses WHERE hypothesis_id=?",
                          (spec["hypothesis_id"],)).fetchone()
        if hyp is None:
            return [f"hypothesis {spec['hypothesis_id']} is not registered"]
        trial_number = int(hyp["trials_used"]) + 1
        if int(hyp["trials_used"]) >= 3:
            return [f"{spec['hypothesis_id']} has no trial budget left ({hyp['trials_used']}/3)"]
        if trial_number == 1:
            # A baseline trial is not a revision and needs no failing axis, but a sealed
            # revision must never be spent as the baseline.
            if spec["run_id"] in sealed:
                return [f"'{spec['run_id']}' is a sealed revision and cannot run as the baseline trial"]
            return []
        row = con.execute(
            "SELECT * FROM trials WHERE hypothesis_id=? AND trial_number_in_hyp=?",
            (spec["hypothesis_id"], trial_number - 1)).fetchone()
        if row is None:
            return [f"no preceding trial row for {spec['hypothesis_id']} before trial {trial_number}"]
        if row["stage_reached"] != "STAGE_3_STRESS" or row["verdict"] != "REVISE":
            return [(f"preceding trial {row['run_id']} is stage={row['stage_reached']} "
                     f"verdict={row['verdict']}; only an observed Stage-3 REVISE can justify a revision "
                     "(a FAILED row's zeroed metrics would otherwise satisfy the axis rules)")]
        prior = con.execute(
            "SELECT created_at FROM trials WHERE hypothesis_id=? AND trial_number_in_hyp=?",
            (spec["hypothesis_id"], trial_number - 2)).fetchone()
        grid = grid_sharpes_for(con, spec["hypothesis_id"], float(row["created_at"]),
                                float(prior["created_at"]) if prior else float("-inf"))
    finally:
        con.close()

    index = int(str(spec["run_id"]).rsplit("-", 1)[-1])
    if index != trial_number:
        return [(f"run '{spec['run_id']}' would be trial {trial_number} for this hypothesis; a sealed "
                 f"revision must run as trial {index}, so T3 cannot run when T2 did not")]

    revision = sealed.get(spec["run_id"])
    if revision is None:
        return [f"run_id '{spec['run_id']}' is not in the preregistration; only {sorted(sealed)} may run"]
    if _canonical(spec["parameters"]) != _canonical(revision["parameters"]):
        return [(f"run '{spec['run_id']}' parameters differ from the sealed configuration "
                 f"(sealed {_canonical(revision['parameters'])}, spec {_canonical(spec['parameters'])})")]

    fails, detail = axis_fails(revision["axis"], dict(row), grid)
    if not fails:
        return [(f"revision '{revision['run_id']}' addresses '{revision['axis']}' but the preceding trial "
                 f"({row['run_id']}) does not fail it: {detail}")]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check S05 revision preregistration admissibility.")
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--spec", type=Path, default=None, help="trial spec to admit against the registry")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    errors = check(args.prereg, args.spec, args.db)
    if errors:
        print("INADMISSIBLE", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(f"ADMISSIBLE: {args.prereg}" + (f" admits {args.spec}" if args.spec else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
