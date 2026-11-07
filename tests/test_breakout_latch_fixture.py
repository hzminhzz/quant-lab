"""Deterministic regression test for Strategy 21 Donchian breakout post-stop reset latch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from warden.runner import ExperimentRunner, load_module_from_path

s21_mod = load_module_from_path(Path(__file__).resolve().parent / "fixtures" / "untracked_review" / "ts_breakout_20_1h.py")
generate_positions = s21_mod.generate_positions


def test_s21_post_stop_reset_latch():
    r = ExperimentRunner()
    contract = r.resolve_contract("INDICES_1H_V2")

    start = datetime(2023, 1, 1, 0, 0, tzinfo=UTC)
    timestamps = [start + timedelta(hours=i) for i in range(100)]
    prices = []

    # Construct price path:
    # Bar 0-23: flat around 100.0
    # Bar 24: breakout above 100.2
    # Bar 25-35: continues climbing to 112.0
    # Bar 36: adverse dip to 85.0 (triggers trailing ATR stop)
    # Bar 37-49: rallies back to 108.0 (above old channel, but was stopped out)
    # Bar 50-71: pulls back to 95.0 (inside channel, fc == 0, resets latch)
    # Bar 72: fresh breakout to 115.0 -> cleanly re-enters!

    for i in range(100):
        if i < 24:
            close_p = 100.0
            high_p, low_p = 100.2, 99.8
        elif i < 36:
            close_p = 100.0 + (i - 23) * 1.0
            high_p, low_p = close_p + 0.1, close_p - 0.1
        elif i == 36:
            close_p = 85.0
            high_p, low_p = 112.0, 84.0
        elif i < 50:
            close_p = 108.0
            high_p, low_p = 108.1, 107.9
        elif i < 72:
            close_p = 95.0
            high_p, low_p = 95.2, 94.8
        else:
            close_p = 115.0
            high_p, low_p = 115.2, 114.8
        prices.append({
            "timestamp": timestamps[i],
            "asset": "TEST",
            "open": close_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": 1000.0,
            "funding_rate": 0.0,
        })

    df_test = pl.DataFrame(prices)
    res = generate_positions(
        df_test,
        contract,
        channel_window=20,
        vol_window=10,
        rebalance_bars=24,
        stop_multiple=2.0,
    )
    pos = res["positions"][:, 0]

    # Bar 24: breakout rebalance entry
    assert pos[24] > 0.0
    # Bar 30: active long holding
    assert pos[30] > 0.0
    # Bar 36: stopped out by adverse dip
    assert pos[36] == 0.0
    # Bar 48: rebalance bar while price is still above channel -> latch holds position at 0.0
    assert pos[48] == 0.0
    # Bar 60: inside channel
    assert pos[60] == 0.0
    # Bar 72: fresh breakout rebalance entry after channel reset
    assert pos[72] > 0.0


def test_s21_asynchronous_sparse_union_handling():
    r = ExperimentRunner()
    contract = r.resolve_contract("INDICES_1H_V2")

    start = datetime(2023, 1, 1, 0, 0, tzinfo=UTC)
    rows = []
    # 80 bars total:
    # Bar 0-19: flat 100.0
    # Bar 20-33: breakout climbing to 107.0 (rebalances at 24 enter long)
    # Bar 34 (even): adverse dip to 85.0 (triggers 2x ATR stop -> pos drops to 0, latch locks)
    # Bar 35 (odd): Asset A is closed. Latch must NOT be cleared by inactive zero forecast!
    # Bar 36-50: price re-breakout staircase from 109.0 to 116.0 (strictly above upper channel, so active fc > 0)
    for i in range(80):
        ts = start + timedelta(hours=i)
        if i < 20:
            p_a, h_a, l_a = 100.0, 100.2, 99.8
        elif i < 34:
            p_a = 100.0 + (i - 19) * 0.5
            h_a, l_a = p_a + 0.2, p_a - 0.2
        elif i == 34:
            p_a, h_a, l_a = 85.0, 107.5, 84.0
        else:
            # Rising staircase strictly above previous channel
            p_a = 109.0 + (i - 35) * 0.5
            h_a, l_a = p_a + 0.2, p_a - 0.2

        if i % 2 == 0:
            rows.append({"timestamp": ts, "asset": "A", "open": p_a, "high": h_a, "low": l_a, "close": p_a, "volume": 1000.0, "funding_rate": 0.0})
        else:
            rows.append({"timestamp": ts, "asset": "B", "open": 50.0, "high": 50.5, "low": 49.5, "close": 50.0, "volume": 1000.0, "funding_rate": 0.0})

    df_async = pl.DataFrame(rows)
    res = generate_positions(
        df_async,
        contract,
        channel_window=10,
        vol_window=10,
        rebalance_bars=3,  # Cadence 3: bar 24 (even, A active), bar 27 (odd, A closed), bar 30 (even, A active)...
        stop_multiple=2.0,
    )
    pos = res["positions"]
    # 1. Bar 24: Asset A enters breakout position on active bar
    assert pos[24, 0] > 0.0, "Asset A failed to open breakout position at bar 24"

    # 2. Position for Asset A must NOT be liquidated when Asset A is closed (odd hours)
    for t in range(25, 34):
        if t % 2 != 0:  # Asset A closed
            assert pos[t, 0] == pos[t - 1, 0], f"Asset A position altered on inactive bar {t}"
        else:  # Asset B closed
            assert pos[t, 1] == pos[t - 1, 1], f"Asset B position altered on inactive bar {t}"

    # Bar 27 is an odd rebalance bar (27 % 3 == 0) while Asset A is closed:
    # Position must be held unchanged and remain non-zero!
    assert pos[27, 0] == pos[26, 0] > 0.0, "Rebalance on closed bar failed to preserve held non-zero position"

    # 3. Bar 34: Asset A stopped out by adverse dip
    assert pos[34, 0] == 0.0, "Asset A was not stopped out at bar 34"

    # 4. Bar 36 is an active rebalance bar (36 % 3 == 0). Price is 109.5 (strictly above channel).
    # Because latch was NOT reset by inactive bar 35 (or other closed bars), position MUST remain 0.0!
    assert pos[36, 0] == 0.0, "Latch was falsely reset by inactive zero forecast, causing premature re-entry at bar 36"
