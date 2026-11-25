"""``binance_um_perp`` raw-lake adapter: point-in-time perpetual builds (#104).

A crypto lake is not a file list: it is one directory per symbol of per-period Parquet
partitions, plus a funding feed whose settlement instants are jittered by the venue.  This
adapter is the only place that knows those three facts, and it makes the two decisions that
turn a lake into a *point-in-time* dataset — **before** it opens anything:

1. **Which symbols.**  The reviewed catalog declares ``symbol, listed_at, delisted_at``.
   The universe is every symbol listed before ``exploratory_end_exclusive``.  Symbols are
   never ranked, filtered or chosen by volume (calendar-year volume is future information
   at decision time), a symbol the catalog lists before the cutoff cannot be silently
   omitted from the declared universe, and a symbol that delisted inside the window is
   **retained** with its ``last_tradable_ts`` recorded.
2. **Which partitions.**  Each partition filename carries its period.  A partition is
   *decided* against the window (before the window, opened, or at/after the cutoff) and a
   partition that begins at or after ``exploratory_end_exclusive`` is never opened.  Rows
   outside the window inside an opened partition are clipped and counted, and the quality
   gate's temporal fence re-checks the panel so a mislabelled file cannot leak a
   post-cutoff bar.

Funding is aggregated **forward only**.  Each settlement is snapped onto the exact hour
boundary it jitters around (outside ``snap_tolerance_seconds`` it is refused, never moved),
attributed to the first bar at or after it, and never carried onto a bar eight hours or
more later; the funding rate on a bar is the sum of the settlements attributed to bars in
``(t - 8h, t]``.  There is no backward ``dt.truncate`` bucket assignment and no backward
``join_asof``: a settlement can only ever move forward in time.  A bar with no settlement in
its window fails the build under ``missing_policy: fail``; under
``missing_policy: contract_fallback`` the exact declared rate is charged and every
substituted bar, the affected symbols and the affected-symbol count are audited in the
manifest.  ``premium_index_close`` is not funding and is never substituted for it.

The result is a :class:`RawBundle` whose ``audit`` block the v2 manifest publishes verbatim,
and whose ``inputs`` cover every byte actually read (catalog and opened partitions only), so
"never opened the 2026 partitions" is a claim both the Merkle commitment and the audit
describe.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

import polars as pl

from warden_evidence.dataset_builder.adapters import (
    ParquetReader,
    RawBundle,
    canonicalise_frame,
    register_source_adapter,
    require_utc_column,
)
from warden_evidence.dataset_builder.containment import (
    AdapterError,
    ListingRawFileSystem,
    canonical_source_path,
)
from warden_evidence.dataset_builder.merkle import merkle_sha256
from warden_evidence.dataset_builder.spec import DatasetSpec, SourceInput

ADAPTER_NAME = "binance_um_perp"

#: A funding column spelled the way vendors spell it.  Never a price: the premium index is
#: not funding and is refused outright (see ``adapters.canonicalise_frame``).
FUNDING_COLUMN_ALIASES: dict[str, str] = {
    "fundingRate": "funding_rate",
    "lastFundingRate": "funding_rate",
    "last_funding_rate": "funding_rate",
}

#: ``<period>`` component count per declared partition granularity.
PERIOD_COMPONENTS: dict[str, int] = {"year": 1, "month": 2, "day": 3}

#: Partition-name components that are metadata, not data (``_SUCCESS`` markers, dotfiles).
IGNORABLE_NAMES: tuple[str, ...] = (".", "_")

SETTLEMENT_UNIT_US = 3_600_000_000  # one UTC hour in microseconds


@dataclass(frozen=True)
class PeriodWindow:
    """One partition's covered instant range, ``[start, end)``, parsed from its name."""

    path: str
    start: datetime
    end: datetime

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name


@dataclass(frozen=True)
class PartitionDecision:
    """The partition decision for one symbol: taken before any file was opened."""

    opened: tuple[PeriodWindow, ...]
    skipped_before_window: tuple[PeriodWindow, ...]
    skipped_at_or_after_cutoff: tuple[PeriodWindow, ...]

    def paths(self, decision: str) -> list[str]:
        if decision == "opened":
            return [item.path for item in self.opened]
        if decision == "skipped_before_window":
            return [item.path for item in self.skipped_before_window]
        if decision == "skipped_at_or_after_cutoff":
            return [item.path for item in self.skipped_at_or_after_cutoff]
        raise ValueError(f"unknown partition decision {decision!r}")  # pragma: no cover


@dataclass(frozen=True)
class ListingEntry:
    """One catalog row: when a symbol began and (if it did) stopped trading."""

    symbol: str
    listed_at: datetime
    delisted_at: datetime | None


