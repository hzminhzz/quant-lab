"""Independent dataset provenance resolution.

A file digest proves bytes, not origin: the digest is checked *against* an
independently declared manifest, and market eligibility requires the contract
and manifest to agree on the exact path and the declared provenance. Missing
data fails closed; nothing is ever substituted from another contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from warden.contracts import (
    CURRENT_SPEC_VERSION,
    GOVERNANCE_KEYS,
    MARKET,
    ContractPolicy,
    ContractValidationError,
    EvaluationContract,
)

MANIFEST_DIR = Path("contracts") / "datasets"
#: Human-held attestations of *origin* (see ``warden_evidence.data_admission``).  Absent
#: directory means "no attestation exists", which is a state, not an error.
ATTESTATION_DIR = Path("contracts") / "attestations"
#: HMAC key an operator holds.  Never read from the repository, never defaulted, never
#: printed: without it every attestation is unverifiable and market admission stays closed.
ADMISSION_KEY_ENV = "WARDEN_ADMISSION_HMAC_KEY"
PROVENANCES = frozenset({MARKET, "synthetic"})
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
#: Lifecycle metadata a dataset manifest may declare.  ``spec_version`` is
#: deliberately excluded: a manifest annotates a contract, it never re-versions it.
GOVERNANCE_MANIFEST_KEYS = tuple(key for key in GOVERNANCE_KEYS if key != "spec_version")


class DatasetProvenanceError(ValueError):
    """The contract's dataset is absent or unresolvable. Never substituted."""


@dataclass(frozen=True)
class DatasetResolution:
    contract_id: str
    role: str
    path: str
    sha256: str
    provenance: str
    source: str
    eligible_market: bool
    reason: str
    #: Signed-origin admission state for a market dataset (``warden_evidence.data_admission``).
    #: ``""`` for a non-market or synthetic resolution; ``legacy_grandfathered`` for a v1
    #: contract, whose historical evidence #106 must not re-adjudicate.
    admission_state: str = ""
    admission_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_contract_governance(
    contract: EvaluationContract, repo_root: Path = Path(".")
) -> ContractPolicy:
    """Resolve a contract's evidence tier and execution policy, fail closed.

    Authority order is fixed:

    1. a v2 contract owns its governance block in YAML, and that block is part
       of ``spec_hash()`` — the manifest may not override it;
    2. a legacy v1 contract may read ``evidence_tier``/``selection_group_id``
       (plus the remaining governance keys) from its dataset manifest, which is
       lifecycle metadata and therefore not part of the digest;
    3. otherwise the legacy default (``native_venue``, no leverage) applies and
       every historical behaviour is preserved.

    A declared ``dataset_manifest_sha256`` must match the manifest bytes, so a
    tier cannot be moved between manifests without a visible pin change.
    """
    declared = contract.governance
    if declared.declares_governance:
        return declared
    manifest = Path(repo_root) / MANIFEST_DIR / f"{contract.contract_id}.json"
    if not manifest.is_file():
        return declared
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ContractValidationError(f"{manifest.name}: dataset manifest must be a JSON object")
    if record.get("contract_id") not in (None, contract.contract_id):
        raise ContractValidationError(
            f"{manifest.name}: manifest contract_id {record.get('contract_id')!r} != {contract.contract_id!r}")
    lifecycle = {key: record[key] for key in GOVERNANCE_MANIFEST_KEYS if key in record}
    if not lifecycle:
        return declared
    policy = ContractPolicy.from_mapping({"spec_version": declared.spec_version, **lifecycle})
    if policy.dataset_manifest_sha256:
        actual = hashlib.sha256(manifest.read_bytes()).hexdigest()
        if actual != policy.dataset_manifest_sha256:
            raise ContractValidationError(
                f"{manifest.name}: declared dataset_manifest_sha256 {policy.dataset_manifest_sha256} "
                f"!= manifest digest {actual}")
    return policy


def _ineligible(contract_id: str, role: str, rel: str, reason: str) -> DatasetResolution:
    """Fail closed without touching the dataset bytes: no digest, no eligibility."""
    return DatasetResolution(contract_id, role, rel, "", "unknown", "", False, reason)


LEGACY_GRANDFATHERED = "legacy_grandfathered"


