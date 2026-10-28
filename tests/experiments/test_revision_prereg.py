"""Admissibility gate for preregistered S05 revisions (issue #30).

The gate exists so a revision cannot be invented after seeing a trial result, cannot run
when its predecessor failed on something other than the axis it claims to fix, and cannot
be authorised by an engineering failure whose zeroed metrics look like failing ones.
"""

from __future__ import annotations

import json
import runpy
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from warden.registry import ResearchRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = runpy.run_path(str(REPO_ROOT / "scripts" / "check_revision_prereg.py"))
check = GATE["check"]

SEALED_PREREG = REPO_ROOT / ".workspace" / "af30_campaign" / "revisions" / "HYP-2026-099.md"

BASE_PARAMS = {"fast": 64, "slow": 256, "vol_span": 35, "vol_target": 0.16,
               "per_instrument_cap": 0.5, "warmup": 1}
PASSING = {"stage_reached": "STAGE_3_STRESS", "verdict": "REVISE",
           "net_sharpe": 0.8, "stress_2x_cost_sharpe": 0.81, "delay_plus1_sharpe": 0.20,
           "breakeven_cost_bps": 12.0, "param_neighbor_stability": 0.95}


def write_prereg(path: Path, *, axes: tuple[str, str] = ("2x_cost_survival", "latency_survival"),
                 params: tuple[dict | None, dict | None] = (None, None)) -> Path:
    revisions = []
    for idx, axis in enumerate(axes, start=2):
        revisions.append({"run_id": f"RUN-HYP099-{idx:03d}", "axis": axis,
                          "parameters": params[idx - 2] or {**BASE_PARAMS, "fast": 60 + idx},
                          "rationale": f"addresses {axis}"})
    block = {"hypothesis_id": "HYP-2026-099", "contract_id": "MULTI_ASSET_1D_V1",
             "revisions": revisions}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# prereg\n\n```yaml\n" + yaml.safe_dump(block) + "```\n", encoding="utf-8")
    return path


def make_registry(tmp_path: Path, *, rows: list[dict[str, Any]] | None = None,
                  grids: list[list[float]] | None = None) -> Path:
    """Create a registry holding one hypothesis and its recorded trials.

    `grids[n]` are the `param_grid` net Sharpes recorded during trial n+1; they are written
    just before that trial row, exactly as `run_spec` does.
    """
    rows = rows or []
    grids = grids or []
    db = tmp_path / "registry.db"
    ResearchRegistry(db)  # creates the schema
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO hypotheses (hypothesis_id,contract_id,title,strategy_family,economic_mechanism,"
        "counterparty_or_friction,expected_holding_bars,falsification_criteria,trials_used) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("HYP-2026-099", "MULTI_ASSET_1D_V1", "AF30 S05", "rules_ts", "mechanism text here",
         "friction text here", 200, "stage-1 criteria", len(rows)))
    for n, overrides in enumerate(rows, start=1):
        for j, sharpe in enumerate(grids[n - 1] if n - 1 < len(grids) else []):
            con.execute(
                "INSERT INTO selection_attempts (attempt_id,attempt_base,hypothesis_id,contract_id,kind,"
                "stage,outcome,returns_observed,diagnostics_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"ATT-{n}-{j}", f"base-{n}-{j}", "HYP-2026-099", "MULTI_ASSET_1D_V1", "param_grid",
                 "STAGE_3_STRESS", "GRID", 1, json.dumps({"net_sharpe": sharpe}), n - 0.5))
        values: dict[str, Any] = {"run_id": f"RUN-HYP099-{n:03d}", "hypothesis_id": "HYP-2026-099",
                                  "contract_id": "MULTI_ASSET_1D_V1", "trial_number_in_hyp": n,
                                  "created_at": float(n), **PASSING}
        values.update(overrides)
        con.execute(f"INSERT INTO trials ({','.join(values)}) VALUES ({','.join('?' * len(values))})",
                    tuple(values.values()))
    con.commit()
    con.close()
    return db


