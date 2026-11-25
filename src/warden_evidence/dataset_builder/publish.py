"""Deterministic, write-once publication of a built dataset and its v2 manifest.

Publication is a single atomic step per artifact and never overwrites: each file
is created with ``O_EXCL``.  The Parquet bytes are produced in memory first, so
the manifest's digest and Merkle commitment describe exactly the bytes that are
published, and a second build of the same spec reproduces the same digest.

The manifest carries the evidence the wire node needs — ``spec_sha256``,
``builder.code_closure_sha256``, ``raw_inputs_merkle_sha256``, ``polars_version``,
``parquet_write`` and the full ``quality_gate_report`` — plus the legacy-compatible
keys ``provenance``/``path``/``sha256`` that :class:`MarketDataGateway` resolves.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from warden_evidence.dataset_builder.spec import DatasetSpec

MANIFEST_SCHEMA_VERSION = 2
BUILDER_NAME = "warden_evidence.dataset_builder"
BUILDER_VERSION = "1.0.0"
CLOSURE_TAG = b"warden.dataset_builder.closure.v1\x00"


class WriteOnceError(FileExistsError):
    """The dataset or manifest already exists; write-once refuses to overwrite."""


@dataclass(frozen=True)
class Manifest:
    payload: dict[str, Any]
    sha256: str

    @property
    def dataset_sha256(self) -> str:
        return str(self.payload["sha256"])

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.payload)


def canonical_json_bytes(payload: Any) -> bytes:
    """Canonical manifest bytes: sorted keys, two-space indent, trailing newline."""
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def builder_code_closure_sha256(package_dir: Path | None = None) -> str:
    """Digest every module of the builder package.

    The closure is the builder's own code: every ``.py`` file in this package,
    sorted by relative path, hashed with its path so a renamed module changes the
    digest.  Third-party dependencies are pinned by ``uv.lock`` and recorded as
    versions instead of being proxied by an ad-hoc hash.
    """
    root = Path(package_dir) if package_dir is not None else Path(__file__).resolve().parent
    files = sorted(path for path in root.rglob("*.py") if path.is_file())
    if not files:
        raise WriteOnceError(f"builder code closure is empty under {root}")
    digest = hashlib.sha256(CLOSURE_TAG)
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8") + b"\x00")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def serialise_parquet(frame: pl.DataFrame, spec: DatasetSpec) -> bytes:
    """Deterministic Parquet bytes: declared compression, fixed row groups, canonical order."""
    write = spec.parquet_write
    ordered = frame.sort(list(write.sort_keys))
    buffer = io.BytesIO()
    ordered.write_parquet(
        file=buffer,
        compression=write.compression,
        compression_level=write.compression_level,
        statistics=write.statistics,
        row_group_size=write.row_group_size,
    )
    return buffer.getvalue()


def build_manifest(
    *,
    spec: DatasetSpec,
    parquet_sha256: str,
    parquet_bytes: int,
    dataset_relative_path: str,
    raw_inputs: list[dict[str, Any]],
    raw_inputs_merkle_sha256: str,
    quality_gate_report: dict[str, Any],
    source_summary: dict[str, Any],
    builder_closure_sha256: str,
    rows: int,
    first_bar: str,
    last_bar: str,
    assets: list[str],
    source_audit: dict[str, Any] | None = None,
    lineage: dict[str, Any] | None = None,
) -> Manifest:
    """Construct the canonical v2 manifest dictionary.

    ``source_audit`` is the adapter's own evidence — which symbols were retained, which
    partitions were opened or skipped, what a missing-settlement policy substituted.  It is
    published verbatim (and so is covered by ``manifest_sha256``) instead of being prose in
    a log: a reviewer can diff the decisions a build took.
    """
    payload: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_id": spec.dataset_id,
        # Legacy gateway keys (contracts/datasets/<contract_id>.json resolution).
        "contract_id": spec.dataset_id,
        "path": dataset_relative_path,
        "sha256": parquet_sha256,
        # A builder never claims market verification: only a synthetic build is
        # resolvable provenance here; every other tier stays "unknown" and therefore
        # market-ineligible until a human attests origin.
        "provenance": "synthetic" if spec.evidence_tier == "synthetic" else "unknown",
        "source": spec.source.adapter,
        "pipeline": f"{BUILDER_NAME}/v{BUILDER_VERSION}",
        "bytes": parquet_bytes,
        "spec_sha256": spec.spec_sha256(),
        "builder": {
            "name": BUILDER_NAME,
            "version": BUILDER_VERSION,
            "code_closure_sha256": builder_closure_sha256,
        },
        "raw_inputs_merkle_sha256": raw_inputs_merkle_sha256,
        "raw_inputs": raw_inputs,
        # A derived or composite build states the parents it verified, with the digest it
        # verified them at; a raw build states that it has none rather than omitting the key.
        "lineage": dict(lineage) if lineage is not None else {"kind": "lake_raw", "parents": []},
        "polars_version": pl.__version__,
        "parquet_write": {
            "compression": spec.parquet_write.compression,
            "compression_level": spec.parquet_write.compression_level,
            "statistics": spec.parquet_write.statistics,
            "row_group_size": spec.parquet_write.row_group_size,
            "sort_keys": list(spec.parquet_write.sort_keys),
        },
        "quality_gate_report": quality_gate_report,
        "dataset": {
            "dataset_id": spec.dataset_id,
            "asset_class": spec.asset_class,
            "evidence_tier": spec.evidence_tier,
            "venue": spec.venue,
            "frequency": spec.frequency,
            "price_basis": spec.price_basis,
            "volume_semantics": spec.volume_semantics,
            "rows": rows,
            "assets": list(assets),
            "first_bar": first_bar,
            "last_bar": last_bar,
            "time_window": spec.time_window.model_dump(mode="json"),
            "time_contract": spec.time_contract.model_dump(mode="json"),
            "feed_spec": spec.feed_spec_dict(),
            "source_summary": source_summary,
        },
        # Absent until a human seals a holdout for this dataset; a build never declares one.
        "holdout": {"sealed": False},
    }
    if source_audit:
        payload["source_audit"] = source_audit
    return Manifest(payload=payload, sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest())


def holdout_pin(payload: dict[str, Any]) -> dict[str, Any]:
    """The ``holdout`` block a seal records: the digest, the window and the gate report."""
    return {
        "path": payload["path"],
        "sha256": payload["sha256"],
        "sealed": True,
        "rows": payload["rows"],
        "assets": list(payload["assets"]),
        "first_bar": payload["first_bar"],
        "last_bar": payload["last_bar"],
        "window": payload["window"],
        "quality_gate_report": payload["quality_gate_report"],
    }


def with_holdout(manifest: Manifest, *, holdout: dict[str, Any]) -> Manifest:
    """The same v2 manifest with a sealed-holdout pin added; one digest, one spelling.

    The legacy top-level ``holdout_sha256`` key is written from the *same* value as
    ``holdout.sha256``, so a reader of either cannot disagree with the other.
    """
    payload = dict(manifest.payload)
    payload["holdout"] = holdout
    payload["holdout_sha256"] = holdout["sha256"]
    return Manifest(
        payload=payload,
        sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    )


def _create_exclusive(path: Path, data: bytes) -> None:
    """Create ``path`` with ``data``; fail if anything exists there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise WriteOnceError(
            f"{path} already exists; a dataset is write-once and is never rebuilt in place"
        ) from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:  # pragma: no cover - platform without O_DIRECTORY
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@dataclass(frozen=True)
class Published:
    dataset_path: Path
    manifest_path: Path
    dataset_sha256: str
    manifest: Manifest = field(repr=False)


