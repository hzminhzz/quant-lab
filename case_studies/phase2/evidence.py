"""Only read-only projections from the installed appliance; no registry access."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

LAUNCHER = Path("/home/quant/dev/quant-lab-appliance/warden")


def governed_view(hypothesis_id: str = "") -> dict:
    """Explicit user-triggered observation, never imports a candidate or reads artifacts."""
    identifier = hypothesis_id.strip()
    if identifier and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identifier):
        raise ValueError("invalid registered hypothesis ID")
    request = {"schema_version": 1, "operation": "observe", "limit": 1}
    if identifier:
        request["hypothesis_id"] = identifier
    if not LAUNCHER.is_file():
        return {"status": "UNAVAILABLE", "reason": "installed appliance not found"}
    try:
        result = subprocess.run(
            [str(LAUNCHER), "control", "--request", json.dumps(request), "--json"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        payload = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"status": "UNAVAILABLE", "reason": type(exc).__name__}
    if result.returncode or not payload.get("ok"):
        return {"status": "UNAVAILABLE", "reason": payload.get("status", "refused")}
    value = payload.get("value", {})
    if value.get("scientific_writes") != 0 or value.get("evaluator_calls") != 0:
        raise RuntimeError("not a read-only observation")
    if value.get("sealed_access") is not False:
        raise RuntimeError("sealed-access status unknown")
    ledger = value.get("ledger", {})
    runtime = value.get("runtime", {})
    matching = [
        row for row in ledger.get("hypotheses", [])
        if identifier and row.get("hypothesis_id") == identifier
    ]
    return {
        "status": "READ_ONLY_OBSERVATION",
        "runtime": {key: runtime.get(key) for key in ("status", "source_commit", "release_id")},
        "ledger_schema": ledger.get("schema_version"),
        "counts": ledger.get("counts"),
        "matching_hypothesis": matching,
        "requested_hypothesis": identifier or None,
        "scientific_authority": runtime.get("scientific_authority"),
        "scientific_writes": 0,
        "evaluator_calls": 0,
        "sealed_access": False,
        "note": "Recorded metadata is not a verified forecast or qualification result.",
    }