def write_spec(path: Path, *, run_id: str, parameters: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump({"contract_id": "MULTI_ASSET_1D_V1",
                                    "hypothesis_id": "HYP-2026-099",
                                    "run_id": run_id, "parameters": parameters}), encoding="utf-8")
    return path


def test_sealed_preregistration_parses_and_is_admissible():
    assert check(SEALED_PREREG) == []


def test_missing_preregistration_is_refused(tmp_path):
    errors = check(tmp_path / "nope.md")
    assert errors and "not found" in errors[0]


def test_duplicate_axis_is_refused(tmp_path):
    prereg = write_prereg(tmp_path / "p.md", axes=("latency_survival", "latency_survival"))
    assert any("repeats an earlier revision" in e for e in check(prereg))


def test_unknown_axis_is_refused(tmp_path):
    prereg = write_prereg(tmp_path / "p.md", axes=("net_sharpe", "latency_survival"))
    assert any("is not one of" in e for e in check(prereg))


def test_baseline_trial_needs_no_failing_axis(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path)
    spec = write_spec(tmp_path / "t1.yaml", run_id="RUN-HYP099-001", parameters=BASE_PARAMS)
    assert check(prereg, spec, db) == []


def test_sealed_revision_cannot_be_spent_as_the_baseline(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path)
    spec = write_spec(tmp_path / "t1.yaml", run_id="RUN-HYP099-002",
                      parameters={**BASE_PARAMS, "fast": 62})
    assert any("cannot run as the baseline trial" in e for e in check(prereg, spec, db))


def test_baseline_run_id_must_follow_the_campaign_convention(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path)
    wrong_number = write_spec(tmp_path / "t1.yaml", run_id="RUN-HYP099-777", parameters=BASE_PARAMS)
    assert any("is numbered trial 777" in e for e in check(prereg, wrong_number, db))
    foreign = write_spec(tmp_path / "t1b.yaml", run_id="RUN-FOO-001", parameters=BASE_PARAMS)
    assert any("does not follow the campaign convention" in e for e in check(prereg, foreign, db))


def test_baseline_cannot_spend_a_sealed_configuration(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path)
    sealed_params = {**BASE_PARAMS, "fast": 62}  # revision 002's sealed parameters
    spec = write_spec(tmp_path / "t1.yaml", run_id="RUN-HYP099-001", parameters=sealed_params)
    assert any("cannot be spent as the baseline trial" in e for e in check(prereg, spec, db))


def test_unsealed_parameters_are_refused(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path, rows=[{}])
    spec = write_spec(tmp_path / "t2.yaml", run_id="RUN-HYP099-002",
                      parameters={**BASE_PARAMS, "fast": 999})
    assert any("differ from the sealed configuration" in e for e in check(prereg, spec, db))


def test_revision_admitted_when_predecessor_fails_its_axis(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path, rows=[{"stress_2x_cost_sharpe": 0.30}])
    spec = write_spec(tmp_path / "t2.yaml", run_id="RUN-HYP099-002",
                      parameters={**BASE_PARAMS, "fast": 62})
    assert check(prereg, spec, db) == []


def test_revision_refused_when_predecessor_passes_its_axis(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path, rows=[{}])
    spec = write_spec(tmp_path / "t2.yaml", run_id="RUN-HYP099-002",
                      parameters={**BASE_PARAMS, "fast": 62})
    errors = check(prereg, spec, db)
    assert any("does not fail it" in e and "stress_2x_cost_sharpe=0.8100" in e for e in errors)


def test_failed_predecessor_cannot_authorise_a_revision(tmp_path):
    """A FAILED row's zeroed metrics would satisfy every numeric predicate."""
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path, rows=[{"stage_reached": "FAILED", "verdict": "FAILED",
                                        "stress_2x_cost_sharpe": 0.0, "delay_plus1_sharpe": 0.0,
                                        "breakeven_cost_bps": 0.0}])
    spec = write_spec(tmp_path / "t2.yaml", run_id="RUN-HYP099-002",
                      parameters={**BASE_PARAMS, "fast": 62})
    errors = check(prereg, spec, db)
    assert any("only an observed Stage-3 REVISE" in e for e in errors)


