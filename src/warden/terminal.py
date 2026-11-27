"""Append-only terminal hypothesis disposition in the canonical registry."""
from __future__ import annotations

import hashlib
import json
import time

from warden.contracts import canonical_json

TERMINAL_DDL = """
CREATE TABLE IF NOT EXISTS hypothesis_terminal (
    hypothesis_id TEXT PRIMARY KEY REFERENCES hypotheses(hypothesis_id),
    terminal_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL
      CHECK(status IN ('REJECTED','INCONCLUSIVE','EXHAUSTED','EARLY_STOPPED')),
    reason TEXT NOT NULL, evidence_json TEXT NOT NULL,
    attributed_to TEXT NOT NULL, recorded_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS hypothesis_terminal_no_update BEFORE UPDATE ON hypothesis_terminal BEGIN SELECT RAISE(ABORT,'Append-only violation: terminal hypothesis is immutable'); END;
CREATE TRIGGER IF NOT EXISTS hypothesis_terminal_no_delete BEFORE DELETE ON hypothesis_terminal BEGIN SELECT RAISE(ABORT,'Append-only violation: terminal hypothesis is immutable'); END;
CREATE TRIGGER IF NOT EXISTS terminal_hypothesis_no_reactivate BEFORE UPDATE ON hypotheses
WHEN EXISTS (SELECT 1 FROM hypothesis_terminal WHERE hypothesis_id=OLD.hypothesis_id)
AND NEW.status IS NOT (SELECT status FROM hypothesis_terminal WHERE hypothesis_id=OLD.hypothesis_id)
BEGIN SELECT RAISE(ABORT,'Governance violation: terminal hypothesis cannot be reactivated'); END;
CREATE TRIGGER IF NOT EXISTS terminal_hypothesis_no_delete BEFORE DELETE ON hypotheses
WHEN EXISTS (SELECT 1 FROM hypothesis_terminal WHERE hypothesis_id=OLD.hypothesis_id)
BEGIN SELECT RAISE(ABORT,'Governance violation: terminal hypothesis cannot be deleted'); END;
"""
STATUSES = {"REJECTED", "INCONCLUSIVE", "EXHAUSTED", "EARLY_STOPPED"}


