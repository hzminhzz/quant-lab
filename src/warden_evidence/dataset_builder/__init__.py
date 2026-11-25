"""Spec-driven, write-once dataset builder with a mandatory ML4T quality gate (#103).

Entry points for the CLI / MCP wire node::

    from warden_evidence.dataset_builder import (
        DatasetSpec, DatasetSpecError, load_dataset_spec, parse_dataset_spec,
        build_dataset, BuildOutcome, DatasetBuildError,
        SourceContainmentError, TemporalFenceError, QualityGateError, WriteOnceError,
        LocalTracerAdapter, register_source_adapter, get_source_adapter,
        LocalRawFileSystem, RawFileSystem,
    )

    spec = load_dataset_spec("specs/syncster_es_15m_r1.yaml")
    outcome = build_dataset(spec, repo_root=Path("."), source_root=Path("data/raw"))
    print(outcome.to_dict())          # JSON for `warden data build --json`

The builder never imports a Parquet reader (raw Parquet inputs take an injected
``parquet_reader``), never writes outside the two declared directories, and never
overwrites an existing dataset or manifest.
"""

from __future__ import annotations

# Imported for its registration side effect: ``binance_um_perp`` joins the adapter registry
# exactly when this package is imported, never through a call-site import.
import warden_evidence.dataset_builder.crypto_perp as _crypto_perp  # noqa: F401
from warden_evidence.dataset_builder.adapters import (
    BUILDER_AUDIT_COLUMNS,
    CompositeUnionAdapter,
    DerivedFromDatasetAdapter,
    DukascopyLocalAdapter,
    LocalTracerAdapter,
    RawBundle,
    SourceAdapter,
    get_source_adapter,
    register_source_adapter,
    source_adapter_names,
)
from warden_evidence.dataset_builder.build import (
    DEFAULT_DATASET_DIR,
    DEFAULT_MANIFEST_DIR,
    BuildOutcome,
    DatasetBuildError,
    build_dataset,
)
from warden_evidence.dataset_builder.containment import (
    AdapterError,
    ListingRawFileSystem,
    LocalRawFileSystem,
    RawFileSystem,
    SourceContainmentError,
    canonical_source_path,
)
from warden_evidence.dataset_builder.merkle import MERKLE_VERSION, RawInputRecord, merkle_sha256
from warden_evidence.dataset_builder.publish import (
    BUILDER_NAME,
    BUILDER_VERSION,
    MANIFEST_SCHEMA_VERSION,
    Manifest,
    Sealed,
    WriteOnceError,
    builder_code_closure_sha256,
    holdout_pin,
    publish_sealed,
    with_holdout,
)
from warden_evidence.dataset_builder.quality import (
    GATE_VERSION,
    GateOutcome,
    QualityGateError,
    TemporalFenceError,
    run_quality_gate,
)
from warden_evidence.dataset_builder.seal import (
    CONFIRMATION_TOKEN,
    HOLDOUT_DIR,
    SealError,
    SealOutcome,
    SealRefused,
    seal_holdout,
    sealed_holdout_target,
)
from warden_evidence.dataset_builder.spec import (
    ASSET_CLASSES,
    EVIDENCE_TIERS,
    FREQUENCIES,
    PRICE_BASES,
    SCHEMA_VERSION,
    SESSION_START_WEEKDAYS,
    VOLUME_SEMANTICS,
    AnomalyFilter,
    CryptoPartitionSpec,
    DatasetSpec,
    DatasetSpecError,
    FundingSpec,
    QualityGateSpec,
    ResamplingSpec,
    SessionAnchor,
    SourceInput,
    SourceSpec,
    TimeContract,
    TimeWindow,
    UniverseSpec,
    load_dataset_spec,
    parse_daily_roll_anchor,
    parse_dataset_spec,
)

__all__ = [
    "ASSET_CLASSES",
    "BUILDER_AUDIT_COLUMNS",
    "BUILDER_NAME",
    "BUILDER_VERSION",
    "CONFIRMATION_TOKEN",
    "DEFAULT_DATASET_DIR",
    "DEFAULT_MANIFEST_DIR",
    "EVIDENCE_TIERS",
    "FREQUENCIES",
    "GATE_VERSION",
    "HOLDOUT_DIR",
    "MANIFEST_SCHEMA_VERSION",
    "MERKLE_VERSION",
    "PRICE_BASES",
    "SCHEMA_VERSION",
    "SESSION_START_WEEKDAYS",
    "VOLUME_SEMANTICS",
    "AdapterError",
    "AnomalyFilter",
    "BuildOutcome",
    "CompositeUnionAdapter",
    "CryptoPartitionSpec",
    "DatasetBuildError",
    "DatasetSpec",
    "DatasetSpecError",
    "DerivedFromDatasetAdapter",
    "DukascopyLocalAdapter",
    "FundingSpec",
    "GateOutcome",
    "ListingRawFileSystem",
    "LocalRawFileSystem",
    "LocalTracerAdapter",
    "Manifest",
    "QualityGateError",
    "QualityGateSpec",
    "RawBundle",
    "RawFileSystem",
    "RawInputRecord",
    "ResamplingSpec",
    "SealError",
    "SealOutcome",
    "SealRefused",
    "Sealed",
    "SessionAnchor",
    "SourceAdapter",
    "SourceContainmentError",
    "SourceInput",
    "SourceSpec",
    "TemporalFenceError",
    "TimeContract",
    "TimeWindow",
    "UniverseSpec",
    "WriteOnceError",
    "build_dataset",
    "builder_code_closure_sha256",
    "canonical_source_path",
    "get_source_adapter",
    "holdout_pin",
    "load_dataset_spec",
    "merkle_sha256",
    "parse_daily_roll_anchor",
    "parse_dataset_spec",
    "publish_sealed",
    "register_source_adapter",
    "run_quality_gate",
    "seal_holdout",
    "sealed_holdout_target",
    "source_adapter_names",
    "with_holdout",
]
