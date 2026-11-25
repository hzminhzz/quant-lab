"""UTC timestamp alignment for Warden's selection evidence.

A selection history is only comparable when every stream covers the same clock.  The
registry stores a trial's net-return vector twice: as ``returns_json`` in SQLite (the
authoritative values) and as a Parquet artifact.  Older artifacts carry a single
``net_return`` column and no time index at all; newer ones carry ``timestamp`` (UTC) and
``net_return`` together.

This module is deliberately pure: it takes already-read vectors plus their UTC
timestamps and decides whether they may be correlated, and on which index.  It never
reads a file, never touches the registry and never invents a timestamp:

* a stream **with** timestamps is aligned by exact timestamp intersection — accepted only
  when no stream, candidate or prior, loses more than half of its own observations to it —
  or, when the grids are genuinely different frequencies, by compounding each UTC day's
  returns;
* a stream **without** timestamps is never assigned a fabricated index.  It joins only
  on the pre-existing positional proof (same bar count as an un-timestamped candidate on
  the same contract) and is otherwise reported as excluded debt.

Every exclusion is returned to the caller so the DSR panel charges the half-attempt
penalty instead of silently shrinking the search.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

NS_PER_SECOND = 1_000_000_000
NS_PER_DAY = 86_400 * NS_PER_SECOND
#: Share of the shortest stream's index that must survive the intersection.  A stream
#: overlapping less than half its own window is not the same experiment.
DEFAULT_MIN_OVERLAP = 0.5
#: ``compute_selection_report`` already refuses fewer than ten observations.
DEFAULT_MIN_BARS = 10
#: How far an ``inherits_selection_from`` chain may be followed before it is refused.
MAX_INHERITANCE_DEPTH = 8

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

BAR_GRID = "bar"
DAILY_GRID = "utc_day"
LEGACY_GRID = "legacy_positional"
NO_GRID = "none"


class AlignmentError(ValueError):
    """Two streams cannot be placed on one clock; nothing is padded or imputed."""


@dataclass(frozen=True)
class ReturnStream:
    """One observed return vector and, when the artifact proves it, its UTC index.

    ``timestamps_ns`` is ``None`` exactly when the evidence carries no time index.
    ``required`` streams raise on an incompatible grid (the contract's own history);
    optional streams (group peers, other lineage members) are excluded and reported.
    """

    stream_id: str
    returns: np.ndarray
    timestamps_ns: np.ndarray | None = None
    contract_id: str = ""
    origin: str = "trial"
    required: bool = False
    legacy_positional: bool = False
    detail: str = ""

    def __post_init__(self) -> None:
        values = np.asarray(self.returns, dtype=float)
        if values.ndim != 1:
            raise AlignmentError(f"{self.stream_id}: returns must be one-dimensional")
        object.__setattr__(self, "returns", values)
        if self.timestamps_ns is not None:
            stamps = np.asarray(self.timestamps_ns, dtype=np.int64)
            if stamps.ndim != 1:
                raise AlignmentError(f"{self.stream_id}: timestamps must be one-dimensional")
            _require_strictly_increasing(stamps, self.stream_id)
            if len(stamps) != len(values):
                raise AlignmentError(
                    f"{self.stream_id}: {len(values)} returns but {len(stamps)} timestamps")
            object.__setattr__(self, "timestamps_ns", stamps)

    @property
    def has_timestamps(self) -> bool:
        return self.timestamps_ns is not None


@dataclass(frozen=True)
class ExcludedStream:
    """A stream that could not join the panel, and why.  Never dropped silently."""

    stream_id: str
    reason: str
    legacy: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"stream_id": self.stream_id, "reason": self.reason, "legacy": self.legacy}


@dataclass(frozen=True)
class AlignmentResult:
    """One comparable panel: the candidate, its peers, and the index they share."""

    candidate: np.ndarray
    matrix: np.ndarray
    stream_ids: tuple[str, ...]
    index_ns: np.ndarray
    frequency: str
    excluded: tuple[ExcludedStream, ...] = ()
    overlap_fraction: float = 1.0
    periods_per_year: float | None = None

    @property
    def legacy_excluded(self) -> int:
        return sum(1 for item in self.excluded if item.legacy)

    @property
    def n_bars(self) -> int:
        return int(self.candidate.size)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frequency": self.frequency,
            "aligned_bars": self.n_bars,
            "streams_aligned": len(self.stream_ids),
            "overlap_fraction": float(self.overlap_fraction),
            "periods_per_year": self.periods_per_year,
            "legacy_excluded": self.legacy_excluded,
            "excluded": [item.to_dict() for item in self.excluded],
        }


# --------------------------------------------------------------------------- clock


def _require_strictly_increasing(stamps: np.ndarray, label: str) -> None:
    if stamps.size and bool(np.any(np.diff(stamps) <= 0)):
        raise AlignmentError(
            f"{label}: timestamps must be strictly increasing (no duplicates or reordering)")


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, np.datetime64):
        raise AlignmentError(
            "naive datetime64 timestamps are refused: pass UTC-aware datetimes so no "
            "timezone is inferred from the data")
    raise AlignmentError(f"unsupported timestamp value {value!r}; UTC-aware datetimes are required")


def utc_ns(values: Iterable[Any] | Any) -> np.ndarray:
    """Normalise timestamps to int64 UTC nanoseconds, refusing naive input.

    Accepts any iterable of timezone-aware datetimes, a polars ``Series`` or a pandas
    ``DatetimeIndex``.  A tz-naive value is an *unknown* clock, so it is refused rather
    than assumed to be UTC: an assumed clock is exactly the inference this ticket
    forbids.  A non-UTC aware value is converted (that is a fact, not a guess).
    """
    if hasattr(values, "to_list"):
        values = values.to_list()
    elif hasattr(values, "tolist") and not isinstance(values, np.ndarray):
        values = values.tolist()
    stamps = np.empty(len(values), dtype=np.int64)
    for position, raw in enumerate(values):
        moment = _as_datetime(raw)
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise AlignmentError(
                "naive timestamps are refused: UTC-aware datetimes are required so the "
                "alignment clock is declared, never inferred")
        delta = moment.astimezone(UTC) - _EPOCH
        stamps[position] = ((delta.days * 86_400 + delta.seconds) * NS_PER_SECOND
                            + delta.microseconds * 1_000)
    _require_strictly_increasing(stamps, "timestamps")
    return stamps


def utc_days(stamps_ns: np.ndarray) -> np.ndarray:
    """UTC calendar day number (int64) for each epoch-nanosecond timestamp."""
    return np.asarray(stamps_ns, dtype=np.int64) // NS_PER_DAY


def observed_periods_per_year(stamps_ns: np.ndarray) -> float | None:
    """Observation rate measured from the index itself (never assumed from the contract)."""
    if stamps_ns.size < 2:
        return None
    days = int(utc_days(stamps_ns)[-1] - utc_days(stamps_ns)[0]) + 1
    span = days / 365.25
    if span <= 0.0:
        return None
    return float(stamps_ns.size) / span


# ------------------------------------------------------------------- intersection


def intersect_sorted(*arrays: np.ndarray) -> np.ndarray:
    """Exact intersection of strictly increasing int64 arrays."""
    if not arrays:
        return np.empty(0, dtype=np.int64)
    common = np.asarray(arrays[0], dtype=np.int64)
    for other in arrays[1:]:
        common = np.intersect1d(common, np.asarray(other, dtype=np.int64), assume_unique=True)
        if common.size == 0:
            break
    return common


def compound_by_utc_day(stamps_ns: np.ndarray, returns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Compound each UTC day's returns: ``prod(1 + r) - 1`` per calendar day.

    Used only when two streams share dates but not bar boundaries (e.g. a DAX-only grid
    against a multi-exchange union).  Compounding is done in log space for stability; a
    total loss (``1 + r <= 0``) is refused because the log is then undefined.
    """
    values = np.asarray(returns, dtype=float)
    stamps = np.asarray(stamps_ns, dtype=np.int64)
    if values.size != stamps.size:
        raise AlignmentError("compound_by_utc_day: returns and timestamps differ in length")
    if not np.isfinite(values).all():
        raise AlignmentError("compound_by_utc_day: returns must be finite")
    gross = 1.0 + values
    if np.any(gross <= 0.0):
        raise AlignmentError("compound_by_utc_day: a return of -100% or worse cannot be compounded")
    days, inverse = np.unique(utc_days(stamps), return_inverse=True)
    totals = np.zeros(days.size, dtype=float)
    np.add.at(totals, inverse, np.log(gross))
    return days, np.expm1(totals)


# ------------------------------------------------------------------------ alignment


@dataclass
class _Attempt:
    """One candidate index plus the streams that fit it."""

    frequency: str
    index: np.ndarray
    candidate: np.ndarray
    chosen: list[tuple[ReturnStream, np.ndarray]] = field(default_factory=list)
    excluded: list[ExcludedStream] = field(default_factory=list)
    fatal: list[ReturnStream] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.frequency != NO_GRID and not self.fatal and self.candidate.size > 0


def _positions(stamps: np.ndarray, common: np.ndarray, label: str) -> np.ndarray:
    positions = np.searchsorted(stamps, common)
    if positions.size and (int(positions.max()) >= stamps.size
                           or not np.array_equal(stamps[positions], common)):
        raise AlignmentError(f"{label}: timestamp intersection is not reproducible")
    return positions


def _take(values: np.ndarray, stamps: np.ndarray, common: np.ndarray, label: str) -> np.ndarray:
    taken = np.asarray(values, dtype=float)[_positions(stamps, common, label)]
    if not np.isfinite(taken).all():
        raise AlignmentError(f"{label}: aligned window contains non-finite returns")
    return taken


def _coverage(common: np.ndarray, *window_sizes: int) -> float:
    """Worst per-stream share of its own window that survives the intersection.

    A comparison is only the same experiment when *no* stream — candidate or prior —
    loses more than ``min_overlap`` of its own observations.  Measuring against the
    candidate alone made the choice depend on which trial happened to be the candidate:
    the same two streams aligned bar by bar one way and compounded daily the other way,
    so the DSR basis was not reciprocal.  Returning the minimum (rather than the share
    of the shortest window) keeps both protections: a wide stream is never compared on
    the handful of stamps it shares with a narrower one, and the answer no longer
    depends on which side is the candidate.
    """
    shares = [float(common.size) / float(size) for size in window_sizes if int(size) > 0]
    return min(shares) if shares else 0.0


def project_onto_grid(
    stamps_ns: np.ndarray,
    values: np.ndarray,
    grid_ns: np.ndarray,
) -> np.ndarray:
    """Place one stream's returns on a wider clock, at zero where it held nothing.

    Used when a trial observes only part of the contract's validation grid (a
    ``target_symbols`` subset).  On a bar the trial could not observe it holds no
    position, so its portfolio return there is exactly zero by the accounting
    identity — the projection never imputes a price, a fill or a return.  Every
    recorded observation lands on its own timestamp, and a recorded timestamp that
    the grid does not contain means the two clocks are not comparable: that is
    refused, never dropped or shifted.
    """
    stamps = np.asarray(stamps_ns, dtype=np.int64)
    grid = np.asarray(grid_ns, dtype=np.int64)
    vals = np.asarray(values, dtype=float)
    if stamps.size != vals.size:
        raise AlignmentError("project_onto_grid: returns and timestamps differ in length")
    if vals.ndim != 1:
        raise AlignmentError("project_onto_grid: returns must be one-dimensional")
    if not np.isfinite(vals).all():
        raise AlignmentError("project_onto_grid: returns must be finite")
    if grid.size and bool(np.any(np.diff(grid) <= 0)):
        raise AlignmentError("project_onto_grid: the target grid must be strictly increasing")
    positions = np.searchsorted(grid, stamps)
    if stamps.size and (int(positions.max()) >= grid.size
                        or not np.array_equal(grid[positions], stamps)):
        raise AlignmentError(
            "project_onto_grid: an observed timestamp is absent from the target grid; the "
            "clocks are not comparable and nothing is dropped, shifted or padded")
    out = np.zeros(grid.size, dtype=float)
    out[positions] = vals
    return out


def _bar_attempt(
    cand_ns: np.ndarray, candidate: np.ndarray, timestamped: Sequence[ReturnStream],
    *, min_overlap: float, min_bars: int,
) -> _Attempt:
    attempt = _Attempt(BAR_GRID, cand_ns, candidate)
    if not timestamped:
        return attempt  # the candidate's own grid; there is no prior stream to intersect
    common = intersect_sorted(cand_ns, *_stamps_all(timestamped))
    coverage = _coverage(common, cand_ns.size, *[stream.returns.size for stream in timestamped])
    if common.size < min_bars or coverage < min_overlap:
        attempt.frequency = NO_GRID
        return attempt
    included: list[ReturnStream] = []
    for stream in timestamped:
        stream_common = intersect_sorted(common, _stamps(stream))
        if stream_common.size < min_bars:
            attempt.excluded.append(ExcludedStream(
                stream.stream_id, "timestamp intersection below the overlap floor"))
            if stream.required:
                attempt.fatal.append(stream)
            continue
        included.append(stream)
    if not included:
        attempt.frequency = NO_GRID
        return attempt
    shared = intersect_sorted(cand_ns, *_stamps_all(included))
    if shared.size < min_bars:
        attempt.excluded.extend(ExcludedStream(
            stream.stream_id, "shared bar grid collapsed below the minimum") for stream in included)
        attempt.fatal.extend(stream for stream in included if stream.required)
        attempt.frequency = NO_GRID
        return attempt
    attempt.index = shared
    attempt.candidate = _take(candidate, cand_ns, shared, "candidate")
    attempt.chosen = [
        (stream, _take(stream.returns, _stamps(stream), shared, stream.stream_id))
        for stream in included
    ]
    return attempt


def _stamps(stream: ReturnStream) -> np.ndarray:
    """Non-optional timestamp accessor for streams already known to carry an index."""
    if stream.timestamps_ns is None:
        raise AlignmentError(f"{stream.stream_id}: expected a timestamped stream")
    return stream.timestamps_ns


def _stamps_all(streams: Sequence[ReturnStream]) -> list[np.ndarray]:
    return [_stamps(stream) for stream in streams]


def _daily_attempt(
    cand_ns: np.ndarray, candidate: np.ndarray, timestamped: Sequence[ReturnStream],
    *, min_overlap: float, min_bars: int,
) -> _Attempt:
    attempt = _Attempt(DAILY_GRID, np.empty(0, dtype=np.int64), np.empty(0))
    if not timestamped:
        attempt.frequency = NO_GRID
        return attempt
    cand_days = np.unique(utc_days(cand_ns))
    eligible: list[ReturnStream] = []
    for stream in timestamped:
        stream_days = np.unique(utc_days(_stamps(stream)))
        shared = intersect_sorted(cand_days, stream_days)
        if shared.size >= min_bars:
            eligible.append(stream)
        else:
            attempt.excluded.append(ExcludedStream(
                stream.stream_id, "UTC-date intersection below the overlap floor"))
            if stream.required:
                attempt.fatal.append(stream)
    if not eligible:
        attempt.frequency = NO_GRID
        return attempt
    shared_days = intersect_sorted(cand_days, *[np.unique(utc_days(_stamps(s))) for s in eligible])
    if shared_days.size < min_bars or _coverage(
        shared_days,
        cand_days.size,
        *[np.unique(utc_days(_stamps(stream))).size for stream in eligible],
    ) < min_overlap:
        attempt.frequency = NO_GRID
        attempt.fatal.extend(s for s in eligible if s.required)
        return attempt
    cand_day_index, cand_daily = compound_by_utc_day(cand_ns, candidate)
    attempt.index = shared_days * NS_PER_DAY
    attempt.candidate = _take(cand_daily, cand_day_index, shared_days, "candidate")
    for stream in eligible:
        day_index, daily = compound_by_utc_day(_stamps(stream), stream.returns)
        attempt.chosen.append((stream, _take(daily, day_index, shared_days, stream.stream_id)))
    return attempt


def _legacy_on_bar_grid(
    legacy: Sequence[ReturnStream], candidate_len: int, same_grid: bool
) -> tuple[list[ReturnStream], list[ExcludedStream]]:
    included, excluded = [], []
    for stream in legacy:
        if same_grid and stream.legacy_positional and stream.returns.size == candidate_len:
            included.append(stream)
            continue
        excluded.append(ExcludedStream(
            stream.stream_id,
            "legacy stream without timestamps cannot be placed on a timestamped grid",
            legacy=True))
    return included, excluded


def _legacy_positional(
    streams: Sequence[ReturnStream], candidate: np.ndarray, expected_len: int
) -> AlignmentResult:
    """Historical proof only: equal, non-empty lengths on the same contract."""
    included, excluded = [], []
    for stream in streams:
        if stream.returns.size != expected_len:
            if stream.required:
                raise AlignmentError(
                    f"Unaligned selection history: {stream.stream_id} has "
                    f"{stream.returns.size} bars but the candidate has {expected_len}; "
                    "refusing to pad, slice or correlate incompatible windows")
            excluded.append(ExcludedStream(
                stream.stream_id, "legacy stream length differs from the candidate", legacy=True))
            continue
        included.append(stream)
    return AlignmentResult(
        candidate=candidate,
        matrix=_stack([s.returns for s in included]),
        stream_ids=tuple(s.stream_id for s in included),
        index_ns=np.empty(0, dtype=np.int64),
        frequency=LEGACY_GRID,
        excluded=tuple(excluded),
        overlap_fraction=1.0,
        periods_per_year=None,
    )


def _stack(vectors: Sequence[np.ndarray]) -> np.ndarray:
    if not vectors:
        return np.empty((0, 0))
    return np.column_stack([np.asarray(v, dtype=float) for v in vectors])


def align_streams(
    streams: Sequence[ReturnStream],
    candidate_returns: Sequence[float] | np.ndarray,
    candidate_timestamps: Iterable[Any] | np.ndarray | None = None,
    *,
    expected_len: int | None = None,
    min_overlap: float = DEFAULT_MIN_OVERLAP,
    min_bars: int = DEFAULT_MIN_BARS,
) -> AlignmentResult:
    """Place the candidate and every prior stream on one index.

    Three modes, in order of preference:

    ``bar``
        every timestamped stream shares the candidate's grid and no stream — candidate
        or prior — loses more than ``min_overlap`` of its own observations to the
        intersection; returns are then correlated bar by bar.
    ``utc_day``
        the grids differ in frequency, so each stream is compounded per UTC day and the
        days are intersected.
    ``legacy_positional``
        neither the candidate nor any prior stream carries timestamps.  This preserves
        the historical same-contract proof: equal lengths only, never a padded or sliced
        vector.

    ``required`` streams raise :class:`AlignmentError` when they cannot be placed;
    optional streams are recorded in ``excluded`` so the caller can charge them.
    """
    candidate = np.asarray(candidate_returns, dtype=float)
    if candidate.ndim != 1:
        raise AlignmentError("candidate returns must be one-dimensional")
    if not np.isfinite(candidate).all():
        raise AlignmentError("candidate returns must be finite")

    cand_ns = utc_ns(candidate_timestamps) if candidate_timestamps is not None else None
    if cand_ns is not None and cand_ns.size != candidate.size:
        raise AlignmentError(
            f"candidate has {candidate.size} returns but {cand_ns.size} timestamps")
    if expected_len is None:
        expected_len = candidate.size
    if expected_len != candidate.size:
        raise AlignmentError(
            f"candidate carries {candidate.size} bars but {expected_len} were expected")

    timestamped = [s for s in streams if s.has_timestamps]
    legacy = [s for s in streams if not s.has_timestamps]

    if cand_ns is None and not timestamped:
        return _legacy_positional(streams, candidate, expected_len)
    if cand_ns is None:
        raise AlignmentError(
            "the candidate has no timestamps while its history does; a positional guess "
            "would invent a clock, which is refused")

    bar = _bar_attempt(cand_ns, candidate, timestamped, min_overlap=min_overlap, min_bars=min_bars)
    if bar.usable:
        included_legacy, legacy_excluded = _legacy_on_bar_grid(
            legacy, cand_ns.size, np.array_equal(bar.index, cand_ns))
        matrix = [values for _, values in bar.chosen]
        identifiers = [stream.stream_id for stream, _ in bar.chosen]
        matrix.extend(stream.returns for stream in included_legacy)
        identifiers.extend(stream.stream_id for stream in included_legacy)
        return AlignmentResult(
            candidate=bar.candidate,
            matrix=_stack(matrix),
            stream_ids=tuple(identifiers),
            index_ns=bar.index,
            frequency=BAR_GRID,
            excluded=tuple(bar.excluded) + tuple(legacy_excluded),
            overlap_fraction=_coverage(
                bar.index, cand_ns.size, *[stream.returns.size for stream, _ in bar.chosen]),
            periods_per_year=observed_periods_per_year(bar.index),
        )

    daily = _daily_attempt(cand_ns, candidate, timestamped, min_overlap=min_overlap,
                           min_bars=min_bars)
    if daily.usable:
        legacy_excluded = [
            ExcludedStream(stream.stream_id,
                           "legacy stream without timestamps cannot join a utc_day grid",
                           legacy=True)
            for stream in legacy
        ]
        return AlignmentResult(
            candidate=daily.candidate,
            matrix=_stack([values for _, values in daily.chosen]),
            stream_ids=tuple(stream.stream_id for stream, _ in daily.chosen),
            index_ns=daily.index,
            frequency=DAILY_GRID,
            excluded=tuple(bar.excluded) + tuple(daily.excluded) + tuple(legacy_excluded),
            overlap_fraction=_coverage(
                np.unique(utc_days(daily.index)),
                np.unique(utc_days(cand_ns)).size,
                *[np.unique(utc_days(_stamps(stream))).size for stream, _ in daily.chosen],
            ),
            periods_per_year=observed_periods_per_year(daily.index),
        )

    fatal = bar.fatal or daily.fatal
    if fatal:
        raise AlignmentError(
            f"{fatal[0].stream_id}: insufficient timestamp overlap and no daily-frequency "
            "fallback; refusing to pad, slice or correlate incompatible windows")
    reported = {item.stream_id for item in (*bar.excluded, *daily.excluded)}
    return AlignmentResult(
        candidate=candidate[:0],
        matrix=np.empty((0, 0)),
        stream_ids=(),
        index_ns=np.empty(0, dtype=np.int64),
        frequency=NO_GRID,
        excluded=tuple(bar.excluded) + tuple(daily.excluded)
        + tuple(ExcludedStream(stream.stream_id, "stream could not join any comparable index")
                for stream in timestamped if stream.stream_id not in reported)
        + tuple(ExcludedStream(s.stream_id, "legacy stream without timestamps", legacy=True)
                for s in legacy),
        overlap_fraction=0.0,
    )


def check_alignment(
    streams: Sequence[ReturnStream],
    candidate_returns: Sequence[float] | np.ndarray,
    candidate_timestamps: Iterable[Any] | np.ndarray,
    *,
    expected_len: int | None = None,
    min_overlap: float = DEFAULT_MIN_OVERLAP,
    min_bars: int = DEFAULT_MIN_BARS,
) -> AlignmentResult:
    """Pre-reservation variant: same rules, and *any* refusal is fatal.

    Used before ``register_trial_start`` so a genuinely incompatible grid cannot consume
    a trial slot.  Streams that merely lack timestamps are still reported as debt rather
    than invented.
    """
    result = align_streams(
        streams, candidate_returns, candidate_timestamps,
        expected_len=expected_len, min_overlap=min_overlap, min_bars=min_bars)
    if result.frequency == NO_GRID:
        raise AlignmentError(
            "no timestamped history can be placed on the candidate's clock; refusing to "
            "reserve a trial slot for an incomparable selection history")
    if result.n_bars < min_bars:
        raise AlignmentError(
            f"only {result.n_bars} aligned bars remain; at least {min_bars} are required")
    return result
