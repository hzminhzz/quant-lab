"""Synthetic-only regression for frozen 2022-Wen-inspired downside volume shock.

Do not read or mutate the live Warden registry, market validation, or holdout.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.contract_source import load_recorded_contract
from warden.strategy_loader import load_module_from_path

ROOT = Path(__file__).resolve().parents[2]
MODULE = load_module_from_path(
    ROOT / "strategies/candidates/soc_wbx_downshock_1h_20261031.py"
)
CONTRACT = load_recorded_contract(
    ROOT, "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1"
)


def test_exact_four_hour_lockout_and_fixed_exposure():
    fixture = _make_panel(110)
    result = MODULE.generate_positions(
        fixture, CONTRACT, target_symbols=list(MODULE.ASSETS)
    )
    weights = result["positions"]
    assert weights.shape == (110, 4)
    assert weights.dtype == np.float64
    assert np.all(weights >= 0)
    assert np.all(np.sum(weights, axis=1) <= 1.0)
    # First genuine event on 85th completed bar.
    for j in range(4):
        assert weights[84, j] == 0
        assert np.array_equal(weights[85:89, j], np.full(4, 0.25))
        assert weights[89, j] == 0
        assert weights[90, j] == 0


def test_prefix_invariant_on_truncated_synthetic_history():
    full_panel = _make_panel(110)
    full = MODULE.generate_positions(
        full_panel, CONTRACT, target_symbols=list(MODULE.ASSETS)
    )["positions"]
    cutoff = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=95)
    head_panel = full_panel.filter(pl.col("timestamp") <= cutoff)
    head = MODULE.generate_positions(
        head_panel, CONTRACT, target_symbols=list(MODULE.ASSETS)
    )["positions"]
    assert np.array_equal(head, full[: len(head)])


def test_fail_closed_on_unapproved_universe_or_wrong_contract_or_gaps():
    panel = _make_panel(110)
    with pytest.raises(ValueError, match="Frozen four-name"):
        MODULE.generate_positions(panel, CONTRACT, target_symbols=["BTCUSDT"])
    with pytest.raises(ValueError, match="Unregistered"):
        MODULE.generate_positions(
            panel, CONTRACT, target_symbols=list(MODULE.ASSETS), tuned_stop=1
        )
    gap_time = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=20)
    incomplete = panel.filter(pl.col("timestamp") != gap_time)
    with pytest.raises(ValueError, match="Missing hourly timestamps"):
        MODULE.generate_positions(
            incomplete, CONTRACT, target_symbols=list(MODULE.ASSETS)
        )


def _make_panel(n: int) -> pl.DataFrame:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    rows: list[dict] = []
    for asset in MODULE.ASSETS:
        prices = 100.0 * np.exp(np.cumsum(
            0.0005 * np.sin(np.arange(n, dtype=float) / 4.0)
        ))
        # On bar 85 all four assets have a broad sell-pressure event.
        prices[85:] *= 0.96
        for i in range(n):
            close = float(prices[i])
            shock = i == 85
            rows.append({
                "timestamp": start + timedelta(hours=i),
                "asset": asset,
                "open": close * 1.0001,
                "high": close * (1.020 if shock else 1.005),
                "low": close * 0.998 if shock else close * 0.995,
                "close": close,
                "volume": 250.0 if shock else 100.0,
                "funding_rate": 0.0,
            })
    return pl.DataFrame(rows)
