"""One application interface for CLI/MCP, with lazy scientific execution."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from pydantic import ValidationError

from warden_ops.control_contracts import Blocker, Result, capabilities, digest, parse_request
from warden_ops.readonly import open_readonly


def _git(root: Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=5, check=False)
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def checkout_view(root: Path) -> dict:
    head = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
    patch = _git(root, "diff", "--binary", "HEAD", "--", ".", ":!data", ":!registry")
    return {"root": str(root), "head": head.decode().strip() if head else None,
            "branch": branch.decode().strip() if branch else None,
            "dirty": bool(status) if status is not None else None,
            "dirty_identity": digest([status.hex() if status is not None else None,
                                      patch.hex() if patch is not None else None]),
            "identity_scope": "tracked diff and untracked names; untracked bytes not attested"}


class Control:
    def __init__(self, root: Path, *, registry_path: Path | None = None, runtime_probe=None):
        self.root = Path(root).resolve()
        self.registry_path = Path(registry_path) if registry_path is not None else self.root / "registry/registry.db"
        self.runtime_probe = runtime_probe

    def refuse(self, classification, reason, *, scope="request", owner="warden.control",
               remedy="inspect capabilities and prepare again", retry="no automatic retry",
               request_id=None) -> dict:
        return Result(ok=False, status="REFUSED", observed_at=time.time(), request_id=request_id,
                      blockers=[Blocker(classification=classification, scope=scope,
                                        predicate=str(reason)[:2000], owner=owner, remedy=remedy,
                                        retry=retry)]).model_dump(mode="json")

    def observe(self, request) -> dict:
        blockers = []
        try:
            registry = open_readonly(self.registry_path)
            ledger = registry.read_control_snapshot(
                hypothesis_id=request.hypothesis_id, after=request.cursor or "", limit=request.limit,
            ) if registry else {
                "status": "MISSING", "counts": {"hypotheses": None, "trials": None,
                "selection_attempts": None}, "complete": False, "revision": None}
        except (OSError, sqlite3.Error, ValueError) as exc:
            ledger = {"status": "UNAVAILABLE", "reason": str(exc)[:300], "complete": False,
                      "revision": None, "counts": {"hypotheses": None, "trials": None,
                                                 "selection_attempts": None}}
        ledger["path"] = str(self.registry_path)
        if ledger["status"] != "AVAILABLE" or ledger.get("missing_fields"):
            blockers.append("ledger facts missing or unavailable; no new science authorized by this view")
        if any(v is None or v > 0 for v in ledger.get("pending", {}).values()):
            blockers.append("pending or unknown operations: inspect receipts before retry")
        if ledger.get("accounting", {}).get("error"):
            blockers.append("accounting requires reconciliation")
        runtime = {"status": "UNKNOWN", "reason": "no installed trusted runtime observer",
                   "release_id": None}
        if self.runtime_probe is not None:
            try:
                runtime = self.runtime_probe()
            except (OSError, ValueError):
                runtime = {"status": "UNAVAILABLE", "release_id": None}
        value = {
            "objective": request.objective, "checkout": checkout_view(self.root),
            "runtime": runtime, "ledger": ledger,
            "authority": {"status": "UNVERIFIED", "actor_uid": os.getuid(), "grants": [],
                          "reason": "coding identity is not principal authentication"},
            "blockers": blockers,
            "eligible_actions": ["inspect", "prepare"],
            "eligibility_note": "affordances only; exact domain admission is rechecked at act",
            "continuation": {"completion": "refresh objective-specific receipts",
                             "next_review": "before scientific admission or protected effects",
                             "next_cursor": ledger.get("next_cursor")},
            "sources": {"ledger": ledger.get("revision"), "runtime": runtime.get("release_id")},
            "scientific_writes": 0, "evaluator_calls": 0, "sealed_access": False,
        }
        snapshot = digest(value)
        if request.since == snapshot:
            value = {"snapshot_id": snapshot, "unchanged": True,
                     "blockers": blockers, "continuation": value["continuation"]}
        else:
            value["snapshot_id"] = snapshot
            value["unchanged"] = False
        result = Result(ok=True, status="OK", observed_at=time.time(), value=value).model_dump(mode="json")
        # Never silently trim a blocker. Reduce only the explicitly paginated detail.
        while len(json.dumps(result).encode()) > 12288 and value.get("ledger", {}).get("hypotheses"):
            rows = value["ledger"]["hypotheses"]
            rows.pop()
            value["ledger"]["complete"] = False
            value["ledger"]["next_cursor"] = rows[-1]["hypothesis_id"] if rows else request.cursor
            value["continuation"]["next_cursor"] = value["ledger"]["next_cursor"]
            value["ledger"]["omitted_for_budget"] = True
            result["value"] = value
        if len(json.dumps(result).encode()) > 12288:
            return self.refuse("EXTERNAL_DEPENDENCY", "situation exceeds bounded output",
                               remedy="use an objective-specific hypothesis view")
        return result

    def handle(self, payload: dict) -> dict:
        try:
            request = parse_request(payload)
        except ValidationError as exc:
            return self.refuse("TECHNICAL_PRE_ADMISSION", str(exc))
        if request.operation == "observe":
            return self.observe(request)
        if request.operation == "inspect" and request.view == "capabilities":
            return Result(ok=True, status="OK", observed_at=time.time(),
                          value=capabilities()).model_dump(mode="json")
        return self.refuse("EXTERNAL_DEPENDENCY", "capability is not installed",
                           scope=request.operation)