@dataclass(frozen=True)
class CryptoUniverse:
    """The point-in-time universe, decided from listing dates only."""

    symbols: tuple[str, ...]
    listings: dict[str, ListingEntry] = field(repr=False)
    delisted_retained: tuple[str, ...]
    excluded_listed_at_or_after_cutoff: tuple[str, ...]
    catalog_symbols_not_declared: tuple[str, ...]


# --------------------------------------------------------------------------- partitions


def _period_start(parts: Sequence[str], period: str) -> tuple[datetime, datetime]:
    """Parse a partition's period components into ``[start, end)`` UTC instants."""
    try:
        numbers = [int(part) for part in parts]
    except ValueError as exc:
        raise AdapterError(f"partition period {list(parts)!r} is not numeric") from exc
    if period == "year":
        (year,) = numbers
        return datetime(year, 1, 1, tzinfo=UTC), datetime(year + 1, 1, 1, tzinfo=UTC)
    if period == "month":
        year, month = numbers
        end = (
            datetime(year + 1, 1, 1, tzinfo=UTC)
            if month == 12
            else datetime(year, month + 1, 1, tzinfo=UTC)
        )
        return datetime(year, month, 1, tzinfo=UTC), end
    if period == "day":
        year, month, day = numbers
        start = datetime(year, month, day, tzinfo=UTC)
        return start, start + timedelta(days=1)
    raise AdapterError(f"unknown partition period {period!r}")  # pragma: no cover


def parse_period_window(
    relative: PurePosixPath, *, symbol: str, kind: str, period: str
) -> PeriodWindow | None:
    """Read one partition name, or ``None`` when it is not this symbol's/kind's file.

    The name is ``<SYMBOL>-<KIND>-<period...>.parquet``.  Anything else under the symbol
    directory that is not a hidden/metadata name is refused rather than guessed at: a
    partition whose period cannot be read is a partition whose cutoff decision cannot be
    taken, and an undecidable file must not sit in a lake a governed build reads.
    """
    name = relative.name
    if name.startswith(IGNORABLE_NAMES):
        return None
    if not name.endswith(".parquet"):
        raise AdapterError(
            f"partition {relative.as_posix()!r} is not a Parquet file; the lake layout is "
            "<SYMBOL>-<KIND>-<period>.parquet"
        )
    parts = name[: -len(".parquet")].split("-")
    if len(parts) < 3:
        raise AdapterError(
            f"partition {relative.as_posix()!r} is not <SYMBOL>-<KIND>-<period>.parquet"
        )
    if parts[0] != symbol or parts[1] != kind:
        return None
    period_parts = parts[2:]
    if len(period_parts) != PERIOD_COMPONENTS[period]:
        raise AdapterError(
            f"partition {relative.as_posix()!r} carries {len(period_parts)} period "
            f"component(s); the declared partition_period {period!r} needs "
            f"{PERIOD_COMPONENTS[period]}"
        )
    start, end = _period_start(period_parts, period)
    return PeriodWindow(path=relative.as_posix(), start=start, end=end)


def decide_partitions(
    candidates: Iterable[PeriodWindow], *, earliest: datetime, exclusive_end: datetime
) -> PartitionDecision:
    """Decide each partition's fate from its period alone; touches no file.

    This is the pure seam the build depends on: *which* files may be opened is settled
    here, so a reader that follows this decision cannot open a partition that begins at or
    after ``exclusive_end``, and cannot open one that ends before ``earliest``.
    """
    opened: list[PeriodWindow] = []
    skipped_before: list[PeriodWindow] = []
    skipped_after: list[PeriodWindow] = []
    for candidate in sorted(candidates, key=lambda item: item.path):
        if candidate.end <= earliest:
            skipped_before.append(candidate)
        elif candidate.start >= exclusive_end:
            skipped_after.append(candidate)
        else:
            opened.append(candidate)
    return PartitionDecision(
        opened=tuple(opened),
        skipped_before_window=tuple(skipped_before),
        skipped_at_or_after_cutoff=tuple(skipped_after),
    )


# --------------------------------------------------------------------------- catalog


def _parse_catalog_payload(payload: bytes, source: SourceInput) -> pl.DataFrame:
    if source.format == "csv":
        return pl.read_csv(io.BytesIO(payload), infer_schema_length=0, null_values=[""])
    if source.format == "tsv":
        return pl.read_csv(
            io.BytesIO(payload), separator="\t", infer_schema_length=0, null_values=[""]
        )
    if source.format == "json":
        return pl.read_json(io.BytesIO(payload))
    if source.format == "ndjson":
        return pl.read_ndjson(io.BytesIO(payload))
    raise AdapterError(
        f"{source.path}: the symbol catalog must be csv/tsv/json/ndjson, "
        f"not {source.format!r}"
    )


