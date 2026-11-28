"""Strict declarative dataset specification for the spec-driven data builder (issue #103).

Every field is required unless a default is explicitly written here, and
``extra="forbid"`` means a typo is a build failure instead of a silently ignored
knob.  The specification owns the things a dataset *is*, so the builder can be a
dumb executor: identity, provenance tier, universe, time contract, quality gate
and the write contract.

``spec_sha256`` is the canonical digest of the parsed document (UTC-normalised
timestamps, sorted keys, no whitespace drift), so a manifest can prove which spec
produced which bytes and a rebuild under a modified spec cannot masquerade as the
same dataset.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from warden_evidence.dataset_builder.containment import canonical_source_path

SCHEMA_VERSION = 1

FREQUENCIES: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
FREQUENCY_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}
ASSET_CLASSES: tuple[str, ...] = (
    "crypto_perps",
    "fx",
    "indices",
    "metals",
    "commodities",
    "multi_asset",
)
EVIDENCE_TIERS: tuple[str, ...] = (
    "native_venue",
    "research_proxy",
    "unverified_roll",
    "synthetic",
)
PRICE_BASES: tuple[str, ...] = ("ohlcv", "ohlcv_funding", "quote_ohlcv")
VOLUME_SEMANTICS: tuple[str, ...] = ("real_contracts", "base_and_quote", "tick_count_proxy")
SOURCE_FORMATS: tuple[str, ...] = ("csv", "tsv", "json", "ndjson", "parquet")

DATASET_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*_r[0-9]+$")
FILTER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*$")

#: Columns a raw input may carry.  Anything else is refused rather than dropped:
#: a silently discarded ``funding_rate`` changes what the dataset means.
CANONICAL_SOURCE_COLUMNS: frozenset[str] = frozenset(
    {"timestamp", "asset", "open", "high", "low", "close", "volume", "funding_rate", "bid", "ask"}
)

OHLCV = ("open", "high", "low", "close", "volume")

#: Session weekdays (Python ``weekday()``: Monday=0) for a Sun-close-to-Fri-close FX
#: week: the session that opens Sunday 17:00 New York is the first of the week and the
#: Thursday-open session is the last.  A Friday or Saturday session does not exist.
SESSION_START_WEEKDAYS: frozenset[int] = frozenset({6, 0, 1, 2, 3})

_ANCHOR_PATTERN = re.compile(r"^(?P<hour>[01][0-9]|2[0-3]):(?P<minute>[0-5][0-9]) (?P<zone>\S+)$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DatasetSpecError(ValueError):
    """A dataset specification is absent, malformed or internally inconsistent."""


@dataclass(frozen=True)
class SessionAnchor:
    """A parsed ``daily_roll_anchor``: the venue's session close, in its own zone.

    ``"17:00 America/New_York"`` means a daily bar runs from 17:00 New York to the next
    17:00 New York, so a Sunday-evening reopen joins the session that opens Sunday 17:00
    instead of becoming a two-hour UTC "Sunday" bar.  The zone is kept (not reduced to a
    fixed offset) precisely because the anchor must follow DST: the same session open is
    22:00 UTC in winter and 21:00 UTC in summer.
    """

    hour: int
    minute: int
    zone: str

    @property
    def time_of_day(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    @property
    def text(self) -> str:
        """Canonical spelling, which is what a manifest records."""
        return f"{self.time_of_day} {self.zone}"

    @property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.zone)

    def offset_minutes(self) -> int:
        return self.hour * 60 + self.minute

    def session_instant(self, day: Any) -> datetime:
        """UTC instant of the session that opens at the anchor on ``day`` (a ``date``)."""
        return datetime.combine(day, time(self.hour, self.minute), tzinfo=self.zoneinfo).astimezone(UTC)

    def session_starts(self, start: datetime, end: datetime) -> list[datetime]:
        """Every session open in ``[start, end)``, on the venue's session clock.

        Only Sun–Thu opens exist (``SESSION_START_WEEKDAYS``), which is what turns an
        every-UTC-day FX calendar (~313 bars/year) into a five-session week (~260).
        """
        local_start = start.astimezone(self.zoneinfo)
        day = local_start.date()
        # Start one day early so a window that opens mid-session still sees that session.
        day -= timedelta(days=1)
        out: list[datetime] = []
        for _ in range(400):
            instant = self.session_instant(day)
            if instant >= end:
                break
            if instant >= start and day.weekday() in SESSION_START_WEEKDAYS:
                out.append(instant)
            day += timedelta(days=1)
        return out

    def session_start_for(self, instant: datetime) -> datetime:
        """The session open that ``instant`` belongs to (pure Python, mirrors the Polars path)."""
        local = instant.astimezone(self.zoneinfo)
        shifted = local - timedelta(minutes=self.offset_minutes())
        return self.session_instant(shifted.date())


def parse_daily_roll_anchor(value: str | None) -> SessionAnchor | None:
    """Parse ``"HH:MM <IANA zone>"``; ``None`` passes through unchanged."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("daily_roll_anchor must be a string 'HH:MM <IANA zone>' or null")
    text = " ".join(value.split())
    match = _ANCHOR_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError(
            f"daily_roll_anchor {value!r} must be 'HH:MM <IANA zone>' "
            "(for example '17:00 America/New_York')"
        )
    zone = match.group("zone")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"daily_roll_anchor names an unknown timezone {zone!r}: {exc}") from exc
    return SessionAnchor(hour=int(match.group("hour")), minute=int(match.group("minute")), zone=zone)


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must carry an explicit timezone offset (never assume UTC)")
    return value.astimezone(UTC)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class TimeWindow(_StrictModel):
    start_inclusive: datetime
    exploratory_end_exclusive: datetime
    holdout_start_inclusive: datetime
    holdout_end_exclusive: datetime

    @field_validator(
        "start_inclusive",
        "exploratory_end_exclusive",
        "holdout_start_inclusive",
        "holdout_end_exclusive",
    )
    @classmethod
    def _normalise_utc(cls, value: datetime, info) -> datetime:
        return _utc(value, info.field_name or "timestamp")

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if not (
            self.start_inclusive
            < self.exploratory_end_exclusive
            <= self.holdout_start_inclusive
            < self.holdout_end_exclusive
        ):
            raise ValueError(
                "time_window must satisfy start_inclusive < exploratory_end_exclusive "
                "<= holdout_start_inclusive < holdout_end_exclusive"
            )
        return self


