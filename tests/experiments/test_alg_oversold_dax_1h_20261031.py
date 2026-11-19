"""DAX oversold adaptation: complete-bar causality and numerical sanity."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.candidates.alg_oversold_dax_1h_20261031 import (
    _rsi2,
    generate_positions,
    pipeline,
)

CONTRACT = SimpleNamespace(
    contract_id="AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1",
    execution_delay_bars=1,
)


def _sample(n=110):
    start = datetime(2024, 1, 2, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        c = 100.0 + 0.6 * np.sin(i / 8) - 0.02 * i
        o = c + 0.13
        lo = c - (0.005 if i % 5 == 0 else 0.2)
        h = max(o, c) + 0.16
        rows.append(dict(timestamp=start + timedelta(hours=i),
                         asset="DAX", open=o, high=h, low=lo,
                         close=c, volume=100.0))
    return pl.DataFrame(rows)


def test_wilder_rsi_is_prefix_causal():
    prices = np.array([100., 101., 99., 98., 99., 96., 97.])
    full = _rsi2(prices)
    assert np.isnan(full[:2]).all()
    assert np.isfinite(full[2:]).all()
    assert (full[2:] >= 0).all() and (full[2:] <= 100).all()
    for n in range(3, len(prices)):
        assert np.array_equal(full[:n], _rsi2(prices[:n]), equal_nan=True)


def test_pipeline_indicator_warmup_and_finite():
    out = pipeline.compute(_sample(), CONTRACT)
    assert out.height == 110
    assert out["bbw"][48] is None
    assert out["bbw"][49] is not None
    assert out["ibs"].is_between(0, 1).all()
    assert out["rsi2"][1] is None


def test_position_prefix_invariance_and_long_only():
    frame = _sample()
    full = generate_positions(frame, CONTRACT, target_symbols=["DAX"])["positions"]
    assert full.shape == (110, 1)
    assert ((full == 0) | (full == 1)).all()
    for n in (30, 55, 86, 109):
        p = generate_positions(frame.head(n), CONTRACT, target_symbols=["DAX"])["positions"]
        assert np.array_equal(full[:n], p)


def test_no_parameter_optimization_or_unapproved_asset():
    with pytest.raises(ValueError, match="Non-predeclared"):
        generate_positions(_sample(), CONTRACT, target_symbols=["DAX"], threshold=0.07)
    with pytest.raises(ValueError, match="DAX only"):
        generate_positions(_sample(), CONTRACT, target_symbols=["NQ"])