def parse_symbol_catalog(payload: bytes, source: SourceInput) -> dict[str, ListingEntry]:
    """Parse the listing-lifecycle catalog into one entry per symbol."""
    try:
        frame = _parse_catalog_payload(payload, source)
    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError(f"{source.path}: cannot parse symbol catalog: {exc}") from exc
    missing = [column for column in ("symbol", "listed_at") if column not in frame.columns]
    if missing:
        raise AdapterError(f"{source.path}: symbol catalog lacks column(s) {missing}")
    unknown = sorted(set(frame.columns) - {"symbol", "listed_at", "delisted_at"})
    if unknown:
        raise AdapterError(
            f"{source.path}: symbol catalog carries unread column(s) {unknown}; a listing "
            "decision may not depend on an undeclared field"
        )
    if frame.is_empty():
        raise AdapterError(f"{source.path}: symbol catalog is empty")
    frame = require_utc_column(frame, source, column="listed_at")
    if "delisted_at" in frame.columns:
        if frame["delisted_at"].null_count() == frame.height:
            frame = frame.with_columns(
                pl.col("delisted_at").cast(pl.Datetime("us", "UTC"))
            )
        else:
            frame = require_utc_column(frame, source, column="delisted_at")

    entries: dict[str, ListingEntry] = {}
    for row in frame.iter_rows(named=True):
        symbol = row["symbol"]
        if not isinstance(symbol, str) or not symbol.strip():
            raise AdapterError(f"{source.path}: symbol catalog has a null or empty symbol")
        if symbol in entries:
            raise AdapterError(f"{source.path}: symbol {symbol!r} is listed twice")
        listed_at = row["listed_at"]
        if listed_at is None:
            raise AdapterError(f"{source.path}: symbol {symbol!r} has no listed_at")
        delisted_at = row.get("delisted_at")
        if delisted_at is not None and delisted_at < listed_at:
            raise AdapterError(
                f"{source.path}: symbol {symbol!r} delisted before it was listed"
            )
        entries[symbol] = ListingEntry(
            symbol=symbol,
            listed_at=listed_at.astimezone(UTC),
            delisted_at=None if delisted_at is None else delisted_at.astimezone(UTC),
        )
    return entries


def _check_symbol_identity(symbol: str) -> None:
    """A symbol is one path component and one partition-name prefix, or it is refused."""
    if "-" in symbol:
        raise AdapterError(
            f"symbol {symbol!r} contains '-', which separates the partition-name fields; "
            "refusing rather than mis-parsing the lake layout"
        )
    if symbol != symbol.strip() or not symbol.replace("_", "").replace(".", "").isalnum():
        raise AdapterError(f"symbol {symbol!r} is not a plain lake directory name")
    if PurePosixPath(symbol).parts != (symbol,):
        raise AdapterError(f"symbol {symbol!r} is not a single path component")


def decide_universe(spec: DatasetSpec, catalog: dict[str, ListingEntry]) -> CryptoUniverse:
    """The point-in-time universe, from listing dates and nothing else.

    Three refusals make "include every symbol listed prior to the cutoff, retain the
    delisted ones, never rank by future volume" enforced rather than intended:

    * a declared symbol missing from the catalog (there is no listing evidence for it);
    * a declared symbol listed at/after the cutoff (future information);
    * a catalog symbol listed before the cutoff that the declared universe omits (a
      silent narrowing is an implicit rank, whatever produced it).
    """
    cutoff = spec.time_window.exploratory_end_exclusive
    declared = list(spec.universe.symbols)
    for symbol in declared:
        _check_symbol_identity(symbol)

    unknown = [symbol for symbol in declared if symbol not in catalog]
    if unknown:
        raise AdapterError(
            f"declared universe symbols {unknown} have no row in the symbol catalog; a "
            "listing date is required to include a symbol point-in-time"
        )
    listed_late = [symbol for symbol in declared if catalog[symbol].listed_at >= cutoff]
    if listed_late:
        raise AdapterError(
            f"declared universe symbols {listed_late} are listed at or after "
            f"exploratory_end_exclusive {cutoff.isoformat()}; that is future information"
        )
    declared_set = set(declared)
    omitted = sorted(
        symbol
        for symbol, entry in catalog.items()
        if entry.listed_at < cutoff and symbol not in declared_set
    )
    if omitted:
        raise AdapterError(
            f"symbol catalog lists {omitted} before exploratory_end_exclusive but the "
            "declared universe omits them; declare every symbol listed before the cutoff "
            "(a build may not narrow the universe by volume or by hindsight)"
        )

    delisted_retained = tuple(
        symbol
        for symbol in declared
        if catalog[symbol].delisted_at is not None and catalog[symbol].delisted_at < cutoff
    )
    if delisted_retained and not spec.universe.retain_delisted:
        raise AdapterError(
            f"symbols {list(delisted_retained)} delisted inside the window; a point-in-time "
            "crypto build retains them (declare retain_delisted: true)"
        )
    return CryptoUniverse(
        symbols=tuple(declared),
        listings={symbol: catalog[symbol] for symbol in declared},
        delisted_retained=delisted_retained,
        excluded_listed_at_or_after_cutoff=tuple(
            sorted(symbol for symbol, entry in catalog.items() if entry.listed_at >= cutoff)
        ),
        catalog_symbols_not_declared=tuple(
            sorted(symbol for symbol in catalog if symbol not in declared_set)
        ),
    )


