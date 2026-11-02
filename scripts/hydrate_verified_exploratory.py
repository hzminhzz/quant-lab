"""Hydrate only original SHA-pinned exploratory fixtures in isolated worktrees.

Never read protected holdouts, edit frozen contracts, or change canonical
source data. Conflicting versions sharing one data path are reported
rather than silently declared valid. All copies are atomic and write-once.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from scripts.prepare_four_xs4h_snapshot import publish_once, sha256
from warden.contracts import load_contract

PRIMARY = Path("/home/quant/dev/quant-lab")
# The already-audited Syncster panels were prepared in PR #40's isolated
# worktree; the primary checkout still has only original 1m sources.
SYNCSTER = Path("/home/quant/.devspace/worktrees/quant-lab-4ac4b629")
OUTPUT = Path(".workspace/campaigns/four-xs4h/exploratory-hydration.json")


def hydrate(roots: list[Path]) -> dict:
    findings = []
    for contract_file in sorted(Path("contracts").glob("*.yaml")):
        contract = load_contract(contract_file)
        relative = Path(contract.exploratory_data_path)
        if not relative.parts[:2] == ("data", "exploratory"):
            raise ValueError(f"Contract exploratory path escapes required boundary: {relative}")
        expected = contract.data_snapshot_sha256
        if not expected:
            findings.append({"contract": contract.contract_id, "status": "NO_PIN"})
            continue
        dest = Path(relative)
        found = None
        for root in roots:
            candidate = root / relative
            if candidate.is_symlink():
                raise ValueError(f"Symlinked source refused: {candidate}")
            if candidate.is_file() and sha256(candidate) == expected:
                found = candidate
                break
        if found is None:
            findings.append({
                "contract": contract.contract_id, "path": str(relative),
                "status": "SOURCE_UNAVAILABLE_OR_SHA_MISMATCH",
            })
            continue
        if dest.is_file() and sha256(dest) != expected:
            findings.append({
                "contract": contract.contract_id, "path": str(relative),
                "status": "LOCAL_SHA_CONFLICT",
            })
            continue
        publish_once(found, dest, expected)
        findings.append({
            "contract": contract.contract_id, "path": str(relative),
            "status": "VERIFIED",
            "sha256": expected,
            "source": str(found),
        })
    return {
        "scope": "exploratory data only; no holdout paths accessed",
        "dataset_contracts": findings,
        "verified_count": sum(x["status"] == "VERIFIED" for x in findings),
        "not_verified_count": sum(x["status"] != "VERIFIED" for x in findings),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", type=Path, default=PRIMARY)
    parser.add_argument("--syncster", type=Path, default=SYNCSTER)
    args = parser.parse_args()
    if OUTPUT.exists():
        raise FileExistsError(f"Immutable hydration evidence already recorded: {OUTPUT}")
    result = hydrate([args.primary, args.syncster])
    data = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=OUTPUT.parent, delete=False) as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
        temporary = Path(fh.name)
    try:
        os.link(temporary, OUTPUT)  # write-once atomic publication
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({
        "verified": result["verified_count"],
        "not_verified": result["not_verified_count"],
        "evidence_sha256": hashlib.sha256(data).hexdigest(),
        "exceptions": [
            x for x in result["dataset_contracts"] if x["status"] != "VERIFIED"
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
