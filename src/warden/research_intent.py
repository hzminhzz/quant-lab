"""RG-05: immutable research plans and principal-attested independent review.

The registry transaction is the admission authority. This is application-level
governance, not authentication of a reviewer or isolation from a hostile same UID.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import fields
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from warden.codeid import code_closure
from warden.contracts import HypothesisSpec, canonical_json
from warden.novelty import NoveltyReview
from warden.resolve import DataRequirement, resolve_data_requirement
from warden.schemas import load_experiment_spec

REVIEW_POLICY = "rg05-independent-per-material-candidate-v1"
INTENT_DDL = """
CREATE TABLE IF NOT EXISTS research_intents (
    hypothesis_id TEXT PRIMARY KEY REFERENCES hypotheses(hypothesis_id),
    intent_sha256 TEXT UNIQUE NOT NULL, intent_json TEXT NOT NULL,
    approved_by TEXT NOT NULL, approved_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conformance_reviews (
    review_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, hypothesis_id TEXT NOT NULL,
    intent_sha256 TEXT NOT NULL REFERENCES research_intents(intent_sha256),
    binding_sha256 TEXT NOT NULL, review_json TEXT NOT NULL, approved_by TEXT NOT NULL,
    recorded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS conformance_candidate ON conformance_reviews(candidate_id);
CREATE TABLE IF NOT EXISTS candidate_intent_bindings (
    candidate_id TEXT PRIMARY KEY REFERENCES candidate_admissions(candidate_id),
    intent_sha256 TEXT NOT NULL REFERENCES research_intents(intent_sha256),
    review_id TEXT NOT NULL REFERENCES conformance_reviews(review_id),
    binding_sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS research_intents_no_update BEFORE UPDATE ON research_intents BEGIN SELECT RAISE(ABORT,'Append-only violation: research intent is immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_intents_no_delete BEFORE DELETE ON research_intents BEGIN SELECT RAISE(ABORT,'Append-only violation: research intent is immutable'); END;
CREATE TRIGGER IF NOT EXISTS conformance_reviews_no_update BEFORE UPDATE ON conformance_reviews BEGIN SELECT RAISE(ABORT,'Append-only violation: conformance review is immutable'); END;
CREATE TRIGGER IF NOT EXISTS conformance_reviews_no_delete BEFORE DELETE ON conformance_reviews BEGIN SELECT RAISE(ABORT,'Append-only violation: conformance review is immutable'); END;
CREATE TRIGGER IF NOT EXISTS candidate_intent_bindings_no_update BEFORE UPDATE ON candidate_intent_bindings BEGIN SELECT RAISE(ABORT,'Append-only violation: candidate intent binding is immutable'); END;
CREATE TRIGGER IF NOT EXISTS candidate_intent_bindings_no_delete BEFORE DELETE ON candidate_intent_bindings BEGIN SELECT RAISE(ABORT,'Append-only violation: candidate intent binding is immutable'); END;
""" + f"""
CREATE TRIGGER IF NOT EXISTS approved_hypothesis_no_change BEFORE UPDATE ON hypotheses
WHEN EXISTS (SELECT 1 FROM research_intents WHERE hypothesis_id=OLD.hypothesis_id)
BEGIN
    SELECT RAISE(ABORT,'Append-only violation: approved hypothesis is immutable')
    WHERE {" OR ".join(f"NEW.{field.name} IS NOT OLD.{field.name}" for field in fields(HypothesisSpec) if field.name != "strategy_module")};
    SELECT RAISE(ABORT,'Append-only violation: approved strategy module is immutable')
    WHERE NEW.strategy_module IS NOT OLD.strategy_module AND
    NEW.strategy_module IS NOT (SELECT json_extract(intent_json,'$.plan.strategy_module')
                                FROM research_intents WHERE hypothesis_id=OLD.hypothesis_id);
END;
CREATE TRIGGER IF NOT EXISTS approved_hypothesis_no_delete BEFORE DELETE ON hypotheses
WHEN EXISTS (SELECT 1 FROM research_intents WHERE hypothesis_id=OLD.hypothesis_id)
BEGIN SELECT RAISE(ABORT,'Append-only violation: approved hypothesis is immutable'); END;
"""


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class IntentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    author: str = Field(min_length=1)
    thesis: str = Field(min_length=1)
    strategy_module: str = Field(min_length=1)
    data_requirement: DataRequirement
    purpose: Literal["absolute", "incremental"]
    evaluation_criteria: str = Field(min_length=1)
    allowed_revisions: dict[str, str] = Field(default_factory=dict)
    benchmark_spec: str | None = None
    comparative_block_length: int = Field(default=5, strict=True, ge=5)
    comparative_block_reason: str | None = None

    @model_validator(mode="after")
    def validate_plan(self):
        if not all(v.strip() for v in (self.author, self.thesis, self.strategy_module, self.evaluation_criteria)):
            raise ValueError("research intent fields must not be blank")
        if any(not k.strip() or not v.strip() for k, v in self.allowed_revisions.items()):
            raise ValueError("allowed revisions require named, substantive rules")
        if (self.purpose == "incremental") != bool(self.benchmark_spec):
            raise ValueError("incremental intent requires exactly one frozen benchmark specification")
        from warden.incremental import approved_policy

        if self.purpose == "incremental":
            approved_policy(self.comparative_block_length, self.comparative_block_reason)
        elif self.comparative_block_length != 5 or self.comparative_block_reason is not None:
            raise ValueError("Comparative settings require incremental intent")
        return self


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: Literal["PASS", "REFUSED", "JUDGE_UNAVAILABLE"]
    reviewer: str = ""
    evidence: str = Field(min_length=1)
    revision: str | None = None
    predecessor_candidate_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    novelty: NoveltyReview | None = None

    @model_validator(mode="after")
    def validate_review(self):
        if not self.evidence.strip():
            raise ValueError("conformance evidence is required")
        if self.verdict != "JUDGE_UNAVAILABLE" and not self.reviewer.strip():
            raise ValueError("reviewer identity is required")
        if bool(self.revision) != bool(self.predecessor_candidate_id):
            raise ValueError("a material revision requires a named rule and predecessor")
        return self


def _safe_path(root, relative, *, under=None):
    root = Path(root).resolve()
    path = Path(relative)
    resolved = (root / path).resolve()
    if path.is_absolute() or ".." in path.parts or not resolved.is_relative_to(root):
        raise ValueError("research intent references an unsafe path")
    if under and not resolved.is_relative_to(root / under):
        raise ValueError("research intent strategy must be under strategies/")
    return resolved


def benchmark_binding(root, path, contract_id):
    if not path:
        return None
    spec = load_experiment_spec(_safe_path(root, path, under="experiments"))
    if spec.contract_id != contract_id:
        raise ValueError("benchmark contract differs from approved research intent")
    _safe_path(root, spec.strategy_module, under="strategies")
    code_sha, _ = code_closure(root, spec.strategy_module)
    return {
        "spec_path": path, "config_sha256": digest(spec.model_dump(mode="json")),
        "strategy_module": spec.strategy_module, "code_sha256": code_sha,
    }


def resolve_plan(root, contract, plan):
    """Use #116, or the exact sealed #117 artifact, never caller-supplied resolution."""
    from warden.provenance import file_sha256, manifest_path_for, resolve_dataset
    from warden.sandbox_seal import verify_sandbox_contract

    _safe_path(root, plan.strategy_module, under="strategies")
    req = plan.data_requirement
    if (req.asset_class, req.frequency, req.execution_mode) != (
        contract.asset_class, contract.frequency, contract.execution_mode
    ):
        raise ValueError("research intent DataRequirement differs from registered contract")
    sandbox = verify_sandbox_contract(root, contract)
    if sandbox:
        block = sandbox["sandbox"]
        if DataRequirement.model_validate(block["requirement"]) != req:
            raise ValueError("research intent differs from sealed sandbox requirement")
        dataset = resolve_dataset(contract, root)
        if (dataset.sha256 != contract.data_snapshot_sha256
                or dataset.provenance != contract.data_provenance):
            raise ValueError("research intent dataset is not eligible")
        resolution = {"decision": "compiled_sandbox", "contract_id": contract.contract_id,
                      "spec_hash": contract.spec_hash(), "dataset_sha256": dataset.sha256,
                      "symbols": sorted(sandbox["dataset"]["assets"]), "symbols_to_drop": [],
                      "provenance": dataset.provenance,
                      "policy_sha256": block["policy"]["sha256"],
                      "selection_group_id": contract.selection_group_id}
    else:
        report = resolve_data_requirement(req, repo_root=root, allow_superset=True)
        match = report.matched_contract
        if (not match or report.decision != "reuse_signed"
                or match["contract_id"] != contract.contract_id
                or match["spec_hash"] != contract.spec_hash()
                or match["dataset_sha256"] != contract.data_snapshot_sha256):
            raise ValueError("research intent requires the resolved contract/dataset from #116")
        resolution = {"decision": report.decision, "contract_id": match["contract_id"],
                      "spec_hash": match["spec_hash"], "dataset_sha256": match["dataset_sha256"],
                      "symbols": sorted(match["symbols"]), "provenance": match["provenance"],
                      "symbols_to_drop": sorted(report.symbols_to_drop),
                      "policy_sha256": report.policy.get("sha256"),
                      "selection_group_id": report.selection_group_id or contract.selection_group_id}
    resolution["manifest_sha256"] = file_sha256(manifest_path_for(contract.contract_id, root))
    resolution["dataset_path"] = contract.exploratory_data_path
    from warden.incremental import approved_policy

    plan_payload = plan.model_dump(mode="json")
    document = {
        "schema_version": 1, "policy": REVIEW_POLICY, "plan": plan_payload,
        "resolution": resolution,
        "benchmark": benchmark_binding(root, plan.benchmark_spec, contract.contract_id),
    }
    if plan.purpose == "incremental":
        document["comparison_policy"] = approved_policy(
            plan.comparative_block_length, plan.comparative_block_reason)
    else:
        # Preserve the digest and idempotent approval of pre-RG06 absolute plans.
        plan_payload.pop("comparative_block_length")
        plan_payload.pop("comparative_block_reason")
    return document


def require_resolution(root, contract, document):
    from warden.provenance import file_sha256, manifest_path_for
    from warden_ops.venue_policy import load_venue_policy

    expected = document["resolution"]
    if (expected["spec_hash"] != contract.spec_hash()
            or expected["dataset_sha256"] != contract.data_snapshot_sha256
            or expected["dataset_path"] != contract.exploratory_data_path
            or expected["manifest_sha256"] != file_sha256(manifest_path_for(contract.contract_id, root))):
        raise ValueError("Registered research intent contract/dataset fingerprint changed")
    if expected["policy_sha256"] and load_venue_policy(root).sha256 != expected["policy_sha256"]:
        raise ValueError("Registered research intent venue policy changed")


def require_dataset_resolution(dataset, document):
    expected = document["resolution"]
    if (dataset.sha256 != expected["dataset_sha256"]
            or dataset.provenance != expected["provenance"]
            or (expected["decision"] == "reuse_signed" and not dataset.eligible_market)):
        raise ValueError("Candidate dataset differs from approved research intent")


def _hypothesis_snapshot(conn, hypothesis_id):
    row = conn.execute("SELECT * FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown hypothesis for research intent")
    # The old registration seam may fill in an empty strategy_module at first admission.
    # The intended path is frozen separately in the plan.
    return {field.name: row[field.name] for field in fields(HypothesisSpec)
            if field.name != "strategy_module"}


def _principal(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Principal attribution is required")
    return value.strip()


def approve(conn, hypothesis_id, resolved, principal):
    principal = _principal(principal)
    snapshot = _hypothesis_snapshot(conn, hypothesis_id)
    resolution = resolved["resolution"]
    contract = conn.execute("SELECT spec_hash,data_snapshot_sha256 FROM contracts WHERE contract_id=?",
                            (snapshot["contract_id"],)).fetchone()
    if (contract is None or resolution["contract_id"] != snapshot["contract_id"]
            or tuple(contract) != (resolution["spec_hash"], resolution["dataset_sha256"])):
        raise ValueError("research intent does not match registered contract/dataset")
    group = conn.execute("SELECT selection_group_id FROM contract_governance WHERE contract_id=?",
                         (snapshot["contract_id"],)).fetchone()
    if resolution["selection_group_id"] and (not group or group[0] != resolution["selection_group_id"]):
        raise ValueError("research intent selection group differs from registered contract governance")
    module = conn.execute("SELECT strategy_module FROM hypotheses WHERE hypothesis_id=?",
                          (hypothesis_id,)).fetchone()[0]
    if module and module.removeprefix("./") != resolved["plan"]["strategy_module"]:
        raise ValueError("research intent strategy differs from registered hypothesis")
    document = {**resolved, "hypothesis": snapshot, "approved_by": principal}
    sha = digest(document)
    old = conn.execute("SELECT intent_sha256,intent_json FROM research_intents WHERE hypothesis_id=?",
                       (hypothesis_id,)).fetchone()
    if old:
        if tuple(old) != (sha, canonical_json(document)):
            raise ValueError("Registered research intent is immutable; material plan changes need a related hypothesis")
        return sha
    for table in ("candidate_admissions", "screen_receipts", "trials", "attempt_receipts", "selection_attempts"):
        if conn.execute(f"SELECT 1 FROM {table} WHERE hypothesis_id=? LIMIT 1", (hypothesis_id,)).fetchone():
            raise ValueError("Cannot approve research intent after prior attempts; register a related hypothesis")
    conn.execute("INSERT INTO research_intents VALUES (?,?,?,?,?)",
                 (hypothesis_id, sha, canonical_json(document), principal, time.time()))
    return sha


def require_intent(conn, hypothesis_id, intent_sha, contract_id=None):
    row = conn.execute("SELECT intent_sha256,intent_json FROM research_intents WHERE hypothesis_id=?",
                       (hypothesis_id,)).fetchone()
    if not row or row[0] != intent_sha:
        raise ValueError("Missing or changed approved research intent")
    document = json.loads(row[1])
    if (digest(document) != row[0] or document["schema_version"] != 1 or document["policy"] != REVIEW_POLICY
            or document["hypothesis"] != _hypothesis_snapshot(conn, hypothesis_id)):
        raise ValueError("Registered research intent integrity drift")
    if contract_id and document["resolution"]["contract_id"] != contract_id:
        raise ValueError("research intent contract drift")
    group_id = document["resolution"]["selection_group_id"]
    if group_id:
        group = conn.execute("SELECT selection_group_id FROM contract_governance WHERE contract_id=?",
                             (document["resolution"]["contract_id"],)).fetchone()
        if not group or group[0] != group_id:
            raise ValueError("research intent selection group drift")
    from warden.incremental import require_policy

    require_policy(document)
    return document


def binding(conn, candidate, robustness_plan, intent_sha):
    from warden.robustness import base_plan

    document = require_intent(conn, candidate.hypothesis_id, intent_sha, candidate.contract_id)
    from warden.incremental import require_plan

    require_plan(robustness_plan, document)
    resolution = document["resolution"]
    if (candidate.contract_hash != resolution["spec_hash"]
            or candidate.dataset_sha256 != resolution["dataset_sha256"]
            or candidate.strategy_module != document["plan"]["strategy_module"]):
        raise ValueError("Candidate contract/dataset/module differs from research intent")
    symbols = json.loads(candidate.effective_parameters_json).get("target_symbols")
    required = set(resolution["symbols"]) - set(resolution["symbols_to_drop"])
    if symbols is not None and set(symbols) != required:
        raise ValueError("Candidate universe differs from research intent")
    if resolution["symbols_to_drop"] and symbols is None:
        raise ValueError("Candidate must filter the approved superset universe")
    value = {"intent_sha256": intent_sha, "candidate_id": candidate.candidate_id,
             "candidate": json.loads(candidate.payload),
             "robustness_plan": robustness_plan if robustness_plan is not None else base_plan(),
             "benchmark": document["benchmark"]}
    return {**value, "binding_sha256": digest(value)}


def record_review(conn, candidate, robustness_plan, intent_sha, review, principal, *, prior_memory=None):
    principal = _principal(principal)
    document = require_intent(conn, candidate.hypothesis_id, intent_sha, candidate.contract_id)
    frozen = binding(conn, candidate, robustness_plan, intent_sha)
    if review.binding_sha256 != frozen["binding_sha256"]:
        raise ValueError("Conformance review fingerprint differs from frozen candidate")
    if review.verdict == "PASS" and review.reviewer.strip().casefold() == document["plan"]["author"].strip().casefold():
        raise ValueError("Conformance reviewer must be independent of strategy author")
    if review.revision and review.revision not in document["plan"]["allowed_revisions"]:
        raise ValueError("Material revision is outside the approved research intent")
    payload = {**review.model_dump(mode="json"), "binding": frozen, "approved_by": principal}
    if prior_memory is not None:
        payload["prior_memory"] = prior_memory
    review_id = digest(payload)
    old = conn.execute("SELECT review_json FROM conformance_reviews WHERE review_id=?", (review_id,)).fetchone()
    if old:
        if old[0] != canonical_json(payload):
            raise ValueError("Conformance review digest collision")
        return review_id
    if conn.execute("SELECT 1 FROM candidate_admissions WHERE candidate_id=?", (candidate.candidate_id,)).fetchone():
        raise ValueError("Conformance review cannot be replaced after scored admission")
    conn.execute("INSERT INTO conformance_reviews VALUES (?,?,?,?,?,?,?,?)",
                 (review_id, candidate.candidate_id, candidate.hypothesis_id, intent_sha,
                  frozen["binding_sha256"], canonical_json(payload), principal, time.time()))
    return review_id


def require_review(conn, candidate, robustness_plan, intent_sha):
    frozen = binding(conn, candidate, robustness_plan, intent_sha)
    row = conn.execute(
        "SELECT review_id,review_json,binding_sha256 FROM conformance_reviews "
        "WHERE candidate_id=? AND intent_sha256=? ORDER BY rowid DESC LIMIT 1",
        (candidate.candidate_id, intent_sha)).fetchone()
    if row is None:
        raise ValueError("Independent conformance review required before scored evaluation")
    payload = json.loads(row[1])
    if (digest(payload) != row[0] or row[2] != frozen["binding_sha256"]
            or payload["binding"] != frozen or payload["verdict"] != "PASS"):
        raise ValueError("Independent conformance review refused, unavailable or fingerprint drift")
    existing = conn.execute("SELECT intent_sha256,review_id,binding_sha256 FROM candidate_intent_bindings "
                            "WHERE candidate_id=?", (candidate.candidate_id,)).fetchone()
    expected = (intent_sha, row[0], frozen["binding_sha256"])
    if existing:
        if tuple(existing) != expected:
            raise ValueError("Frozen candidate intent binding changed")
        return expected
    if conn.execute("SELECT 1 FROM candidate_admissions WHERE candidate_id=?", (candidate.candidate_id,)).fetchone():
        raise ValueError("Historical candidate has no pre-outcome intent binding; review required")
    previous = conn.execute("SELECT candidate_id FROM candidate_admissions WHERE hypothesis_id=?",
                            (candidate.hypothesis_id,)).fetchall()
    if previous:
        if not payload["revision"] or payload["predecessor_candidate_id"] not in {r[0] for r in previous}:
            raise ValueError("Material revision requires approved attribution to an admitted predecessor")
    elif payload["revision"]:
        raise ValueError("First candidate must be the approved baseline, not a revision")
    return expected


def require_trial_binding(conn, run_id):
    from warden.candidate_admission import CandidateIdentity

    row = conn.execute(
        "SELECT c.identity_json,b.intent_sha256,p.plan_json "
        "FROM candidate_operations o JOIN candidate_admissions c ON c.candidate_id=o.candidate_id "
        "JOIN candidate_intent_bindings b ON b.candidate_id=c.candidate_id "
        "JOIN robustness_plans p ON p.candidate_id=c.candidate_id "
        "WHERE o.kind='trial' AND o.operation_id=?", (run_id,)).fetchone()
    if not row:
        raise ValueError("Qualification requires a pre-outcome research intent and conformance binding")
    candidate = CandidateIdentity(**json.loads(row[0]))
    trial = conn.execute("SELECT config_json,strategy_code_sha256,data_snapshot_sha256,contract_id FROM trials WHERE run_id=?", (run_id,)).fetchone()
    if not trial or (canonical_json(json.loads(trial[0] or "{}")), trial[1], trial[2], trial[3]) != (
        candidate.parameters_json, candidate.strategy_code_sha256, candidate.dataset_sha256, candidate.contract_id
    ):
        raise ValueError("Recorded trial differs from reviewed candidate configuration")
    require_review(conn, candidate, json.loads(row[2]), row[1])
    return candidate, require_intent(conn, candidate.hypothesis_id, row[1], candidate.contract_id)


def bind_review(conn, candidate_id, intent_link):
    """Persist the checked link in the same transaction as candidate admission."""
    conn.execute("INSERT INTO candidate_intent_bindings VALUES (?,?,?,?)",
                 (candidate_id, *intent_link))


def require_qualification_conformance(registry, run_id, contract, code_sha, root):
    candidate, intent = registry.require_trial_conformance(run_id)
    from warden.novelty import require_admission

    with registry._connect() as conn:
        link = conn.execute("SELECT review_id FROM candidate_intent_bindings WHERE candidate_id=?",
                            (candidate.candidate_id,)).fetchone()
        require_admission(conn, candidate, registry._novelty_members(conn, candidate),
                          root, link[0], existing=True)
    if (candidate.contract_hash != contract.spec_hash()
            or candidate.dataset_sha256 != contract.data_snapshot_sha256
            or candidate.strategy_code_sha256 != code_sha):
        raise ValueError("Qualification candidate differs from approved research intent")
    require_resolution(root, contract, intent)
    if benchmark_binding(root, intent["plan"]["benchmark_spec"], contract.contract_id) != intent["benchmark"]:
        raise ValueError("Frozen research intent benchmark fingerprint changed")
    if intent["plan"]["purpose"] == "incremental":
        from warden.incremental import require_trial_pair

        with registry._connect() as conn:
            require_trial_pair(conn, registry.db_path.parent / "returns", run_id,
                               verdict="CANDIDATE_FOR_QUALIFICATION")
    return candidate, intent