# --------------------------------------------------------------------------- funding


def snap_settlement_hours(
    frame: pl.DataFrame, *, tolerance_seconds: int, source: str
) -> tuple[pl.DataFrame, float]:
    """Snap jittered settlement instants onto the exact hour boundary they sit around.

    Snapping is *epoch arithmetic* to the nearest hour: a settlement at ``08:00:00.005``
    becomes ``08:00`` and a settlement at ``04:00`` stays ``04:00`` (a sub-8h settlement is
    a legitimate settlement, not a bucket to be truncated away).  A settlement farther than
    the declared tolerance from every hour boundary is refused, never moved: moving it would
    invent a settlement instant.
    """
    if frame.is_empty():
        return frame, 0.0
    epoch_us = pl.col("timestamp").dt.epoch("us")
    remainder = epoch_us % SETTLEMENT_UNIT_US
    deviation = pl.min_horizontal(remainder, SETTLEMENT_UNIT_US - remainder)
    tolerance_us = tolerance_seconds * 1_000_000
    worst = int(frame.select(deviation.max().alias("worst")).item() or 0)
    if worst > tolerance_us:
        offenders = (
            frame.with_columns(deviation.alias("_deviation_us"))
            .filter(pl.col("_deviation_us") > tolerance_us)
            .sort("timestamp")
            .head(5)
        )
        examples = [str(value) for value in offenders["timestamp"].to_list()]
        raise AdapterError(
            f"{source}: {offenders.height} settlement(s) sit more than {tolerance_seconds}s "
            f"from an exact hour boundary (e.g. {examples}); a jittered timestamp is snapped "
            "inside the declared tolerance, never moved from an unrelated instant"
        )
    snapped_us = (
        epoch_us
        - remainder
        + pl.when(remainder * 2 > SETTLEMENT_UNIT_US).then(SETTLEMENT_UNIT_US).otherwise(0)
    )
    snapped = frame.with_columns(
        snapped_us.cast(pl.Datetime("us", "UTC")).alias("timestamp")
    )
    return snapped, worst / 1_000_000.0