def resolve_admission(
    contract: EvaluationContract, repo_root: Path | str = Path("."), role: str = "exploratory"
) -> tuple[str, bool, str]:
    """Signed-origin admission layered on a digest-verified market resolution (#106).

    The digest proves *bytes*; it never proves *origin*.  ``warden_evidence.data_admission``
    was written for exactly that distinction and, until #106, nothing called it.  It is
    invoked here, in the provenance path, for the case it was written for — an exploratory
    market dataset — and its verdict is authoritative for **v2** contracts, which own their
    governance block and are the shape every new dataset must declare.

    A **v1** contract is deliberately not re-adjudicated: its trials, manifests and
    ``spec_hash`` are historical evidence, and #106 must not invalidate them retroactively.
    It reports ``legacy_grandfathered`` so the fact is visible rather than implied.

    Returns ``(state, admitted, reason)``; ``admitted`` is only ever ``False`` for a v2
    contract whose signed-origin admission is incomplete.  No key or attestation is ever
    invented: absent ``$WARDEN_ADMISSION_HMAC_KEY`` the module reports ``STAGED``.
    """
    from warden_evidence.data_admission import classify_recovered_dataset

    policy = contract.governance
    if int(getattr(policy, "spec_version", 1) or 1) < CURRENT_SPEC_VERSION:
        return (
            LEGACY_GRANDFATHERED,
            True,
            "v1 contract: provenance evidence inherited, not re-adjudicated by #106",
        )
    if role != "exploratory":
        return ("", True, "holdout reads are fenced by the manifest pin, not by admission")
    attestation = Path(repo_root) / ATTESTATION_DIR / f"{contract.contract_id}.json"
    raw_key = os.environ.get(ADMISSION_KEY_ENV, "").strip()
    try:
        key = bytes.fromhex(raw_key) if raw_key else None
    except ValueError:
        key = None
    admission = classify_recovered_dataset(
        contract,
        Path(repo_root),
        attestation if attestation.is_file() else None,
        key,
    )
    return (admission.state, bool(admission.promotable), admission.reason)


def resolve_dataset(contract: EvaluationContract, repo_root: Path = Path("."), role: str = "exploratory") -> DatasetResolution:
    """Resolve the contract's own dataset; verify declared provenance, digest and exact path."""
    rel = getattr(contract, f"{role}_data_path")
    manifest = Path(repo_root) / MANIFEST_DIR / f"{contract.contract_id}.json"
    record: dict | None = None
    if role == "holdout":
        # A holdout read is only ever authorized against an explicit pin declared in the
        # dataset manifest, checked before any holdout byte is opened, statted or hashed.
        if not isinstance(rel, str) or not rel.strip():
            return _ineligible(contract.contract_id, role, rel if isinstance(rel, str) else "",
                               "holdout pin missing: contract declares no holdout_data_path")
        if not manifest.is_file():
            return _ineligible(contract.contract_id, role, rel,
                               f"missing dataset manifest '{manifest.relative_to(repo_root)}'")
        record = json.loads(manifest.read_text(encoding="utf-8"))
        pinned_path, pinned_sha = record.get("holdout_path"), record.get("holdout_sha256")
        if not isinstance(pinned_path, str) or not pinned_path.strip():
            return _ineligible(contract.contract_id, role, rel,
                               "holdout pin missing: manifest declares no 'holdout_path'")
        if not isinstance(pinned_sha, str) or not SHA256_HEX.fullmatch(pinned_sha):
            return _ineligible(contract.contract_id, role, rel,
                               "holdout pin missing or invalid: manifest 'holdout_sha256' must be 64 lowercase hex characters")
        if pinned_path != rel:
            return _ineligible(contract.contract_id, role, rel,
                               f"manifest holdout path {pinned_path!r} != contract path {rel!r}")
    path = Path(repo_root) / rel
    if not path.is_file():
        raise DatasetProvenanceError(
            f"missing {role} dataset for {contract.contract_id}: '{rel}' (no cross-contract fallback)")
    sha = file_sha256(path)
    reason, provenance, source = "", "unknown", ""
    if record is None and not manifest.is_file():
        reason = f"missing dataset manifest '{manifest.relative_to(repo_root)}'"
    else:
        record = record if record is not None else json.loads(manifest.read_text(encoding="utf-8"))
        provenance, source = str(record.get("provenance", "unknown")), str(record.get("source", ""))
        exp_path = record.get(f"{role}_path") or (record.get("path") if role == "exploratory" else None)
        exp_sha = record.get(f"{role}_sha256") or (record.get("sha256") if role == "exploratory" else None)
        if provenance not in PROVENANCES:
            reason = f"unknown dataset provenance {provenance!r}"
        elif exp_path is not None and exp_path != rel:
            reason = f"manifest path {exp_path!r} != contract path {rel!r}"
        elif exp_sha is not None and exp_sha != sha:
            reason = "dataset digest differs from manifest (file changed or substituted)"
        elif contract.data_provenance != provenance:
            reason = f"contract provenance {contract.data_provenance!r} != manifest provenance {provenance!r}"
        elif role == "exploratory" and contract.data_snapshot_sha256 != sha:
            reason = "contract data_snapshot_sha256 differs from dataset digest"
    eligible = not reason and provenance == MARKET
    admission_state, admission_reason = "", ""
    if eligible:
        # Digest agreement is not origin: a v2 market dataset must also carry the
        # human-signed admission verdict.  A v1 contract reports ``legacy_grandfathered``
        # and keeps the eligibility it has always had (#106 does not invalidate history).
        admission_state, admitted, admission_reason = resolve_admission(contract, repo_root, role)
        if not admitted:
            eligible = False
            reason = (
                f"market admission refused ({admission_state}): {admission_reason}"
            )
    return DatasetResolution(contract.contract_id, role, rel, sha, provenance, source, eligible,
                             reason or ("verified market data" if eligible else "not market eligible"),
                             admission_state, admission_reason)
