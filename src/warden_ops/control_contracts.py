"""Versioned public control vocabulary; no evaluator imports or effects.

Presentation states never replace recorded Warden verdicts. Capability entries
describe supported effects and the existing owner, including unavailable gates.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Resources(StrictModel):
    cpu: int = Field(default=1, ge=1, le=4)
    memory_mb: int = Field(default=2048, ge=64, le=16384)
    io_mb: int = Field(default=4096, ge=1, le=1048576)
    wall_seconds: int = Field(default=600, ge=1, le=86400)


class ResearchAction(StrictModel):
    kind: Literal["screen", "backtest"]
    spec_path: str = Field(min_length=1, max_length=512)

    @field_validator("spec_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value
                or value.startswith("-") or "holdout" in value.lower()
                or path.suffix not in {".yaml", ".yml"} or str(path) != value):
            raise ValueError("expected normalized relative unsealed specification path")
        return value


class Revision(StrictModel):
    source: str = Field(min_length=1, max_length=512)
    sha256: Digest


class ActionPlan(StrictModel):
    schema_version: Literal[1] = 1
    action: ResearchAction
    revisions: list[Revision] = Field(min_length=1, max_length=512)
    created_at: float
    expires_at: float
    resources: Resources = Field(default_factory=Resources)
    authority: Literal["existing Warden admission"] = "existing Warden admission"
    charge: Literal["existing candidate and selection accounting"] = "existing candidate and selection accounting"
    sealed_access: Literal[False] = False
    plan_sha256: Digest


class Observe(StrictModel):
    schema_version: Literal[1]
    operation: Literal["observe"]
    objective: str = Field(default="", max_length=1000)
    hypothesis_id: Identifier | None = None
    cursor: str | None = Field(default=None, max_length=512)
    since: Digest | None = None
    limit: int = Field(default=10, ge=1, le=25)


class Inspect(StrictModel):
    schema_version: Literal[1]
    operation: Literal["inspect"]
    view: Literal["capabilities", "findings"]
    hypothesis_id: Identifier | None = None
    cursor: str | None = Field(default=None, max_length=512)
    limit: int = Field(default=10, ge=1, le=25)


class Prepare(StrictModel):
    schema_version: Literal[1]
    operation: Literal["prepare"]
    action: ResearchAction
    resources: Resources = Field(default_factory=Resources)


class Act(StrictModel):
    schema_version: Literal[1]
    operation: Literal["act"]
    request_id: Identifier
    plan: ActionPlan


class Receipt(StrictModel):
    schema_version: Literal[1]
    operation: Literal["receipt"]
    request_id: Identifier


class Reconcile(StrictModel):
    schema_version: Literal[1]
    operation: Literal["reconcile"]
    request_id: Identifier
    mode: Literal["mark_interrupted"]


Request = Annotated[Observe | Inspect | Prepare | Act | Receipt | Reconcile,
                    Field(discriminator="operation")]
REQUEST = TypeAdapter(Request)


class Blocker(StrictModel):
    classification: Literal[
        "TECHNICAL_PRE_ADMISSION", "BUSY", "STALE_STATE", "AUTHORIZATION_REQUIRED",
        "SCIENTIFIC_REJECT", "INCONCLUSIVE", "BUDGET_EXHAUSTED", "UNCERTAIN_EFFECT",
        "INTEGRITY_INCIDENT", "EXTERNAL_DEPENDENCY",
    ]
    scope: str = Field(max_length=512)
    predicate: str = Field(max_length=2000)
    owner: str = Field(max_length=200)
    remedy: str = Field(max_length=2000)
    retry: str = Field(max_length=300)


class Result(StrictModel):
    schema_version: Literal[1] = 1
    ok: bool
    status: Literal["OK", "REFUSED", "RUNNING", "COMPLETE", "UNCERTAIN"]
    source: Literal["warden.control"] = "warden.control"
    observed_at: float
    request_id: str | None = None
    recorded_verdict: str | None = None
    current_eligibility: Literal["UNKNOWN", "ELIGIBLE", "INELIGIBLE"] = "UNKNOWN"
    value: dict[str, JsonValue] = Field(default_factory=dict)
    blockers: list[Blocker] = Field(default_factory=list, max_length=50)
    next_actions: list[str] = Field(default_factory=list, max_length=20)


def parse_request(payload: dict) -> Request:
    return REQUEST.validate_python(payload)


def capabilities() -> dict:
    """One current-to-target effect catalog. Availability is implementation, not permission."""
    rows = []
    for name, owner, write, authority, receipt, available in [
        ("observe", "warden_ops.readonly", False, "authorized metadata",
         "attributed snapshot", True),
        ("inspect", "warden_ops.readonly", False, "authorized evidence",
         "bounded projection", True),
        ("prepare", "warden_ops.control", False, "authorized metadata",
         "content-addressed plan; no reservation", True),
        ("screen", "warden.runner.ExperimentRunner.screen", True, "existing Warden admission",
         "screen_receipts and selection_attempts", True),
        ("backtest", "warden.runner.ExperimentRunner.run", True, "existing Warden admission",
         "attempt_receipts and trials", True),
        ("receipt", "warden.registry.ResearchRegistry", False, "authorized metadata",
         "original domain receipt", True),
        ("reconcile", "warden.registry.ResearchRegistry", True, "explicit interrupted disposition",
         "existing review-required receipt; no refund", True),
        ("qualify", "warden.runner.ExperimentRunner.qualify", True, "separate principal and batch approval",
         "holdout claim; information cannot be refunded", False),
        ("release", "warden_ops.release_authority", False, "independently authenticated exact decision",
         "protected authority receipt; installed integration required", False),
        ("handoff", "warden_ops.checkpoint", False, "authorized operational context",
         "append-only operational checkpoint", True),
    ]:
        science = name in {"screen", "backtest"}
        rows.append({
            "name": name, "owner": owner, "available": available,
            "scientific_write": write, "authority": authority,
            "sealed_access": name == "qualify",
            "charge": "existing candidate and selection accounting" if science else "none; existing owner rules",
            "receipt": receipt,
            "cancellation": "retain admitted or uncertain effects" if write else "no scientific effect",
            "retry": "lookup original receipt; never repeat scoring" if science else "refresh or inspect original receipt",
        })
    return {"schema_version": 1, "actions": rows, "request_schema": REQUEST.json_schema(),
            "result_schema": Result.model_json_schema(),
            "compatibility": "additive interface; existing CLI verdicts, exits and sentinels retained"}