class TimeContract(_StrictModel):
    timezone: Literal["UTC"]
    bar_timestamp_semantics: Literal["open_left_closed_left"]
    session_calendar: str = Field(min_length=1)
    #: Venue session close, e.g. ``"17:00 America/New_York"``.  Declaring it makes a
    #: daily dataset bin on the venue's session clock instead of the UTC calendar; it is
    #: never assumed, because a wrong anchor silently relabels every daily bar.
    daily_roll_anchor: str | None = None

    @field_validator("session_calendar")
    @classmethod
    def _known_calendar(cls, value: str) -> str:
        try:
            from ml4t.specs import FeedSpec

            FeedSpec(calendar=value, time_contract_version=2)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError(f"unknown session_calendar {value!r}: {exc}") from exc
        return value

    @field_validator("daily_roll_anchor")
    @classmethod
    def _anchor(cls, value: str | None) -> str | None:
        anchor = parse_daily_roll_anchor(value)
        return None if anchor is None else anchor.text

    @property
    def anchor(self) -> SessionAnchor | None:
        return parse_daily_roll_anchor(self.daily_roll_anchor)


class SourceInput(_StrictModel):
    path: str
    format: Literal["csv", "tsv", "json", "ndjson", "parquet"]
    timestamp_column: str = "timestamp"
    entity_column: str = "asset"
    #: Zone of a naive raw timestamp column.  A tz-aware column is converted by
    #: instant, never relabelled; a naive column without this declaration fails.
    source_timezone: str | None = None
    #: What the declared file *is*.  A lake adapter uses the role to decide how to read
    #: a file; ``local_tracer`` reads only ``ohlcv`` inputs and refuses any other role
    #: rather than silently treating a listing catalog as bars.
    role: Literal["ohlcv", "funding", "symbol_catalog"] = "ohlcv"
    #: Digest pin for a *built* dataset this spec derives from.  Required by the
    #: ``derived_from_dataset``/``composite_union`` kinds and refused on ``lake_raw``,
    #: where the Merkle commitment over the raw bytes is the identity instead.
    sha256: str | None = None
    ignored_columns: tuple[str, ...] = Field(default=(), exclude_if=lambda value: not value)

    @field_validator("ignored_columns")
    @classmethod
    def _ignored_columns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # Only the known auxiliary kline fields may be omitted by this OHLCV
        # adapter; never discard canonical fields or unknown venue semantics.
        allowed = {"quote_volume", "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume"}
        if set(value) - allowed or len(set(value)) != len(value):
            raise ValueError("unsupported or duplicate OHLCV auxiliary columns")
        return value

    @field_validator("path")
    @classmethod
    def _contained(cls, value: str) -> str:
        return canonical_source_path(value).as_posix()

    @field_validator("timestamp_column", "entity_column")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not value or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError(f"invalid column name {value!r}")
        return value

    @field_validator("sha256")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _DIGEST_PATTERN.fullmatch(value):
            raise ValueError(f"sha256 {value!r} is not a 64-character lowercase hex digest")
        return value


