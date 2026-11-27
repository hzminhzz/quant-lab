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


def current_registry(path, *, writable=False):
    """Control effects require an explicitly migrated, already existing ledger."""
    from warden.registry import SCHEMA_VERSION, ResearchRegistry
    view = ResearchRegistry(path, readonly=True)
    if view.schema_version != SCHEMA_VERSION:
        raise ValueError(f"separately authorized ledger migration required: {view.schema_version} -> {SCHEMA_VERSION}")
    return ResearchRegistry(path) if writable else view


class Control:
    def __init__(self, root: Path, *, registry_path: Path | None = None, runtime_probe=None):
        self.root = Path(root).resolve()
        self.registry_path = Path(registry_path) if registry_path is not None else self.root / "registry/registry.db"
        self.runtime_probe = runtime_probe
        if runtime_probe is None and os.environ.get("QUANT_LAB_APPLIANCE_HOME"):
            from warden_ops.appliance import Appliance
            home = Path(os.environ["QUANT_LAB_APPLIANCE_HOME"])
            self.runtime_probe = lambda: Appliance(home).observe()

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
        if any((ledger.get("job_states") or {}).get(s, 0) for s in
               ("RUNNING", "CANCEL_REQUESTED", "STOP_REQUESTED", "EXPIRED_UNKNOWN")):
            blockers.append("active or uncertain job resources retained; inspect job-status")
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
            "objective": request.objective,
            "checkout": checkout_view(Path(os.environ.get("QUANT_LAB_CALLER_ROOT", self.root))),
            "runtime": runtime, "ledger": ledger,
            "authority": {
                "status": "MAINTENANCE_DELEGATED" if runtime.get("authority_model") == "trusted-research-appliance" else "UNVERIFIED",
                "actor_uid": os.getuid(), "grants": [], "scientific_authority": False,
                "maintenance_model": runtime.get("authority_model"),
                "reason": "maintenance delegation confers no scientific approval or independent conformance"},
            "blockers": blockers,
            "eligible_actions": ["inspect", "prepare"],
            "eligibility_note": "affordances only; exact domain admission is rechecked at act",
            "continuation": {"completion": "refresh objective-specific receipts",
                             "next_review": "before scientific admission or protected effects",
                             "next_cursor": ledger.get("next_cursor")},
            "sources": {"ledger": ledger.get("revision"), "runtime": runtime.get("release_id")},
            "scientific_writes": 0, "evaluator_calls": 0, "sealed_access": False,
        }
        # A fresh on-demand probe has a new PID/time but the same state revision.
        stable_runtime = {key: item for key, item in runtime.items() if key not in {"pid", "observed_at"}}
        snapshot = digest({**value, "runtime": stable_runtime})
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
            if len(json.dumps(payload, allow_nan=False).encode()) > 131072:
                raise ValueError("control request exceeds 128 KiB")
            request = parse_request(payload)
        except (ValidationError, ValueError, TypeError) as exc:
            return self.refuse("TECHNICAL_PRE_ADMISSION", str(exc))
        if request.operation == "observe":
            return self.observe(request)
        if request.operation == "inspect" and request.view == "capabilities":
            return Result(ok=True, status="OK", observed_at=time.time(),
                          value=capabilities()).model_dump(mode="json")
        if request.operation == "inspect" and request.view == "findings":
            from warden_ops.findings import inspect_findings
            return inspect_findings(self, request)
        if request.operation in {"enqueue", "job-status", "cancel-job"}:
            from warden_ops.jobs import JobQueue
            try:
                readonly = request.operation == "job-status"
                registry = current_registry(self.registry_path, writable=not readonly)
                queue = JobQueue(registry)
                if request.operation == "enqueue":
                    payload = {"schema_version": 1, "operation": "act", "request_id": request.request_id,
                               "plan": request.plan.model_dump(mode="json")}
                    value = queue.submit(request.request_id, payload, objective=request.objective,
                                         resources=request.plan.resources)
                elif request.operation == "cancel-job":
                    value = queue.cancel(request.job_id)
                else:
                    value = queue.status(request.job_id)
                return Result(ok=True, status="OK", observed_at=time.time(), value=value,
                              next_actions=["job-status", "receipt"]).model_dump(mode="json")
            except (OSError, ValueError, sqlite3.Error) as exc:
                return self.refuse("TECHNICAL_PRE_ADMISSION", str(exc))
        if request.operation in {"prepare", "act", "receipt", "reconcile"}:
            from warden.lease import MutationBusy
            from warden_ops import control_actions
            try:
                if request.operation == "receipt":
                    return control_actions.receipt(self, request.request_id)
                return getattr(control_actions, request.operation)(self, request)
            except MutationBusy as exc:
                return self.refuse("BUSY", str(exc), retry="backoff; lookup receipt before retry")
            except (OSError, ValueError, sqlite3.Error) as exc:
                category = ("UNCERTAIN_EFFECT" if request.operation in {"act", "reconcile"}
                            else "TECHNICAL_PRE_ADMISSION")
                return self.refuse(category, str(exc), retry="inspect receipt; no automatic replay")
        return self.refuse("EXTERNAL_DEPENDENCY", "capability is not installed",
                           scope=request.operation)
