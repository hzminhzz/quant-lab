"""``warden data build`` handler: spec -> raw bundle -> gate -> write-once publish.

The handler owns ordering, not policy.  It refuses an existing target *before*
the first raw read, so rebuilding onto an existing ``dataset_id`` cannot even
look at the source tree, and it publishes only what the gate approved.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from warden_evidence.dataset_builder.adapters import (
    Ml4tLakeAdapter,
    ParquetReader,
    SourceAdapter,
    get_source_adapter,
)
from warden_evidence.dataset_builder.containment import (
    LocalRawFileSystem,
    RawFileSystem,
    SourceContainmentError,
    assert_directory_contained,
    canonical_source_path,
)
from warden_evidence.dataset_builder.publish import (
    Manifest,
    WriteOnceError,
    build_manifest,
    builder_code_closure_sha256,
    publish_once,
    serialise_parquet,
)
from warden_evidence.dataset_builder.quality import GateOutcome, QualityGateError, run_quality_gate
from warden_evidence.dataset_builder.spec import DatasetSpec

DEFAULT_DATASET_DIR = "data/exploratory"
DEFAULT_MANIFEST_DIR = "contracts/datasets"


class DatasetBuildError(ValueError):
    """The build could not run at all (target, adapter or specification problem)."""


@dataclass(frozen=True)
class BuildOutcome:
    dataset_id: str
    dataset_path: str
    manifest_path: str
    parquet_sha256: str
    manifest_sha256: str
    spec_sha256: str
    raw_inputs_merkle_sha256: str
    builder_code_closure_sha256: str
    rows: int
    assets: tuple[str, ...]
    first_bar: str
    last_bar: str
    quality_gate_report: dict[str, Any] = field(repr=False)
    manifest: dict[str, Any] = field(repr=False)
    #: Adapter-owned audit (universe/partition decisions, funding substitutions); ``{}`` for
    #: an adapter that declares none.  Published verbatim as the manifest's ``source_audit``.
    source_audit: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe summary for ``warden data build --json``."""
        return {
            "dataset_id": self.dataset_id,
            "dataset_path": self.dataset_path,
            "manifest_path": self.manifest_path,
            "parquet_sha256": self.parquet_sha256,
            "manifest_sha256": self.manifest_sha256,
            "spec_sha256": self.spec_sha256,
            "raw_inputs_merkle_sha256": self.raw_inputs_merkle_sha256,
            "builder_code_closure_sha256": self.builder_code_closure_sha256,
            "rows": self.rows,
            "assets": list(self.assets),
            "first_bar": self.first_bar,
            "last_bar": self.last_bar,
            "source_audit": self.source_audit,
            "quality_gate_report": self.quality_gate_report,
            "manifest": self.manifest,
        }


def _refuse_protected_root(root: Path) -> None:
    for part in root.parts:
        if part.casefold() == "holdout":
            raise SourceContainmentError(
                f"repo_root {root} names protected data; the builder refuses to write there"
            )


def _relative_target(
    repo_root: Path, directory: str, filename: str, *, role: str
) -> tuple[PurePosixPath, Path]:
    relative_dir = canonical_source_path(directory, role=role)
    absolute_dir = assert_directory_contained(repo_root, relative_dir, role=role)
    return relative_dir / filename, absolute_dir / filename