class FundingSpec(_StrictModel):
    """The funding-settlement contract a perpetual build asserts (#104).

    Settlements are aggregated **forward**: a settlement instant is attributed to the
    first bar at or after it and is never carried backwards (no ``dt.truncate`` bucket
    assignment, no stale backward ``join_asof``), and never onto a bar eight hours or
    more later.  A missing settlement fails closed unless an exact fallback rate is
    declared, in which case every substituted bar is audited in the manifest.
    """

    settlement_interval: Literal["8h"] = "8h"
    #: A venue timestamp jittered around an exact hour boundary is snapped onto it.  A
    #: settlement farther than this from every hour boundary is refused, never moved.
    snap_tolerance_seconds: int = Field(default=15, ge=0, le=300)
    missing_policy: Literal["fail", "contract_fallback"] = "fail"
    #: The exact rate charged where a settlement is missing (``contract_fallback`` only).
    fallback_rate: float | None = None

    @model_validator(mode="after")
    def _policy_is_complete(self) -> FundingSpec:
        if self.missing_policy == "contract_fallback":
            if self.fallback_rate is None:
                raise ValueError(
                    "missing_policy='contract_fallback' requires an explicit fallback_rate; "
                    "an undeclared substitution is the silent-zero bug this gate exists for"
                )
            if not math.isfinite(self.fallback_rate):
                raise ValueError("fallback_rate must be finite")
            if self.fallback_rate == 0.0:
                raise ValueError(
                    "fallback_rate must be nonzero: a declared zero is indistinguishable from "
                    "a silent 0.0 substitution; use missing_policy='fail' instead"
                )
            if abs(self.fallback_rate) > 1.0:
                raise ValueError("fallback_rate must lie within [-1, 1] per settlement")
        elif self.fallback_rate is not None:
            raise ValueError("fallback_rate is only meaningful with missing_policy='contract_fallback'")
        return self

    @property
    def settlement_window_hours(self) -> int:
        """The aggregation horizon: one declared 8h settlement interval."""
        return 8