def read(conn, hypothesis_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='hypothesis_terminal' AND type='table'").fetchone():
        return None
    row = conn.execute("SELECT * FROM hypothesis_terminal WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["evidence"] = json.loads(result.pop("evidence_json"))
    body = {k: result[k] for k in ("hypothesis_id", "status", "reason", "evidence", "attributed_to")}
    status = conn.execute("SELECT status FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
    if (hashlib.sha256(canonical_json(body).encode()).hexdigest() != result["terminal_id"]
            or not status or status[0] != result["status"]):
        raise ValueError("Terminal hypothesis evidence integrity drift")
    return result


def _evidence(conn, hid):
    return {
        "trials": [dict(r) for r in conn.execute(
            "SELECT t.run_id,t.verdict,t.stage_reached,r.state,t.diagnostics_json "
            "FROM trials t LEFT JOIN attempt_receipts r ON r.run_id=t.run_id "
            "WHERE t.hypothesis_id=? ORDER BY t.run_id", (hid,))],
        "screens": [dict(r) for r in conn.execute(
            "SELECT r.screen_key,r.state,r.attempt_id,a.outcome FROM screen_receipts r "
            "LEFT JOIN selection_attempts a ON a.attempt_id=r.attempt_id "
            "WHERE r.hypothesis_id=? ORDER BY r.screen_key", (hid,))],
        "receipts": [dict(r) for r in conn.execute(
            "SELECT run_id,state,reason FROM attempt_receipts WHERE hypothesis_id=? ORDER BY run_id", (hid,))],
        "robustness": [dict(r) for r in conn.execute(
            "SELECT r.scenario_id,r.run_id,r.scenario_key,COALESCE(o.state,'RESERVED') AS state "
            "FROM robustness_receipts r JOIN attempt_receipts a ON a.run_id=r.run_id "
            "LEFT JOIN robustness_outcomes o ON o.scenario_id=r.scenario_id "
            "WHERE a.hypothesis_id=? ORDER BY r.scenario_id", (hid,))],
    }


def _budget(registry, conn, hid):
    from warden.registry import LINEAGE_CAP, PER_HYPOTHESIS_CAP

    lineage, members = registry._lineage(conn, hid)
    usage = {member: registry._candidate_usage(conn, member)["used"] for member in sorted(members)}
    return {"used": usage[hid], "cap": PER_HYPOTHESIS_CAP, "lineage_id": lineage,
            "lineage_members": sorted(members), "lineage_usage": usage,
            "lineage_used": sum(usage.values()), "lineage_cap": LINEAGE_CAP}


def _reserved(evidence):
    return any(r["state"] == "RESERVED" for r in
               evidence["receipts"] + evidence["screens"] + evidence["robustness"])


def _advanceable(conn, hid, evidence):
    if any(t["verdict"] == "CANDIDATE_FOR_QUALIFICATION" for t in evidence["trials"]):
        return True
    return bool(conn.execute(
        "SELECT 1 FROM screen_receipts s JOIN selection_attempts a ON a.attempt_id=s.attempt_id "
        "JOIN candidate_operations o ON o.kind='screen' AND o.operation_id=s.screen_key "
        "WHERE s.hypothesis_id=? AND s.state='FINALIZED' AND a.outcome='PASS' "
        "AND NOT EXISTS (SELECT 1 FROM candidate_operations t WHERE t.candidate_id=o.candidate_id "
        "AND t.kind='trial')", (hid,)).fetchone())


def close(registry, conn, hid, status, reason, principal):
    if status not in STATUSES or len(reason.strip()) < 20 or not principal.strip():
        raise ValueError("Terminal disposition needs status, substantive reason and attribution")
    existing = read(conn, hid)
    if existing:
        if (existing["status"], existing["reason"], existing["attributed_to"]) == (status, reason, principal):
            return existing
        raise ValueError("Terminal hypothesis disposition is immutable")
    row = conn.execute("SELECT status FROM hypotheses WHERE hypothesis_id=?", (hid,)).fetchone()
    if not row or row[0] not in ("ACTIVE", "RETROSPECTIVE_REPLICATION_ONLY", "REJECTED"):
        raise ValueError("Hypothesis is missing or not closable")
    if row[0] == "REJECTED" and status != "REJECTED":
        raise ValueError("Historical rejected hypothesis cannot be relabeled")
    evidence = _evidence(conn, hid)
    if _reserved(evidence):
        raise ValueError("Resolve reserved/interrupted writers before closing hypothesis")
    if conn.execute(
        "SELECT 1 FROM holdout_claims h JOIN trials t ON t.run_id=h.run_id "
        "WHERE t.hypothesis_id=? AND h.state IN ('CLAIMED','READ_STARTED')", (hid,)
    ).fetchone():
        raise ValueError("Resolve the outstanding holdout claim before closing hypothesis")
    budget = _budget(registry, conn, hid)
    outcomes = {r["outcome"] for r in evidence["screens"] if r["state"] == "FINALIZED"}
    verdicts = {r["verdict"] for r in evidence["trials"] if r["state"] == "FINALIZED"}
    negative = []
    for trial in evidence["trials"]:
        if trial["state"] != "FINALIZED":
            continue
        diag = json.loads(trial["diagnostics_json"] or "{}")
        if isinstance(diag, str):
            diag = json.loads(diag)
        if not isinstance(diag, dict):
            raise ValueError("Trial diagnostics cannot support terminal disposition")  # noqa: TRY004 (invalid persisted evidence)
        if trial["verdict"] == "REJECTED_EARLY" or (
            trial["verdict"] == "REVISE" and not (
                diag.get("failed_gates") == ["incremental_comparison"]
                and diag.get("incremental_comparison", {}).get("status") == "INCONCLUSIVE"
            )
        ):
            negative.append(trial["run_id"])
    if status == "REJECTED" and not ({"REJECT"} & outcomes or negative):
        raise ValueError("Rejected disposition requires a finalized negative scientific outcome")
    if status == "INCONCLUSIVE" and not (outcomes or verdicts or evidence["receipts"] or evidence["screens"]):
        raise ValueError("Inconclusive disposition requires recorded evidence or interruption")
    if status == "EXHAUSTED":
        if budget["used"] < budget["cap"] and budget["lineage_used"] < budget["lineage_cap"]:
            raise ValueError("Hypothesis/lineage budget is not exhausted")
        if _advanceable(conn, hid, evidence):
            raise ValueError("Frozen candidate can still advance; budget is not terminal")
    # Preserve only evidence IDs and outcome metadata, not a new copy of return vectors.
    for trial in evidence["trials"]:
        trial.pop("diagnostics_json", None)
    evidence["budget"] = budget
    body = {"hypothesis_id": hid, "status": status, "reason": reason,
            "evidence": evidence, "attributed_to": principal}
    terminal_id = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    conn.execute("INSERT INTO hypothesis_terminal VALUES (?,?,?,?,?,?,?)",
                 (hid, terminal_id, status, reason, canonical_json(evidence), principal, time.time()))
    conn.execute("UPDATE hypotheses SET status=? WHERE hypothesis_id=?", (status, hid))
    return read(conn, hid)


def maybe_exhaust(registry, conn, hid):
    # Only governed hypotheses receive prospective terminal evidence. If the
    # shared lineage cap has been spent, project the same closure for siblings
    # that cannot advance, without relabeling an unfinished or qualified run.
    if not conn.execute("SELECT 1 FROM research_intents WHERE hypothesis_id=?", (hid,)).fetchone():
        return
    budget = _budget(registry, conn, hid)
    members = budget["lineage_members"] if budget["lineage_used"] >= budget["lineage_cap"] else [hid]
    for member in members:
        if budget["lineage_usage"][member] < budget["cap"] and budget["lineage_used"] < budget["lineage_cap"]:
            continue
        status = conn.execute("SELECT h.status FROM hypotheses h JOIN research_intents i "
                              "ON i.hypothesis_id=h.hypothesis_id WHERE h.hypothesis_id=?",
                              (member,)).fetchone()
        if not status or status[0] not in ("ACTIVE", "RETROSPECTIVE_REPLICATION_ONLY") or read(conn, member):
            continue
        evidence = _evidence(conn, member)
        if _reserved(evidence) or _advanceable(conn, member, evidence):
            continue
        # Failed/interrupted technical operations require a principal disposition
        # rather than automatic scientific rejection.
        if any(r["state"] != "FINALIZED" for r in
               evidence["receipts"] + evidence["screens"] + evidence["robustness"]):
            continue
        if any(r["verdict"] == "FAILED" for r in evidence["trials"]):
            continue
        close(registry, conn, member, "EXHAUSTED",
              "Candidate/lineage cap reached with no remaining advanceable candidate", "warden")
