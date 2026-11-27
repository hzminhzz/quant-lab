"""Evaluator-owned fixed robustness plan and append-only evidence helpers.

No scenario is an independent candidate or a substitute for a trial. The plan
is frozen at first candidate admission. Only predeclared numeric perturbations
of known baseline parameters are accepted; broader searches need a new candidate.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any

import numpy as np

from warden.contracts import canonical_json
from warden.params_model import ParamsModelError, validate_strategy_params
from warden.protocols import resolve_effective_params
from warden_evidence.selection_alignment import utc_ns

ROBUSTNESS_DDL = """
CREATE TABLE IF NOT EXISTS robustness_plans (
    candidate_id TEXT PRIMARY KEY REFERENCES candidate_admissions(candidate_id),
    plan_json TEXT NOT NULL, plan_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS robustness_receipts (
    scenario_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES robustness_plans(candidate_id),
    contract_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES attempt_receipts(run_id),
    scenario_key TEXT NOT NULL, scenario_json TEXT NOT NULL, reserved_at REAL NOT NULL,
    UNIQUE(run_id,scenario_key)
);
CREATE TABLE IF NOT EXISTS robustness_outcomes (
    scenario_id TEXT PRIMARY KEY REFERENCES robustness_receipts(scenario_id),
    state TEXT NOT NULL CHECK(state IN ('FINALIZED','INTERRUPTED_REVIEW')),
    evidence_json TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
    attempt_id TEXT NOT NULL DEFAULT '', finalized_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS robustness_plans_no_update BEFORE UPDATE ON robustness_plans BEGIN SELECT RAISE(ABORT,'Append-only violation: robustness plan is immutable'); END;
CREATE TRIGGER IF NOT EXISTS robustness_plans_no_delete BEFORE DELETE ON robustness_plans BEGIN SELECT RAISE(ABORT,'Append-only violation: robustness plan is immutable'); END;
CREATE TRIGGER IF NOT EXISTS robustness_receipts_no_update BEFORE UPDATE ON robustness_receipts BEGIN SELECT RAISE(ABORT,'Append-only violation: robustness receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS robustness_receipts_no_delete BEFORE DELETE ON robustness_receipts BEGIN SELECT RAISE(ABORT,'Append-only violation: robustness receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS robustness_outcomes_no_update BEFORE UPDATE ON robustness_outcomes BEGIN SELECT RAISE(ABORT,'Append-only violation: robustness outcome is immutable'); END;
CREATE TRIGGER IF NOT EXISTS robustness_outcomes_no_delete BEFORE DELETE ON robustness_outcomes BEGIN SELECT RAISE(ABORT,'Append-only violation: robustness outcome is immutable'); END;
"""


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def base_plan() -> dict[str, dict[str, Any]]:
    return {
        "cost_1p5": {"kind": "cost", "multiplier": 1.5, "method": "analytical"},
        "cost_2x": {"kind": "cost", "multiplier": 2.0, "method": "engine"},
        "latency_plus1": {"kind": "latency", "delay_offset": 1},
    }


def build_robustness_plan(mod, protocol, parameters, grid):
    """Freeze exact execution configurations, not a caller-selected winner.

    Keep at most two distinct perturbed values per numeric axis, with at most
    two distinct configurations per perturbed axis overall. Repeated baseline
    and duplicate entries remain raw operations, not new search identities.
    This bounds the fixed local check's cardinality without changing the
    existing numeric-neighbor stability gate or imposing a new effect threshold.
    """
    base = validate_strategy_params(mod, parameters)
    baseline = base.baseline_effective
    axes = set(base.numeric_fields)
    base_symbols = resolve_effective_params(mod, protocol, parameters).get("target_symbols")
    plan = base_plan()
    changed_values: dict[str, set[float]] = {}
    distinct: set[str] = set()
    merged_grid = []
    for i, raw in enumerate(grid):
        merged = {**parameters, **raw}
        try:
            effective = validate_strategy_params(mod, merged).baseline_effective
        except ParamsModelError as exc:
            raise ParamsModelError(f"param_grid[{i}]: {exc}") from exc
        delta = {key for key in baseline.keys() | effective.keys()
                 if baseline.get(key) != effective.get(key)}
        if not delta <= axes or any(
            isinstance(effective.get(key), bool)
            or not isinstance(effective.get(key), (int, float))
            or not math.isfinite(float(effective[key]))
            or key not in base.baseline_numeric
            for key in delta
        ):
            raise ValueError("param_grid is not fixed numeric robustness; use a separately admitted candidate")
        if resolve_effective_params(mod, protocol, merged).get("target_symbols") != base_symbols:
            raise ValueError("param_grid changes the data universe; use a separately admitted candidate")
        for key in delta:
            changed_values.setdefault(key, set()).add(float(effective[key]))
        if delta:
            distinct.add(canonical_json(effective))
        if (any(len(values) > 2 for values in changed_values.values())
                or len(distinct) > 2 * len(changed_values)):
            raise ValueError("param_grid is a selectable sweep; use separately admitted candidates")
        merged_grid.append(merged)
        plan[f"neighbor:{i}"] = {"kind": "neighbor", "parameters": merged}
    return plan, merged_grid


def bind_plan(conn, candidate_id: str, plan: dict | None, *, existing: bool) -> None:
    plan = base_plan() if plan is None else plan
    required = base_plan()
    if any(plan.get(key) != value for key, value in required.items()):
        raise ValueError("Fixed cost and latency scenarios are evaluator-owned")
    extras = set(plan) - set(required)
    if "benchmark" in extras:
        benchmark = plan["benchmark"]
        if (set(benchmark) != {"kind", "benchmark", "comparison_policy"}
                or benchmark["kind"] != "benchmark"):
            raise ValueError("Invalid frozen benchmark plan")
        extras.remove("benchmark")
    if extras != {f"neighbor:{i}" for i in range(len(extras))} or any(
        set(plan[key]) != {"kind", "parameters"}
        or plan[key]["kind"] != "neighbor"
        or not isinstance(plan[key]["parameters"], dict)
        for key in extras
    ):
        raise ValueError("Invalid frozen neighbor plan")
    payload = canonical_json(plan)
    row = conn.execute(
        "SELECT plan_json,plan_sha256 FROM robustness_plans WHERE candidate_id=?", (candidate_id,)
    ).fetchone()
    if row:
        if row[0] != payload or row[1] != _digest(payload):
            raise ValueError("Frozen candidate robustness plan changed after admission")
        return
    if existing:
        raise ValueError("Historical candidate has no frozen robustness plan; review required")
    conn.execute("INSERT INTO robustness_plans VALUES (?,?,?)",
                 (candidate_id, payload, _digest(payload)))


def _read_plan(conn, candidate_id):
    row = conn.execute(
        "SELECT plan_json,plan_sha256 FROM robustness_plans WHERE candidate_id=?", (candidate_id,)
    ).fetchone()
    if row is None or _digest(row[0]) != row[1]:
        raise ValueError("Missing or inconsistent frozen robustness plan")
    return json.loads(row[0])


def _parent(conn, run_id):
    row = conn.execute(
        "SELECT c.candidate_id,c.contract_id,c.hypothesis_id,c.identity_json,r.state "
        "FROM candidate_operations o JOIN candidate_admissions c ON c.candidate_id=o.candidate_id "
        "JOIN attempt_receipts r ON r.run_id=o.operation_id "
        "WHERE o.kind='trial' AND o.operation_id=?", (run_id,),
    ).fetchone()
    if row is None or row[4] != "RESERVED":
        raise ValueError("Robustness operation requires an active admitted full trial")
    return row


def reserve_scenario(conn, run_id, scenario_key):
    candidate_id, contract_id, _, _, _ = _parent(conn, run_id)
    plan = _read_plan(conn, candidate_id)
    if scenario_key not in plan:
        raise ValueError("Scenario is not in the frozen robustness plan")
    scenario_id = _digest(canonical_json([candidate_id, run_id, scenario_key]))
    if conn.execute("SELECT 1 FROM robustness_receipts WHERE scenario_id=?", (scenario_id,)).fetchone():
        raise ValueError("Robustness scenario already admitted; review, never replay")
    conn.execute("INSERT INTO robustness_receipts VALUES (?,?,?,?,?,?,?)",
                 (scenario_id, candidate_id, contract_id, run_id, scenario_key,
                  canonical_json(plan[scenario_key]), time.time()))
    return scenario_id


def finalize_scenario(conn, scenario_id, metrics, returns=None, timestamps=None, *, attempt_id="", paired=False):
    row = conn.execute(
        "SELECT run_id,candidate_id,scenario_key,scenario_json FROM robustness_receipts WHERE scenario_id=?",
        (scenario_id,),
    ).fetchone()
    if row is None:
        raise ValueError("No matching admitted robustness scenario")
    parent = _parent(conn, row[0])
    if parent[0] != row[1]:
        raise ValueError("Robustness candidate binding changed")
    plan = _read_plan(conn, row[1])
    if canonical_json(plan[row[2]]) != row[3]:
        raise ValueError("Robustness receipt differs from its frozen plan")
    if conn.execute("SELECT 1 FROM robustness_outcomes WHERE scenario_id=?", (scenario_id,)).fetchone():
        raise ValueError("Robustness scenario already finalized; never replay")
    values = None
    clock = None
    if returns is not None:
        array = np.asarray(returns, dtype=float)
        if array.ndim != 1 or not len(array) or not np.isfinite(array).all():
            raise ValueError("Robustness returns must be a nonempty finite vector")
        if timestamps is None:
            raise ValueError("Robustness returns require a declared clock")
        clock = utc_ns(timestamps).tolist()
        if len(clock) != len(array):
            raise ValueError("Robustness evidence clock and returns differ")
        values = array.tolist()
    elif timestamps is not None:
        raise ValueError("Robustness clock without returns")
    if plan[row[2]]["kind"] == "neighbor":
        if not attempt_id or values is None:
            raise ValueError("Neighbor requires its matching immutable operation evidence")
        identity = json.loads(parent[3])
        attempt = conn.execute(
            "SELECT run_id,hypothesis_id,contract_id,kind,parameters_json,returns_json,"
            "strategy_module,strategy_family,simulation_epoch "
            "FROM selection_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if not attempt or tuple(attempt) != (
            row[0], parent[2], parent[1], "param_grid",
            canonical_json(plan[row[2]]["parameters"]), canonical_json(values),
            identity["strategy_module"], identity["strategy_family"], identity["simulation_epoch"],
        ):
            raise ValueError("Neighbor evidence differs from frozen scenario")
    elif attempt_id:
        raise ValueError("Only neighbor evidence links a legacy-format grid row")
    if plan[row[2]]["kind"] == "benchmark":
        if not paired or set(metrics) != {"incremental_comparison"} or values is not None:
            raise ValueError("Benchmark requires governed paired artifact finalization")
    else:
        if plan[row[2]].get("method") != "analytical" and values is None:
            raise ValueError("Engine scenario requires timestamped returns")
        if "net_sharpe" not in metrics or not math.isfinite(float(metrics["net_sharpe"])):
            raise ValueError("Robustness evidence requires finite Sharpe")
    payload = canonical_json({
        "scenario": json.loads(row[3]), "metrics": metrics,
        "returns": values, "timestamps_ns": clock, "attempt_id": attempt_id,
    })
    conn.execute("INSERT INTO robustness_outcomes VALUES (?,?,?,?,?,?)",
                 (scenario_id, "FINALIZED", payload, _digest(payload), attempt_id, time.time()))


def scenario_status(conn, run_id):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='robustness_receipts'"
    ).fetchone():
        return []
    rows = conn.execute(
        "SELECT r.scenario_id,r.scenario_key,r.scenario_json,o.state,o.evidence_json,o.evidence_sha256,o.attempt_id "
        "FROM robustness_receipts r LEFT JOIN robustness_outcomes o ON o.scenario_id=r.scenario_id "
        "WHERE r.run_id=? ORDER BY r.reserved_at,r.scenario_key", (run_id,)
    )
    result = []
    for sid, key, spec, state, evidence, digest, attempt_id in rows:
        actual_state = state or "RESERVED"
        if state and (not evidence or _digest(evidence) != digest):
            actual_state = "EVIDENCE_DRIFT"
        if state == "FINALIZED" and actual_state != "EVIDENCE_DRIFT":
            decoded = json.loads(evidence)
            if decoded.get("scenario") != json.loads(spec) or decoded.get("attempt_id") != (attempt_id or ""):
                actual_state = "EVIDENCE_DRIFT"
        if state == "FINALIZED" and key == "benchmark" and actual_state != "EVIDENCE_DRIFT":
            from pathlib import Path

            from warden.incremental import verify_artifacts

            try:
                root = Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent / "returns"
                verify_artifacts(decoded["metrics"]["incremental_comparison"], root)
            except (OSError, ValueError, KeyError):
                actual_state = "EVIDENCE_DRIFT"
        if state == "FINALIZED" and attempt_id and actual_state != "EVIDENCE_DRIFT":
            attempt = conn.execute(
                "SELECT returns_json,returns_sha256 FROM selection_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if (not attempt or _digest(attempt[0]) != attempt[1]
                    or json.loads(attempt[0]) != json.loads(evidence)["returns"]):
                actual_state = "EVIDENCE_DRIFT"
        result.append({"scenario_id": sid, "scenario_key": key, "state": actual_state,
                       "evidence_sha256": digest or "", "scenario": json.loads(spec)})
    return result


def require_complete(conn, run_id, candidate_id):
    plan = _read_plan(conn, candidate_id)
    observed = scenario_status(conn, run_id)
    if {row["scenario_key"] for row in observed if row["state"] == "FINALIZED"} != set(plan):
        raise ValueError("Stage 3 outcome requires all frozen robustness evidence finalized")


def interrupt_scenarios(conn, run_id):
    for row in scenario_status(conn, run_id):
        if row["state"] != "RESERVED":
            continue
        payload = canonical_json({"reason": "interrupted before immutable scenario outcome"})
        conn.execute("INSERT INTO robustness_outcomes VALUES (?,?,?,?,?,?)",
                     (row["scenario_id"], "INTERRUPTED_REVIEW", payload, _digest(payload), "", time.time()))
