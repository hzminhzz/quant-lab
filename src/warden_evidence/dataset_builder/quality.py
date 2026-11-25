"""The mandatory quality gate: one pass from raw rows to an auditable panel.

Everything that can remove a row is declared in the spec and recorded here:

* universe selection (declared symbols) — dropped rows per symbol;
* ``explicit_anomaly_filters`` — matched and dropped counts per filter, per asset;
* sub-bar coverage on resampling — dropped bins and the source rows inside them.

Nothing else may drop a row, and the ledger is reconciled: the report must
account for every input row, or the build fails.  ML4T's ``OHLCVValidator`` and
``AnomalyManager`` (MAD) are mandatory — there is no switch that turns them off.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import polars as pl
from ml4t.data.anomaly import AnomalyConfig, AnomalyManager
from ml4t.data.anomaly.base import AnomalyType
from ml4t.data.anomaly.config import PriceStalenessConfig, ReturnOutlierConfig, VolumeSpikeConfig
from ml4t.data.validation import OHLCVValidator
from ml4t.data.validation.rules import ValidationRulePresets

from warden_evidence.dataset_builder.spec import (
    CANONICAL_SOURCE_COLUMNS,
    AnomalyFilter,
    DatasetSpec,
    SessionAnchor,
)

#: The gate is one implementation with two declared windows; the bucket that a bar must
#: fall in is a property of the dataset, never of the caller's convenience.
WindowRole = Literal["exploratory", "holdout"]
WINDOW_ROLES: tuple[str, ...] = ("exploratory", "holdout")

GATE_VERSION = 2

#: ML4T validation preset per declared asset class.  Mirrors
#: ``warden_evidence.market_validation`` (which owns the contract-facing gate);
#: the builder must not invent its own tolerance for the same asset class.
ASSET_CLASS_PRESETS: dict[str, str] = {
    "crypto_perps": "crypto",
    "fx": "forex",
    "indices": "equities",
    "futures": "futures",
    "metals": "commodities",
    "commodities": "commodities",
    "multi_asset": "equities",
}

_UTC_ZONES = frozenset({"UTC", "Etc/UTC"})
_OHLCV = ("open", "high", "low", "close", "volume")
_EXAMPLES = 5


class QualityGateError(ValueError):
    """The gate refused the panel; the report says exactly why."""

    def __init__(self, message: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = report or {}


class TemporalFenceError(QualityGateError):
    """A bar reached at or past the exploratory fence."""


@dataclass(frozen=True)
class GateOutcome:
    frame: pl.DataFrame
    report: dict[str, Any]


def _asset_counts(frame: pl.DataFrame) -> dict[str, int]:
    if frame.is_empty():
        return {}
    counts = frame.group_by("asset").len().sort("asset")
    return {str(asset): int(count) for asset, count in counts.iter_rows()}


def _shape(raw: pl.DataFrame, spec: DatasetSpec) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Validate and normalise the raw schema; no rows are removed here."""
    unknown = sorted(set(raw.columns) - CANONICAL_SOURCE_COLUMNS)
    if unknown:
        raise QualityGateError(f"raw frame carries unknown columns {unknown}")
    missing = [column for column in spec.required_columns if column not in raw.columns]
    if missing:
        raise QualityGateError(
            f"raw frame lacks required columns {missing} for price_basis {spec.price_basis!r}"
        )
    frame = raw

    dtype = frame.schema["timestamp"]
    converted_from: str | None = None
    if not isinstance(dtype, pl.Datetime):
        raise QualityGateError(
            f"'timestamp' must be a Datetime before the gate, got {dtype}"
        )
    if dtype.time_zone is None:
        raise QualityGateError(
            "'timestamp' has no timezone; a naive column is never silently labelled UTC"
        )
    if dtype.time_zone not in _UTC_ZONES:
        converted_from = str(dtype.time_zone)
        frame = frame.with_columns(pl.col("timestamp").dt.convert_time_zone("UTC"))
    frame = frame.with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
        pl.col("asset").cast(pl.String),
        *[pl.col(column).cast(pl.Float64, strict=False) for column in _OHLCV],
        *(
            [pl.col(column).cast(pl.Float64, strict=False) for column in ("funding_rate", "bid", "ask")
             if column in frame.columns]
        ),
    )

    if frame.is_empty():
        raise QualityGateError("raw frame is empty")
    bad_asset = frame.filter(
        pl.col("asset").is_null() | (pl.col("asset").str.len_chars() == 0)
    )
    if bad_asset.height:
        raise QualityGateError(f"{bad_asset.height} rows have a null or empty asset")

    if frame.select(pl.col("timestamp").is_null().any()).item():
        raise QualityGateError("raw frame has null timestamps")
    for column in (*_OHLCV, "funding_rate", "bid", "ask"):
        if column not in frame.columns:
            continue
        bad = frame.filter(pl.col(column).is_null() | ~pl.col(column).is_finite())
        if bad.height:
            raise QualityGateError(
                f"{bad.height} rows have null or non-finite values in '{column}'"
            )
    for column in ("open", "high", "low", "close"):
        bad = frame.filter(pl.col(column) <= 0)
        if bad.height:
            raise QualityGateError(f"{bad.height} rows have non-positive '{column}'")
    negative_volume = frame.filter(pl.col("volume") < 0)
    if negative_volume.height:
        raise QualityGateError(f"{negative_volume.height} rows have negative volume")

    duplicate = frame.group_by(["timestamp", "asset"]).len().filter(pl.col("len") > 1)
    if duplicate.height:
        raise QualityGateError(
            f"{duplicate.height} duplicated (timestamp, asset) keys; refusing silent data loss"
        )

    ordered = frame.sort(["timestamp", "asset"])
    was_sorted = ordered.equals(frame)
    return ordered, {
        "converted_from_timezone": converted_from,
        "raw_was_sorted": was_sorted,
        "rows": ordered.height,
        "assets": sorted({str(value) for value in ordered["asset"].unique().to_list()}),
    }


