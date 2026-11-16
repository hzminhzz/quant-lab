"""Calendar causality tests; no market returns or protected data are read."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from warden.runner import load_module_from_path

_mod = load_module_from_path(
    Path("strategies/candidates/quantconnect_turn_month_es_cfd_1d.py")
)
CONTRACT_ID = _mod.CONTRACT_ID
generate_positions = _mod.generate_positions
_calendar_windows = _mod._calendar_windows


def contract(contract_id=CONTRACT_ID, delay=1, horizon=5):
    return SimpleNamespace(
        contract_id=contract_id, execution_delay_bars=delay,
        label_horizon_bars=horizon,
    )


def data(first=datetime(2024, 9, 21, tzinfo=timezone.utc),
         n=100, bad_oct_es=False):
    rows = []
    for offset in range(n):
        stamp = first + timedelta(days=offset)
        if stamp.weekday() == 5:  # Saturday closed; low-volume Sunday is retained.
            continue
        for name in ("ES", "NQ"):
            volume = 0 if (bad_oct_es and name == "ES"
                           and stamp.date().isoformat() == "2024-10-31") else 100
            rows.append({
                "timestamp": stamp, "asset": name,
                "open": 100, "high": 102, "low": 98,
                "close": 100, "volume": volume,
            })
    return pl.DataFrame(rows).sort("timestamp", "asset")


def test_spy_nyse_calendar_monthend_and_third_trading_day_on_es_cfd():
    bars = data()
    stamps = sorted(bars["timestamp"].unique().to_list())
    output = generate_positions(bars, contract(), ["ES"])["positions"]
    assert output.shape == (len(stamps), 2)
    # Sorted assets ES=0, NQ=1; only ES should trade.
    assert np.count_nonzero(output[:, 1]) == 0
    idx = lambda date_str: stamps.index(datetime.fromisoformat(date_str).replace(
        tzinfo=timezone.utc))
    assert output[idx("2024-10-30"), 0] == 0
    assert output[idx("2024-10-31"), 0] == 1
    # Sunday November 3 is an ES CFD day, but NOT an NYSE trading session.
    assert output[idx("2024-11-03"), 0] == 1
    assert output[idx("2024-11-04"), 0] == 1
    assert output[idx("2024-11-05"), 0] == 0
    assert output[idx("2024-11-06"), 0] == 0
    # Warden NEXT_BAR, not this module, chooses actual cash execution.


def test_prefix_invariance_even_before_future_calendar_exit():
    bars = data()
    full = generate_positions(bars, contract(), ["ES"])["positions"]
    cutoff = datetime(2024, 11, 3, tzinfo=timezone.utc)
    prefix = bars.filter(pl.col("timestamp") <= cutoff)
    truncated = generate_positions(prefix, contract(), ["ES"])["positions"]
    assert np.array_equal(full[:len(truncated)], truncated)


def test_known_calendar_windows_and_no_pending_july2025_order():
    starts, ends = _calendar_windows(
        datetime(2025, 5, 1).date(), datetime(2025, 6, 30).date(),
    )
    assert datetime(2025, 5, 30).date() in starts
    assert datetime(2025, 6, 4).date() in ends
    assert datetime(2025, 6, 30).date() not in starts


def test_no_trade_when_source_es_bar_invalid():
    bars = data(bad_oct_es=True)
    stamps = sorted(bars["timestamp"].unique().to_list())
    output = generate_positions(bars, contract(), ["ES"])["positions"]
    october_end = stamps.index(datetime(2024, 10, 31, tzinfo=timezone.utc))
    assert output[october_end, 0] == 0


@pytest.mark.parametrize("cid,lag,horizon", [
    ("INDICES_1H_V2", 1, 5),
    (CONTRACT_ID, 0, 5),
    (CONTRACT_ID, 1, 3),
])
def test_wrong_warden_contract_safely_refused(cid, lag, horizon):
    with pytest.raises(ValueError):
        generate_positions(data(), contract(cid, lag, horizon), ["ES"])


def test_unreviewed_assets_and_parameters_fail_closed():
    with pytest.raises(ValueError, match="No ETF"):
        generate_positions(data(), contract(), ["NQ"])
    with pytest.raises(ValueError, match="Unreviewed"):
        generate_positions(data(), contract(), ["ES"], holding_days=7)
