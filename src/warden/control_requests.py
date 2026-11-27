"""Minimal canonical request-to-domain-receipt mapping (schema 9).

No scientific verdict is duplicated here. A link is inserted in the existing
scientific reservation transaction; a killed caller cannot erase that identity.
"""
from __future__ import annotations

import hashlib
import json
import time

from warden.contracts import canonical_json

CONTROL_DDL = """
CREATE TABLE IF NOT EXISTS control_requests (
    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, plan_json TEXT NOT NULL,
    actor TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS control_request_links (
    request_id TEXT PRIMARY KEY REFERENCES control_requests(request_id),
    kind TEXT NOT NULL CHECK(kind IN ('screen','trial')), operation_id TEXT NOT NULL,
    UNIQUE(kind, operation_id));
CREATE TABLE IF NOT EXISTS control_request_errors (
    request_id TEXT PRIMARY KEY REFERENCES control_requests(request_id),
    reason TEXT NOT NULL, created_at REAL NOT NULL);
""" + "\n".join(
    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
    "BEGIN SELECT RAISE(ABORT,'Append-only control identity'); END;"
    for table in ("control_requests", "control_request_links", "control_request_errors")
    for op in ("UPDATE", "DELETE")
)


def dependencies(conn, hypothesis_id):
    """Exact relevant metadata, retaining conservative global novelty invalidation."""
    queries = {
        "hypotheses": (("SELECT hypothesis_id,contract_id,status,trials_used,strategy_module,"
                       "parent_hypothesis_id,economic_mechanism FROM hypotheses ORDER BY hypothesis_id"), ()),
        "intent": ("SELECT intent_sha256,intent_json FROM research_intents WHERE hypothesis_id=?", (hypothesis_id,)),
        "reviews": ("SELECT * FROM conformance_reviews WHERE hypothesis_id=? ORDER BY rowid", (hypothesis_id,)),
        "candidates": ("SELECT candidate_id,identity_json FROM candidate_admissions ORDER BY candidate_id", ()),
        "screens": (("SELECT screen_key,state,attempt_id,data_sha256 FROM screen_receipts "
                    "WHERE hypothesis_id=? ORDER BY screen_key"), (hypothesis_id,)),
        "trials": (("SELECT run_id,state,artifact_sha256 FROM attempt_receipts "
                   "WHERE hypothesis_id=? ORDER BY run_id"), (hypothesis_id,)),
        "outcomes": (("SELECT attempt_id,hypothesis_id,contract_id,outcome,config_hash "
                     "FROM selection_attempts ORDER BY attempt_id"), ()),
        "floors": ("SELECT * FROM contract_search_state ORDER BY contract_id", ()),
        "terminal": ("SELECT * FROM hypothesis_terminal ORDER BY hypothesis_id", ()),
        "exposure": ("SELECT lineage_id,run_id,state FROM holdout_claims ORDER BY lineage_id", ()),
    }
    result = {}
    for name, (sql, args) in queries.items():
        h = hashlib.sha256()
        for row in conn.execute(sql, args):
            h.update(canonical_json(dict(row)).encode())
            h.update(b"\n")
        result[name] = h.hexdigest()
    return result


def insert(conn, request_id, fingerprint, plan, actor):
    old = conn.execute("SELECT fingerprint FROM control_requests WHERE request_id=?",
                       (request_id,)).fetchone()
    if old:
        if old[0] != fingerprint:
            raise ValueError("request-ID conflict: changed payload or actor")
        return False
    conn.execute("INSERT INTO control_requests VALUES (?,?,?,?,?)",
                 (request_id, fingerprint, canonical_json(plan), actor, time.time()))
    return True


def bind(conn, request_id, kind, operation_id):
    if request_id is not None:
        conn.execute("INSERT INTO control_request_links VALUES (?,?,?)",
                     (request_id, kind, operation_id))


def read(conn, request_id):
    row = conn.execute("SELECT * FROM control_requests WHERE request_id=?", (request_id,)).fetchone()
    if not row:
        return None
    request = dict(row)
    link = conn.execute("SELECT kind,operation_id FROM control_request_links WHERE request_id=?",
                        (request_id,)).fetchone()
    error = conn.execute("SELECT reason FROM control_request_errors WHERE request_id=?",
                         (request_id,)).fetchone()
    result = {"request_id": request_id, "fingerprint": request["fingerprint"],
              "plan": json.loads(request["plan_json"]), "actor": request["actor"],
              "state": "SUBMITTED", "operation_id": None, "kind": None,
              "recorded_verdict": None, "retry_allowed": False,
              "error": error[0] if error else None}
    if not link:
        result["state"] = "PRE_ADMISSION_REFUSED" if error else "UNLINKED_REVIEW"
        return result
    result.update(kind=link[0], operation_id=link[1])
    if link[0] == "screen":
        receipt = conn.execute(
            "SELECT r.state,r.attempt_id,a.outcome FROM screen_receipts r LEFT JOIN selection_attempts a "
            "ON a.attempt_id=r.attempt_id WHERE r.screen_key=?", (link[1],)).fetchone()
        if receipt:
            result.update(state=receipt[0], evidence_id=receipt[1], recorded_verdict=receipt[2])
    else:
        receipt = conn.execute(
            "SELECT r.state,r.artifact_sha256,t.verdict FROM attempt_receipts r "
            "LEFT JOIN trials t ON t.run_id=r.run_id WHERE r.run_id=?", (link[1],)).fetchone()
        if receipt:
            result.update(state=receipt[0], artifact_sha256=receipt[1], recorded_verdict=receipt[2])
    if not receipt or (result["state"] == "FINALIZED" and result["recorded_verdict"] is None):
        result["state"] = "EVIDENCE_DRIFT"
    return result