def _select_universe(
    frame: pl.DataFrame, spec: DatasetSpec
) -> tuple[pl.DataFrame, dict[str, Any]]:
    declared = set(spec.universe.symbols)
    present = {str(value) for value in frame["asset"].unique().to_list()}
    missing = sorted(declared - present)
    if missing:
        raise QualityGateError(
            f"declared universe symbols {missing} are absent from the raw inputs; "
            "an explicit universe is not satisfied by fewer symbols"
        )
    extra = present - declared
    dropped: dict[str, int] = {}
    if extra:
        dropped = _asset_counts(frame.filter(pl.col("asset").is_in(sorted(extra))))
        frame = frame.filter(pl.col("asset").is_in(sorted(declared)))
    return frame, {
        "declared": list(spec.universe.symbols),
        "present": sorted(present),
        "retain_delisted": spec.universe.retain_delisted,
        "dropped_unselected_symbols": dropped,
        "dropped_rows": int(sum(dropped.values())),
    }


def _filter_mask(frame: pl.DataFrame, item: AnomalyFilter) -> pl.Expr:
    if item.column == "bar_return":
        expression = pl.col("close").pct_change().over("asset")
    else:
        expression = pl.col(item.column)
    comparison = item.comparison
    if comparison == "gt":
        mask = expression > item.value
    elif comparison == "ge":
        mask = expression >= item.value
    elif comparison == "lt":
        mask = expression < item.value
    elif comparison == "le":
        mask = expression <= item.value
    elif comparison == "abs_gt":
        mask = expression.abs() > item.value
    elif comparison == "abs_ge":
        mask = expression.abs() >= item.value
    else:  # pragma: no cover - the spec Literal forbids it
        raise QualityGateError(f"unsupported comparison {comparison!r}")
    mask = mask.fill_null(False)
    if item.assets:
        mask = mask & pl.col("asset").is_in(sorted(item.assets))
    return mask


