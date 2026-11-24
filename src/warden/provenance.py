"""Independent dataset provenance resolution.

A file digest proves bytes, not origin: the digest is checked *against* an
independently declared manifest, and market eligibility requires the contract
and manifest to agree on the exact path and the declared provenance. Missing
data fails closed; nothing is ever substituted from another contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from warden.contracts import MARKET, EvaluationContract

MANIFEST_DIR = Path("contracts") / "datasets"
PROVENANCES = frozenset({MARKET, "synthetic"})
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


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

    def to_dict(self) -> dict:
        return asdict(self)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ineligible(contract_id: str, role: str, rel: str, reason: str) -> DatasetResolution:
    """Fail closed without touching the dataset bytes: no digest, no eligibility."""
    return DatasetResolution(contract_id, role, rel, "", "unknown", "", False, reason)


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
    return DatasetResolution(contract.contract_id, role, rel, sha, provenance, source, eligible,
                             reason or ("verified market data" if eligible else "not market eligible"))
