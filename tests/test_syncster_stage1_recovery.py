"""Causal, boundary and gap tests for pinned Syncster CME research candidates.

Fixtures are synthetic, NEVER a market-performance claim. Real Warden
screens and their append-only evidence run separately in the worktree.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import pytest

from strategies.candidates import syncster_es_down_day_15m as es
from strategies.candidates import syncster_nq_orb_15m as nq
from warden.contracts import load_contract
from warden.leakage import verify_prefix_invariance

NY = ZoneInfo("America/New_York")


def contract(symbol: str):
    return load_contract(f"contracts/cme_{symbol.lower()}_15m_syncster_v1.yaml")


def fixtures(symbol: str, *, days: int = 3, bad_day1_last: bool = False,
             bad_nq_opening: bool = False) -> pl.DataFrame:
    rows = []
    for day in range(1, days + 1):
        start = datetime(2024, 7, day, 9, 30, tzinfo=NY)
        for slot in range(26):
            stamp = (start + timedelta(minutes=15 * slot)).astimezone(UTC)
            if symbol == "ES":
                op = 100.0 - 0.10 * slot if day == 1 else 100.0 + 0.03 * slot
                cl = op - 0.05 if day == 1 else op + 0.02
                high, low = max(op, cl) + 0.10, min(op, cl) - 0.10
            else:
                op = 100.0 if slot < 4 else 102.0
                cl = 100.0 if slot < 4 else 102.0
                high, low = (101.0, 99.0) if slot < 4 else (103.0, 101.0)
            observed = 10 if (day == 1 and slot == 25 and bad_day1_last) else 15
            if bad_nq_opening and day == 1 and slot == 2:
                observed = 10
            rows.append({
                "timestamp": stamp, "asset": symbol, "open": op, "high": high,
                "low": low, "close": cl, "volume": 100.0,
                "observed_minutes": observed,
            })
    return pl.DataFrame(rows)


def test_es_next_session_down_day_signal_and_flat_outside_window() -> None:
    data = fixtures("ES", days=3)
    target = es.generate_positions(data, contract("ES"), target_symbols=["ES"])["positions"][:, 0]
    assert np.all(target[:26] == 0)
    assert np.all(target[26:34] == 1)
    assert np.all(target[34:] == 0)
    assert int(target.sum()) == 8


def test_es_rejects_incomplete_prior_rth_session() -> None:
    data = fixtures("ES", days=2, bad_day1_last=True)
    target = es.generate_positions(data, contract("ES"), target_symbols=["ES"])["positions"][:, 0]
    assert not target.any()


def test_nq_opening_range_break_and_exact_eight_decision_bars() -> None:
    data = fixtures("NQ", days=2)
    target = nq.generate_positions(data, contract("NQ"), target_symbols=["NQ"])["positions"][:, 0]
    assert np.all(target[:4] == 0)
    assert np.all(target[4:12] == 1)
    assert np.all(target[12:26] == 0)
    assert int(target[:26].sum()) == 8


def test_nq_rejects_incomplete_opening_range() -> None:
    data = fixtures("NQ", days=1, bad_nq_opening=True)
    target = nq.generate_positions(data, contract("NQ"), target_symbols=["NQ"])["positions"][:, 0]
    assert not target.any()


@pytest.mark.parametrize("symbol,pipeline", [("ES", es.pipeline), ("NQ", nq.pipeline)])
def test_causal_prefix_and_future_shock(symbol: str, pipeline: object) -> None:
    # Warden's generic leak test assumes perpetual funding_rate even on
    # futures OHLCV. Add a test-only inert column, not to frozen real data.
    frame = fixtures(symbol, days=3).with_columns(pl.lit(0.0).alias("funding_rate"))
    verify_prefix_invariance(pipeline, frame, contract(symbol))


@pytest.mark.parametrize("symbol", ["ES", "NQ"])
def test_frozen_contract_and_unreviewed_parameters_rejected(symbol: str) -> None:
    frame = fixtures(symbol, days=2)
    fn = es.generate_positions if symbol == "ES" else nq.generate_positions
    with pytest.raises(ValueError, match="Unapproved"):
        fn(frame, contract(symbol), target_symbols=[symbol], lookup=20)
    with pytest.raises(ValueError, match="Unapproved"):
        fn(frame, contract(symbol), target_symbols=["WRONG"])