def build_dataset(
    spec: DatasetSpec,
    *,
    repo_root: Path | str,
    source_root: Path | str | None = None,
    dataset_dir: str = DEFAULT_DATASET_DIR,
    manifest_dir: str = DEFAULT_MANIFEST_DIR,
    adapter: SourceAdapter | None = None,
    fs: RawFileSystem | None = None,
    parquet_reader: ParquetReader | None = None,
) -> BuildOutcome:
    """Build one dataset from a parsed spec.

    Args:
        spec: a validated :class:`DatasetSpec` (parse it with ``load_dataset_spec``).
        repo_root: root the published paths are relative to.
        source_root: local root of the declared raw inputs; ignored when ``fs`` or
            ``adapter`` is supplied. If omitted, ``ml4t_lake`` uses the configured lake.
        dataset_dir / manifest_dir: repository-relative output directories.
        adapter: explicit adapter (used by tests and by venue-specific adapters).
        fs: explicit raw filesystem (containment still applies per input).
        parquet_reader: reader for Parquet raw inputs; never imported by the builder.
    """
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise DatasetBuildError(f"repo_root {root} is not a directory")
    _refuse_protected_root(root)

    filename = f"{spec.dataset_id}.parquet"
    dataset_relative, dataset_path = _relative_target(
        root, dataset_dir, filename, role="dataset_dir"
    )
    manifest_relative, manifest_path = _relative_target(
        root, manifest_dir, f"{spec.dataset_id}.json", role="manifest_dir"
    )

    # Write-once precedes every raw read: a refused rebuild touches nothing.
    for target in (dataset_path, manifest_path):
        if target.exists():
            raise WriteOnceError(
                f"{target} already exists; dataset {spec.dataset_id!r} is write-once and "
                f"spec {spec.spec_sha256()} cannot replace it"
            )

    if adapter is None:
        if fs is None and source_root is not None:
            fs = LocalRawFileSystem(source_root)
        if fs is not None:
            adapter = get_source_adapter(spec.source.adapter, fs=fs, parquet_reader=parquet_reader)
        elif spec.source.adapter == Ml4tLakeAdapter.name:
            adapter = get_source_adapter(
                spec.source.adapter, repo_root=root, parquet_reader=parquet_reader
            )
        else:
            raise DatasetBuildError(
                f"adapter {spec.source.adapter!r} needs source_root or an explicit fs"
            )
    if getattr(adapter, "name", None) != spec.source.adapter:
        raise DatasetBuildError(
            f"adapter {getattr(adapter, 'name', None)!r} does not match declared "
            f"source.adapter {spec.source.adapter!r}"
        )

    bundle = adapter.load(spec)
    outcome: GateOutcome = run_quality_gate(bundle.frame, spec)
    frame = outcome.frame
    if frame.is_empty():  # pragma: no cover - the gate already refuses emptiness
        raise QualityGateError("gate produced an empty panel")

    parquet_bytes = serialise_parquet(frame, spec)
    parquet_sha256 = hashlib.sha256(parquet_bytes).hexdigest()
    closure = builder_code_closure_sha256()
    assets = sorted({str(value) for value in frame["asset"].unique().to_list()})
    first_bar = str(frame["timestamp"].min())
    last_bar = str(frame["timestamp"].max())
    manifest: Manifest = build_manifest(
        spec=spec,
        parquet_sha256=parquet_sha256,
        parquet_bytes=len(parquet_bytes),
        dataset_relative_path=dataset_relative.as_posix(),
        raw_inputs=[record.to_dict() for record in bundle.inputs],
        raw_inputs_merkle_sha256=bundle.merkle_sha256,
        quality_gate_report=outcome.report,
        source_summary=bundle.summary,
        source_audit=bundle.audit,
        builder_closure_sha256=closure,
        rows=frame.height,
        first_bar=first_bar,
        last_bar=last_bar,
        assets=assets,
        lineage=bundle.summary.get("lineage"),
    )
    published = publish_once(
        dataset_path=dataset_path,
        parquet_bytes=parquet_bytes,
        manifest_path=manifest_path,
        manifest=manifest,
    )
    return BuildOutcome(
        dataset_id=spec.dataset_id,
        dataset_path=dataset_relative.as_posix(),
        manifest_path=manifest_relative.as_posix(),
        parquet_sha256=parquet_sha256,
        manifest_sha256=published.manifest.sha256,
        spec_sha256=spec.spec_sha256(),
        raw_inputs_merkle_sha256=bundle.merkle_sha256,
        builder_code_closure_sha256=closure,
        rows=frame.height,
        assets=tuple(assets),
        first_bar=first_bar,
        last_bar=last_bar,
        quality_gate_report=outcome.report,
        manifest=manifest.payload,
        source_audit=bundle.audit,
    )
