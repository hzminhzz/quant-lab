"""Read-only recovered-data admission: staged bytes are not market authority.

Contract pins and manifest digests prove byte identity, not vendor origin. A
separate human/authority-held HMAC verifier can attest origin without giving
agents any signer or access to protected holdouts.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path

from warden.contracts import EvaluationContract


@dataclass(frozen=True)
class Admission:
    state: str
    promotable: bool
    reason: str
    contract_id: str
    data_sha256: str = ""
    source_id: str = ""


def classify_recovered_dataset(contract: EvaluationContract, repo_root: Path,
                               attestation: Path | None = None,
                               verifier_key: bytes | None = None) -> Admission:
    """No data writes, holdout reads, self-signed attestations or auto-promotion."""
    root = Path(repo_root).resolve()
    rel = Path(contract.exploratory_data_path)
    if rel.is_absolute() or ".." in rel.parts or "holdout" in str(rel).lower():
        return Admission("BLOCKED", False, "protected or noncanonical source path", contract.contract_id)
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return Admission("BLOCKED", False, "dataset unavailable or escapes repository", contract.contract_id)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    origin = ""
    signed = False
    if attestation is not None and attestation.is_file() and verifier_key:
        try:
            data = json.loads(attestation.read_text(encoding="utf-8"))
            origin = str(data.get("source_id") or "")
            signature = str(data.pop("signature"))
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
            expected = hmac.new(verifier_key, canonical, hashlib.sha256).hexdigest()
            signed = (data.get("schema") == 1 and data.get("contract_id") == contract.contract_id
                      and data.get("data_sha256") == sha and bool(origin)
                      and len(str(data.get("raw_source_sha256", ""))) == 64
                      and hmac.compare_digest(signature, expected))
        except (OSError, TypeError, ValueError, KeyError):
            signed = False
    if not signed:
        return Admission("STAGED", False, "independent source-origin attestation absent or unverified",
                         contract.contract_id, sha)
    manifest = root / "contracts" / "datasets" / f"{contract.contract_id}.json"
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
        exact = (record.get("path") == str(rel) and record.get("sha256") == sha and
                 record.get("provenance") == "verified_market" and
                 contract.data_snapshot_sha256 == sha and contract.data_provenance == "verified_market")
    except (OSError, TypeError, ValueError):
        exact = False
    if not exact:
        return Admission("PROVENANCE_VERIFIED", False, "source attested but human contract/manifest not admitted",
                         contract.contract_id, sha, origin)
    return Admission("CONTRACT_ADMITTED", True, "independent origin and signed-source identity match pinned contract",
                     contract.contract_id, sha, origin)
