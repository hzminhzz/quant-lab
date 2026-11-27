"""RG-07: prospective, human-attested novelty over the existing outcome memory.

Exact fingerprints and declared lineages are deterministic checks. Economic
similarity is a review judgment, never an automated semantic classifier.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from warden.contracts import canonical_json

LESSONS = Path(".workspace/memory/LESSONS.md")


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    relation: Literal["RELATED", "DISTINCT", "UNRESOLVED"]
    mechanism: str = Field(min_length=1)
    signal: str = Field(min_length=1)
    universe: str = Field(min_length=1)
    outcome_disposition: str = Field(min_length=1)


class NoveltyReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lessons_disposition: str = Field(min_length=1)
    search_disposition: str = Field(min_length=1)
    relationships: dict[str, Relationship] = Field(default_factory=dict)

    @model_validator(mode="after")
    def nonblank(self):
        values = [self.lessons_disposition, self.search_disposition]
        for relation in self.relationships.values():
            values.extend((relation.mechanism, relation.signal, relation.universe,
                           relation.outcome_disposition))
        if not all(len(v.strip()) >= 20 for v in values):
            raise ValueError("novelty review requires substantive dispositions (at least 20 characters)")
        return self


def _normal(value):
    return " ".join(str(value or "").casefold().split())


def _module(value):
    return str(value or "").removeprefix("./")


def _files(root):
    from warden_ops.research_outcome_memory import INDEX, load_index

    root = Path(root).resolve()
    lesson_path, index_path = root / LESSONS, root / INDEX
    try:
        lessons = lesson_path.read_bytes()
        if not lessons.strip():
            raise ValueError("Empty prior lessons")
        index_bytes = index_path.read_bytes()
        index = load_index(index_path)
        if index_path.read_bytes() != index_bytes:
            raise ValueError("Outcome index changed during review")
        text = lessons.decode("utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"novelty prior lessons/outcome memory unavailable: {exc}") from exc
    return {
        "lessons_path": str(LESSONS),
        "lessons_sha256": hashlib.sha256(lessons).hexdigest(),
        "lesson_headings": re.findall(r"^#{1,6}\s+(.+)$", text, flags=re.MULTILINE),
        "index_path": str(INDEX),
        "index_sha256": hashlib.sha256(index_bytes).hexdigest(),
        "branch_outcomes": sorted(index["outcomes"], key=lambda x: (x["hypothesis_id"], x["contract_id"])),
        "branch_blockers": sorted(index["original_unattempted"], key=lambda x: x["hypothesis_id"]),
        "branch_sources": index["source_refs"],
    }


def exact_duplicates(conn, candidate):
    """Refuse same executable, effective config and market identity even under a renamed ID."""
    # Legacy trials with an actual code closure can still prove an exact
    # duplicate under the same epoch/contract/data and declared configuration.
    for row in conn.execute(
        "SELECT run_id,config_json FROM trials WHERE strategy_code_sha256=? "
        "AND contract_id=? AND data_snapshot_sha256=? AND simulation_epoch=? "
        "AND NOT EXISTS (SELECT 1 FROM candidate_operations o WHERE o.kind='trial' "
        "AND o.operation_id=trials.run_id)",
        (candidate.strategy_code_sha256, candidate.contract_id,
         candidate.dataset_sha256, candidate.simulation_epoch),
    ):
        if canonical_json(json.loads(row[1] or "{}")) == candidate.parameters_json:
            raise ValueError(f"Exact duplicate historical trial: {row[0]}; never rerun")
    for row in conn.execute(
        "SELECT candidate_id,identity_json FROM candidate_admissions "
        "WHERE code_content_sha256=? AND candidate_id!=?",
        (candidate.code_content_sha256, candidate.candidate_id),
    ):
        identity = json.loads(row[1])
        fields = ("contract_hash", "dataset_sha256", "effective_parameters_json", "simulation_epoch")
        if all(identity[k] == getattr(candidate, k) for k in fields):
            raise ValueError(f"Exact duplicate candidate: existing evidence {row[0]}; never rerun")


def snapshot(conn, candidate, members, root):
    """Read the canonical catalog and historical outcome references in the admission snapshot.

    Keep every prior outcome visible to the reviewer. Deterministic hints mark
    required dispositions, but reviewers may identify additional related ideas.
    No current candidate performance is computed.
    """
    files = _files(root)
    hid = candidate.hypothesis_id
    catalog = [dict(r) for r in conn.execute(
        "SELECT hypothesis_id,contract_id,title,strategy_family,economic_mechanism,"
        "counterparty_or_friction,parent_hypothesis_id,strategy_module,status "
        "FROM hypotheses ORDER BY hypothesis_id")]
    own = next(r for r in catalog if r["hypothesis_id"] == hid)
    # The first admission may fill the current module. Normalize it here.
    own["strategy_module"] = candidate.strategy_module
    known = {r["hypothesis_id"] for r in catalog}
    must_share = set(members) - {hid}
    required = set(must_share)
    for row in catalog:
        other = row["hypothesis_id"]
        if other == hid:
            continue
        if _module(row["strategy_module"]) == candidate.strategy_module:
            required.add(other)
            must_share.add(other)
        if _normal(row["economic_mechanism"]) == _normal(own["economic_mechanism"]):
            required.add(other)
    own_intent = conn.execute("SELECT intent_json FROM research_intents WHERE hypothesis_id=?", (hid,)).fetchone()
    own_thesis = json.loads(own_intent[0])["plan"]["thesis"]
    intents = []
    for row in conn.execute("SELECT hypothesis_id,intent_json FROM research_intents ORDER BY hypothesis_id"):
        doc = json.loads(row[1])
        intents.append({"hypothesis_id": row[0], "thesis": doc["plan"]["thesis"],
                        "data_requirement": doc["plan"]["data_requirement"],
                        "strategy_module": doc["plan"]["strategy_module"]})
        if row[0] != hid:
            if _module(doc["plan"]["strategy_module"]) == candidate.strategy_module:
                required.add(row[0])
                must_share.add(row[0])
            if _normal(doc["plan"]["thesis"]) == _normal(own_thesis):
                required.add(row[0])
    outcomes = [dict(r) for r in conn.execute(
        "SELECT hypothesis_id,contract_id,run_id AS evidence_id,strategy_module,"
        "verdict AS outcome,stage_reached AS stage FROM trials "
        "UNION ALL SELECT hypothesis_id,contract_id,attempt_id,strategy_module,outcome,stage "
        "FROM selection_attempts WHERE kind!='technical_preflight' AND kind!='param_grid' "
        "UNION ALL SELECT hypothesis_id,contract_id,run_id,'',state,'INTERRUPTED' "
        "FROM attempt_receipts WHERE state='INTERRUPTED_REVIEW' "
        "UNION ALL SELECT hypothesis_id,contract_id,screen_key,strategy_module,state,'INTERRUPTED' "
        "FROM screen_receipts WHERE state='INTERRUPTED_REVIEW' "
        "ORDER BY hypothesis_id,evidence_id")]
    terminals = [dict(r) for r in conn.execute(
        "SELECT hypothesis_id,terminal_id,status,reason FROM hypothesis_terminal "
        "ORDER BY hypothesis_id")]
    historical_decisions = [dict(r) for r in conn.execute(
        "SELECT t.hypothesis_id,t.contract_id,a.audit_id AS evidence_id,a.verdict AS outcome,'AUDIT' AS stage "
        "FROM audit_evidence a JOIN trials t ON t.run_id=a.run_id "
        "UNION ALL SELECT t.hypothesis_id,t.contract_id,d.disposition_id,d.disposition,'DISPOSITION' "
        "FROM trial_dispositions d JOIN trials t ON t.run_id=d.run_id "
        "UNION ALL SELECT t.hypothesis_id,t.contract_id,h.run_id,h.state,'HOLDOUT_EXPOSURE' "
        "FROM holdout_claims h JOIN trials t ON t.run_id=h.run_id "
        "ORDER BY hypothesis_id,evidence_id")]
    if any(r["hypothesis_id"] == hid for r in outcomes + historical_decisions):
        required.add(hid)
        must_share.add(hid)
    for row in outcomes:
        if row["hypothesis_id"] != hid and _module(row["strategy_module"]) == candidate.strategy_module:
            required.add(row["hypothesis_id"])
            must_share.add(row["hypothesis_id"])
    branch_ids = {r["hypothesis_id"] for r in files["branch_outcomes"]}
    for row in files["branch_outcomes"]:
        other = row["hypothesis_id"]
        if other == hid:
            raise ValueError(f"Historical branch outcome locked: PR #{row['source_pr']} {row['evidence_id']}")
        if _module(row["strategy_module"]) == candidate.strategy_module or other in must_share:
            required.add(other)
            must_share.add(other)
    if any(r["hypothesis_id"] == hid for r in files["branch_blockers"]):
        raise ValueError("Original branch data blocker unresolved; review existing evidence")
    value = {
        "version": 1, "candidate_id": candidate.candidate_id, **files,
        "catalog": catalog, "registered_intents": intents, "outcomes": outcomes,
        "terminal_outcomes": terminals, "historical_decisions": historical_decisions,
        "required_relationships": sorted(required),
        "must_share_lineage": sorted(must_share),
        "known_hypotheses": sorted(known | branch_ids),
    }
    return {**value, "snapshot_sha256": digest(value)}


def validate_review(review, memory, members, candidate):
    if review is None:
        raise ValueError("PASS requires a principal-attested novelty and prior lessons review")
    if review.snapshot_sha256 != memory["snapshot_sha256"]:
        raise ValueError("novelty memory changed; describe candidate and review again")
    relationships = review.relationships
    if not set(memory["required_relationships"]) <= set(relationships):
        raise ValueError("Unresolved novelty: prior related outcomes require explicit disposition")
    if not set(relationships) <= set(memory["known_hypotheses"]):
        raise ValueError("novelty review references unknown prior research")
    native = {(r["hypothesis_id"], r["contract_id"], r["evidence_id"]) for r in memory["outcomes"]}
    for hid, rel in relationships.items():
        if rel.relation == "UNRESOLVED":
            raise ValueError(f"Unresolved novelty for {hid}")
        if hid in memory["must_share_lineage"] and rel.relation != "RELATED":
            raise ValueError(f"Related prior identity {hid} cannot be declared distinct")
        if rel.relation == "RELATED":
            if hid not in members:
                raise ValueError(f"Related research {hid} requires registered shared lineage")
            # Branch-only results have no proven canonical debt. Refuse rather
            # than invent an import or count an unlinked record as consumed.
            for row in memory["branch_outcomes"]:
                if row["hypothesis_id"] == hid and (hid, row["contract_id"], row["evidence_id"]) not in native:
                    raise ValueError(f"Related branch outcome {row['evidence_id']} requires canonical reconciliation")
    if candidate.hypothesis_id in relationships and relationships[candidate.hypothesis_id].relation != "RELATED":
        raise ValueError("A revision must retain its own prior negative evidence")


def require_admission(conn, candidate, members, root, review_id, *, existing):
    exact_duplicates(conn, candidate)
    row = conn.execute("SELECT review_json FROM conformance_reviews WHERE review_id=?", (review_id,)).fetchone()
    payload = json.loads(row[0]) if row else {}
    if not payload.get("novelty") or not payload.get("prior_memory"):
        raise ValueError("Missing pre-outcome novelty review; historical evidence remains read-only")
    prior = payload["prior_memory"]
    if digest({k: v for k, v in prior.items() if k != "snapshot_sha256"}) != prior["snapshot_sha256"]:
        raise ValueError("novelty memory integrity drift")
    review = NoveltyReview.model_validate(payload["novelty"])
    if existing:
        # Screen -> full reuses the original candidate's pre-outcome review.
        validate_review(review, prior, members, candidate)
        # Advancing an unchanged candidate does not require a new review of its
        # own observed result. Newly discovered branch-locked outcomes are
        # nevertheless never ignored merely because a screen ran earlier.
        files = _files(root)
        current_branches = [r for r in files["branch_outcomes"]
                            if r["hypothesis_id"] in members
                            or _module(r["strategy_module"]) == candidate.strategy_module]
        prior_branches = prior["branch_outcomes"]
        if any(r not in prior_branches for r in current_branches):
            raise ValueError("New related branch outcome requires reconciliation; never replay")
        if any(r["hypothesis_id"] == candidate.hypothesis_id for r in files["branch_blockers"]):
            raise ValueError("Original branch data blocker unresolved; review existing evidence")
        return
    current = snapshot(conn, candidate, members, root)
    validate_review(review, current, members, candidate)
