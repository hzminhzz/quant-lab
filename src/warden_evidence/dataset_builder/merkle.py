"""Raw-input Merkle commitment.

A manifest must prove *which raw bytes* produced a dataset, at file granularity
and as one comparable digest.  The tree is built over the raw files exactly as
they were read (path, byte length, SHA-256), sorted by path so declaration order
cannot change the commitment.

Two rules make the digest reproducible rather than merely plausible:

* leaves are domain-separated (``...leaf.v1``) from interior nodes
  (``...node.v1``), so a single-file tree cannot be confused with a leaf;
* an odd node is promoted unchanged instead of duplicated, so the classic
  duplicate-last-node ambiguity cannot make two different input sets collide.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

LEAF_TAG = b"warden.dataset.raw_input.leaf.v1\x00"
NODE_TAG = b"warden.dataset.raw_input.node.v1\x00"
MERKLE_VERSION = 1


@dataclass(frozen=True)
class RawInputRecord:
    """One raw input exactly as read by the adapter."""

    path: str
    bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _leaf(path: str, payload: bytes, digest: str) -> bytes:
    material = b"\x00".join(
        (
            path.encode("utf-8"),
            str(len(payload)).encode("ascii"),
            digest.encode("ascii"),
        )
    )
    return hashlib.sha256(LEAF_TAG + material).digest()


def merkle_sha256(inputs: Iterable[tuple[str, bytes]]) -> tuple[str, list[RawInputRecord]]:
    """Commit to raw input bytes; returns (root hex digest, per-file records).

    ``inputs`` is a sequence of ``(relative_path, payload)`` pairs.  A path may
    appear once; duplicate paths are refused by the spec parser and re-checked
    here so an adapter cannot hash the same file twice.
    """
    records: list[RawInputRecord] = []
    payloads: dict[str, bytes] = {}
    for path, payload in inputs:
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError(f"raw input {path!r} must be bytes, got {type(payload).__name__}")
        if path in payloads:
            raise ValueError(f"raw input {path!r} was read twice; refusing an ambiguous commitment")
        data = bytes(payload)
        payloads[path] = data
        records.append(
            RawInputRecord(path=path, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        )
    if not records:
        raise ValueError("cannot commit to an empty raw input set")

    records.sort(key=lambda record: record.path)
    level = [_leaf(record.path, payloads[record.path], record.sha256) for record in records]
    while len(level) > 1:
        promoted: list[bytes] = []
        for index in range(0, len(level) - 1, 2):
            promoted.append(hashlib.sha256(NODE_TAG + level[index] + level[index + 1]).digest())
        if len(level) % 2:
            promoted.append(level[-1])  # promoted, never duplicated
        level = promoted
    root = hashlib.sha256(NODE_TAG + level[0]).hexdigest()
    return root, records