def test_third_revision_cannot_skip_an_unrun_second(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path, rows=[{"stress_2x_cost_sharpe": 0.30}])
    spec = write_spec(tmp_path / "t3.yaml", run_id="RUN-HYP099-003",
                      parameters={**BASE_PARAMS, "fast": 63})
    errors = check(prereg, spec, db)
    assert any("is numbered trial 3" in e for e in errors)


def test_third_revision_admitted_after_second_fails_its_axis(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path, rows=[{"stress_2x_cost_sharpe": 0.30}, {"delay_plus1_sharpe": -0.2}])
    spec = write_spec(tmp_path / "t3.yaml", run_id="RUN-HYP099-003",
                      parameters={**BASE_PARAMS, "fast": 63})
    assert check(prereg, spec, db) == []


def test_neighbour_axis_reads_the_grid_points_of_that_trial(tmp_path):
    prereg = write_prereg(tmp_path / "p.md", axes=("param_neighbour_stability", "breakeven_cost"))
    spec = write_spec(tmp_path / "t2.yaml", run_id="RUN-HYP099-002",
                      parameters={**BASE_PARAMS, "fast": 62})

    losing = make_registry(tmp_path / "a", rows=[{}], grids=[[0.4, 0.3, -0.05]])
    assert check(prereg, spec, losing) == []

    healthy = make_registry(tmp_path / "b", rows=[{}], grids=[[0.4, 0.35, 0.30]])
    assert any("all positive" in e for e in check(prereg, spec, healthy))

    dispersed = make_registry(tmp_path / "c", rows=[{"param_neighbor_stability": 0.30}],
                              grids=[[0.4, 0.35, 0.30]])
    assert check(prereg, spec, dispersed) == []


def test_no_grid_trial_cannot_justify_the_neighbour_axis(tmp_path):
    """S01-style trials store the no-grid default 1.0 and own no grid rows."""
    prereg = write_prereg(tmp_path / "p.md", axes=("param_neighbour_stability", "breakeven_cost"))
    db = make_registry(tmp_path, rows=[{"param_neighbor_stability": 1.0}])
    spec = write_spec(tmp_path / "t2.yaml", run_id="RUN-HYP099-002",
                      parameters={**BASE_PARAMS, "fast": 62})
    assert any("all positive" in e for e in check(prereg, spec, db))


def test_missing_metric_fails_closed(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path, rows=[{"stress_2x_cost_sharpe": None}])
    spec = write_spec(tmp_path / "t2.yaml", run_id="RUN-HYP099-002",
                      parameters={**BASE_PARAMS, "fast": 62})
    with pytest.raises(ValueError, match="cannot judge"):
        check(prereg, spec, db)


def test_exhausted_budget_is_refused(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path, rows=[{}, {}, {}])
    spec = write_spec(tmp_path / "t3.yaml", run_id="RUN-HYP099-003",
                      parameters={**BASE_PARAMS, "fast": 63})
    assert any("no trial budget left" in e for e in check(prereg, spec, db))


def test_admit_judges_a_sealed_revision_without_a_spec_file(tmp_path):
    """The refusal record must not require authoring a spec for a run that never happens."""
    prereg = write_prereg(tmp_path / "p.md")
    admit = GATE["admit"]

    passing = make_registry(tmp_path / "a", rows=[{"stress_2x_cost_sharpe": 0.86}])
    errors = admit(prereg, "RUN-HYP099-002", passing)
    assert any("does not fail it" in e and "0.8600" in e for e in errors)

    failing = make_registry(tmp_path / "b", rows=[{"stress_2x_cost_sharpe": 0.30}])
    assert admit(prereg, "RUN-HYP099-002", failing) == []


def test_admit_refuses_an_unsealed_run_id(tmp_path):
    prereg = write_prereg(tmp_path / "p.md")
    db = make_registry(tmp_path, rows=[{}])
    assert any("is not in the preregistration" in e for e in GATE["admit"](prereg, "RUN-HYP099-009", db))
