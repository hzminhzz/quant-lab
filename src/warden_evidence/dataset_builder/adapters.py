"""Pluggable raw-source adapters.

The builder knows nothing about venues.  An adapter turns one declared
``source`` block into a canonical raw frame plus the exact bytes it read, so the
Merkle commitment is computed over read inputs rather than over a path list.

Four adapters ship here, and each refuses the shape it is not:

* ``local_tracer`` — a local-file tracer that reads declared CSV, TSV, JSON/NDJSON
  and (with an injected reader) Parquet inputs beneath one source root;
* ``dukascopy_local`` — a Dukascopy-shaped vendor feed: a non-crypto feed has no
  funding rate to keep and its volume is a tick count, so the adapter enforces both
  declarations instead of letting them drift into the panel;
* ``derived_from_dataset`` — resample one digest-verified built dataset;
* ``composite_union`` — union several digest-verified built datasets into one panel.

Vendored lake adapters register through :func:`register_source_adapter` without
touching the builder.  No adapter imports a Parquet reader: the owning caller injects
the one reader (``warden_evidence.integrity`` allows it only in the market-data
reader), so a new adapter cannot quietly widen the read surface.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Protocol, runtime_checkable

import polars as pl

from warden_evidence.dataset_builder.containment import (
    AdapterError,
    RawFileSystem,
    canonical_source_path,
)
from warden_evidence.dataset_builder.merkle import RawInputRecord, merkle_sha256
from warden_evidence.dataset_builder.spec import (
    CANONICAL_SOURCE_COLUMNS,
    FREQUENCY_SECONDS,
    DatasetSpec,
    SourceInput,
    SourceSpec,
)

#: Column aliases a raw tracer file may use for the canonical names.
ALIASES: dict[str, str] = {
    "symbol": "asset",
    "ticker": "asset",
    "instrument": "asset",
    "datetime": "timestamp",
    "ts": "timestamp",
    "time": "timestamp",
}

_UTC_ALIASES = frozenset({"UTC", "Etc/UTC", "utc", "Z"})

#: Columns the builder itself adds to a built dataset.  A parent dataset read by a derived
#: adapter carries them; they are re-derived here, so they are not "unknown raw columns".
BUILDER_AUDIT_COLUMNS: tuple[str, ...] = ("observed_sub_bars",)

ParquetReader = Callable[[bytes], pl.DataFrame]


@dataclass(frozen=True)
class RawBundle:
    """Canonical raw frame plus the commitment to the bytes it came from."""

    adapter: str
    frame: pl.DataFrame
    inputs: tuple[RawInputRecord, ...]
    merkle_sha256: str
    summary: dict[str, Any] = field(default_factory=dict)
    #: Adapter-owned audit the manifest publishes verbatim (universe/partition decisions,
    #: funding substitutions).  Empty for an adapter that has nothing to declare.
    audit: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SourceAdapter(Protocol):
    name: str

    def load(self, spec: DatasetSpec, *, source: Any = None) -> RawBundle:  # pragma: no cover
        ...


_ADAPTER_FACTORIES: dict[str, Callable[..., SourceAdapter]] = {}


def register_source_adapter(name: str) -> Callable[[Callable[..., SourceAdapter]], Callable[..., SourceAdapter]]:
    """Register an adapter factory under a spec ``source.adapter`` name."""

    def decorator(factory: Callable[..., SourceAdapter]) -> Callable[..., SourceAdapter]:
        if not name:
            raise ValueError("adapter name must not be empty")
        _ADAPTER_FACTORIES[name] = factory
        return factory

    return decorator


def source_adapter_names() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTER_FACTORIES))


def get_source_adapter(name: str, **kwargs: Any) -> SourceAdapter:
    try:
        factory = _ADAPTER_FACTORIES[name]
    except KeyError:
        raise AdapterError(
            f"unknown source adapter {name!r}; registered adapters: {list(source_adapter_names())}"
        ) from None
    return factory(**kwargs)


def _parse_delimited(payload: bytes, separator: str) -> pl.DataFrame:
    # Read every scalar as text, then cast explicitly: inferred dtypes would make
    # the output depend on the rows seen (a single bad row changes the schema).
    try:
        return pl.read_csv(
            io.BytesIO(payload),
            separator=separator,
            infer_schema_length=0,
            try_parse_dates=False,
            null_values=[""],
        )
    except Exception as exc:  # polars raises several concrete parse errors
        raise AdapterError(f"cannot parse delimited raw input: {exc}") from exc


def _parse_json(payload: bytes) -> pl.DataFrame:
    try:
        return pl.read_json(io.BytesIO(payload))
    except Exception as exc:
        raise AdapterError(f"cannot parse JSON raw input: {exc}") from exc


def _parse_ndjson(payload: bytes) -> pl.DataFrame:
    try:
        return pl.read_ndjson(io.BytesIO(payload))
    except Exception as exc:
        raise AdapterError(f"cannot parse NDJSON raw input: {exc}") from exc


def require_utc_column(
    frame: pl.DataFrame, source: SourceInput, *, column: str = "timestamp"
) -> pl.DataFrame:
    """Return ``frame`` with ``column`` as tz-aware ``Datetime('us', 'UTC')``.

    A naive column is only ever labelled from a declared ``source_timezone``; a foreign
    zone is converted by instant, never relabelled; a string column must carry its own
    offset.  ``column`` is parameterised so a listing catalog (two timestamps per row)
    goes through the same discipline as a bar or a settlement file.
    """
    dtype = frame.schema[column]
    if isinstance(dtype, pl.Datetime):
        if dtype.time_zone is None:
            if not source.source_timezone:
                raise AdapterError(
                    f"{source.path}: naive timestamps require source_timezone; "
                    "assuming UTC would silently relabel the data"
                )
            frame = frame.with_columns(
                pl.col(column).dt.replace_time_zone(source.source_timezone)
            )
        elif source.source_timezone and dtype.time_zone not in _UTC_ALIASES:
            if dtype.time_zone != source.source_timezone:
                raise AdapterError(
                    f"{source.path}: {column} column is {dtype.time_zone!r} but "
                    f"source_timezone declares {source.source_timezone!r}"
                )
        # Convert by instant: the zone name is replaced by UTC, the wall clock is
        # shifted, so no timestamp silently keeps a foreign zone.
        return frame.with_columns(
            pl.col(column).dt.convert_time_zone("UTC").cast(pl.Datetime("us", "UTC"))
        )
    if dtype == pl.String:
        if source.source_timezone:
            try:
                converted = pl.col(column).str.to_datetime(
                    time_zone=source.source_timezone, strict=True
                )
            except Exception as exc:
                raise AdapterError(f"{source.path}: invalid {column}: {exc}") from exc
            return frame.with_columns(
                converted.dt.convert_time_zone("UTC").cast(pl.Datetime("us", "UTC"))
            )
        if frame.select(
            pl.col(column).str.contains(r"(Z|[+-]\d{2}:\d{2})$").all()
        ).item() is not True:
            raise AdapterError(
                f"{source.path}: {column} strings carry no offset and no source_timezone "
                "is declared; refusing to assume a zone"
            )
        try:
            return frame.with_columns(
                pl.col(column)
                .str.to_datetime(time_zone="UTC", strict=True)
                .dt.convert_time_zone("UTC")
                .cast(pl.Datetime("us", "UTC"))
            )
        except Exception as exc:
            raise AdapterError(f"{source.path}: invalid {column}: {exc}") from exc
    raise AdapterError(
        f"{source.path}: unsupported {column} dtype {dtype}; expected Datetime or ISO-8601 text"
    )


def _require_utc_dtype(frame: pl.DataFrame, source: SourceInput) -> pl.DataFrame:
    return require_utc_column(frame, source, column="timestamp")


def canonicalise_frame(
    frame: pl.DataFrame, source: SourceInput, *, ignored_columns: Iterable[str] = ()
) -> tuple[pl.DataFrame, tuple[str, ...]]:
    """Normalise one lake file to the canonical columns, or refuse it.

    ``ignored_columns`` is a *declared* vendor-column drop: it returns which names were
    actually dropped so the manifest can prove nothing was discarded silently.  Every
    canonical column, and every undeclared vendor column, is still refused rather than
    guessed at.
    """
    dropped: list[str] = []
    for name in ignored_columns:
        if name in frame.columns:
            frame = frame.drop(name)
            dropped.append(name)
    if "premium_index_close" in frame.columns or "premiumIndexClose" in frame.columns:
        raise AdapterError(
            f"{source.path}: 'premium_index_close' is not a funding rate; declare it in "
            "ignored_columns to drop it, never alias it into funding"
        )
    renamed = {
        name: target
        for name, target in ALIASES.items()
        if name in frame.columns and target not in frame.columns
    }
    if renamed:
        frame = frame.rename(renamed)
    if source.timestamp_column != "timestamp" and source.timestamp_column in frame.columns:
        if "timestamp" in frame.columns:
            raise AdapterError(
                f"{source.path}: both 'timestamp' and declared timestamp_column "
                f"{source.timestamp_column!r} are present"
            )
        frame = frame.rename({source.timestamp_column: "timestamp"})
    if source.entity_column != "asset" and source.entity_column in frame.columns:
        if "asset" in frame.columns:
            raise AdapterError(
                f"{source.path}: both 'asset' and declared entity_column "
                f"{source.entity_column!r} are present"
            )
        frame = frame.rename({source.entity_column: "asset"})

    missing = {"timestamp", "asset"} - set(frame.columns)
    if missing:
        raise AdapterError(f"{source.path}: raw input lacks columns {sorted(missing)}")
    unknown = sorted(set(frame.columns) - CANONICAL_SOURCE_COLUMNS)
    if unknown:
        # Dropping a column could discard funding, quotes or a venue tag silently.
        raise AdapterError(
            f"{source.path}: unknown raw columns {unknown}; declare them in the canonical "
            "schema instead of having the builder drop them"
        )
    frame = _require_utc_dtype(frame, source)
    return frame, tuple(dropped)


def _canonicalise(frame: pl.DataFrame, source: SourceInput) -> pl.DataFrame:
    """``canonicalise_frame`` for an adapter that declares no vendor-column drops."""
    return canonicalise_frame(frame, source)[0]


class LocalTracerAdapter:
    """Read declared local files beneath one source root.

    ``parquet_reader`` is injected rather than imported here: the evaluator tree
    permits Parquet reads only in the declared market-data readers
    (``warden_ops.integrity.check_ast_boundaries``), so the owning caller supplies
    the single reader for raw Parquet lake files.
    """

    name = "local_tracer"

    def __init__(self, *, fs: RawFileSystem, parquet_reader: ParquetReader | None = None) -> None:
        self._fs = fs
        self._parquet_reader = parquet_reader

    def _parse(self, source: SourceInput, payload: bytes) -> pl.DataFrame:
        if source.format == "csv":
            return _parse_delimited(payload, ",")
        if source.format == "tsv":
            return _parse_delimited(payload, "\t")
        if source.format == "json":
            return _parse_json(payload)
        if source.format == "ndjson":
            return _parse_ndjson(payload)
        if source.format == "parquet":
            if self._parquet_reader is None:
                raise AdapterError(
                    f"{source.path}: Parquet raw input requires an injected parquet_reader; "
                    "the builder never imports a Parquet reader itself"
                )
            try:
                return self._parquet_reader(payload)
            except Exception as exc:
                raise AdapterError(f"{source.path}: cannot parse Parquet raw input: {exc}") from exc
        raise AdapterError(f"{source.path}: unsupported format {source.format!r}")

    def load(self, spec: DatasetSpec, *, source: SourceSpec | None = None) -> RawBundle:
        declared = source or spec.source
        read: list[tuple[str, bytes]] = []
        frames: list[pl.DataFrame] = []
        summary_inputs: list[dict[str, Any]] = []
        # Declaration order is irrelevant to the result: inputs are visited by
        # canonical path so the Merkle tree and concat order are stable.
        ordered = sorted(declared.inputs.items(), key=lambda item: item[1].path)
        for key, raw_input in ordered:
            if raw_input.role != "ohlcv":
                raise AdapterError(
                    f"{raw_input.path}: role {raw_input.role!r} needs the lake adapter that consumes "
                    "it; local_tracer reads every declared input as OHLCV bars"
                )
            relative: PurePosixPath = canonical_source_path(raw_input.path)
            # Containment precedes the first filesystem call, per input.
            payload = self._fs.read_bytes(relative)
            read.append((relative.as_posix(), payload))
            frame = _canonicalise(self._parse(raw_input, payload), raw_input)
            frames.append(frame)
            summary_inputs.append(
                {
                    "key": key,
                    "path": relative.as_posix(),
                    "format": raw_input.format,
                    "declared_timezone": raw_input.source_timezone,
                    "rows": frame.height,
                }
            )
        merkle, records = merkle_sha256(read)
        combined = pl.concat(frames, how="vertical") if len(frames) > 1 else frames[0]
        return RawBundle(
            adapter=self.name,
            frame=combined,
            inputs=tuple(records),
            merkle_sha256=merkle,
            summary={
                "adapter": self.name,
                "inputs": summary_inputs,
                "rows_read": combined.height,
            },
        )


@register_source_adapter(LocalTracerAdapter.name)
def _local_tracer_factory(*, fs: RawFileSystem, parquet_reader: ParquetReader | None = None) -> SourceAdapter:
    return LocalTracerAdapter(fs=fs, parquet_reader=parquet_reader)


class DukascopyLocalAdapter(LocalTracerAdapter):
    """A Dukascopy-shaped vendor feed read from local files.

    Two things a Dukascopy export cannot tell you and a spec must not pretend:

    * a non-crypto feed carries no funding.  A ``funding_rate`` column of synthetic
      ``0.0`` is dropped (and counted) rather than published as if the vendor had
      reported funding;
    * FX volume is a tick count, never lots, so ``volume_semantics='tick_count_proxy'``
      is required and a claims-of-contracts feed is refused.

    A daily FX feed must also declare its venue close: without ``daily_roll_anchor``
    the builder would bin on the UTC calendar and manufacture a two-hour "Sunday" bar.
    """

    name = "dukascopy_local"

    def load(self, spec: DatasetSpec, *, source: SourceSpec | None = None) -> RawBundle:
        if spec.asset_class != "crypto_perps" and spec.price_basis == "ohlcv_funding":
            raise AdapterError(
                f"{spec.dataset_id}: a {spec.asset_class} Dukascopy feed has no funding rate, so "
                "price_basis 'ohlcv_funding' cannot be declared for it"
            )
        if spec.asset_class != "crypto_perps" and spec.volume_semantics != "tick_count_proxy":
            raise AdapterError(
                f"{spec.dataset_id}: a {spec.asset_class} Dukascopy feed carries tick counts, "
                "not contracts; declare volume_semantics='tick_count_proxy'"
            )
        if spec.frequency == "1d" and spec.asset_class != "crypto_perps" and spec.session_anchor is None:
            raise AdapterError(
                f"{spec.dataset_id}: a daily {spec.asset_class} session feed must declare "
                "time_contract.daily_roll_anchor (for example '17:00 America/New_York'); "
                "binning a 24/5 venue on the UTC calendar manufactures a two-hour Sunday bar"
            )
        bundle = super().load(spec, source=source)
        frame = bundle.frame
        if spec.asset_class == "crypto_perps" or "funding_rate" not in frame.columns:
            return bundle
        dropped = frame.height
        frame = frame.drop("funding_rate")
        summary = dict(bundle.summary)
        summary.update(
            {
                "adapter": self.name,
                "volume_semantics": spec.volume_semantics,
                "dropped_columns": [
                    {
                        "column": "funding_rate",
                        "reason": (
                            f"a {spec.asset_class} feed has no funding rate; a synthetic 0.0 "
                            "column is dropped instead of published as vendor funding"
                        ),
                        "rows": dropped,
                    }
                ],
            }
        )
        return RawBundle(
            adapter=self.name,
            frame=frame,
            inputs=bundle.inputs,
            merkle_sha256=bundle.merkle_sha256,
            summary=summary,
        )


@register_source_adapter(DukascopyLocalAdapter.name)
def _dukascopy_local_factory(
    *, fs: RawFileSystem, parquet_reader: ParquetReader | None = None
) -> SourceAdapter:
    return DukascopyLocalAdapter(fs=fs, parquet_reader=parquet_reader)


def _verify_pinned(payload: bytes, source: SourceInput, *, label: str) -> str:
    """Check the declared digest *before* the bytes are parsed or resampled.

    Verification precedes parsing deliberately: a payload that fails its pin must never
    reach a frame, so a truncated or swapped parent file is refused as a pin mismatch
    rather than surfacing later as a plausible-looking panel.
    """
    if source.sha256 is None:  # pragma: no cover - the spec refuses this first
        raise AdapterError(f"{label} {source.path!r} declares no sha256 pin")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != source.sha256:
        raise AdapterError(
            f"{label} {source.path!r} digest {actual} does not match the declared pin "
            f"{source.sha256}; a derived build refuses an unpinned parent"
        )
    return actual


class _PinnedDatasetAdapter:
    """Shared reading for adapters that derive from built (digest-pinned) datasets."""

    name = ""

    def __init__(self, *, fs: RawFileSystem, parquet_reader: ParquetReader | None = None) -> None:
        if parquet_reader is None:
            raise AdapterError(
                f"adapter {self.name!r} reads built Parquet datasets and requires an injected "
                "parquet_reader; the builder never imports a Parquet reader itself"
            )
        self._fs = fs
        self._parquet_reader = parquet_reader

    def _read_component(
        self, key: str, source: SourceInput
    ) -> tuple[pl.DataFrame, bytes, dict[str, Any]]:
        relative: PurePosixPath = canonical_source_path(source.path)
        # Containment precedes the first filesystem call, per component.
        payload = self._fs.read_bytes(relative)
        digest = _verify_pinned(payload, source, label=f"parent dataset {key!r}")
        try:
            raw = self._parquet_reader(payload)
        except Exception as exc:
            raise AdapterError(f"{source.path}: cannot parse pinned Parquet dataset: {exc}") from exc
        if not isinstance(raw, pl.DataFrame):
            raise AdapterError(f"{source.path}: parquet_reader returned {type(raw).__name__}")
        # ``observed_sub_bars`` is the builder's own audit column on a built dataset, not a
        # source column: it is re-derived by this build, so it is dropped from the input
        # frame rather than refused as an unknown column.
        audit = [column for column in BUILDER_AUDIT_COLUMNS if column in raw.columns]
        if audit:
            raw = raw.drop(audit)
        frame = _canonicalise(raw, source)
        lineage = {
            "key": key,
            "path": relative.as_posix(),
            "sha256": digest,
            "verified": True,
            "rows": frame.height,
            "first_bar": str(frame["timestamp"].min()),
            "last_bar": str(frame["timestamp"].max()),
            "assets": sorted({str(value) for value in frame["asset"].unique().to_list()}),
        }
        return frame, payload, lineage

    @staticmethod
    def _assert_grid_is_coarser_or_equal(frame: pl.DataFrame, spec: DatasetSpec) -> int:
        """Refuse a parent grid that cannot be resampled onto the declared frequency."""
        stamps = frame["timestamp"].unique().sort()
        if stamps.len() < 2:
            raise AdapterError(f"{spec.dataset_id}: parent dataset holds fewer than two bars")
        deltas = stamps.diff().drop_nulls()
        step_seconds = int(deltas.dt.total_seconds().min() or 0)
        if step_seconds <= 0:
            raise AdapterError(f"{spec.dataset_id}: parent dataset has no positive bar spacing")
        target = FREQUENCY_SECONDS[spec.frequency]
        if step_seconds > target:
            raise AdapterError(
                f"{spec.dataset_id}: parent bars are {step_seconds}s wide, coarser than the "
                f"declared {spec.frequency} ({target}s); a resample cannot invent resolution"
            )
        if target % step_seconds:
            raise AdapterError(
                f"{spec.dataset_id}: declared {spec.frequency} ({target}s) is not a whole "
                f"multiple of the parent step ({step_seconds}s); the derivation would be ragged"
            )
        return step_seconds


class DerivedFromDatasetAdapter(_PinnedDatasetAdapter):
    """Resample exactly one digest-verified built dataset onto the declared frequency."""

    name = "derived_from_dataset"

    def load(self, spec: DatasetSpec, *, source: SourceSpec | None = None) -> RawBundle:
        declared = source or spec.source
        (key, parent), = sorted(declared.inputs.items(), key=lambda item: item[1].path)
        frame, payload, lineage = self._read_component(key, parent)
        step_seconds = self._assert_grid_is_coarser_or_equal(frame, spec)
        # Commit to the bytes that were verified, not to a path: one read, one digest.
        merkle, records = merkle_sha256([(lineage["path"], payload)])
        return RawBundle(
            adapter=self.name,
            frame=frame,
            inputs=tuple(records),
            merkle_sha256=merkle,
            summary={
                "adapter": self.name,
                "kind": spec.source.kind,
                "rows_read": frame.height,
                "parent_step_seconds": step_seconds,
                "lineage": {"kind": spec.source.kind, "parents": [lineage]},
            },
        )


class CompositeUnionAdapter(_PinnedDatasetAdapter):
    """Union several digest-verified built datasets into one panel."""

    name = "composite_union"

    def load(self, spec: DatasetSpec, *, source: SourceSpec | None = None) -> RawBundle:
        declared = source or spec.source
        frames: list[pl.DataFrame] = []
        payloads: list[tuple[str, bytes]] = []
        lineage: list[dict[str, Any]] = []
        seen_assets: dict[str, str] = {}
        for key, component in sorted(declared.inputs.items(), key=lambda item: item[1].path):
            frame, payload, item_lineage = self._read_component(key, component)
            for asset in item_lineage["assets"]:
                if asset in seen_assets:
                    raise AdapterError(
                        f"composite components {seen_assets[asset]!r} and {key!r} both carry "
                        f"asset {asset!r}; a union of overlapping panels duplicates every bar"
                    )
                seen_assets[asset] = key
            frames.append(frame)
            payloads.append((item_lineage["path"], payload))
            lineage.append(item_lineage)
        try:
            combined = pl.concat(frames, how="vertical")
        except Exception as exc:
            raise AdapterError(f"composite components do not share one canonical schema: {exc}") from exc
        _PinnedDatasetAdapter._assert_grid_is_coarser_or_equal(combined, spec)
        # Commit to the verified bytes of every component, not to a path list.
        merkle, records = merkle_sha256(payloads)
        return RawBundle(
            adapter=self.name,
            frame=combined,
            inputs=tuple(records),
            merkle_sha256=merkle,
            summary={
                "adapter": self.name,
                "kind": spec.source.kind,
                "rows_read": combined.height,
                "lineage": {"kind": spec.source.kind, "parents": lineage},
            },
        )


@register_source_adapter(DerivedFromDatasetAdapter.name)
def _derived_factory(*, fs: RawFileSystem, parquet_reader: ParquetReader | None = None) -> SourceAdapter:
    return DerivedFromDatasetAdapter(fs=fs, parquet_reader=parquet_reader)


@register_source_adapter(CompositeUnionAdapter.name)
def _composite_factory(*, fs: RawFileSystem, parquet_reader: ParquetReader | None = None) -> SourceAdapter:
    return CompositeUnionAdapter(fs=fs, parquet_reader=parquet_reader)
