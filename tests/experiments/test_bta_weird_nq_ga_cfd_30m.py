"""Source fixed formula, event-time and prefix-causality tests; no live capital."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import pytest

from warden.strategy_loader import load_module_from_path

mod = load_module_from_path(Path("strategies/candidates/bta_weird_nq_ga_cfd_30m.py"))
CONTRACT = mod.CONTRACT_ID
ET = ZoneInfo("America/New_York")


def contract(cid=CONTRACT, delay=1):
    return SimpleNamespace(contract_id=cid, execution_delay_bars=delay)


def sample(start: datetime, n: int = 240, breakout_hour: int | None = 8):
    rows = []
    for i in range(n):
        t = start + timedelta(minutes=30 * i)
        local = t.astimezone(ET)
        breakout = local.weekday() < 5 and local.hour == breakout_hour and local.minute == 0
        for sym in ("ES", "NQ"):
            high = 102.0 if breakout and sym == "NQ" else 100.5
            rows.append({
                "timestamp": t, "asset": sym, "open": 100.0,
                "high": high, "low": 99.5, "close": 100.0,
                "volume": 100.0,
            })
    return pl.DataFrame(rows).sort("timestamp", "asset")


@pytest.mark.parametrize("start,day,offset", [
    (datetime(2026, 10, 25, tzinfo=timezone.utc), "2026-10-26", 4),
    (datetime(2026, 11, 1, tzinfo=timezone.utc), "2026-11-02", 5),
])
def test_eastern_session_dst_and_no_overnight(start, day, offset):
    bars = sample(start, n=200)
    stamps = sorted(bars["timestamp"].unique().to_list())
    actual = mod.generate_positions(bars, contract(), ["NQ"])["positions"]
    assert np.count_nonzero(actual[:, 0]) == 0
    # The 08:00 ET source high observation is only visible AFTER its close.
    mark = datetime.fromisoformat(day+"T08:00:00").replace(tzinfo=ET).astimezone(timezone.utc)
    idx = stamps.index(mark)
    assert actual[idx, 1] == 1.0
    assert actual[idx + 1, 1] == 1.0
    # Warden fills NEXT_BAR, positions are desired not simulated.
    exit_at = datetime.fromisoformat(day+"T17:30:00").replace(tzinfo=ET).astimezone(timezone.utc)
    assert actual[stamps.index(exit_at) - 1, 1] == 1.0
    assert actual[stamps.index(exit_at), 1] == 0.0
    assert all(actual[i, 1] == 0 for i, t in enumerate(stamps)
               if t.astimezone(ET).weekday() >= 5)


def test_same_bar_high_does_not_move_prior_formula():
    b = sample(datetime(2026, 10, 25, tzinfo=timezone.utc))
    fc = mod.pipeline.compute(b, contract())
    mark = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    row = fc.filter((pl.col("timestamp") == mark) & (pl.col("asset") == "NQ"))
    # Previous high=100.5, prior typical=(100.5+99.5+100)/3=100
    # 35 prior opens all100, so stop 100.5, not same-bar touched 102.
    assert row["breakout_level"][0] == pytest.approx(100.5)
    assert row["high"][0] == 102.0


def test_full_and_prefix_signal_equal():
    b = sample(datetime(2026, 10, 25, tzinfo=timezone.utc), n=250)
    cut = datetime(2026, 10, 26, 17, tzinfo=timezone.utc)
    small = b.filter(pl.col("timestamp") <= cut)
    full_positions = mod.generate_positions(b, contract(), ["NQ"])["positions"]
    prefix_positions = mod.generate_positions(small, contract(), ["NQ"])["positions"]
    assert np.array_equal(full_positions[:len(prefix_positions)], prefix_positions)


def test_no_cross_above_no_entry():
    b = sample(datetime(2026, 10, 25, tzinfo=timezone.utc), breakout_hour=None)
    p = mod.generate_positions(b, contract(), ["NQ"])["positions"]
    assert np.count_nonzero(p) == 0


@pytest.mark.parametrize("cid,delay,target", [
    ("INDICES_30M_V1", 1, ["NQ"]),
    (CONTRACT, 0, ["NQ"]),
    (CONTRACT, 1, ["ES"]),
])
def test_contract_and_asset_are_frozen(cid, delay, target):
    b = sample(datetime(2026, 10, 25, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        mod.generate_positions(b, contract(cid, delay), target)


def test_unreviewed_parameters_refused():
    with pytest.raises(ValueError, match="Unreviewed"):
        mod.generate_positions(
            sample(datetime(2026, 10, 25, tzinfo=timezone.utc)),
            contract(), ["NQ"], lookback=20
        )