def _apply_filters(
    frame: pl.DataFrame, spec: DatasetSpec
) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    applied: list[dict[str, Any]] = []
    for item in spec.quality_gate.explicit_anomaly_filters:
        mask = _filter_mask(frame, item)
        matched = frame.filter(mask)
        dropped_by_asset = _asset_counts(matched)
        applied.append(
            {
                "id": item.id,
                "column": item.column,
                "comparison": item.comparison,
                "value": item.value,
                "assets": sorted(item.assets) if item.assets else None,
                "reason": item.reason,
                "rows_before": frame.height,
                "matched": matched.height,
                "dropped": matched.height,
                "dropped_by_asset": dropped_by_asset,
                "examples": [
                    str(value)
                    for value in matched["timestamp"].sort().head(_EXAMPLES).to_list()
                ],
            }
        )
        frame = frame.filter(~mask)
        if frame.is_empty():
            raise QualityGateError(f"anomaly filter {item.id!r} removed every row")
    return frame, applied


def _zero_volume_policy(frame: pl.DataFrame, spec: DatasetSpec) -> dict[str, Any]:
    zero = frame.filter(pl.col("volume") == 0)
    if zero.height and not spec.quality_gate.allow_zero_volume:
        raise QualityGateError(
            f"{zero.height} zero-volume rows by asset {_asset_counts(zero)}; "
            "declare allow_zero_volume or an explicit filter instead of an implicit drop"
        )
    return {"zero_volume_rows": zero.height, "allowed": spec.quality_gate.allow_zero_volume}


def _validator(spec: DatasetSpec) -> OHLCVValidator:
    preset = ASSET_CLASS_PRESETS[spec.asset_class]
    rules = ValidationRulePresets.for_asset_class(preset)
    return OHLCVValidator(
        check_nulls=rules.check_nulls,
        check_price_consistency=rules.check_price_consistency,
        negative_price_policy="forbid",
        check_negative_volume=rules.check_negative_volume,
        check_duplicate_timestamps=rules.check_duplicate_timestamps,
        check_chronological_order=rules.check_chronological_order,
        check_price_staleness=rules.check_price_staleness,
        # Extreme returns are enforced below against max_abs_bar_return, where an
        # explicit filter can cover them with an audited count.
        check_extreme_returns=False,
        max_return_threshold=spec.quality_gate.max_abs_bar_return,
        staleness_threshold=rules.staleness_threshold,
    )


def _run_validator(frame: pl.DataFrame, spec: DatasetSpec, *, stage: str) -> int:
    validator = _validator(spec)
    for sub in frame.partition_by("asset", maintain_order=True):
        symbol = str(sub["asset"][0])
        result = validator.validate(sub)
        issues = [
            issue
            for issue in result.issues
            if getattr(issue.severity, "value", "") in {"critical", "error"}
        ]
        if issues or not result.passed:
            detail = "; ".join(f"{issue.check}: {issue.message}" for issue in issues) or "failed"
            raise QualityGateError(f"OHLCVValidator ({stage}) refused {symbol}: {detail}")
    return len({str(value) for value in frame["asset"].unique().to_list()})


def _extreme_returns(frame: pl.DataFrame, spec: DatasetSpec) -> dict[str, Any]:
    threshold = spec.quality_gate.max_abs_bar_return
    if frame.height < 2:
        return {"threshold": threshold, "max_abs_bar_return": 0.0, "violations": 0}
    frame = frame.with_columns(pl.col("close").pct_change().over("asset").alias("_bar_return"))
    worst = (
        frame.filter(pl.col("_bar_return").is_not_null())
        .group_by("asset")
        .agg(pl.col("_bar_return").abs().max().alias("max_abs"))
        .sort("asset")
    )
    violations = frame.filter(pl.col("_bar_return").abs() > threshold)
    if violations.height:
        details = _asset_counts(violations)
        raise QualityGateError(
            f"{violations.height} bars exceed max_abs_bar_return {threshold} by asset {details}; "
            "cover them with an explicit_anomaly_filter (audited) or fix the source"
        )
    return {
        "threshold": threshold,
        "max_abs_bar_return": float(worst["max_abs"].max() or 0.0),
        "violations": 0,
    }


