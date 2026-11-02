"""PIT roster-exit executable-source tests. No new strategy trial."""

import hashlib
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from scripts.audit_pit_monthly_exit_prices import (
    VAL_END,
    classify,
    exact_next_hour_source,
)


def source(tmp_path):
    last = datetime(2024, 7, 31, 23, tzinfo=UTC)
    next_hour = last + timedelta(hours=1)
    rel = "symbol=EOSUSDT/year=2024/month=8/data.parquet"
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    pl.DataFrame({
        "timestamp": [next_hour, next_hour + timedelta(hours=1)],
        "symbol": ["EOSUSDT", "EOSUSDT"],
        "open": [1.1, 1.2],
        "volume": [1200., 1300.],
    }).write_parquet(path)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    return last, path, {rel: {"sha256": expected, "path": rel}}


def audit_events(last):
    return [
        {
            "time_utc": last.isoformat(),
            "symbol": "EOSUSDT",
            "reason": "missing_next_open",
            "position_weight": 0.05,
        },
        {
            "time_utc": (last + timedelta(hours=1)).isoformat(),
            "symbol": "EOSUSDT",
            "reason": "missing_current_open",
            "position_weight": 0.05,
        },
        {
            "time_utc": (VAL_END - timedelta(hours=1)).isoformat(),
            "symbol": "BTCUSDT",
            "reason": "terminal_no_next_exploratory_open",
            "position_weight": -0.05,
        },
    ]


def test_raw_next_hour_is_sha_verified_not_assumed_filled(tmp_path) -> None:
    last, _, manifest = source(tmp_path)
    r = exact_next_hour_source("EOSUSDT", last, manifest, tmp_path)
    assert r["status"] == "SOURCE_OBSERVED_NEXT_OPEN"
    assert r["observed_next_open"] == pytest.approx(1.1)
    assert r["actual_fill_verified"] is False
    assert r["required_exit_hour"].startswith("2024-08-01")


def test_missing_historical_source_is_explicit(tmp_path) -> None:
    last, _, _ = source(tmp_path)
    r = exact_next_hour_source("EOSUSDT", last, {}, tmp_path)
    assert r["status"] == "SOURCE_NOT_IN_PINNED_PRE_CUTOFF_AUDIT"


def test_tampered_raw_market_parquet_refused(tmp_path) -> None:
    last, path, manifest = source(tmp_path)
    path.write_bytes(b"tampered source")
    with pytest.raises(ValueError, match="mutated"):
        exact_next_hour_source("EOSUSDT", last, manifest, tmp_path)


def test_malformed_or_missing_next_bar_not_tradable(tmp_path) -> None:
    last, path, manifest = source(tmp_path)
    pl.DataFrame({
        "timestamp": [last + timedelta(hours=2)],
        "symbol": ["EOSUSDT"],
        "open": [1.2],
        "volume": [1200.],
    }).write_parquet(path)
    manifest[next(iter(manifest))]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    r = exact_next_hour_source("EOSUSDT", last, manifest, tmp_path)
    assert r["status"] == "HISTORICAL_NEXT_OPEN_ABSENT"


def test_protected_boundary_never_scanned(tmp_path) -> None:
    with pytest.raises(ValueError, match="protected"):
        exact_next_hour_source(
            "BTCUSDT", VAL_END - timedelta(hours=1), {}, tmp_path
        )


def test_month_exit_case_requires_matching_old_exposure(tmp_path) -> None:
    last, _, manifest = source(tmp_path)
    report = classify(audit_events(last), manifest, tmp_path)
    assert report["monthly_position_boundary_pairs"] == 1
    assert report["monthly_exit_quote_source"] == {"SOURCE_OBSERVED_NEXT_OPEN": 1}
    assert report["terminal_positions_without_legal_next_price"] == 1
    assert report["terminal_exposure_abs_sum"] == .05


def test_inconsistent_positions_at_roster_change_fail(tmp_path) -> None:
    last, _, manifest = source(tmp_path)
    events = audit_events(last)
    events[1]["position_weight"] = .1
    with pytest.raises(ValueError, match="Inconsistent"):
        classify(events, manifest, tmp_path)


def test_unmatched_exit_pair_refused(tmp_path) -> None:
    last, _, manifest = source(tmp_path)
    events = audit_events(last)
    events.pop(1)
    with pytest.raises(ValueError, match="Missing matched"):
        classify(events, manifest, tmp_path)
