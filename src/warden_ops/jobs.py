"""Durable bounded objectives over the canonical registry; no scientific retry.

One canonical host policy accounts for reservations across objectives. Expiry is
uncertainty, not proof that a worker stopped: it keeps resources until confirmed.
"""
from __future__ import annotations

import json
import math
import os
import time
import uuid

from pydantic import TypeAdapter

from warden_ops.control_contracts import Identifier, Resources, digest

ResourceLimits = Resources
_HELD = {"RUNNING", "CANCEL_REQUESTED", "STOP_REQUESTED", "EXPIRED_UNKNOWN"}


class JobQueue:
    def __init__(self, registry, *, limits=None, clock=time.time):
        self.registry, self.clock = registry, clock
        self.limits = limits or ResourceLimits(cpu=4, memory_mb=8192, io_mb=16384, wall_seconds=86400)
        if registry.schema_version < 10:
            raise ValueError("canonical job migration required")
        if not registry.readonly:
            policy = json.dumps(self.limits.model_dump(), sort_keys=True)
            with registry._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                old = conn.execute("SELECT policy_json FROM control_resource_policy WHERE policy_id='host'").fetchone()
                if old and old[0] != policy:
                    raise ValueError("host resource policy differs; explicit reviewed migration required")
                conn.execute("INSERT OR IGNORE INTO control_resource_policy VALUES ('host',?)", (policy,))

    def _event(self, conn, job, state, fence, details):
        conn.execute("INSERT INTO control_job_events(job_id,state,fence,details_json,created_at) VALUES (?,?,?,?,?)",
                     (job, state, fence, json.dumps(details, sort_keys=True, allow_nan=False), self.clock()))

    def _status(self, conn, job):
        row = conn.execute("SELECT * FROM control_jobs WHERE job_id=?", (job,)).fetchone()
        if row is None:
            raise ValueError("unknown job")
        event = conn.execute("SELECT * FROM control_job_events WHERE job_id=? ORDER BY sequence DESC LIMIT 1",
                             (job,)).fetchone()
        state = event["state"]
        details = json.loads(event["details_json"])
        return {"job_id": job, "objective": row["objective"], "state": state,
                "fence": event["fence"], "updated_at": event["created_at"],
                "resources": json.loads(row["resources_json"]), "resources_held": state in _HELD,
                "details": details, "claimed": False, "retry_allowed": False,
                "scientific_authority": "existing domain receipt only"}

    def status(self, job):
        with self.registry._connect() as conn:
            return self._status(conn, job)

    def submit(self, job, payload, *, objective, resources):
        TypeAdapter(Identifier).validate_python(job)
        if not isinstance(objective, str) or not 1 <= len(objective) <= 1000:
            raise ValueError("bounded objective and completion criterion required")
        raw = json.dumps(payload, sort_keys=True, allow_nan=False)
        if len(raw.encode()) > 131072:
            raise ValueError("job payload exceeds bound")
        resources = ResourceLimits.model_validate(resources.model_dump())
        identity = digest([payload, objective, resources.model_dump()])
        with self.registry._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT fingerprint FROM control_jobs WHERE job_id=?", (job,)).fetchone()
            if old:
                if old[0] != identity:
                    raise ValueError("job identity conflict")
                return self._status(conn, job)
            conn.execute("INSERT INTO control_jobs VALUES (?,?,?,?,?,?)",
                         (job, identity, raw, objective, json.dumps(resources.model_dump()), self.clock()))
            self._event(conn, job, "QUEUED", None, {"completion": "finalized domain receipt or durable stop disposition"})
            return self._status(conn, job)

    def claim(self, job):
        with self.registry._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._status(conn, job)
            if current["state"] != "QUEUED":
                return current
            rows = conn.execute("SELECT j.job_id,j.resources_json,e.state,e.details_json FROM control_jobs j "
                                "JOIN control_job_events e ON e.sequence=(SELECT MAX(sequence) "
                                "FROM control_job_events WHERE job_id=j.job_id)").fetchall()
            used = {k: sum(json.loads(r["resources_json"])[k] for r in rows if r["state"] in _HELD)
                    for k in ("cpu", "memory_mb", "io_mb")}
            request = current["resources"]
            if (any(request[k] + used[k] > getattr(self.limits, k) for k in used)
                    or request["wall_seconds"] > self.limits.wall_seconds):
                return {**current, "state": "BUSY", "retry_after_seconds": 5, "reservations": used}
            held = [json.loads(r["details_json"]) for r in rows if r["state"] in _HELD]
            occupied = {cpu for item in held for cpu in item.get("cpu_ids", [])}
            free = [cpu for cpu in sorted(os.sched_getaffinity(0))[:self.limits.cpu] if cpu not in occupied]
            if any("cpu_ids" not in item for item in held) or len(free) < request["cpu"]:
                return {**current, "state": "BUSY", "retry_after_seconds": 5, "reservations": used}
            fence = uuid.uuid4().hex
            self._event(conn, job, "RUNNING", fence, {"started_at": self.clock(), "usage": None, "cpu_ids": free[:request["cpu"]],
                                                   "progress": {"completed": 0, "total": None}})
            payload = json.loads(conn.execute("SELECT payload_json FROM control_jobs WHERE job_id=?", (job,)).fetchone()[0])
            return {**self._status(conn, job), "claimed": True, "payload": payload}

    @staticmethod
    def _usage(usage):
        allowed = {"elapsed_seconds", "cpu_seconds", "peak_rss_mb", "io_bytes"}
        if set(usage) - allowed or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                      or not math.isfinite(v) or v < 0 for v in usage.values()):
            raise ValueError("invalid resource measurements")
        return {k: usage.get(k) for k in sorted(allowed)}

    @staticmethod
    def _fence(current, fence, *, running=True):
        if current["fence"] != fence:
            raise ValueError("wrong worker fence")
        if running and current["state"] != "RUNNING":
            raise ValueError("worker completion fenced by cancellation, expiry or prior result")

    @staticmethod
    def _exceeded(usage, limits):
        return ((usage["elapsed_seconds"] or 0) > limits["wall_seconds"]
                or (usage["cpu_seconds"] or 0) > limits["cpu"] * limits["wall_seconds"]
                or (usage["peak_rss_mb"] or 0) > limits["memory_mb"]
                or (usage["io_bytes"] or 0) > limits["io_mb"] * 1024 * 1024)

    def heartbeat(self, job, fence, *, progress, usage):
        if (set(progress) != {"completed", "total"} or type(progress["completed"]) is not int
                or type(progress["total"]) is not int
                or not 0 <= progress["completed"] <= progress["total"]):
            raise ValueError("invalid bounded progress")
        usage = self._usage(usage)
        with self.registry._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._status(conn, job)
            self._fence(current, fence)
            exceeded = self._exceeded(usage, current["resources"])
            self._event(conn, job, "STOP_REQUESTED" if exceeded else "RUNNING", fence,
                        {**current["details"], "usage": usage, "progress": progress})
            return self._status(conn, job)

    def finish(self, job, fence, *, receipt, usage):
        usage = self._usage(usage)
        with self.registry._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._status(conn, job)
            self._fence(current, fence)
            exceeded = self._exceeded(usage, current["resources"])
            self._event(conn, job, "STOPPED_REVIEW" if exceeded else "FINISHED", fence,
                        {"receipt": receipt, "usage": usage, "limit_exceeded": exceeded,
                         "outcome": "operational completion; inspect domain verdict"})
            return self._status(conn, job)

    def cancel(self, job):
        with self.registry._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._status(conn, job)
            if current["state"] == "QUEUED":
                self._event(conn, job, "CANCELLED_NO_EFFECT", None, {"reason": "not dispatched"})
            elif current["state"] == "RUNNING":
                self._event(conn, job, "CANCEL_REQUESTED", current["fence"], current["details"])
            return self._status(conn, job)

    def expire(self, job):
        with self.registry._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._status(conn, job)
            start = current["details"].get("started_at", current["updated_at"])
            if current["state"] == "RUNNING" and (
                    self.clock() - start > current["resources"]["wall_seconds"]
                    or self.clock() - current["updated_at"] > min(30, current["resources"]["wall_seconds"])):
                self._event(conn, job, "EXPIRED_UNKNOWN", current["fence"], current["details"])
            return self._status(conn, job)

    def confirm_stopped(self, job, fence, *, receipt, process_exited, usage=None):
        """Record the observed stop; unknown process exit retains all reservations."""
        with self.registry._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._status(conn, job)
            self._fence(current, fence, running=False)
            if current["state"] not in _HELD:
                raise ValueError("job already terminal")
            confirmed = process_exited is True
            state = "STOPPED_REVIEW" if confirmed else (
                "CANCEL_REQUESTED" if current["state"] == "RUNNING" else current["state"])
            self._event(conn, job, state, fence, {**current["details"], "receipt": receipt,
                        "usage": self._usage(usage or {}), "reason":
                        "worker stopped; retain scientific charge or uncertainty" if confirmed else
                        "process exit unverified; resources and scientific state remain held"})
            return self._status(conn, job)
