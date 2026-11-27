"""Project raw operations and selectable search debt from one ledger snapshot.

Historical unbound evidence is never deduplicated speculatively. A frozen candidate
has one statistical search identity, regardless of its screen/full/fixed scenarios.
Unknown-epoch interrupted legacy receipts are conservatively debt in each queried epoch.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from warden.contracts import DEFAULT_SIMULATION_EPOCH


@dataclass(frozen=True)
class SelectionAccounting:
    raw_operations: int
    selection_count: int
    trial_ids: frozenset[str]
    attempt_ids: frozenset[str]


def _exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _rows(conn, table, fields, contract_id):
    if not _exists(conn, table):
        return []
    columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    names = [name if name in columns else f"NULL AS {name}" for name in fields]
    return conn.execute(
        f"SELECT {','.join(names)} FROM {table} WHERE contract_id=?", (contract_id,)
    ).fetchall()


def _epoch(value):
    return value or DEFAULT_SIMULATION_EPOCH


def project_selection(conn: sqlite3.Connection, contract_id: str, epoch: str | None,
                      *, exclude_candidate_id: str = "") -> SelectionAccounting:
    trials = {r[0]: r for r in _rows(
        conn, "trials", ("run_id", "simulation_epoch", "stage_reached", "returns_sha256"), contract_id)}
    attempts = {r[0]: r for r in _rows(
        conn, "selection_attempts", ("attempt_id", "simulation_epoch", "kind", "returns_observed"), contract_id)}
    full = {r[0]: r for r in _rows(conn, "attempt_receipts", ("run_id", "state"), contract_id)}
    screens = {r[0]: r for r in _rows(conn, "screen_receipts", ("screen_key", "attempt_id"), contract_id)}
    candidates = {r[0]: json.loads(r[1]) for r in _rows(
        conn, "candidate_admissions", ("candidate_id", "identity_json"), contract_id)}
    operations: dict[str, dict[str, str]] = {cid: {} for cid in candidates}
    if candidates:
        for cid, kind, operation_id in conn.execute(
            "SELECT o.candidate_id,o.kind,o.operation_id FROM candidate_operations o "
            "JOIN candidate_admissions c ON c.candidate_id=o.candidate_id WHERE c.contract_id=?",
            (contract_id,),
        ):
            operations[cid][kind] = operation_id
    bound_full = {ops["trial"]: cid for cid, ops in operations.items() if "trial" in ops}
    bound_screens = {ops["screen"]: cid for cid, ops in operations.items() if "screen" in ops}
    bound_attempts = {
        screens[key][1]: cid for key, cid in bound_screens.items()
        if key in screens and screens[key][1]
    }
    # Scenario outcomes and their old-format grid selection rows represent the same
    # operation. Fixed scenarios never create a selectable identity.
    scenarios = {r[0]: r for r in _rows(
        conn, "robustness_receipts", ("scenario_id", "candidate_id"), contract_id)}
    fixed_attempt_ids = set()
    if scenarios:
        fixed_attempt_ids = {r[0] for r in conn.execute(
            "SELECT o.attempt_id FROM robustness_outcomes o JOIN robustness_receipts r "
            "ON r.scenario_id=o.scenario_id WHERE r.contract_id=? AND o.attempt_id!=''",
            (contract_id,),
        )}
    def in_epoch(value):
        return epoch is None or _epoch(value) == epoch

    def candidate_in_epoch(cid):
        return in_epoch(candidates[cid]["simulation_epoch"])

    # Only prospectively frozen v5 plans justify collapsing operations. Earlier
    # screens and trials were independently charged; never erase historical debt.
    planned_ids = set()
    if _exists(conn, "robustness_plans"):
        planned_ids = {r[0] for r in conn.execute(
            "SELECT p.candidate_id FROM robustness_plans p JOIN candidate_admissions c "
            "ON p.candidate_id=c.candidate_id WHERE c.contract_id=?", (contract_id,))}
    selected_trials = {rid for rid, row in trials.items()
                       if bound_full.get(rid) not in planned_ids and in_epoch(row[1])}
    selected_attempts = {aid for aid, row in attempts.items()
                         if bound_attempts.get(aid) not in planned_ids and aid not in fixed_attempt_ids
                         and row[2] != 'technical_preflight' and in_epoch(row[1])}
    pending_full = {rid for rid in full if rid not in trials and rid not in bound_full}
    pending_screens = {key for key, row in screens.items()
                       if row[1] not in attempts and key not in bound_screens}
    selection_count = len(selected_trials) + len(selected_attempts) + len(pending_full) + len(pending_screens)

    selection_count += sum(rid not in trials and cid not in planned_ids and candidate_in_epoch(cid)
                           for rid, cid in bound_full.items())
    selection_count += sum((key not in screens or screens[key][1] not in attempts)
                           and cid not in planned_ids and candidate_in_epoch(cid)
                           for key, cid in bound_screens.items())
    for cid, ops in operations.items():
        if cid not in planned_ids or not candidate_in_epoch(cid) or cid == exclude_candidate_id:
            continue
        selection_count += 1
        run_id = ops.get("trial")
        screen = screens.get(ops.get("screen"))
        # Deterministic precedence, never choose the tape with the better return.
        # A failed/early trial with no usable tape leaves its observed screen as evidence.
        trial = trials.get(run_id)
        if trial and trial[2] not in ("FAILED", "STAGE_1_SIGNAL") and trial[3]:
            selected_trials.add(run_id)
        elif screen and screen[1] in attempts:
            selected_attempts.add(screen[1])
        elif trial:
            selected_trials.add(run_id)

    raw = sum(in_epoch(row[1]) for row in trials.values())
    raw += sum(in_epoch(row[1]) for aid, row in attempts.items() if aid not in fixed_attempt_ids and row[2] != 'technical_preflight')
    raw += len(pending_full) + len(pending_screens)
    raw += sum(rid not in trials and candidate_in_epoch(cid) for rid, cid in bound_full.items())
    raw += sum((key not in screens or screens[key][1] not in attempts) and candidate_in_epoch(cid)
               for key, cid in bound_screens.items())
    raw += sum(candidate_in_epoch(row[1]) for row in scenarios.values())
    return SelectionAccounting(raw, selection_count, frozenset(selected_trials), frozenset(selected_attempts))
