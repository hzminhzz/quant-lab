"""Independent dataset provenance resolution.

A file digest proves bytes, not origin: the digest is checked *against* an
independently declared manifest, and market eligibility requires the contract
and manifest to agree on the exact path and the declared provenance. Missing
data fails closed; nothing is ever substituted from another contract.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from warden.contracts import EvaluationContract

MANIFEST_DIR = Path("contracts") / "datasets"
MARKET = "verified_market"
PROVENANCES = frozenset({MARKET, "synthetic"})


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


def resolve_dataset(contract: EvaluationContract, repo_root: Path = Path("."), role: str = "exploratory") -> DatasetResolution:
    """Resolve the contract's own dataset; verify declared provenance, digest and exact path."""
    rel = getattr(contract, f"{role}_data_path")
    path = Path(repo_root) / rel
    if not path.is_file():
        raise DatasetProvenanceError(
            f"missing {role} dataset for {contract.contract_id}: '{rel}' (no cross-contract fallback)")
    sha = file_sha256(path)
    reason, provenance, source = "", "unknown", ""
    manifest = Path(repo_root) / MANIFEST_DIR / f"{contract.contract_id}.json"
    if not manifest.is_file():
        reason = f"missing dataset manifest '{manifest.relative_to(repo_root)}'"
    else:
        record = json.loads(manifest.read_text(encoding="utf-8"))
        provenance, source = str(record.get("provenance", "unknown")), str(record.get("source", ""))
        if provenance not in PROVENANCES:
            reason = f"unknown dataset provenance {provenance!r}"
        elif record.get("path") != rel:
            reason = f"manifest path {record.get('path')!r} != contract path {rel!r}"
        elif record.get("sha256") != sha:
            reason = "dataset digest differs from manifest (file changed or substituted)"
        elif contract.data_provenance != provenance:
            reason = f"contract provenance {contract.data_provenance!r} != manifest provenance {provenance!r}"
        elif contract.data_snapshot_sha256 != sha:
            reason = "contract data_snapshot_sha256 differs from dataset digest"
    eligible = not reason and provenance == MARKET
    return DatasetResolution(contract.contract_id, role, rel, sha, provenance, source, eligible,
                             reason or ("verified market data" if eligible else "not market eligible"))
