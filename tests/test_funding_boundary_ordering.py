"""Settlement/open fill ordering tests; no protected data or native trial."""

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from scripts.audit_funding_boundary_ordering import reconcile_event_positions


def samples():
    first = datetime(2024, 7, 1, 0, tzinfo=UTC)
    hours = [first + timedelta(hours=i) for i in range(5)]
    # Positions are AFTER the corresponding fill at each open.
    positions = np.array([[1.0], [0.0], [-2.0], [-2.0], [0.0]])
    return first, hours, positions


def events(*rows):
    return pl.DataFrame({
        "timestamp": [x[0] for x in rows],
        "asset": [x[1] for x in rows],
        "funding_rate": [x[2] for x in rows],
        "funding_interval_hours": [x[3] for x in rows],
    })


def test_event_at_open_during_close_pays_prior_holder_if_ordering_pre() -> None:
    first, stamps, held = samples()
    funding = events((first + timedelta(hours=1), "BTCUSDT", .001, 4))
    out = reconcile_event_positions(funding, stamps, ["BTCUSDT"], held)
    assert out["funding_cost_pre_open"][1] == pytest.approx(.001)
    assert out["funding_cost_post_open"][1] == 0
    assert out["funding_cost_best_bound"][1] == 0
    assert out["funding_cost_worst_bound"][1] == pytest.approx(.001)
    assert out["event_statistics"]["ambiguous_events_with_changed_position"] == 1


def test_funding_one_millisecond_after_open_stays_ambiguous() -> None:
    first, stamps, held = samples()
    event = first + timedelta(hours=2, milliseconds=1)
    funding = events((event, "BTCUSDT", .0002, 1))
    out = reconcile_event_positions(funding, stamps, ["BTCUSDT"], held)
    assert out["funding_cost_pre_open"][2] == 0
    assert out["funding_cost_post_open"][2] == pytest.approx(-.0004)
    assert out["event_statistics"]["nonzero_ms_offset_in_boundary_window"] == 1


def test_later_event_uses_filled_position_not_prior() -> None:
    first, stamps, held = samples()
    funding = events((
        first + timedelta(hours=2, seconds=2), "BTCUSDT", .001, 4
    ))
    out = reconcile_event_positions(funding, stamps, ["BTCUSDT"], held)
    assert out["funding_cost_pre_open"][2] == pytest.approx(-.002)
    assert out["funding_cost_post_open"][2] == pytest.approx(-.002)
    assert out["event_statistics"]["events_later_than_open_window"] == 1


def test_first_validation_open_has_zero_prior_position() -> None:
    first, stamps, held = samples()
    funding = events((first, "BTCUSDT", .001, 8))
    out = reconcile_event_positions(funding, stamps, ["BTCUSDT"], held)
    assert out["funding_cost_pre_open"][0] == 0.0
    assert out["funding_cost_post_open"][0] == .001


def test_negative_funding_bounds_are_ordered_by_cost_not_position() -> None:
    first, stamps, held = samples()
    funding = events((first + timedelta(hours=2), "BTCUSDT", -.001, 4))
    out = reconcile_event_positions(funding, stamps, ["BTCUSDT"], held)
    assert out["funding_cost_best_bound"][2] == pytest.approx(-.0)
    assert out["funding_cost_worst_bound"][2] == pytest.approx(.002)


def test_duplicated_hourly_events_are_not_silently_deduplicated() -> None:
    first, stamps, held = samples()
    funding = events(
        (first + timedelta(hours=1), "BTCUSDT", .001, 8),
        (first + timedelta(hours=1, milliseconds=1), "BTCUSDT", -.0005, 4),
    )
    out = reconcile_event_positions(funding, stamps, ["BTCUSDT"], held)
    assert out["observed_event_count"][1, 0] == 2
    assert out["event_statistics"]["duplicate_asset_hour"] == 1
    assert out["funding_cost_pre_open"][1] == pytest.approx(.0005)
    assert out["funding_cost_post_open"][1] == 0


def test_events_beyond_frozen_window_are_ignored_not_used_as_exit() -> None:
    first, stamps, held = samples()
    funding = events(
        (first - timedelta(hours=1), "BTCUSDT", .01, 8),
        (first + timedelta(hours=5), "BTCUSDT", .01, 8),
    )
    out = reconcile_event_positions(funding, stamps, ["BTCUSDT"], held)
    assert out["event_statistics"]["events_before_window"] == 1
    assert out["event_statistics"]["events_after_window"] == 1
    assert out["funding_cost_post_open"].sum() == 0


def test_missing_observation_stays_explicitly_unknown() -> None:
    _, stamps, held = samples()
    e = events((stamps[3], "BTCUSDT", .001, 4))
    out = reconcile_event_positions(e, stamps, ["BTCUSDT"], held)
    assert out["observed_event_count"][2, 0] == 0
    assert out["event_statistics"]["events_in_window"] == 1
    # Output zeros represent only cash from OBSERVED events, not full
    # historical funding. No policy may promote zero to completeness.
    assert out["funding_cost_post_open"][2] == 0


@pytest.mark.parametrize("uncertainty", [-1, 1.5])
def test_invalid_uncertainty_policy_rejected(uncertainty) -> None:
    _, stamps, held = samples()
    with pytest.raises((ValueError, TypeError)):
        reconcile_event_positions(
            events((stamps[1], "BTCUSDT", .001, 8)),
            stamps, ["BTCUSDT"], held, boundary_uncertainty_ms=uncertainty,
        )


def test_offset_hourly_clock_is_rejected() -> None:
    _, stamps, held = samples()
    offset = [t + timedelta(minutes=30) for t in stamps]
    with pytest.raises(ValueError, match="exact UTC hour"):
        reconcile_event_positions(
            events((stamps[0], "BTCUSDT", .001, 8)),
            offset, ["BTCUSDT"], held,
        )


def test_wrong_shape_and_hour_gaps_fail_closed() -> None:
    _, stamps, held = samples()
    empty = events((stamps[0], "BTCUSDT", .001, 8))
    with pytest.raises(ValueError, match="shape mismatch"):
        reconcile_event_positions(empty, stamps, ["BTCUSDT"], held[:2])
    stamps[1] = stamps[1] + timedelta(minutes=1)
    with pytest.raises(ValueError, match="not contiguous"):
        reconcile_event_positions(empty, stamps, ["BTCUSDT"], held)
