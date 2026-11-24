"""Causal, DST-aware tests for BTA Monday NQ CFD delayed adaptation.

No simulated trading returns are selected in these tests, and no publisher
code or protected market data is accessed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import pytest

from warden.strategy_loader import load_module_from_path

_mod = load_module_from_path(Path("strategies/candidates/bta_monday_nq_bollinger_cfd_1h.py"))
CONTRACT_ID = _mod.CONTRACT_ID
generate_positions = _mod.generate_positions
pipeline = _mod.pipeline

CHICAGO = ZoneInfo("America/Chicago")


def _contract(contract_id=CONTRACT_ID, delay=1):
    return SimpleNamespace(contract_id=contract_id, execution_delay_bars=delay)


def _bars(first: datetime, n: int = 150, *, cross: bool = True):
    records = []
    for i in range(n):
        ts = first + timedelta(hours=i)
        loc = ts.astimezone(CHICAGO)
        breakout = cross and loc.weekday() == 0 and loc.hour == 0
        for name in ("NQ", "ES"):
            records.append({
                "timestamp": ts, "asset": name, "open": 100.0,
                "high": 103.0 if name == "NQ" and breakout else 101.0,
                "low": 99.0, "close": 100.0, "volume": 100.0,
                "funding_rate": 0.0,
            })
    return pl.DataFrame(records).sort("timestamp", "asset")


@pytest.mark.parametrize("first,monday_zero_utc,monday_flat_utc", [
    (
        datetime(2026, 10, 24, 0, tzinfo=UTC),
        datetime(2026, 10, 26, 5, tzinfo=UTC),
        datetime(2026, 10, 27, 3, tzinfo=UTC),
    ),
    (
        datetime(2026, 10, 31, 0, tzinfo=UTC),
        datetime(2026, 11, 2, 6, tzinfo=UTC),
        datetime(2026, 11, 3, 4, tzinfo=UTC),
    ),
])
def test_monday_signal_and_23_chicago_exit_across_dst(
    first, monday_zero_utc, monday_flat_utc,
):
    data = _bars(first)
    timestamps = sorted(data["timestamp"].unique().to_list())
    values = generate_positions(data, _contract(), ["NQ"])["positions"]
    assert values.shape == (len(timestamps), 2)
    # Sorted assets: ES index0, NQ index1.
    assert np.all(values[:, 0] == 0)
    assert values[timestamps.index(monday_zero_utc), 1] == 1
    assert values[timestamps.index(monday_flat_utc) - 1, 1] == 1
    # Desired flat at Monday 22:00 Chicago: Warden fills at 23:00.
    assert values[timestamps.index(monday_flat_utc), 1] == 0
    assert all(
        values[i, 1] == 0
        for i, ts in enumerate(timestamps) if ts.astimezone(CHICAGO).weekday() != 0
    )


def test_previous_completed_bars_and_future_prefix_invariance():
    data = _bars(datetime(2026, 10, 24, tzinfo=UTC))
    features = pipeline.compute(data, _contract())
    first_mon = datetime(2026, 10, 26, 5, tzinfo=UTC)
    check = features.filter(
        (pl.col("timestamp") == first_mon) & (pl.col("asset") == "NQ")
    ).row(0, named=True)
    # The spike on the current bar CANNOT update the already-established ATR.
    assert check["atr_previous"] == pytest.approx(2.0)
    assert check["threshold_previous"] == pytest.approx(101.6)

    cut = datetime(2026, 10, 26, 14, tzinfo=UTC)
    prefix = data.filter(pl.col("timestamp") <= cut)
    full_positions = generate_positions(data, _contract(), ["NQ"])["positions"]
    early_positions = generate_positions(prefix, _contract(), ["NQ"])["positions"]
    assert np.array_equal(full_positions[:len(early_positions)], early_positions)


def test_falsification_no_trigger_no_trade():
    data = _bars(datetime(2026, 10, 24, tzinfo=UTC), cross=False)
    got = generate_positions(data, _contract(), ["NQ"])["positions"]
    assert np.count_nonzero(got) == 0


def test_mismatched_execution_contract_or_source_universe_fail_closed():
    data = _bars(datetime(2026, 10, 24, tzinfo=UTC))
    with pytest.raises(ValueError, match="Contract mismatch"):
        pipeline.compute(data, _contract(contract_id="INDICES_1H_V2"))
    with pytest.raises(ValueError, match="Source-specific"):
        generate_positions(data, _contract(), ["ES"])
    with pytest.raises(ValueError, match="next-hour"):
        generate_positions(data, _contract(delay=0), ["NQ"])
    with pytest.raises(ValueError, match="Unreviewed"):
        generate_positions(data, _contract(), ["NQ"], multiplier=1.2)