class CryptoPartitionSpec(_StrictModel):
    """The lake layout a ``binance_um_perp`` build reads (#104).

    Nothing here enumerates partitions: the declared roots, the partition period and the
    reviewed symbol catalog are the declaration, and the *listing* is the enumeration.
    Which partitions exist is discovered by name and decided against the window **before**
    any file is opened; a partition that begins at or after ``exploratory_end_exclusive``
    is never opened.
    """

    #: Key of the declared ``source.inputs`` entry holding the listing lifecycle catalog.
    symbol_catalog: str
    #: Directory holding one subdirectory per symbol of ``<SYMBOL>-<freq>-<period>.parquet``.
    klines_root: str
    #: Directory holding one subdirectory per symbol of ``<SYMBOL>-funding-<period>.parquet``.
    funding_root: str
    partition_period: Literal["year", "month", "day"]
    timestamp_column: str = "timestamp"
    entity_column: str = "symbol"
    #: Zone of a naive raw timestamp column; ``None`` requires tz-aware timestamps.
    source_timezone: str | None = None
    #: Vendor columns dropped deliberately.  Canonical columns are refused here, and
    #: ``premium_index_close`` may only ever be *dropped*, never aliased into funding.
    ignored_columns: tuple[str, ...] = ()

    @field_validator("symbol_catalog", "klines_root", "funding_root")
    @classmethod
    def _contained(cls, value: str, info) -> str:
        if info.field_name == "symbol_catalog":
            if not FILTER_ID_PATTERN.fullmatch(value):
                raise ValueError(f"symbol_catalog {value!r} must name a declared source input key")
            return value
        return canonical_source_path(value, role=info.field_name or "crypto root").as_posix()

    @field_validator("timestamp_column", "entity_column")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not value or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError(f"invalid column name {value!r}")
        return value

    @field_validator("ignored_columns")
    @classmethod
    def _droppable(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        for name in value:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"invalid ignored column {name!r}")
            if name in CANONICAL_SOURCE_COLUMNS:
                raise ValueError(
                    f"ignored_columns may not drop the canonical column {name!r}; that would "
                    "delete data the build is supposed to carry"
                )
            if name in seen:
                raise ValueError(f"duplicate ignored column {name!r}")
            seen.add(name)
        return value

    def source_input(self, relative: str) -> SourceInput:
        """The per-partition reader declaration this lake layout implies."""
        return SourceInput(
            path=relative,
            format="parquet",
            timestamp_column=self.timestamp_column,
            entity_column=self.entity_column,
            source_timezone=self.source_timezone,
        )


class SourceSpec(_StrictModel):
    kind: Literal["lake_raw", "derived_from_dataset", "composite_union"]
    adapter: str = Field(min_length=1)
    inputs: dict[str, SourceInput] = Field(min_length=1)
    #: Extra lake declaration for the ``binance_um_perp`` adapter; absent otherwise.
    crypto: CryptoPartitionSpec | None = None

    @field_validator("inputs")
    @classmethod
    def _keys_and_paths(cls, value: dict[str, SourceInput]) -> dict[str, SourceInput]:
        if not value:
            raise ValueError("source.inputs must declare at least one raw input")
        seen: dict[str, str] = {}
        for key, item in value.items():
            if not FILTER_ID_PATTERN.fullmatch(key):
                raise ValueError(f"source input key {key!r} must be a lowercase identifier")
            if item.path in seen:
                raise ValueError(
                    f"source inputs {seen[item.path]!r} and {key!r} declare the same path "
                    f"{item.path!r}; a raw input may be hashed exactly once"
                )
            seen[item.path] = key
        return value

    @model_validator(mode="after")
    def _crypto_is_self_consistent(self) -> SourceSpec:
        if self.crypto is None:
            if any(item.role != "ohlcv" for item in self.inputs.values()):
                raise ValueError(
                    "source.inputs declares a funding/symbol_catalog role without a lake "
                    "adapter that consumes it"
                )
            return self
        if self.adapter != "binance_um_perp":
            raise ValueError(
                f"source.crypto only describes the 'binance_um_perp' adapter, not {self.adapter!r}"
            )
        catalog = [
            key for key, item in self.inputs.items() if item.role == "symbol_catalog"
        ]
        if catalog != [self.crypto.symbol_catalog]:
            raise ValueError(
                "source.inputs must declare exactly one symbol_catalog input, and it must be "
                f"{self.crypto.symbol_catalog!r}; found {sorted(catalog)}"
            )
        if any(item.role != "symbol_catalog" for item in self.inputs.values()):
            raise ValueError(
                "a binance_um_perp build discovers kline/funding partitions beneath "
                "klines_root/funding_root; those files must not also be declared as inputs"
            )
        if self.inputs[self.crypto.symbol_catalog].format not in {"csv", "tsv", "json", "ndjson"}:
            raise ValueError(
                "the symbol catalog must be a delimited or JSON file; a Parquet catalog "
                "would need an injected reader the roster decision cannot wait for"
            )
        return self

    @model_validator(mode="after")
    def _pins(self) -> SourceSpec:
        """Pinned-parent kinds must be fully pinned; a raw lake input must not pretend to be.

        A digest on a raw vendor file would be a second, weaker identity beside the Merkle
        commitment over the bytes that were actually read, so it is refused rather than
        allowed to disagree with it later.
        """
        if self.kind == "lake_raw":
            pinned = sorted(key for key, item in self.inputs.items() if item.sha256)
            if pinned:
                raise ValueError(
                    f"source inputs {pinned} declare sha256 under kind 'lake_raw'; raw inputs "
                    "are committed by Merkle — pin a built dataset through 'derived_from_dataset'"
                )
            return self
        if self.adapter != self.kind:
            raise ValueError(
                f"source kind {self.kind!r} requires adapter {self.kind!r}, got {self.adapter!r}"
            )
        unpinned = sorted(key for key, item in self.inputs.items() if not item.sha256)
        if unpinned:
            raise ValueError(
                f"source inputs {unpinned} lack a sha256 pin; kind {self.kind!r} derives only "
                "from digest-verified datasets"
            )
        not_parquet = sorted(key for key, item in self.inputs.items() if item.format != "parquet")
        if not_parquet:
            raise ValueError(
                f"source inputs {not_parquet} must declare format 'parquet' under kind "
                f"{self.kind!r}: a derived dataset is read from a built Parquet dataset"
            )
        if self.kind == "derived_from_dataset" and len(self.inputs) != 1:
            raise ValueError("kind 'derived_from_dataset' takes exactly one parent dataset")
        if self.kind == "composite_union" and len(self.inputs) < 2:
            raise ValueError("kind 'composite_union' needs at least two component datasets")
        return self

    @property
    def pinned(self) -> bool:
        return self.kind != "lake_raw"


