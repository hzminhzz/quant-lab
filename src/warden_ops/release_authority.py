"""Release admission/activation protocol, owned by a separately installed identity.

This library does not install a service, obtain privilege, discover keys, or
certify that its caller is independent. Production use requires the AC-10
bootstrap proof. Tests explicitly supply isolated verifier/executor adapters.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from pydantic import Field

from warden.lease import mutation_lease
from warden_ops.control_contracts import Digest, StrictModel, digest
from warden_ops.decisions import Packet, Proof
from warden_ops.release import verify_candidate


class Baseline(StrictModel):
    identity: Digest
    required_checks: list[str] = Field(min_length=1, max_length=50)


def classify(previous, candidate):
    """Conservative classification from actual inventory differences, never caller labels."""
    old, new = previous["inventory"], candidate["inventory"]
    if any(previous[key] != candidate[key] for key in ("ledger_min", "ledger_max", "dependency_lock_sha256")):
        return "protected_runtime"
    paths = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
    def plain_document(name):
        # Machine-read evidence, executable content and agent instructions are
        # protected even when their author places them beneath docs/.
        return ((name == "README.md" or (name.startswith("docs/") and Path(name).suffix == ".md"))
                and not name.startswith("docs/research-outcomes/")
                and Path(name).name not in {"AGENTS.md", "CLAUDE.md"}
                and not any(entry.get("executable", False) for entry in (old.get(name, {}), new.get(name, {}))))

    if paths and all(plain_document(name) for name in paths):
        return "documentation"
    if any(name.startswith(("tests/", ".github/")) for name in paths):
        return "verification"
    if any(name.startswith("contracts/") for name in paths):
        return "scientific_policy"
    return "protected_runtime"


class ReleaseAuthority:
    def __init__(self, store, *, gate, baseline: Baseline, verifier, executor=None, clock=time.time):
        self.store = Path(store)
        self.gate, self.baseline = gate, baseline
        self.verifier, self.executor, self.clock = verifier, executor, clock
        self.store.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS authority_events (
                    sequence INTEGER PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TRIGGER IF NOT EXISTS authority_no_update BEFORE UPDATE ON authority_events
                    BEGIN SELECT RAISE(ABORT,'append-only authority evidence'); END;
                CREATE TRIGGER IF NOT EXISTS authority_no_delete BEFORE DELETE ON authority_events
                    BEGIN SELECT RAISE(ABORT,'append-only authority evidence'); END;
            """)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.store)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _append(self, kind, subject, payload):
        with self._connect() as conn:
            conn.execute("INSERT INTO authority_events(kind,subject,payload,created_at) VALUES (?,?,?,?)",
                         (kind, subject, json.dumps(payload, sort_keys=True, allow_nan=False), self.clock()))

    def history(self):
        with self._connect() as conn:
            return [{"kind": row["kind"], "subject": row["subject"], "payload": json.loads(row["payload"]),
                     "created_at": row["created_at"]} for row in
                    conn.execute("SELECT * FROM authority_events ORDER BY sequence")]

    def _last(self, kind, subject):
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM authority_events WHERE kind=? AND subject=? "
                               "ORDER BY sequence DESC LIMIT 1", (kind, subject)).fetchone()
        return json.loads(row[0]) if row else None

    def register_grant(self, grant, *, packet, proof):
        import math
        required = {"schema_version", "recipient", "baseline", "classes", "effects", "expires_at"}
        if (set(grant) != required or grant["schema_version"] != 1
                or grant["recipient"] != "release-authority" or grant["baseline"] != self.baseline.identity
                or grant["classes"] != ["documentation"] or not grant["effects"]
                or not set(grant["effects"]).issubset({"release", "activation"})
                or not math.isfinite(grant["expires_at"])
                or grant["expires_at"] > min(packet.expires_at, proof.expires_at)
                or grant["expires_at"] <= self.clock()):
            raise ValueError("unsupported standing grant scope")
        grant_id = digest(grant)
        decision = self.gate.authorize(packet, proof, effect="policy", subject=grant_id, now=self.clock())
        with mutation_lease(self.store):
            old = self._last("grant", grant_id)
            payload = {"grant": grant, "packet": packet.model_dump(), "proof": proof.model_dump(),
                       "decision": decision}
            if not old:
                self._append("grant", grant_id, payload)
        return grant_id

    def _grant(self, grant_id, effect, classification):
        payload = self._last("grant", grant_id)
        if payload is None:
            raise ValueError("unknown standing grant")
        grant = payload["grant"]
        self.gate.authorize(Packet(**payload["packet"]), Proof(**payload["proof"]),
                            effect="policy", subject=grant_id, now=self.clock())
        if (grant["baseline"] != self.baseline.identity or classification not in grant["classes"]
                or effect not in grant["effects"] or self.clock() >= grant["expires_at"]):
            raise ValueError("standing grant scope or expiry refused")
        return grant_id

    def _checked(self, path):
        result = verify_candidate(path)
        if not result["valid"]:
            raise ValueError("candidate integrity: " + result["reason"])
        return result["manifest"]

    def _verification(self, path, release_id):
        # No candidate-provided check set, command, result, or verifier is consumed.
        result = self.verifier(Path(path), self.baseline.identity)
        if (result.get("release_id") != release_id or result.get("baseline") != self.baseline.identity
                or not result.get("evidence")
                or any(result.get("checks", {}).get(name) != "PASS"
                       for name in self.baseline.required_checks)):
            raise ValueError("trusted required verification missing, failed or skipped")
        if self._checked(path)["release_id"] != release_id:
            raise ValueError("candidate changed during verification")
        return result

    def admit(self, path, *, previous_path, packet=None, proof=None, grant_id=None):
        with mutation_lease(self.store):
            candidate, previous = self._checked(path), self._checked(previous_path)
            release_id = candidate["release_id"]
            kind = classify(previous, candidate)
            if self.executor is None or self.executor.observe()["release_id"] != previous["release_id"]:
                raise ValueError("previous release does not match observed authorized runtime")
            if grant_id:
                authorization = self._grant(grant_id, "release", kind)
                auth = {"grant_id": grant_id}
            else:
                if packet is None or proof is None:
                    raise ValueError("exact release decision required")
                decision = self.gate.authorize(packet, proof, effect="release", subject=release_id, now=self.clock())
                authorization = decision["decision_id"]
                auth = {"packet": packet.model_dump(), "proof": proof.model_dump()}
            verification = self._verification(path, release_id)
            receipt = {"release_id": release_id, "authorized_by": authorization, "classification": kind,
                       "baseline": self.baseline.identity, "verification": verification,
                       "previous_release": previous["release_id"], "authorization": auth}
            self._append("admitted", release_id, receipt)
            return receipt

    def _revalidate_admission(self, admitted):
        if admitted["baseline"] != self.baseline.identity:
            raise ValueError("trusted baseline changed; release requires fresh verification")
        auth = admitted["authorization"]
        if "grant_id" in auth:
            self._grant(auth["grant_id"], "release", admitted["classification"])
        else:
            self.gate.authorize(Packet(**auth["packet"]), Proof(**auth["proof"]), effect="release",
                                subject=admitted["release_id"], now=self.clock())

    def _halt(self, state, release_id, reason=None):
        outcome = {"state": state, "release_id": release_id, "reason": reason}
        try:
            self.executor.halt()
            outcome["halt_request_returned"] = True
        except Exception as exc:  # noqa: BLE001 -- failed halt must remain uncertain
            outcome.update(state="UNCERTAIN_HALT_FAILED", halt_request_returned=False,
                           halt_error=str(exc)[:1000])
        return outcome

    def _recover(self, release_id, before, rollback_path):
        try:
            current = self.executor.observe()
            rollback = self._checked(rollback_path) if rollback_path is not None else None
        except Exception as exc:  # noqa: BLE001 -- no observation means no safe rollback
            return self._halt("HALTED_RECOVERY_UNVERIFIED", release_id, str(exc)[:1000])
        compatible = (rollback is not None and rollback["release_id"] == before["release_id"]
                      and rollback["ledger_min"] <= current["ledger_schema"] <= rollback["ledger_max"])
        if not compatible:
            return self._halt("HALTED_INCOMPATIBLE_ROLLBACK", release_id)
        try:
            self.executor.activate(Path(rollback_path), current["release_id"])
            recovered = self.executor.observe()
            if (recovered["release_id"] != rollback["release_id"] or not recovered["healthy"]
                    or not rollback["ledger_min"] <= recovered["ledger_schema"] <= rollback["ledger_max"]):
                raise ValueError("rollback identity, health or schema unverified")
            return {"state": "ROLLED_BACK", "release_id": release_id, "observed": recovered}
        except Exception as exc:  # noqa: BLE001 -- no unsafe continuation
            return self._halt("HALTED_ROLLBACK_UNVERIFIED", release_id, str(exc)[:1000])

    def activate(self, path, *, packet=None, proof=None, grant_id=None, rollback_path=None):
        if self.executor is None:
            raise ValueError("protected activation capability absent")
        with mutation_lease(self.store):
            candidate = self._checked(path)
            release_id = candidate["release_id"]
            admitted = self._last("admitted", release_id)
            if admitted is None:
                raise ValueError("release was not admitted by this authority")
            self._revalidate_admission(admitted)
            if grant_id:
                self._grant(grant_id, "activation", admitted["classification"])
            else:
                if packet is None or proof is None:
                    raise ValueError("separate activation effect decision required")
                self.gate.authorize(packet, proof, effect="activation", subject=release_id, now=self.clock())
            before = self.executor.observe()
            if before["release_id"] not in {admitted["previous_release"], release_id}:
                raise ValueError("runtime changed since release admission")
            schema = before["ledger_schema"]
            if not candidate["ledger_min"] <= schema <= candidate["ledger_max"]:
                raise ValueError("candidate is incompatible with accrued ledger schema")
            if before["release_id"] == release_id and before["healthy"]:
                return {"state": "ACTIVE", "release_id": release_id, "recovered": True}
            # Intent survives loss of the response. Re-entry observes rather than blindly activates.
            self._append("activation-intent", release_id, {"before": before, "admission": admitted["authorized_by"]})
            try:
                self.executor.activate(Path(path), before["release_id"])
                after = self.executor.observe()
                if after["release_id"] != release_id or not after["healthy"]:
                    raise ValueError("runtime identity or health verification failed")
                if not candidate["ledger_min"] <= after["ledger_schema"] <= candidate["ledger_max"]:
                    raise ValueError("ledger became incompatible during activation")
                outcome = {"state": "ACTIVE", "release_id": release_id, "observed": after}
            except Exception as exc:  # noqa: BLE001 -- record and halt uncertain activation
                outcome = self._recover(release_id, before, rollback_path)
                outcome["failure"] = str(exc)[:1000]
            self._append("activation-result", release_id, outcome)
            return outcome