def publish_once(
    *, dataset_path: Path, parquet_bytes: bytes, manifest_path: Path, manifest: Manifest
) -> Published:
    """Publish Parquet then manifest, creating each exactly once.

    Both existence checks run before the first write; if the manifest write still
    loses a race the Parquet file created by this call is removed, so a refused
    build leaves the existing dataset untouched and no half-published artifact.
    """
    if dataset_path.exists():
        raise WriteOnceError(
            f"{dataset_path} already exists; refusing to overwrite a dataset"
        )
    if manifest_path.exists():
        raise WriteOnceError(
            f"{manifest_path} already exists; refusing to overwrite a manifest"
        )
    _create_exclusive(dataset_path, parquet_bytes)
    try:
        _create_exclusive(manifest_path, manifest.to_bytes())
    except BaseException:
        dataset_path.unlink(missing_ok=True)
        raise
    return Published(
        dataset_path=dataset_path,
        manifest_path=manifest_path,
        dataset_sha256=str(manifest.payload["sha256"]),
        manifest=manifest,
    )


def _replace_manifest_atomically(*, manifest_path: Path, data: bytes, expected: bytes) -> None:
    """Swap one manifest for another in a single ``rename``, refusing a changed pre-image.

    The sealed pin is the only mutation a v2 manifest ever takes, so it is also the only
    place a stale writer could clobber a human's edit.  The current bytes are compared to
    the bytes this seal read; the new manifest is written to a sibling temporary file
    (never a staging *directory*) and moved into place, so a reader sees either the
    unsealed manifest or the sealed one, never a truncated file.
    """
    current = manifest_path.read_bytes()
    if current != expected:
        raise WriteOnceError(
            f"{manifest_path} changed after it was read for sealing; refusing to overwrite "
            "a manifest this seal did not verify"
        )
    temporary = manifest_path.with_name(f".{manifest_path.name}.sealing-{os.getpid()}")
    try:
        _create_exclusive(temporary, data)
        os.replace(temporary, manifest_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    try:
        directory_fd = os.open(manifest_path.parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:  # pragma: no cover - platform without O_DIRECTORY
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@dataclass(frozen=True)
class Sealed:
    holdout_path: Path
    holdout_sha256: str
    manifest_path: Path
    manifest: Manifest = field(repr=False)


def publish_sealed(
    *,
    holdout_path: Path,
    parquet_bytes: bytes,
    manifest_path: Path,
    manifest_before: Manifest,
    manifest_after: Manifest,
) -> Sealed:
    """Write the sealed dataset straight to its final path, then pin the manifest.

    There is no staging directory and no staging copy: the bytes are created exactly once
    at ``data/holdout/<dataset_id>.parquet``.  If the manifest pin cannot be applied (a
    concurrent edit, a lost race) the newly written file is removed, so a refused seal
    leaves neither a sealed file nor a pin — never one without the other.
    """
    if holdout_path.exists():
        raise WriteOnceError(
            f"{holdout_path} already exists; a sealed holdout is write-once and is never rebuilt"
        )
    _create_exclusive(holdout_path, parquet_bytes)
    try:
        _replace_manifest_atomically(
            manifest_path=manifest_path,
            data=manifest_after.to_bytes(),
            expected=manifest_before.to_bytes(),
        )
    except BaseException:
        holdout_path.unlink(missing_ok=True)
        raise
    return Sealed(
        holdout_path=holdout_path,
        holdout_sha256=str(manifest_after.payload["sha256"]),
        manifest_path=manifest_path,
        manifest=manifest_after,
    )