def _mad_anomalies(frame: pl.DataFrame, spec: DatasetSpec) -> dict[str, Any]:
    manager = AnomalyManager(
        AnomalyConfig(
            return_outliers=ReturnOutlierConfig(
                method="mad", threshold=spec.quality_gate.mad_outlier_threshold
            ),
            volume_spikes=VolumeSpikeConfig(enabled=False),
            price_staleness=PriceStalenessConfig(enabled=False),
        )
    )
    preset = ASSET_CLASS_PRESETS[spec.asset_class]
    total = 0
    examples: list[str] = []
    for sub in frame.partition_by("asset", maintain_order=True):
        symbol = str(sub["asset"][0])
        report = manager.analyze(sub, symbol=symbol, asset_class=preset)
        outliers = report.get_by_type(AnomalyType.RETURN_OUTLIER)
        if outliers:
            total += len(outliers)
            if len(examples) < _EXAMPLES:
                examples.append(f"{symbol}: {outliers[0].message}")
    if total:
        raise QualityGateError(
            f"{total} MAD return outliers at threshold {spec.quality_gate.mad_outlier_threshold} "
            f"remain after the explicit filters ({examples}); the gate does not drop them silently"
        )
    return {
        "method": "mad",
        "threshold": spec.quality_gate.mad_outlier_threshold,
        "return_outliers": 0,
    }


def _window_bounds(spec: DatasetSpec, role: WindowRole) -> tuple[datetime, datetime]:
    """The declared half-open window one build is allowed to cover."""
    window = spec.time_window
    if role == "holdout":
        return window.holdout_start_inclusive, window.holdout_end_exclusive
    if role == "exploratory":
        return window.start_inclusive, window.exploratory_end_exclusive
    raise QualityGateError(f"unknown window role {role!r}; expected one of {WINDOW_ROLES}")


def _duration(minutes: int) -> str:
    """A Polars duration string; ``offset_by`` takes text, and wall-clock arithmetic."""
    sign = "-" if minutes < 0 else ""
    hours, rest = divmod(abs(minutes), 60)
    if hours and rest:
        return f"{sign}{hours}h{rest}m"
    if hours:
        return f"{sign}{hours}h"
    return f"{sign}{rest}m"


def _session_open_expr(anchor: SessionAnchor) -> pl.Expr:
    """The session open each row belongs to, in UTC, on the venue's session clock.

    The shift is done on the *naive local wall clock* (``replace_time_zone(None)``), not on
    the instant: Polars' ``offset_by`` on a zoned column keeps the instant and re-expresses
    it locally, so around a DST switch it lands on the wrong calendar day.  Subtracting the
    anchor on the wall clock and re-attaching the zone is what makes a Sunday-evening reopen
    join the session that opens Sunday 17:00 New York in both winter (22:00 UTC) and summer
    (21:00 UTC).
    """
    wall = pl.col("timestamp").dt.convert_time_zone(anchor.zone).dt.replace_time_zone(None)
    shifted = wall.dt.offset_by(_duration(-anchor.offset_minutes()))
    return (
        shifted.dt.date()
        .cast(pl.Datetime("us"))
        .dt.offset_by(_duration(anchor.offset_minutes()))
        .dt.replace_time_zone(anchor.zone)
        .dt.convert_time_zone("UTC")
        .cast(pl.Datetime("us", "UTC"))
    )


