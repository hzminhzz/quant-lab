"""Causal mechanics checks for the source-inspired Dual Thrust ES research rule."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.candidates.qc_dual_thrust_es_cfd_30m_20261031 import generate_positions

CHICAGO = ZoneInfo("America/Chicago")
CONTRACT = SimpleNamespace(
    contract_id="AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1",
    execution_delay_bars=1,
)


def _bars(dates: list[date], *, incomplete: set[date] | None = None) -> pl.DataFrame:
    rows = []
    for session_date in dates:
        steps = 7 if incomplete and session_date in incomplete else 13
        for i in range(steps):
            local = datetime(
                session_date.year, session_date.month, session_date.day,
                8, 30, tzinfo=CHICAGO,
            ) + timedelta(minutes=30 * i)
            close = 100.75 if i == 1 and session_date == date(2026, 10, 23) else 100.0
            rows.append({
                "timestamp": local.astimezone(ZoneInfo("UTC")),
                "asset": "ES",
                "open": 100.0,
                "high": max(101.0, close),
                "low": 99.0,
                "close": close,
                "volume": 10.0,
            })
    return pl.DataFrame(rows).sort("timestamp")


def test_four_prior_full_sessions_trigger_and_flatten():
    days = [
        date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21),
        date(2026, 10, 22), date(2026, 10, 23),
    ]
    rows = _bars(days)
    result = generate_positions(rows, CONTRACT, target_symbols=["ES"])["positions"]
    assert result.shape == (65, 1)
    assert not result[:52].any()
    assert result[52, 0] == 0
    assert result[53, 0] == 1
    assert result[54, 0] == 1
    assert result[64, 0] == 0


def test_prefix_invariance_even_mid_session():
    days = [
        date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21),
        date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26),
    ]
    full = _bars(days)
    a = generate_positions(full, CONTRACT, target_symbols=["ES"])["positions"]
    for length in (13, 51, 53, 65, 70):
        prefix = full.head(length)
        b = generate_positions(prefix, CONTRACT, target_symbols=["ES"])["positions"]
        assert (a[:length] == b).all()


def test_incomplete_prior_session_is_not_treated_as_complete():
    days = [
        date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21),
        date(2026, 10, 22), date(2026, 10, 23),
    ]
    rows = _bars(days, incomplete={date(2026, 10, 21)})
    positions = generate_positions(rows, CONTRACT, target_symbols=["ES"])["positions"]
    assert not positions.any()


def test_unapproved_parameter_and_contract_rejected():
    rows = _bars([date(2026, 10, 19)])
    import pytest
    with pytest.raises(ValueError, match="Non-predeclared"):
        generate_positions(rows, CONTRACT, target_symbols=["ES"], k1=0.3)
    with pytest.raises(ValueError, match="Only preregistered"):
        generate_positions(rows, CONTRACT, target_symbols=["NQ"])
