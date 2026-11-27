"""A13/A24: exact decisions require an independent authenticated proof."""
import hashlib
import hmac

import pytest

from warden_ops.decisions import DecisionGate, Effect, Packet, Proof, make_packet, proof_bytes


def packet():
    return make_packet(
        effects=[Effect(kind="release", subject_sha256="a" * 64),
                 Effect(kind="migration", subject_sha256="b" * 64)],
        evidence=["synthetic:checks"], consequences=["Source activation is separate from migration"],
        limitations=["No production authority fixture"], rollback="compatible release or halt",
        now=1000., expires_at=2000.)


def signed(p, **overrides):
    values = {"issuer": "fixture-principal", "key_id": "fixture", "packet_sha256": p.packet_sha256,
              "approved_effects": ["release"], "issued_at": 1000., "expires_at": 1500.,
              "nonce": "decision-1", "generation": 1}
    values.update(overrides)
    proof = Proof(**values, signature="0" * 64)
    return proof.model_copy(update={"signature": hmac.new(b"fixture-only",
                            proof_bytes(proof), hashlib.sha256).hexdigest()})


def gate():
    return DecisionGate(keys={("fixture-principal", "fixture"): b"fixture-only"},
                        current_generation=lambda issuer: 1, revoked=lambda nonce: False)


def test_exact_authenticated_scope_is_reusable_but_never_implies_other_effects():
    p = packet()
    proof = signed(p)
    verifier = gate()
    first = verifier.authorize(p, proof, effect="release", subject="a" * 64, now=1200.)
    assert verifier.authorize(p, proof, effect="release", subject="a" * 64, now=1200.) == first
    for kind, subject in [("migration", "b" * 64), ("release", "b" * 64), ("holdout", "a" * 64)]:
        with pytest.raises(ValueError):
            verifier.authorize(p, proof, effect=kind, subject=subject, now=1200.)
    with pytest.raises(ValueError, match="expired"):
        verifier.authorize(p, proof, effect="release", subject="a" * 64, now=1600.)


def test_copied_chat_changed_packet_revocation_and_unknown_key_are_refused():
    p = packet()
    with pytest.raises(ValueError):
        Proof.model_validate({"principal": "Boss", "chat": "approved"})
    forged = signed(p).model_copy(update={"issuer": "copied-name"})
    with pytest.raises(ValueError, match="authentication"):
        gate().authorize(p, forged, effect="release", subject="a" * 64, now=1200.)
    changed = p.model_copy(update={"rollback": "different"})
    with pytest.raises(ValueError, match="packet"):
        gate().authorize(changed, signed(p), effect="release", subject="a" * 64, now=1200.)
    verifier = DecisionGate(keys={("fixture-principal", "fixture"): b"fixture-only"},
                            current_generation=lambda issuer: 2, revoked=lambda nonce: False)
    with pytest.raises(ValueError, match="revoked"):
        verifier.authorize(p, signed(p), effect="release", subject="a" * 64, now=1200.)


def test_unknown_revocation_status_never_means_permission():
    p = packet()
    verifier = DecisionGate(keys={("fixture-principal", "fixture"): b"fixture-only"},
                            current_generation=lambda issuer: 1, revoked=lambda nonce: None)
    with pytest.raises(ValueError, match="revocation"):
        verifier.authorize(p, signed(p), effect="release", subject="a" * 64, now=1200.)


def test_uninstalled_channel_returns_exact_bootstrap_packet():
    p = packet()
    result = DecisionGate().request(p)
    assert result["status"] == "BLOCKED_CAPABILITY"
    assert result["packet_sha256"] == p.packet_sha256
    assert result["principal_cli_commands"] == 0
    assert result["authority_integrated"] is False
    assert Packet.model_validate(result["packet"]) == p