def _aggregate(frame: pl.DataFrame, spec: DatasetSpec) -> pl.DataFrame:
    aggregations: list[pl.Expr] = [
        pl.col("open").first(),
        pl.col("high").max(),
        pl.col("low").min(),
        pl.col("close").last(),
        pl.len().cast(pl.UInt32).alias("observed_sub_bars"),
    ]
    if spec.volume_semantics == "tick_count_proxy":
        aggregations.append(pl.len().cast(pl.Float64).alias("volume"))
    else:
        aggregations.append(pl.col("volume").sum())
    for column in ("funding_rate", "bid", "ask"):
        if column in frame.columns:
            aggregations.append(pl.col(column).last())
    anchor = spec.session_anchor
    if anchor is None:
        return frame.group_by_dynamic(
            "timestamp",
            every=spec.frequency,
            closed="left",
            label="left",
            group_by="asset",
        ).agg(aggregations)

    # Session bins are explicit: ``group_by_dynamic`` floors on the UTC grid, which is what
    # produced the two-hour Sunday bar.  ``open``/``close`` are taken with an explicit
    # sort so a session's first and last *row* decide them, not the grouping order.
    ordered = frame.sort(["asset", "timestamp"]).with_columns(
        _session_open_expr(anchor).alias("_session")
    )
    session_aggregations: list[pl.Expr] = [
        pl.col("open").sort_by("timestamp").first(),
        pl.col("high").max(),
        pl.col("low").min(),
        pl.col("close").sort_by("timestamp").last(),
        pl.len().cast(pl.UInt32).alias("observed_sub_bars"),
    ]
    if spec.volume_semantics == "tick_count_proxy":
        session_aggregations.append(pl.len().cast(pl.Float64).alias("volume"))
    else:
        session_aggregations.append(pl.col("volume").sum())
    for column in ("funding_rate", "bid", "ask"):
        if column in frame.columns:
            session_aggregations.append(pl.col(column).sort_by("timestamp").last())
    extras = [column for column in ("funding_rate", "bid", "ask") if column in frame.columns]
    return (
        ordered.group_by(["asset", "_session"])
        .agg(session_aggregations)
        .rename({"_session": "timestamp"})
        .select(
            [
                "timestamp",
                "asset",
                "open",
                "high",
                "low",
                "close",
                "observed_sub_bars",
                "volume",
                *extras,
            ]
        )
        # ``group_by`` does not promise an order, and the post-resample validator refuses a
        # frame whose bars are not chronological; the canonical order is restored here.
        .sort(["timestamp", "asset"])
    )


def _resample(frame: pl.DataFrame, spec: DatasetSpec) -> tuple[pl.DataFrame, dict[str, Any]]:
    if not spec.resampling.enabled:
        frame = frame.with_columns(pl.lit(1, dtype=pl.UInt32).alias("observed_sub_bars"))
        return frame, {
            "enabled": False,
            "closed": "left",
            "label": "left",
            "min_sub_bars": spec.resampling.require_min_sub_bars,
            "dropped_bins": 0,
            "dropped_source_rows": 0,
            "source_rows": frame.height,
            "kept_source_rows": frame.height,
        }

    minimum = spec.resampling.require_min_sub_bars
    bins = _aggregate(frame, spec)
    dropped = bins.filter(pl.col("observed_sub_bars") < minimum) if minimum else bins.head(0)
    kept = bins.filter(pl.col("observed_sub_bars") >= minimum) if minimum else bins
    if kept.is_empty():
        raise QualityGateError("resampling produced no bin")
    observed = kept["observed_sub_bars"]
    anchor = spec.session_anchor
    return kept, {
        "enabled": True,
        "closed": "left",
        "label": "left",
        "every": spec.frequency,
        # A session-binned daily dataset groups on the venue's session clock, not on a UTC
        # day boundary; recording which one ran keeps the two indistinguishable in words
        # only by accident.
        "grouping": "session_close" if anchor is not None else "utc_grid",
        "daily_roll_anchor": None if anchor is None else anchor.text,
        "min_sub_bars": minimum,
        "sub_bars_min": int(observed.min() or 0),
        "sub_bars_max": int(observed.max() or 0),
        "dropped_bins": dropped.height,
        "dropped_source_rows": int(dropped["observed_sub_bars"].sum() or 0),
        "source_rows": frame.height,
        "kept_source_rows": int(observed.sum() or 0),
    }


