"""Exact principal decision protocol; no signing key discovery or self approval.

An installation supplies the independently controlled verifier/channel. This
module has no default authority, no human-confirm flag, and no signing endpoint.
HMAC verification preserves the existing mechanism for controlled integrations;
tests use isolated keys. Fixture verification is not evidence of installation.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Literal

from pydantic import Field, model_validator

from warden_ops.control_contracts import Digest, Identifier, StrictModel, digest

EffectKind = Literal["release", "installation", "migration", "activation", "policy",
                     "history_attestation", "batch", "holdout", "capital"]


class Effect(StrictModel):
    kind: EffectKind
    subject_sha256: Digest


class Packet(StrictModel):
    schema_version: Literal[1] = 1
    effects: list[Effect] = Field(min_length=1, max_length=9)
    evidence: list[str] = Field(min_length=1, max_length=50)
    consequences: list[str] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(max_length=30)
    rollback: str = Field(min_length=1, max_length=3000)
    created_at: float
    expires_at: float
    packet_sha256: Digest

    @model_validator(mode="after")
    def unique_and_finite(self):
        import math
        if (len({x.kind for x in self.effects}) != len(self.effects)
                or not all(math.isfinite(x) for x in (self.created_at, self.expires_at))
                or self.expires_at <= self.created_at):
            raise ValueError("decision packet requires unique effects and finite validity")
        if any(len(x) > 2000 for x in [*self.evidence, *self.consequences, *self.limitations]):
            raise ValueError("decision packet text exceeds bounds")
        return self


class Proof(StrictModel):
    schema_version: Literal[1] = 1
    issuer: Identifier
    key_id: Identifier
    packet_sha256: Digest
    approved_effects: list[EffectKind] = Field(min_length=1, max_length=9)
    issued_at: float
    expires_at: float
    nonce: Identifier
    generation: int = Field(ge=1)
    signature: Digest


def packet_bytes(packet):
    return {k: v for k, v in packet.model_dump(mode="json").items() if k != "packet_sha256"}


def make_packet(*, effects, evidence, consequences, limitations, rollback, now, expires_at):
    fields = {"schema_version": 1, "effects": [x.model_dump(mode="json") for x in effects],
              "evidence": evidence, "consequences": consequences, "limitations": limitations,
              "rollback": rollback, "created_at": now, "expires_at": expires_at}
    return Packet(**fields, packet_sha256=digest(fields))


def proof_bytes(proof):
    import json
    fields = {k: v for k, v in proof.model_dump(mode="json").items() if k != "signature"}
    return ("warden-principal-decision-v1\n" +
            json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False)).encode()


class DecisionGate:
    """Verifier seam. Key/grant state must be provisioned outside coding control.

    Supplying keys to a Python object does not prove OS isolation; installed
    capability evidence is deliberately separate from this protocol behavior.
    """
    def __init__(self, *, keys=None, current_generation=None, revoked=None, channel=None):
        self._keys = {} if keys is None else dict(keys)
        self._generation = current_generation
        self._revoked = revoked
        self._channel = channel

    def request(self, packet: Packet) -> dict:
        packet = Packet.model_validate(packet.model_dump(mode="json"))
        if digest(packet_bytes(packet)) != packet.packet_sha256:
            raise ValueError("changed decision packet")
        if self._channel is None:
            return {"status": "BLOCKED_CAPABILITY", "packet_sha256": packet.packet_sha256,
                    "packet": packet.model_dump(mode="json"), "authority_integrated": False,
                    "principal_cli_commands": 0,
                    "blocker": "independent authenticated principal UI and protected verifier/installer absent",
                    "required_capabilities": ["principal-authenticated exact packet decision",
                                              "verifier and revocation state outside coding control",
                                              "separately protected executor"]}
        # Transport presents the exact packet. It cannot produce approval itself.
        return self._channel.present(packet.model_dump(mode="json"))

    def authorize(self, packet: Packet, proof: Proof, *, effect: str, subject: str, now: float) -> dict:
        import math
        packet = Packet.model_validate(packet.model_dump(mode="json"))
        proof = Proof.model_validate(proof.model_dump(mode="json"))
        if digest(packet_bytes(packet)) != packet.packet_sha256 or proof.packet_sha256 != packet.packet_sha256:
            raise ValueError("decision packet identity mismatch")
        key = self._keys.get((proof.issuer, proof.key_id))
        if not key or self._generation is None or self._revoked is None:
            raise ValueError("independent principal authentication unavailable")
        expected = hmac.new(key, proof_bytes(proof), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, proof.signature):
            raise ValueError("principal authentication failed")
        if (not all(math.isfinite(x) for x in (now, proof.issued_at, proof.expires_at))
                or not packet.created_at <= proof.issued_at <= now < proof.expires_at <= packet.expires_at):
            raise ValueError("decision expired or outside packet validity")
        if self._generation(proof.issuer) != proof.generation or self._revoked(proof.nonce):
            raise ValueError("decision revoked")
        effects = {x.kind: x.subject_sha256 for x in packet.effects}
        if (len(set(proof.approved_effects)) != len(proof.approved_effects)
                or not set(proof.approved_effects).issubset(effects)
                or effect not in proof.approved_effects or effects.get(effect) != subject):
            raise ValueError("effect or subject not approved")
        return {"issuer": proof.issuer, "decision_id": proof.nonce,
                "packet_sha256": packet.packet_sha256, "effect": effect, "subject_sha256": subject,
                "expires_at": proof.expires_at, "generation": proof.generation}
