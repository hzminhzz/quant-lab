"""Source H018 single-instrument PIT smoke tests; never touch Warden registry."""
from datetime import UTC, datetime, timedelta
from math import sin
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from warden.runner import load_module_from_path

module = load_module_from_path(Path("strategies/candidates/soc_hl_stochk_btc_1h_20261031.py"))
CONTRACT_ID = module.CONTRACT_ID
generate_positions = module.generate_positions
pipeline = module.pipeline


CONTRACT = SimpleNamespace(
    contract_id=CONTRACT_ID,
    frequency="1h",
    execution_mode="NEXT_BAR",
    execution_delay_bars=1,
    label_horizon_bars=24,
)


def _bars(n: int = 120) -> pl.DataFrame:
    t0 = datetime(2024, 7, 1, tzinfo=UTC)
    closes = [100 + 10 * sin(i / 3) for i in range(n)]
    return pl.DataFrame({
        "timestamp": [t0 + timedelta(hours=i) for i in range(n)],
        "asset": ["BTCUSDT"] * n,
        "open": closes,
        "high": [c + 0.3 for c in closes],
        "low": [c - 0.3 for c in closes],
        "close": closes,
        "volume": [10.0] * n,
    })


def test_source_k_is_past_only_and_prefix_positions_identical() -> None:
    all_bars = _bars()
    full = generate_positions(all_bars, CONTRACT, target_symbols=["BTCUSDT"])["positions"]
    part = generate_positions(all_bars.head(65), CONTRACT, target_symbols=["BTCUSDT"])["positions"]
    assert full.shape == (120, 1)
    assert np.all(np.isin(full, (0.0, 0.5)))
    assert np.any(full == 0.5)
    assert np.array_equal(part, full[:65])
    assert pipeline.compute(all_bars, CONTRACT)["stoch_k"].null_count() == 13


def test_wrong_contract_and_silent_universe_expansion_rejected() -> None:
    with pytest.raises(ValueError, match="BTCUSDT only"):
        generate_positions(_bars(), CONTRACT, target_symbols=["ETHUSDT"])
    with pytest.raises(ValueError, match="Non-preregistered"):
        generate_positions(_bars(), CONTRACT, target_symbols=["BTCUSDT"], window=7)
    with pytest.raises(ValueError, match="Wrong governed"):
        pipeline.compute(_bars(), SimpleNamespace(contract_id="OTHER"))
