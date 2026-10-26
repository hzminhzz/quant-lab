"""Pinned asymmetric human trust root. A checksum the agent can write is integrity, not approval.

The pinned key is deliberately empty: until a human operator pins their offline Ed25519 public
key, nothing verifies and every approval gate fails closed.
"""

from __future__ import annotations

MANIFEST_PUBLIC_KEY_HEX = ""


def verify_approval(payload: bytes, signature_hex: str, public_key_hex: str | None = None) -> bool:
    """True only for a valid Ed25519 signature over ``payload`` by the pinned key."""
    key_hex = MANIFEST_PUBLIC_KEY_HEX if public_key_hex is None else public_key_hex
    if not key_hex or not signature_hex:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex.strip())).verify(
            bytes.fromhex(signature_hex.strip()), payload)
        return True
    except (InvalidSignature, ValueError, TypeError, ImportError):
        return False


def verify_manifest_signature(manifest_bytes: bytes, signature_hex: str) -> bool:
    return verify_approval(manifest_bytes, signature_hex)