def _assert_output_invariants(frame: pl.DataFrame) -> None:
    bad_high = frame.filter(pl.col("high") < pl.max_horizontal("open", "close"))
    bad_low = frame.filter(pl.col("low") > pl.min_horizontal("open", "close"))
    if bad_high.height or bad_low.height:
        raise QualityGateError(
            f"aggregated OHLC is inconsistent ({bad_high.height} high, {bad_low.height} low)"
        )
    for column in ("open", "high", "low", "close"):
        if frame.filter(pl.col(column) <= 0).height:
            raise QualityGateError(f"aggregated '{column}' has non-positive values")
    if "bid" in frame.columns and "ask" in frame.columns:
        if frame.filter(pl.col("ask") < pl.col("bid")).height:
            raise QualityGateError("aggregated quote feed has ask < bid")
        for column in ("bid", "ask"):
            if frame.filter(pl.col(column) <= 0).height:
                raise QualityGateError(f"aggregated '{column}' has non-positive values")


def _grid_alignment(frame: pl.DataFrame, spec: DatasetSpec) -> dict[str, Any]:
    anchor = spec.session_anchor
    if anchor is not None:
        local = frame["timestamp"].dt.convert_time_zone(anchor.zone)
        # The bar *label* is the session open.  Sub-minute drift, a fixed-offset
        # approximation or a UTC-day bar all land off this clock and are refused.
        off_clock = frame.filter(
            (local.dt.hour() != anchor.hour) | (local.dt.minute() != anchor.minute)
        )
        if off_clock.height:
            examples = [str(value) for value in off_clock["timestamp"].head(_EXAMPLES).to_list()]
            raise QualityGateError(
                f"{off_clock.height} daily bars are not on the declared session clock "
                f"{anchor.text!r} {examples}"
            )
        return {
            "aligned": True,
            "clock": "daily_roll_anchor",
            "daily_roll_anchor": anchor.text,
        }
    step_us = spec.bar_seconds * 1_000_000
    misaligned = frame.filter(pl.col("timestamp").dt.epoch("us") % step_us != 0)
    if misaligned.height:
        examples = [str(value) for value in misaligned["timestamp"].head(_EXAMPLES).to_list()]
        raise QualityGateError(
            f"{misaligned.height} bars are off the {spec.frequency} UTC grid {examples}"
        )
    return {"aligned": True, "step_seconds": spec.bar_seconds, "clock": "utc_grid"}


def _fence(frame: pl.DataFrame, spec: DatasetSpec, role: WindowRole) -> dict[str, Any]:
    start, end = _window_bounds(spec, role)
    earliest = frame["timestamp"].min()
    latest = frame["timestamp"].max()
    if earliest is None or latest is None:
        raise QualityGateError("panel has no timestamps after the gate")
    if earliest < start:
        raise QualityGateError(
            f"{role} panel starts at {earliest}, before {role}_start {start.isoformat()}; "
            "a window may not be filled with bars from the other side of the fence"
        )
    if latest >= end:
        raise TemporalFenceError(
            f"max(timestamp) {latest} reaches {role}_end_exclusive {end.isoformat()}; "
            "the temporal fence is closed",
            {"max_timestamp": str(latest), "fence": end.isoformat(), "window_role": role},
        )
    window = spec.time_window
    return {
        "window_role": role,
        "max_timestamp": str(latest),
        "min_timestamp": str(earliest),
        "window_start_inclusive": start.isoformat(),
        "window_end_exclusive": end.isoformat(),
        "exploratory_end_exclusive": window.exploratory_end_exclusive.isoformat(),
        "holdout_start_inclusive": window.holdout_start_inclusive.isoformat(),
        "fence_satisfied": True,
    }