class UniverseSpec(_StrictModel):
    selection_mode: Literal["explicit_symbols"]
    symbols: list[str] = Field(min_length=1)
    retain_delisted: bool

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for symbol in value:
            text = symbol.strip()
            if not text or text != symbol:
                raise ValueError(f"invalid universe symbol {symbol!r}")
            if text in cleaned:
                raise ValueError(f"duplicate universe symbol {text!r}")
            cleaned.append(text)
        return cleaned


class ResamplingSpec(_StrictModel):
    enabled: bool
    require_min_sub_bars: int | None = Field(default=None, ge=1)


class AnomalyFilter(_StrictModel):
    """One audited, explicitly declared row filter.

    A filter drops rows only by its own declaration: the gate records exactly how
    many rows each filter matched and removed, per asset.  There is no implicit
    drop anywhere else in the builder.
    """

    id: str
    column: Literal["open", "high", "low", "close", "volume", "bar_return"]
    comparison: Literal["gt", "ge", "lt", "le", "abs_gt", "abs_ge"]
    value: float
    assets: list[str] | None = None
    reason: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not FILTER_ID_PATTERN.fullmatch(value):
            raise ValueError(f"anomaly filter id {value!r} must be a lowercase identifier")
        return value

    @field_validator("value")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("anomaly filter value must be finite")
        return value

    @model_validator(mode="after")
    def _absolute(self) -> AnomalyFilter:
        if self.comparison.startswith("abs_") and self.value < 0:
            raise ValueError("an absolute comparison requires a non-negative threshold")
        return self


class QualityGateSpec(_StrictModel):
    max_abs_bar_return: float = Field(gt=0)
    mad_outlier_threshold: float = Field(gt=0)
    allow_zero_volume: bool
    explicit_anomaly_filters: list[AnomalyFilter]
    #: Strict by default: a hole inside an asset's own span is a coverage failure.
    #: A session-based dataset declares its tolerated ratio instead of hiding it.
    min_coverage_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _unique_filters(self) -> QualityGateSpec:
        ids = [item.id for item in self.explicit_anomaly_filters]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate anomaly filter ids {sorted(ids)}")
        return self


