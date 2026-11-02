"""Event-time funding alignment controls (no Warden evaluator modifications)."""

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from scripts.audit_lowvol_event_funding import event_rate_matrix


def test_captures_four_hour_event_ignored_by_eight_hour_mask() -> None:
    start = datetime(2024, 7, 1, 0, tzinfo=UTC)
    hours = [start + timedelta(hours=i) for i in range(10)]
    events=pl.DataFrame({
        "timestamp":[start + timedelta(hours=4, milliseconds=1),
                     start + timedelta(hours=8)],
        "asset":["BTCUSDT","BTCUSDT"],
        "funding_rate":[0.0002,0.0001],
        "funding_interval_hours":[4,8],
    })
    rates,counts,audit=event_rate_matrix(events,hours,["BTCUSDT"])
    assert counts.sum() == 2
    assert np.isclose(rates[4,0],0.0002)
    assert np.isclose(rates[8,0],0.0001)
    assert rates[0,0] == 0
    assert audit["event_intervals_hours"] == {"4":1,"8":1}


def test_simultaneous_funding_events_are_aggregated_not_dropped() -> None:
    start=datetime(2024,7,1,0,tzinfo=UTC)
    events=pl.DataFrame({
        "timestamp":[start,start + timedelta(milliseconds=100)],
        "asset":["ETHUSDT","ETHUSDT"],
        "funding_rate":[0.0001,-0.00005],
        "funding_interval_hours":[8,1],
    })
    rate,count,audit=event_rate_matrix(events,[start],["ETHUSDT"])
    assert count[0,0]==2
    assert np.isclose(rate[0,0], 0.00005)
    assert audit["duplicate_same_asset_hour"]==1


def test_unknown_funding_event_does_not_acquire_future_event() -> None:
    start=datetime(2024,7,1,0,tzinfo=UTC)
    times=[start + timedelta(hours=i) for i in range(3)]
    e=pl.DataFrame({
        "timestamp":[start + timedelta(hours=3)],
        "asset":["BTCUSDT"],
        "funding_rate":[0.001],
        "funding_interval_hours":[4],
    })
    rate,count,audit=event_rate_matrix(e,times,["BTCUSDT"])
    assert np.all(rate == 0)
    assert count.sum() == 0
    assert audit["off_grid_events"] == 1
