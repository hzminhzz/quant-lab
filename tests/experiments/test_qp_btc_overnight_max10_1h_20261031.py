"""Synthetic timestamp and causal invariance for Bitcoin MAX10 overnight."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import pytest

from warden.runner import load_module_from_path

mod = load_module_from_path(Path("strategies/candidates/qp_btc_overnight_max10_1h_20261031.py"))
NY = ZoneInfo("America/New_York")


def contract():
    return SimpleNamespace(
        contract_id=mod.CONTRACT_ID, execution_delay_bars=1,
        label_horizon_bars=24, execution_mode="NEXT_BAR"
    )


def synthetic():
    # Span ten regular NYSE sessions and DST, including unobserved holidays.
    start = datetime(2024, 3, 1, tzinfo=UTC)
    rows = []
    for i in range(24 * 25):
        t = start + timedelta(hours=i)
        rows.append({
            "timestamp": t, "asset": "BTCUSDT",
            "open": 100.0, "high": 102.0, "low": 99.0,
            "close": 100.0 + i * 0.01, "volume": 100.0
        })
    return pl.DataFrame(rows)


def test_causal_prefix_and_dst():
    frame = synthetic()
    full = mod.generate_positions(frame, contract(), ["BTCUSDT"])["positions"]
    cut = datetime(2024, 3, 21, tzinfo=UTC)
    subset = frame.filter(pl.col("timestamp") <= cut)
    prefix = mod.generate_positions(subset, contract(), ["BTCUSDT"])["positions"]
    assert np.array_equal(prefix, full[:len(prefix)])
    assert full.shape == (600, 1)
    assert np.all(np.isin(np.unique(full), [0.0, 1.0]))
    # Monday/Tuesday after enough sessions show a signal on NYSE 15:00
    active_15 = [
        t for t, flag in zip(frame["timestamp"].to_list(), full[:, 0])
        if flag > 0 and t.astimezone(NY).hour == 15
    ]
    assert active_15
    assert all(t.astimezone(NY).weekday() in (0, 1) for t in active_15)


def test_xnys_early_close_and_friday_exclusions():
    from datetime import date

    sessions = mod._regular_nyse_sessions(date(2024, 11, 25), date(2024, 12, 4))
    assert date(2024, 11, 28) not in sessions  # Thanksgiving holiday
    assert date(2024, 11, 29) not in sessions  # 13:00 NY early close
    assert date(2024, 12, 2) in sessions

    frame = synthetic()
    positions = mod.generate_positions(frame, contract(), ["BTCUSDT"])["positions"]
    for t, flag in zip(frame["timestamp"].to_list(), positions[:, 0]):
        local = t.astimezone(NY)
        if local.weekday() == 4 and local.hour == 15:  # Friday NEVER enters
            assert flag == 0.0
        if local.weekday() in (1, 2) and local.hour == 9:  # Exit at 10 NY
            assert flag == 0.0


def test_wrong_contract_and_parameters_fail_closed():
    data = synthetic()
    with pytest.raises(ValueError):
        mod.generate_positions(data, contract(), ["ETHUSDT"])
    with pytest.raises(ValueError, match="Non-preregistered"):
        mod.generate_positions(data, contract(), ["BTCUSDT"], lookback=5)
    with pytest.raises(ValueError):
        mod.generate_positions(data, SimpleNamespace(
            contract_id=mod.CONTRACT_ID, execution_delay_bars=0,
            label_horizon_bars=24, execution_mode="NEXT_BAR"), ["BTCUSDT"])
