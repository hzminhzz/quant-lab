"""RG-08: principal-owned holdout scope and one-shot frozen qualification batches.

A claim spends exposure even if execution never starts. Legacy claims are captured
without inventing scope; a signed operator policy must reconcile every unknown.
No operation here reads holdout data or performance.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from warden.contracts import canonical_json, utc

POLICY_PATH = "contracts/holdout_exposure_policy.yaml"
VERSION = "rg08-exposure-v1"
TABLES = ("holdout_batches", "holdout_exposures", "holdout_legacy_scopes",
          "holdout_batch_reads", "holdout_batch_outcomes")
EXPOSURE_DDL = """
CREATE TABLE IF NOT EXISTS holdout_batches (
    batch_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
    document_json TEXT NOT NULL, approval_json TEXT NOT NULL, approved_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS holdout_exposures (
    exposure_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source TEXT NOT NULL
      CHECK(source IN ('legacy','external','batch')),
    record_json TEXT NOT NULL, recorded_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS holdout_legacy_scopes (
    exposure_id TEXT PRIMARY KEY REFERENCES holdout_exposures(exposure_id),
    scope_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holdout_batch_reads (
    batch_id TEXT PRIMARY KEY REFERENCES holdout_batches(batch_id),
    read_started_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS holdout_batch_outcomes (
    batch_id TEXT PRIMARY KEY REFERENCES holdout_batches(batch_id),
    result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL, finalized_at REAL NOT NULL
);
""" + "\n".join(
    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
    f"BEGIN SELECT RAISE(ABORT,'Append-only violation: {table}'); END;"
    for table in TABLES for op in ("UPDATE", "DELETE")
)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Scope(_Strict):
    # Signed metadata must cover the entire readable artifact, not just positions
    # or the selected evaluation slice. For legacy history only, a digest can be
    # unknown when the full temporal/universe scope is independently attested.
    dataset_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    start: datetime
    end: datetime
    symbols: dict[str, str] = Field(min_length=1)

    @field_validator("start", "end")
    @classmethod
    def timezone(cls, value):
        return utc(value)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.end < self.start:
            raise ValueError("Invalid exposure window")
        if any(not k.strip() or not v.strip() or k != k.strip().upper()
               or v != v.strip().upper() for k, v in self.symbols.items()):
            raise ValueError("Exposure aliases and canonical underlyings must be nonempty uppercase identities")
        return self

    def payload(self):
        return self.model_dump(mode="json")


class HistoricalScope(_Strict):
    scope: Scope
    evidence: str = Field(min_length=20)
    hypotheses: list[str] = Field(default_factory=list)
    lineages: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def known_identities(self):
        if not all(x.strip() for x in self.hypotheses + self.lineages):
            raise ValueError("Uncertain historical relationship identity")
        return self


class ExternalExposure(HistoricalScope):
    hypotheses: list[str] = Field(min_length=1)
    lineages: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def identities(self):
        if not all(x.strip() for x in self.hypotheses + self.lineages):
            raise ValueError("Uncertain historical relationship identity")
        return self


class Policy(_Strict):
    version: Literal["rg08-exposure-v1"]
    history_attestation: str = Field(min_length=20)
    windows: dict[str, Scope] = Field(min_length=1)
    legacy_scopes: dict[str, HistoricalScope] = Field(default_factory=dict)
    external_exposures: dict[str, ExternalExposure] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent_aliases(self):
        aliases = {}
        scopes = list(self.windows.values()) + [
            x.scope for x in (*self.legacy_scopes.values(), *self.external_exposures.values())
        ]
        for scope in scopes:
            for alias, underlying in scope.symbols.items():
                if aliases.setdefault(alias, underlying) != underlying:
                    raise ValueError("Ambiguous canonical economic underlying mapping")
        return self


class Approval(_Strict):
    batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    independence: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def substantive(self):
        if any(len(v.strip()) < 20 for v in self.independence.values()):
            raise ValueError("Explicit substantive independence review required")
        return self


def load_policy(root, contract, intent):
    """Verify only signed metadata before claim. Never hash the holdout itself."""
    from warden.provenance import manifest_path_for
    from warden_ops.integrity import check_worktree, read_signed_manifest_entry

    root = Path(root).resolve()
    manifest_file = root / ".warden_manifest.sha256"
    manifest_bytes = manifest_file.read_bytes() if manifest_file.is_file() else b""
    if not check_worktree(root):
        raise ValueError("Holdout qualification requires intact signed worktree")
    raw = read_signed_manifest_entry(root, POLICY_PATH)
    policy = Policy.model_validate(yaml.safe_load(raw))
    manifest_path = manifest_path_for(contract.contract_id, root)
    manifest = json.loads(read_signed_manifest_entry(root, manifest_path.relative_to(root).as_posix()))
    scope = policy.windows.get(contract.contract_id)
    if (not scope or not scope.dataset_sha256
            or manifest.get("holdout_path") != contract.holdout_data_path
            or manifest.get("holdout_sha256") != scope.dataset_sha256
            or manifest.get("provenance") != "verified_market"
            or scope.start > contract.holdout_start or scope.end < contract.holdout_end):
        raise ValueError("Missing or mismatched signed holdout scope/pin")
    if not set(intent["resolution"]["symbols"]) <= set(scope.symbols):
        raise ValueError("Signed scope does not cover the approved universe")
    path = (root / contract.holdout_data_path).resolve()
    if not path.is_relative_to(root / "data" / "holdout") or path != root / contract.holdout_data_path:
        raise ValueError("Unsafe holdout artifact path")
    if manifest_file.read_bytes() != manifest_bytes:
        raise ValueError("Signed manifest changed during scope verification")
    return policy, scope, hashlib.sha256(raw).hexdigest(), hashlib.sha256(manifest_bytes).hexdigest()


def _put_exposure(conn, eid, run_id, source, record):
    old = conn.execute("SELECT record_json FROM holdout_exposures WHERE exposure_id=?", (eid,)).fetchone()
    if old:
        if old[0] != canonical_json(record):
            raise ValueError(f"Historical exposure identity drift: {eid}")
        return
    conn.execute("INSERT INTO holdout_exposures VALUES (?,?,?,?,?)",
                 (eid, run_id, source, canonical_json(record), time.time()))


def capture_legacy(conn):
    """Idempotently preserve every legacy claim and qualification (including reuse).

    Scope is deliberately unknown until a principal supplies signed evidence.
    Historical rows and their old status/timestamps are not rewritten.
    """
    rows = conn.execute(
        "SELECT h.run_id,h.lineage_id,COALESCE(t.hypothesis_id,q.hypothesis_id,''),"
        "COALESCE(t.contract_id,q.contract_id,'') "
        "FROM holdout_claims h LEFT JOIN trials t ON t.run_id=h.run_id "
        "LEFT JOIN qualifications q ON q.run_id=h.run_id "
        "UNION SELECT run_id,lineage_id,COALESCE(hypothesis_id,''),COALESCE(contract_id,'') FROM qualifications"
    ).fetchall()
    groups = {}
    for run, lineage, hyp, cid in rows:
        if conn.execute("SELECT 1 FROM holdout_exposures WHERE run_id=? AND source='batch'", (run,)).fetchone():
            continue
        record = groups.setdefault(run, {"run_id": run, "lineages": set(),
                                         "hypotheses": set(), "contracts": set()})
        for field, value in (("lineages", lineage), ("hypotheses", hyp), ("contracts", cid)):
            if value:
                record[field].add(value)
    for run, group in groups.items():
        eid = "legacy:" + run
        record = {k: sorted(v) if isinstance(v, set) else v for k, v in group.items()}
        old = conn.execute("SELECT record_json FROM holdout_exposures WHERE exposure_id=?", (eid,)).fetchone()
        if old:
            if json.loads(old[0]) != record:
                raise ValueError(f"Historical exposure identity changed: {eid}")
        else:
            _put_exposure(conn, eid, run, "legacy", record)


def _history(conn, policy, *, persist):
    """Resolve legacy evidence and retain external exposures even across policy updates."""
    records = {}
    for eid, run, source, raw in conn.execute(
            "SELECT exposure_id,run_id,source,record_json FROM holdout_exposures ORDER BY exposure_id"):
        record = json.loads(raw)
        if source == "legacy":
            bound = conn.execute("SELECT scope_json FROM holdout_legacy_scopes WHERE exposure_id=?", (eid,)).fetchone()
            proposed = policy.legacy_scopes.get(run)
            if not proposed and not bound:
                raise ValueError(f"Unknown historical exposure scope: {eid}")
            if proposed:
                scope = proposed.scope
                for cid in record["contracts"]:
                    window = conn.execute("SELECT holdout_start,holdout_end FROM contracts WHERE contract_id=?", (cid,)).fetchone()
                    if window and (scope.start > utc(window[0]) or scope.end < utc(window[1])):
                        raise ValueError(f"Historical scope understates recorded contract window: {eid}")
                value = proposed.model_dump(mode="json")
                if bound and json.loads(bound[0]) != value:
                    raise ValueError(f"Previously reconciled historical scope changed: {eid}")
                if persist and not bound:
                    conn.execute("INSERT INTO holdout_legacy_scopes VALUES (?,?)", (eid, canonical_json(value)))
            else:
                value = json.loads(bound[0])
            # Signed reconciliation may supply missing identities, never erase
            # the actual lineage/hypothesis identities captured from the ledger.
            for field in ("lineages", "hypotheses"):
                value[field] = sorted(set(record[field]) | set(value[field]))
            if not value["lineages"]:
                raise ValueError(f"Unknown historical exposure lineage: {eid}")
            record.update(value)
        elif source == "batch":
            outcome = conn.execute("SELECT result_json FROM holdout_batch_outcomes WHERE batch_id=?", (eid,)).fetchone()
            if outcome and json.loads(outcome[0]).get("scope_uncertain"):
                raise ValueError(f"Unresolved holdout scope incident: {eid}")
        records[eid] = record
    for key, history in policy.external_exposures.items():
        eid = "external:" + key
        record = history.model_dump(mode="json")
        record["hypotheses"] = sorted(set(record["hypotheses"]))
        record["lineages"] = sorted(set(record["lineages"]))
        if eid in records and records[eid] != record:
            raise ValueError(f"Historical external exposure changed: {eid}")
        if persist:
            _put_exposure(conn, eid, "", "external", record)
        records[eid] = record
    return records


def overlaps(left, right):
    """Inclusive UTC windows (matches gateway), canonical underlying or exact file."""
    a, b = Scope.model_validate(left), Scope.model_validate(right)
    if a.dataset_sha256 and a.dataset_sha256 == b.dataset_sha256:
        return True
    if a.end < b.start or b.end < a.start:
        return False
    return bool(set(a.symbols.values()) & set(b.symbols.values()) or set(a.symbols) & set(b.symbols))


def _document(reg, conn, prepared, root, *, persist=False):
    if not prepared["binding"] or not prepared["audit"] or not prepared["proof"]:
        raise ValueError("Frozen qualification requires governed intent, audit and causal proof")
    candidate, intent = prepared["binding"]
    contract = prepared["contract"]
    run_id = prepared["trial"]["run_id"]
    policy, scope, policy_sha, manifest_sha = load_policy(root, contract, intent)
    lineage, members = reg._lineage(conn, candidate.hypothesis_id,
                                    module=candidate.strategy_module,
                                    code_content_sha256=candidate.code_content_sha256)
    prior = _history(conn, policy, persist=persist)
    overlap = {}
    aliases = dict(scope.symbols)
    for eid, record in prior.items():
        for alias, underlying in Scope.model_validate(record["scope"]).symbols.items():
            if aliases.setdefault(alias, underlying) != underlying:
                raise ValueError(f"Canonical underlying identity drift against {eid}")
        old_members = set(record["hypotheses"]) | set(record["lineages"])
        for hid in tuple(old_members):
            if conn.execute("SELECT 1 FROM hypotheses WHERE hypothesis_id=?", (hid,)).fetchone():
                old_members.update(reg._lineage(conn, hid)[1])
        if old_members & (members | {lineage}):
            raise PermissionError(f"Same or related lineage already spent: {eid}")
        if overlaps(scope.payload(), record["scope"]):
            overlap[eid] = record
    return {
        "version": VERSION, "run_id": run_id, "candidate_id": candidate.candidate_id,
        "candidate": json.loads(candidate.payload), "intent_sha256": digest(intent),
        "benchmark": intent["benchmark"], "comparison_policy": intent.get("comparison_policy"),
        "lineage": lineage, "members": sorted(members),
        "scope": scope.payload(), "scope_id": digest(scope.payload()),
        "policy_sha256": policy_sha, "manifest_sha256": manifest_sha,
        "audit": prepared["audit"], "proof": prepared["proof"],
        "execution_policy": prepared["policy_kw"], "holdout_refit_mode": prepared["governance"].holdout_refit_mode,
        "holdout_path": contract.holdout_data_path,
        "evaluation_start": contract.holdout_start.isoformat(), "evaluation_end": contract.holdout_end.isoformat(),
        "overlapping_exposures": overlap,
    }


def describe(reg, prepared, root):
    with reg._connect() as conn:
        conn.execute("BEGIN")
        doc = _document(reg, conn, prepared, root)
    return {**doc, "batch_sha256": digest(doc)}


def authorize(reg, prepared, root, approval, principal):
    if not isinstance(principal, str) or not principal.strip():
        raise ValueError("Principal attribution required")
    approval = Approval.model_validate(approval)
    with reg._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        capture_legacy(conn)
        doc = _document(reg, conn, prepared, root, persist=True)
        batch_id = digest(doc)
        if approval.batch_sha256 != batch_id:
            raise ValueError("Frozen holdout batch changed; describe and review again")
        if set(approval.independence) != set(doc["overlapping_exposures"]):
            raise ValueError("Every overlapping exposure requires explicit independence review")
        approved = {**approval.model_dump(mode="json"), "principal": principal.strip()}
        old = conn.execute("SELECT batch_id,document_json,approval_json FROM holdout_batches WHERE run_id=?",
                           (doc["run_id"],)).fetchone()
        if old:
            if tuple(old) != (batch_id, canonical_json(doc), canonical_json(approved)):
                raise ValueError("Frozen holdout batch authorization is immutable")
        else:
            conn.execute("INSERT INTO holdout_batches VALUES (?,?,?,?,?)",
                         (batch_id, doc["run_id"], canonical_json(doc), canonical_json(approved), time.time()))
    return {"batch_id": batch_id, "document": doc, "approval": approved}


def claim(reg, prepared, root, batch_id, *, holdout_override=False):
    """Check the current frozen batch, then atomically spend scope and lineage."""
    if not batch_id:
        raise ValueError("Qualification requires an authorized frozen paired batch (absolute or incremental)")
    if holdout_override:
        raise ValueError("Governed qualification cannot substitute a holdout artifact")
    with reg._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT run_id,document_json,approval_json FROM holdout_batches WHERE batch_id=?",
                           (batch_id,)).fetchone()
        if not row or row[0] != prepared["trial"]["run_id"]:
            raise ValueError("Unknown or mismatched authorized frozen batch")
        if conn.execute("SELECT 1 FROM holdout_exposures WHERE exposure_id=?", (batch_id,)).fetchone():
            raise PermissionError("Frozen holdout batch already spent; never replay")
        capture_legacy(conn)
        doc = _document(reg, conn, prepared, root, persist=True)
        if digest(doc) != batch_id or row[1] != canonical_json(doc):
            raise ValueError("Frozen holdout batch or exposure history changed")
        approval = json.loads(row[2])
        if (approval.get("batch_sha256") != batch_id
                or set(approval["independence"]) != set(doc["overlapping_exposures"])):
            raise ValueError("Frozen batch independence approval changed")
        candidate, _ = prepared["binding"]
        # A direct writer may have closed the hypothesis since preflight.
        status = conn.execute("SELECT status FROM hypotheses WHERE hypothesis_id=?", (candidate.hypothesis_id,)).fetchone()
        if not status or status[0] != "ACTIVE":
            raise ValueError("Qualification hypothesis is no longer active")
        audit = conn.execute("SELECT audit_id,verdict FROM audit_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                             (doc["run_id"],)).fetchone()
        if not audit or tuple(audit) != (doc["audit"]["audit_id"], "APPROVED"):
            raise ValueError("Frozen holdout audit changed before claim")
        if not reg.effective_eligibility(doc["run_id"])["eligible"]:
            raise ValueError("Frozen candidate eligibility changed before claim")
        if reg.causal_proof_for_run(doc["run_id"], root) != doc["proof"]:
            raise ValueError("Frozen causal proof changed before claim")
        from warden.codeid import code_closure
        from warden.research_intent import benchmark_binding

        if (code_closure(root, candidate.strategy_module)[0] != candidate.strategy_code_sha256
                or benchmark_binding(root, prepared["binding"][1]["plan"]["benchmark_spec"],
                                     candidate.contract_id) != doc["benchmark"]):
            raise ValueError("Frozen candidate or benchmark code changed before claim")
        if conn.execute("SELECT 1 FROM holdout_claims WHERE lineage_id=?", (doc["lineage"],)).fetchone():
            raise PermissionError("Lineage already spent")
        _put_exposure(conn, batch_id, doc["run_id"], "batch", {
            "run_id": doc["run_id"], "lineages": [doc["lineage"]],
            "hypotheses": doc["members"], "scope": doc["scope"],
        })
        conn.execute("INSERT INTO holdout_claims (lineage_id,run_id,state,claimed_at) VALUES (?,?,?,?)",
                     (doc["lineage"], doc["run_id"], "CLAIMED", time.time()))
    return doc


def mark_read(reg, batch_id, doc):
    with reg._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT document_json FROM holdout_batches WHERE batch_id=?", (batch_id,)).fetchone()
        if (not row or row[0] != canonical_json(doc) or digest(doc) != batch_id
                or not conn.execute("SELECT 1 FROM holdout_exposures WHERE exposure_id=? AND source='batch'", (batch_id,)).fetchone()):
            raise PermissionError("No matching durable batch claim")
        if conn.execute("SELECT 1 FROM holdout_batch_reads WHERE batch_id=?", (batch_id,)).fetchone():
            raise PermissionError("Frozen holdout read already started; never replay")
        updated = conn.execute("UPDATE holdout_claims SET state='READ_STARTED',read_started_at=? "
                               "WHERE lineage_id=? AND run_id=? AND state='CLAIMED'",
                               (time.time(), doc["lineage"], doc["run_id"])).rowcount
        if updated != 1:
            raise PermissionError("No fresh exclusive holdout claim")
        conn.execute("INSERT INTO holdout_batch_reads VALUES (?,?)", (batch_id, time.time()))


def finalize(conn, batch_id, result):
    if not conn.execute("SELECT 1 FROM holdout_batch_reads WHERE batch_id=?", (batch_id,)).fetchone():
        raise ValueError("Holdout result requires durable read-start")
    row = conn.execute("SELECT run_id,document_json FROM holdout_batches WHERE batch_id=?", (batch_id,)).fetchone()
    doc = json.loads(row[1])
    if digest(doc) != batch_id or result["run_id"] != row[0] or result["batch_id"] != batch_id:
        raise ValueError("Holdout outcome differs from frozen batch")
    if not result.get("scope_uncertain"):
        pair = result["comparison"]
        if (result["holdout_sha256"] != doc["scope"]["dataset_sha256"]
                or bool(pair) != bool(doc["benchmark"])
                or (pair and (pair["phase"] != "holdout" or pair["policy"] != doc["comparison_policy"]
                              or pair["candidate_id"] != doc["candidate_id"]
                              or pair["basis"]["dataset_sha256"] != result["holdout_sha256"]
                              or pair["benchmark"]["code_sha256"] != doc["benchmark"]["code_sha256"]
                              or pair["benchmark"]["config_sha256"] != doc["benchmark"]["config_sha256"]
                              or (result["promoted"] and pair["status"] != "PASS")))):
            raise ValueError("Holdout comparison evidence differs from frozen batch")
    conn.execute("INSERT INTO holdout_batch_outcomes VALUES (?,?,?,?)",
                 (batch_id, canonical_json(result), digest(result), time.time()))


class ScopeIncident(ValueError):
    """Read detected an artifact outside its attested scope; never assume it unseen."""


def record_scope_incident(reg, batch_id, run_id, reason):
    with reg._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        finalize(conn, batch_id, {"batch_id": batch_id, "run_id": run_id,
                                 "scope_uncertain": True, "reason": reason})


def status(reg, batch_id):
    with reg._connect() as conn:
        row = conn.execute("SELECT document_json,approval_json FROM holdout_batches WHERE batch_id=?", (batch_id,)).fetchone()
        if not row:
            raise ValueError("Unknown frozen holdout batch")
        doc = json.loads(row[0])
        if digest(doc) != batch_id:
            raise ValueError("Frozen holdout batch integrity drift")
        spent = conn.execute("SELECT 1 FROM holdout_exposures WHERE exposure_id=?", (batch_id,)).fetchone()
        read = conn.execute("SELECT 1 FROM holdout_batch_reads WHERE batch_id=?", (batch_id,)).fetchone()
        outcome = conn.execute("SELECT result_json,result_sha256 FROM holdout_batch_outcomes WHERE batch_id=?", (batch_id,)).fetchone()
    result = json.loads(outcome[0]) if outcome else None
    if outcome and digest(result) != outcome[1]:
        raise ValueError("Frozen holdout outcome integrity drift")
    state = "FINALIZED" if outcome else "READ_STARTED" if read else "CLAIMED" if spent else "AUTHORIZED"
    if result and result.get("scope_uncertain"):
        state = "SCOPE_UNCERTAIN"
    elif result:
        try:
            from warden import incremental
            from warden_evidence.return_artifacts import read_return_stream

            root = reg.db_path.parent / "returns"
            if result["comparison"]:
                incremental.verify_artifacts(result["comparison"], root)
            else:
                art = result["artifacts"]
                read_return_stream(art["candidate_path"], root, expected_sha256=art["candidate_sha256"])
        except (OSError, ValueError, KeyError):
            state = "EVIDENCE_DRIFT"
    return {"batch_id": batch_id, "state": state, "spent": bool(spent),
            "document": doc, "approval": json.loads(row[1]), "result": result}