class ParquetWriteSpec(_StrictModel):
    compression: Literal["zstd"] = "zstd"
    compression_level: int = Field(default=3, ge=1, le=22)
    statistics: bool = True
    row_group_size: int = Field(default=100_000, ge=1)
    sort_keys: tuple[str, ...] = ("timestamp", "asset")

    @field_validator("sort_keys")
    @classmethod
    def _canonical_sort(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(value) != ("timestamp", "asset"):
            raise ValueError(
                "parquet_write.sort_keys must be exactly ['timestamp', 'asset']; "
                "readers rely on the canonical order"
            )
        return tuple(value)


class DatasetSpec(_StrictModel):
    schema_version: Literal[1]
    dataset_id: str
    asset_class: Literal["crypto_perps", "fx", "indices", "metals", "commodities", "multi_asset"]
    evidence_tier: Literal["native_venue", "research_proxy", "unverified_roll", "synthetic"]
    venue: str = Field(min_length=1)
    frequency: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    price_basis: Literal["ohlcv", "ohlcv_funding", "quote_ohlcv"]
    volume_semantics: Literal["real_contracts", "base_and_quote", "tick_count_proxy"]
    time_window: TimeWindow
    time_contract: TimeContract
    source: SourceSpec
    #: Raw inputs for the *holdout* window, declared separately from ``source``.
    #:
    #: The exploratory fence refuses any bar at or past ``exploratory_end_exclusive``, so a
    #: source that spans both windows cannot build the exploratory side at all.  A seal
    #: therefore reads its own declared window-scoped export rather than re-slicing a file
    #: the gate never audited; when this is absent the seal reads ``source`` (which is only
    #: correct for a holdout-scoped dataset).
    seal_source: SourceSpec | None = None
    universe: UniverseSpec
    resampling: ResamplingSpec
    quality_gate: QualityGateSpec
    parquet_write: ParquetWriteSpec
    #: The funding-settlement contract a crypto perpetual build asserts (#104).  Present
    #: exactly when ``source.crypto`` is: the adapter that reads funding is the adapter
    #: that must say how it is snapped, aggregated and made good.
    funding: FundingSpec | None = None

    @field_validator("dataset_id")
    @classmethod
    def _dataset_id(cls, value: str) -> str:
        if not DATASET_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "dataset_id must match <venue>_<asset_class>_<universe_tag>_<freq>_<basis>_r<rev> "
                "(lowercase, digits, underscores, ending in _r<revision>)"
            )
        return value

    @model_validator(mode="after")
    def _consistent(self) -> DatasetSpec:
        anchor = self.session_anchor
        if anchor is None:
            seconds = self.bar_seconds
            for name in ("start_inclusive", "exploratory_end_exclusive"):
                stamp = int(getattr(self.time_window, name).timestamp())
                if stamp % seconds:
                    raise ValueError(
                        f"time_window.{name} is not aligned to the {self.frequency} UTC bar grid; "
                        "left-labelled bins require grid-aligned bounds"
                    )
        else:
            if self.frequency != "1d":
                raise ValueError(
                    f"daily_roll_anchor {anchor.text!r} defines a daily session clock; it cannot "
                    f"be applied to a {self.frequency} dataset"
                )
            # An anchored session has no UTC-grid alignment: its open is 22:00 UTC in winter
            # and 21:00 UTC in summer, so the window must be declared on the venue's clock.
            for name in (
                "start_inclusive",
                "exploratory_end_exclusive",
                "holdout_start_inclusive",
                "holdout_end_exclusive",
            ):
                bound = getattr(self.time_window, name)
                local = bound.astimezone(anchor.zoneinfo)
                if (local.hour, local.minute) != (anchor.hour, anchor.minute):
                    raise ValueError(
                        f"time_window.{name} {bound.isoformat()} is not on the declared "
                        f"session clock {anchor.text!r}; an anchored daily window must start and "
                        "end at the venue's session close"
                    )
        if self.volume_semantics == "tick_count_proxy" and not self.resampling.enabled:
            raise ValueError(
                "volume_semantics='tick_count_proxy' is a resampling product; enable resampling"
            )
        if (self.source.crypto is None) != (self.funding is None):
            raise ValueError(
                "source.crypto and funding are declared together: the lake adapter that reads "
                "settlements is the one that must state how they are aggregated and made good"
            )
        if self.source.crypto is not None:
            if self.price_basis != "ohlcv_funding":
                raise ValueError(
                    "a binance_um_perp build asserts funding: declare "
                    "price_basis='ohlcv_funding' (an unlabelled funding column changes what "
                    "the dataset means)"
                )
            if self.asset_class != "crypto_perps":
                raise ValueError(
                    f"a binance_um_perp build is asset_class 'crypto_perps', not {self.asset_class!r}"
                )
        return self

    # -- derived declarations -------------------------------------------------

    @property
    def session_anchor(self) -> SessionAnchor | None:
        return self.time_contract.anchor

    @property
    def bar_seconds(self) -> int:
        return FREQUENCY_SECONDS[self.frequency]

    def bar_duration(self) -> timedelta:
        return timedelta(seconds=self.bar_seconds)

    @property
    def required_columns(self) -> tuple[str, ...]:
        columns = ["timestamp", "asset", *OHLCV]
        if self.price_basis == "ohlcv_funding":
            columns.append("funding_rate")
        if self.price_basis == "quote_ohlcv":
            columns.extend(["bid", "ask"])
        return tuple(columns)

    def canonical_payload(self) -> dict[str, Any]:
        """The exact document ``spec_sha256`` is computed over.

        ``mode="json"`` normalises datetimes to UTC ISO-8601 and enums to their
        literal values, so two spellings of the same declaration share a digest.

        The two optional blocks added by #104 (``source.crypto`` and ``funding``) are
        pruned when absent rather than serialised as ``null``: a spec that declares no
        crypto lake must keep the digest it had before the crypto extension existed, or
        every already-published dataset identity would silently change meaning.  For the
        same reason each input's ``role`` is pruned while it is the implicit ``ohlcv``.

        The #105 extensions follow the identical rule: a spec that declares no
        ``seal_source`` and no parent digest must hash to the document it hashed to before
        those keys existed (``tests/test_p2_t18.py`` pins the pre-extension digest of the
        T17 fixture).  ``time_contract.daily_roll_anchor`` is *not* pruned: it is part of
        the pinned wave-three document, so dropping it would move every existing digest.
        """
        payload = self.model_dump(mode="json")
        if payload.get("funding") is None:
            payload.pop("funding", None)
        if payload.get("seal_source") is None:
            payload.pop("seal_source", None)
        source = payload.get("source")
        if isinstance(source, dict):
            if source.get("crypto") is None:
                source.pop("crypto", None)
            for item in (source.get("inputs") or {}).values():
                if not isinstance(item, dict):
                    continue
                if item.get("role") == "ohlcv":
                    item.pop("role", None)
                if item.get("sha256") is None:
                    item.pop("sha256", None)
        return payload

    def spec_sha256(self) -> str:
        payload = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def feed_spec_dict(self) -> dict[str, Any]:
        """The ML4T ``FeedSpec`` v2 this dataset declares."""
        from ml4t.specs import FeedSpec

        anchor = self.session_anchor
        spec = FeedSpec(
            timestamp_col="timestamp",
            entity_col="asset",
            open_col="open",
            high_col="high",
            low_col="low",
            close_col="close",
            volume_col="volume",
            bid_col="bid" if self.price_basis == "quote_ohlcv" else None,
            ask_col="ask" if self.price_basis == "quote_ohlcv" else None,
            calendar=self.time_contract.session_calendar,
            timezone=self.time_contract.timezone,
            data_frequency=self.frequency,
            time_contract_version=2,
            bar_grid_anchor="session_open",
            bar_label="left",
            partial_bar_policy="exclude",
            # The timestamps stay UTC; the session clock is what the bar grid follows.
            session_start_time=None if anchor is None else anchor.time_of_day,
        )
        return spec.to_dict()


def _format_errors(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ())) or "<root>"
        parts.append(f"{location}: {error.get('msg')}")
    return "; ".join(parts)


def parse_dataset_spec(document: Mapping[str, Any]) -> DatasetSpec:
    """Parse one YAML/JSON mapping into a strict :class:`DatasetSpec`."""
    if not isinstance(document, Mapping):
        raise DatasetSpecError("dataset spec must be a mapping")
    try:
        return DatasetSpec.model_validate(dict(document))
    except ValidationError as exc:
        raise DatasetSpecError(f"invalid dataset spec: {_format_errors(exc)}") from exc


def load_dataset_spec(path: Path | str) -> DatasetSpec:
    """Load a dataset spec from YAML (or JSON) without any implicit interpolation."""
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetSpecError(f"cannot read dataset spec {source}: {exc}") from exc
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise DatasetSpecError(f"{source}: invalid YAML: {exc}") from exc
    if not isinstance(document, Mapping):
        raise DatasetSpecError(f"{source}: dataset spec must be a YAML mapping")
    return parse_dataset_spec(document)