def aggregate_funding_forward(
    settlements: pl.DataFrame, bars: pl.DataFrame, *, window_hours: int
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Attribute settlements forward, then sum the trailing window onto every bar.

    ``settlement s`` is attributed to ``t = first bar at or after s`` **only** while
    ``t - s < window_hours``; the trailing window ``(t - 8h, t]`` is then summed onto each
    bar.  Two properties follow directly, and both are asserted by tests: a bar never sees a
    settlement from its future, and a settlement is never carried onto a bar eight hours or
    more later (the stale backward-``join_asof`` failure).
    """
    horizon = timedelta(hours=window_hours)
    if bars.is_empty():
        raise AdapterError("cannot attribute funding: the bar panel is empty")
    grid = bars.select(["asset", "timestamp"]).unique().sort(["asset", "timestamp"])
    counts = {
        "settlements": int(settlements.height),
        "attributed": 0,
        "unattributed": 0,
        "without_bar": 0,
    }
    empty = grid.with_columns(pl.lit(None, dtype=pl.Float64).alias("funding_rate"))
    if settlements.is_empty():
        return empty, counts
    ordered = settlements.select(["asset", "timestamp", "funding_rate"]).sort(
        ["asset", "timestamp"]
    )
    # Both sides are sorted explicitly above; polars cannot verify sortedness when a `by`
    # group is supplied, and a silently re-sorted frame would break the as-of contract.
    mapped = ordered.join_asof(
        grid.rename({"timestamp": "bar_timestamp"}),
        left_on="timestamp",
        right_on="bar_timestamp",
        by="asset",
        strategy="forward",
        allow_exact_matches=True,
        coalesce=False,
        check_sortedness=False,
    )
    gap = pl.col("bar_timestamp") - pl.col("timestamp")
    attributed = mapped.filter(pl.col("bar_timestamp").is_not_null() & (gap < horizon))
    counts["attributed"] = int(attributed.height)
    counts["unattributed"] = int(mapped.height - attributed.height)
    counts["without_bar"] = int(mapped.filter(pl.col("bar_timestamp").is_null()).height)
    if attributed.is_empty():
        return empty, counts
    per_bar = attributed.group_by(["asset", "bar_timestamp"]).agg(
        pl.col("funding_rate").sum().alias("_settled")
    )
    seeded = grid.join(
        per_bar,
        left_on=["asset", "timestamp"],
        right_on=["asset", "bar_timestamp"],
        how="left",
    ).sort(["asset", "timestamp"])
    period = f"{window_hours}h"
    rolled = seeded.rolling(
        index_column="timestamp",
        period=period,
        offset=f"-{period}",
        closed="right",
        group_by="asset",
    ).agg(
        pl.col("_settled").sum().alias("funding_rate"),
        # A window *without* a settlement is a null, not a zero: the seeded frame carries
        # 0.0 only so the rolling sum is defined, and a real zero-sum window (settlements
        # that happen to cancel) keeps its 0.0 because the count is non-zero.
        pl.col("_settled").count().alias("_settlement_count"),
    )
    funding = rolled.with_columns(
        pl.when(pl.col("_settlement_count") == 0)
        .then(pl.lit(None, dtype=pl.Float64))
        .otherwise(pl.col("funding_rate"))
        .alias("funding_rate")
    ).select(["asset", "timestamp", "funding_rate"])
    return funding.sort(["asset", "timestamp"]), counts


# --------------------------------------------------------------------------- adapter


class CryptoPerpAdapter:
    """Read a ``binance_um_perp`` lake into one canonical point-in-time panel."""

    name = ADAPTER_NAME

    def __init__(
        self, *, fs: ListingRawFileSystem, parquet_reader: ParquetReader | None = None
    ) -> None:
        self._fs = fs
        self._parquet_reader = parquet_reader

    # -- reading ---------------------------------------------------------

    def _read(self, relative: PurePosixPath, read: list[tuple[str, bytes]]) -> pl.DataFrame:
        if self._parquet_reader is None:
            raise AdapterError(
                f"{relative.as_posix()}: a Parquet lake partition requires an injected "
                "parquet_reader; the builder never imports a Parquet reader itself"
            )
        payload = self._fs.read_bytes(relative)
        read.append((relative.as_posix(), payload))
        try:
            return self._parquet_reader(payload)
        except Exception as exc:
            raise AdapterError(
                f"{relative.as_posix()}: cannot parse Parquet partition: {exc}"
            ) from exc

    def _discover(
        self, root: str, symbol: str, *, kind: str, period: str, missing_ok: bool = False
    ) -> list[PeriodWindow]:
        directory = canonical_source_path(f"{root}/{symbol}", role="partition directory")
        windows: list[PeriodWindow] = []
        for name in self._fs.list_dir(directory, missing_ok=missing_ok):
            relative = canonical_source_path(
                f"{directory.as_posix()}/{name}", role="partition"
            )
            window = parse_period_window(relative, symbol=symbol, kind=kind, period=period)
            if window is not None:
                windows.append(window)
        return windows

    def _lake_frame(
        self, relative: PurePosixPath, crypto, read
    ) -> tuple[pl.DataFrame, set[str]]:
        raw = self._read(relative, read)
        frame, dropped = canonicalise_frame(
            self._rename_funding_columns(raw),
            crypto.source_input(relative.as_posix()),
            ignored_columns=crypto.ignored_columns,
        )
        return frame.with_columns(pl.col("asset").cast(pl.String)), set(dropped)

    @staticmethod
    def _partition_audit(
        decision: PartitionDecision,
        *,
        rows_clipped_before_window: int,
        rows_clipped_at_or_after_cutoff: int,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        block: dict[str, Any] = {
            "decision_precedes_read": True,
            "opened": decision.paths("opened"),
            "skipped_before_window": decision.paths("skipped_before_window"),
            "skipped_at_or_after_cutoff": decision.paths("skipped_at_or_after_cutoff"),
            "rows_clipped_before_window": rows_clipped_before_window,
            "rows_clipped_at_or_after_cutoff": rows_clipped_at_or_after_cutoff,
        }
        if extra:
            block.update(extra)
        return block

    # -- the build -------------------------------------------------------

    def load(self, spec: DatasetSpec) -> RawBundle:
        crypto = spec.source.crypto
        funding_spec = spec.funding
        if crypto is None:
            raise AdapterError("source.crypto is required by the binance_um_perp adapter")
        if funding_spec is None:
            raise AdapterError("funding is required by the binance_um_perp adapter")
        if not isinstance(self._fs, ListingRawFileSystem):
            raise AdapterError(
                "the binance_um_perp adapter discovers partitions and needs a filesystem "
                "that can list a directory (LocalRawFileSystem does)"
            )
        window = spec.time_window
        start = window.start_inclusive
        cutoff = window.exploratory_end_exclusive

        # 1. Read the one declared input: the listing catalog.  The universe decision is
        #    made from it, so it precedes every partition listing and every read.
        catalog_source = spec.source.inputs[crypto.symbol_catalog]
        catalog_relative = canonical_source_path(catalog_source.path)
        read: list[tuple[str, bytes]] = []
        catalog_payload = self._fs.read_bytes(catalog_relative)
        read.append((catalog_relative.as_posix(), catalog_payload))
        catalog = parse_symbol_catalog(catalog_payload, catalog_source)
        universe = decide_universe(spec, catalog)

        # 2. Bars: decide each symbol's partitions, then read only the opened ones.
        bar_frames: list[pl.DataFrame] = []
        kline_audit: dict[str, Any] = {}
        ignored_dropped: set[str] = set()
        rows_before_listing = 0
        rows_at_or_after_delisting = 0
        for symbol in universe.symbols:
            entry = universe.listings[symbol]
            windows = self._discover(
                crypto.klines_root,
                symbol,
                kind=spec.frequency,
                period=crypto.partition_period,
            )
            decision = decide_partitions(windows, earliest=start, exclusive_end=cutoff)
            clipped_before = 0
            clipped_after = 0
            before_listing = 0
            after_delisting = 0
            frames: list[pl.DataFrame] = []
            for window_partition in decision.opened:
                relative = canonical_source_path(window_partition.path, role="partition")
                frame, dropped = self._lake_frame(relative, crypto, read)
                ignored_dropped.update(dropped)
                if "funding_rate" in frame.columns:
                    raise AdapterError(
                        f"{relative.as_posix()}: kline partitions must not carry funding_rate; "
                        "settlements come from the declared funding feed, never from a price file"
                    )
                if _foreign_symbols(frame, symbol):
                    raise AdapterError(
                        f"{relative.as_posix()}: kline partition for {symbol} carries rows for "
                        f"{sorted(_foreign_symbols(frame, symbol))}"
                    )
                clipped_before += int(frame.filter(pl.col("timestamp") < start).height)
                frame = frame.filter(pl.col("timestamp") >= start)
                if entry.listed_at > start:
                    before_listing += int(
                        frame.filter(pl.col("timestamp") < entry.listed_at).height
                    )
                    frame = frame.filter(pl.col("timestamp") >= entry.listed_at)
                if entry.delisted_at is not None:
                    after_delisting += int(
                        frame.filter(pl.col("timestamp") >= entry.delisted_at).height
                    )
                    frame = frame.filter(pl.col("timestamp") < entry.delisted_at)
                clipped_after += int(frame.filter(pl.col("timestamp") >= cutoff).height)
                frame = frame.filter(pl.col("timestamp") < cutoff)
                if not frame.is_empty():
                    frames.append(frame)
            rows_before_listing += before_listing
            rows_at_or_after_delisting += after_delisting
            if frames:
                bar_frames.append(pl.concat(frames, how="vertical"))
            kline_audit[symbol] = self._partition_audit(
                decision,
                rows_clipped_before_window=clipped_before,
                rows_clipped_at_or_after_cutoff=clipped_after,
                extra={
                    "rows_clipped_before_listing": before_listing,
                    "rows_clipped_at_or_after_delisting": after_delisting,
                },
            )
        if not bar_frames:
            raise AdapterError(
                "no kline partition intersects the declared window for any universe symbol"
            )
        bars = pl.concat(bar_frames, how="vertical").sort(["timestamp", "asset"])
        overlapping = bars.group_by(["timestamp", "asset"]).len().filter(pl.col("len") > 1)
        if overlapping.height:
            raise AdapterError(
                f"{overlapping.height} (timestamp, asset) bar(s) appear in more than one opened "
                "partition; overlapping partitions are a lake fault, not something to dedupe"
            )

        # 3. Funding: the same decision shape, one extra 8h of lookback so the first bars of
        #    the window can see a settlement that precedes it.
        funding_floor = start - timedelta(hours=funding_spec.settlement_window_hours)
        settlement_frames: list[pl.DataFrame] = []
        funding_audit: dict[str, Any] = {}
        funding_clipped = 0
        for symbol in universe.symbols:
            windows = self._discover(
                crypto.funding_root,
                symbol,
                kind="funding",
                period=crypto.partition_period,
                missing_ok=True,
            )
            decision = decide_partitions(
                windows, earliest=funding_floor, exclusive_end=cutoff
            )
            clipped = 0
            clipped_at_cutoff = 0
            frames: list[pl.DataFrame] = []
            for window_partition in decision.opened:
                relative = canonical_source_path(window_partition.path, role="partition")
                frame, dropped = self._lake_frame(relative, crypto, read)
                ignored_dropped.update(dropped)
                if "funding_rate" not in frame.columns:
                    raise AdapterError(
                        f"{relative.as_posix()}: funding partition carries no funding_rate "
                        "column; premium_index_close is not funding"
                    )
                if _foreign_symbols(frame, symbol):
                    raise AdapterError(
                        f"{relative.as_posix()}: funding partition for {symbol} carries rows "
                        f"for {sorted(_foreign_symbols(frame, symbol))}"
                    )
                clipped += int(frame.filter(pl.col("timestamp") < funding_floor).height)
                frame = frame.filter(pl.col("timestamp") >= funding_floor)
                clipped_at_cutoff += int(
                    frame.filter(pl.col("timestamp") >= cutoff).height
                )
                frame = frame.filter(pl.col("timestamp") < cutoff).select(
                    ["asset", "timestamp", "funding_rate"]
                )
                if not frame.is_empty():
                    frames.append(frame)
            funding_clipped += clipped
            funding_audit[symbol] = self._partition_audit(
                decision,
                rows_clipped_before_window=clipped,
                rows_clipped_at_or_after_cutoff=clipped_at_cutoff,
                extra={
                    "lookback_start": funding_floor.isoformat(),
                    # 0 means no funding partition was declared for this symbol at all: a
                    # data gap the missing-settlement policy — not a silent zero — judges.
                    "partitions_listed": len(windows),
                },
            )
            if frames:
                settlement_frames.append(pl.concat(frames, how="vertical"))

        settlements = (
            pl.concat(settlement_frames, how="vertical")
            if settlement_frames
            else pl.DataFrame(
                schema={
                    "asset": pl.String,
                    "timestamp": pl.Datetime("us", "UTC"),
                    "funding_rate": pl.Float64,
                }
            )
        )
        settlements = settlements.with_columns(pl.col("funding_rate").cast(pl.Float64))
        bad = settlements.filter(
            pl.col("funding_rate").is_null() | ~pl.col("funding_rate").is_finite()
        )
        if bad.height:
            raise AdapterError(
                f"{bad.height} funding settlement(s) carry a null or non-finite rate; a "
                "missing rate is a gap, never a zero"
            )
        settlements, worst_snap = snap_settlement_hours(
            settlements.sort(["asset", "timestamp"]),
            tolerance_seconds=funding_spec.snap_tolerance_seconds,
            source="funding feed",
        )
        duplicate = (
            settlements.group_by(["asset", "timestamp"]).len().filter(pl.col("len") > 1)
        )
        if duplicate.height:
            raise AdapterError(
                f"{duplicate.height} settlement instant(s) appear twice after snapping; two "
                "settlements cannot share one attribution"
            )

        funding, counts = aggregate_funding_forward(
            settlements, bars, window_hours=funding_spec.settlement_window_hours
        )
        panel = bars.join(funding, on=["asset", "timestamp"], how="left").sort(
            ["timestamp", "asset"]
        )

        # 4. Missing funding: fail closed, or substitute the *declared* rate and audit it.
        missing = panel.filter(pl.col("funding_rate").is_null())
        missing_by_symbol = {
            str(asset): int(count)
            for asset, count in missing.group_by(["asset"]).len().sort("asset").iter_rows()
        }
        affected = sorted(missing_by_symbol)
        if affected and funding_spec.missing_policy == "fail":
            detail = ", ".join(
                f"{symbol} {missing_by_symbol[symbol]} bar(s)" for symbol in affected
            )
            first = (
                missing.sort(["asset", "timestamp"])
                .group_by("asset", maintain_order=True)
                .agg(pl.col("timestamp").min().alias("first"))
            )
            examples = [
                f"{row['asset']}@{row['first']}" for row in first.iter_rows(named=True)
            ][:5]
            raise AdapterError(
                f"missing funding under missing_policy='fail': {detail} without a settlement "
                f"in the trailing 8h window (e.g. {examples}); premium_index_close and a "
                "silent 0.0 are not substitutes — repair the feed or declare "
                "missing_policy: contract_fallback with an explicit rate"
            )
        fallback_rows = 0
        if affected:
            if funding_spec.fallback_rate is None:  # pragma: no cover - spec validation
                raise AdapterError("contract_fallback requires a declared fallback_rate")
            panel = panel.with_columns(
                pl.col("funding_rate").fill_null(funding_spec.fallback_rate)
            )
            fallback_rows = int(missing.height)

        # 5. Assemble the panel, the audit and the raw-byte commitment.
        assets = sorted(str(value) for value in panel["asset"].unique().to_list())
        last_tradable = {
            str(asset): str(last)
            for asset, last in panel.group_by("asset")
            .agg(pl.col("timestamp").max())
            .sort("asset")
            .iter_rows()
        }
        declared_set = set(universe.symbols)
        lake_symbols = sorted(
            name
            for name in self._fs.list_dir(
                canonical_source_path(crypto.klines_root, role="klines root")
            )
            if not name.startswith(IGNORABLE_NAMES) and name not in declared_set
        )
        audit: dict[str, Any] = {
            "adapter": ADAPTER_NAME,
            "delisted_symbols_retained": len(universe.delisted_retained),
            "last_tradable_ts": last_tradable,
            "universe": {
                "selection_mode": "listed_before_exploratory_end_exclusive",
                "volume_ranked": False,
                "catalog": catalog_relative.as_posix(),
                "symbols_declared": list(universe.symbols),
                "symbols_built": assets,
                "listed_at": {
                    symbol: entry.listed_at.isoformat()
                    for symbol, entry in sorted(universe.listings.items())
                },
                "delisted_at": {
                    symbol: entry.delisted_at.isoformat()
                    for symbol, entry in sorted(universe.listings.items())
                    if entry.delisted_at is not None
                },
                "delisted_symbols_retained": list(universe.delisted_retained),
                "excluded_listed_at_or_after_cutoff": list(
                    universe.excluded_listed_at_or_after_cutoff
                ),
                "catalog_symbols_not_declared": list(universe.catalog_symbols_not_declared),
                "lake_symbol_directories_not_declared": lake_symbols,
                "rows_clipped_before_listing": rows_before_listing,
                "rows_clipped_at_or_after_delisting": rows_at_or_after_delisting,
            },
            "partitions": {
                "partition_period": crypto.partition_period,
                "klines": kline_audit,
                "funding": funding_audit,
            },
            "funding": {
                "settlement_interval": funding_spec.settlement_interval,
                "aggregation": "forward_trailing_window",
                "snap_tolerance_seconds": funding_spec.snap_tolerance_seconds,
                "max_snap_seconds": worst_snap,
                "settlements_read": counts["settlements"],
                "settlements_attributed": counts["attributed"],
                "settlements_unattributed": counts["unattributed"],
                "settlements_without_bar": counts["without_bar"],
                "rows_clipped_before_lookback": funding_clipped,
                "missing_policy": funding_spec.missing_policy,
                "bars_missing_settlement": int(missing.height),
                "affected_symbols": affected,
                "affected_symbol_count": len(affected),
                "affected_rows_by_symbol": missing_by_symbol,
                "fallback_rate": funding_spec.fallback_rate if affected else None,
                "fallback_rows_filled": fallback_rows,
                "premium_index_close_used": False,
                "ignored_columns": sorted(ignored_dropped),
            },
        }
        merkle, records = merkle_sha256(read)
        summary = {
            "adapter": self.name,
            "inputs": [
                {"path": record.path, "bytes": record.bytes, "sha256": record.sha256}
                for record in records
            ],
            "catalog": catalog_relative.as_posix(),
            "klines_root": crypto.klines_root,
            "funding_root": crypto.funding_root,
            "partition_period": crypto.partition_period,
            "rows_read": panel.height,
            "delisted_symbols_retained": len(universe.delisted_retained),
            "funding_policy": funding_spec.missing_policy,
        }
        return RawBundle(
            adapter=self.name,
            frame=panel,
            inputs=tuple(records),
            merkle_sha256=merkle,
            summary=summary,
            audit=audit,
        )

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _rename_funding_columns(frame: pl.DataFrame) -> pl.DataFrame:
        renamed = {
            name: target
            for name, target in FUNDING_COLUMN_ALIASES.items()
            if name in frame.columns and target not in frame.columns
        }
        return frame.rename(renamed) if renamed else frame


def _foreign_symbols(frame: pl.DataFrame, symbol: str) -> set[str]:
    return {
        str(value) for value in frame["asset"].unique().to_list() if str(value) != symbol
    }


@register_source_adapter(CryptoPerpAdapter.name)
def _binance_um_perp_factory(
    *, fs: ListingRawFileSystem, parquet_reader: ParquetReader | None = None
) -> CryptoPerpAdapter:
    return CryptoPerpAdapter(fs=fs, parquet_reader=parquet_reader)


__all__ = [
    "ADAPTER_NAME",
    "CryptoPerpAdapter",
    "CryptoUniverse",
    "ListingEntry",
    "PartitionDecision",
    "PeriodWindow",
    "aggregate_funding_forward",
    "decide_partitions",
    "decide_universe",
    "parse_period_window",
    "parse_symbol_catalog",
    "snap_settlement_hours",
]