def _expected_sessions(spec: DatasetSpec, start: datetime, end: datetime) -> list[datetime]:
    anchor = spec.session_anchor
    assert anchor is not None  # caller checked
    return anchor.session_starts(start, end)


def _coverage(
    frame: pl.DataFrame, spec: DatasetSpec, *, stage: str, enforce: bool, role: WindowRole
) -> dict[str, Any]:
    """Coverage of the declared window for the role being built.

    The gate enforces *source* coverage (does the feed describe the whole declared
    window?) before any drop.  Bars removed later by a declared filter or by a
    sub-bar minimum are accounted for in the ledger and reported as output
    coverage; they must never be counted as a feed gap, or an audited drop would
    make an honest feed look incomplete.

    For a session-anchored daily dataset the expected grid is the venue's session
    calendar (Sun–Thu opens), not every UTC day: an FX week has five sessions, and
    counting seven would make an honest feed look 29% incomplete forever.
    """
    start, end = _window_bounds(spec, role)
    anchor = spec.session_anchor
    if anchor is not None:
        expected_stamps = _expected_sessions(spec, start, end)
        expected = len(expected_stamps)
    else:
        step = spec.bar_duration()
        expected = int((end - start) / step)
        expected_stamps = [start + step * index for index in range(expected)]
    if expected <= 0:
        raise QualityGateError("declared window holds no bars")
    expected_set = set(expected_stamps)
    per_asset: dict[str, Any] = {}
    worst = 1.0
    total_observed = 0
    for asset, sub in sorted(
        ((str(part["asset"][0]), part) for part in frame.partition_by("asset", maintain_order=True)),
        key=lambda item: item[0],
    ):
        stamps = sub["timestamp"].sort().to_list()
        available = set(stamps)
        observed = len(available)
        total_observed += observed
        matched = len(available & expected_set)
        missing = expected - matched
        ratio = matched / expected if expected else 0.0
        worst = min(worst, ratio)
        off_calendar = sorted(available - expected_set)
        gap_examples = [str(stamp) for stamp in expected_stamps if stamp not in available][: _EXAMPLES]
        per_asset[asset] = {
            "expected_bars": expected,
            "observed_bars": observed,
            "matched_bars": matched,
            "missing_bars": missing,
            "off_calendar_bars": len(off_calendar),
            "off_calendar_examples": [str(stamp) for stamp in off_calendar[: _EXAMPLES]],
            "coverage_ratio": ratio,
            "missing_examples": gap_examples,
        }
    overall = total_observed / (expected * len(per_asset)) if per_asset else 0.0
    report = {
        "stage": stage,
        "window_role": role,
        "window_start_inclusive": start.isoformat(),
        "window_end_exclusive": end.isoformat(),
        "expected_bars_per_asset": expected,
        "calendar": "session_starts" if anchor is not None else "utc_grid",
        "per_asset": per_asset,
        "coverage_ratio": overall,
        "worst_asset_ratio": worst,
        "min_coverage_ratio": spec.quality_gate.min_coverage_ratio,
        "enforced": enforce,
    }
    if enforce and worst < spec.quality_gate.min_coverage_ratio:
        raise QualityGateError(
            f"coverage {worst:.6f} below declared min_coverage_ratio "
            f"{spec.quality_gate.min_coverage_ratio}: {per_asset}",
            report,
        )
    return report


