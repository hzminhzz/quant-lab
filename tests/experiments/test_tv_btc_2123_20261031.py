"""Synthetic-only, no Warden registry or holdout access."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from runpy import run_path

import polars as pl
import pytest

from warden.contract_source import load_recorded_contract

CONTRACT = load_recorded_contract(Path(__file__).resolve().parents[2], "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1")
generate_positions = run_path("strategies/candidates/tv_btc_2123_20261031.py")["generate_positions"]


def _panel():
    start = datetime(2024, 7, 1, 18, tzinfo=UTC)
    times = [start + timedelta(hours=i) for i in range(9)]
    return pl.DataFrame({
        "timestamp": times, "asset": ["BTCUSDT"] * 9,
        "open": [100.] * 9, "high": [101.] * 9,
        "low": [99.] * 9, "close": [100.] * 9,
        "volume": [1.] * 9,
    })


def test_next_bar_signal_clock():
    got = generate_positions(_panel(), CONTRACT, target_symbols=["BTCUSDT"])["positions"]
    assert got.shape == (9, 1)
    assert got[:, 0].tolist() == [0., 0., 1., 1., 0., 0., 0., 0., 0.]


def test_fails_closed_on_wrong_symbol():
    with pytest.raises(ValueError, match="BTCUSDT-only"):
        generate_positions(_panel(), CONTRACT, target_symbols=["ETHUSDT"])


def test_fails_closed_on_gap():
    f = _panel().filter(pl.col("timestamp") != datetime(2024, 7, 1, 19, tzinfo=UTC))
    with pytest.raises(ValueError, match="continuous"):
        generate_positions(f, CONTRACT, target_symbols=["BTCUSDT"])