def run_quality_gate(
    raw: pl.DataFrame, spec: DatasetSpec, *, window_role: WindowRole = "exploratory"
) -> GateOutcome:
    """Run the one mandatory gate; returns a canonical frame and its report.

    ``window_role`` selects *which* declared window the bars must fall inside; every
    check below it — schema, universe, filters, validator, anomaly, resampling,
    invariants, ledger — is the same code for an exploratory build and a sealed
    holdout build.  There is no second gate to drift from this one.
    """
    if window_role not in WINDOW_ROLES:
        raise QualityGateError(f"unknown window role {window_role!r}; expected one of {WINDOW_ROLES}")
    frame, schema = _shape(raw, spec)
    input_rows = frame.height
    frame, universe = _select_universe(frame, spec)
    # Feed completeness is judged on the source grid, before any declared drop.
    coverage = _coverage(frame, spec, stage="source", enforce=True, role=window_role)
    frame, filters = _apply_filters(frame, spec)
    zero_volume = _zero_volume_policy(frame, spec)
    validated_assets = _run_validator(frame, spec, stage="pre-resample")
    extreme = _extreme_returns(frame, spec)
    mad = _mad_anomalies(frame, spec)
    source_after_filters = frame.height
    frame, resampling = _resample(frame, spec)
    _run_validator(frame, spec, stage="post-resample")
    _assert_output_invariants(frame)
    frame = frame.sort(["timestamp", "asset"])
    alignment = _grid_alignment(frame, spec)
    fence = _fence(frame, spec, window_role)
    output_coverage = _coverage(
        frame, spec, stage="output", enforce=False, role=window_role
    )

    dropped_filters = sum(item["dropped"] for item in filters)
    accounted = (
        universe["dropped_rows"]
        + dropped_filters
        + resampling["dropped_source_rows"]
        + resampling["kept_source_rows"]
    )
    unexplained = input_rows - accounted
    if unexplained:
        raise QualityGateError(
            f"row ledger does not reconcile: {unexplained} of {input_rows} input rows are "
            f"unaccounted (universe {universe['dropped_rows']}, filters {dropped_filters}, "
            f"sub-bar {resampling['dropped_source_rows']}, kept {resampling['kept_source_rows']})"
        )

    report: dict[str, Any] = {
        "gate_version": GATE_VERSION,
        "window_role": window_role,
        "spec_sha256": spec.spec_sha256(),
        "rows": {
            "input": input_rows,
            "dropped_unselected_symbols": universe["dropped_rows"],
            "dropped_by_filters": dropped_filters,
            "source_after_filters": source_after_filters,
            "dropped_by_sub_bar_requirement": resampling["dropped_source_rows"],
            "kept_source_rows": resampling["kept_source_rows"],
            "output_bars": frame.height,
            "unexplained": 0,
        },
        "universe": universe,
        "filters": filters,
        "zero_volume": zero_volume,
        "validator": {
            "library": "ml4t.data.validation.OHLCVValidator",
            "preset": ASSET_CLASS_PRESETS[spec.asset_class],
            "assets_checked": validated_assets,
            "stages": ["pre-resample", "post-resample"],
        },
        "anomalies": {
            "manager": "ml4t.data.anomaly.AnomalyManager",
            "mad": mad,
            "max_abs_bar_return": extreme,
        },
        "resampling": resampling,
        "time": {
            **fence,
            **alignment,
            "timezone": spec.time_contract.timezone,
            "bar_timestamp_semantics": spec.time_contract.bar_timestamp_semantics,
            "daily_roll_anchor": spec.time_contract.daily_roll_anchor,
            "raw_timezone_converted_from": schema["converted_from_timezone"],
        },
        "coverage": coverage,
        "output_coverage": output_coverage,
        "volume_semantics": spec.volume_semantics,
        "output_rows_by_asset": _asset_counts(frame),
    }
    if not math.isfinite(float(output_coverage["coverage_ratio"])):  # pragma: no cover
        raise QualityGateError("coverage ratio is not finite", report)
    return GateOutcome(frame=frame, report=report)
