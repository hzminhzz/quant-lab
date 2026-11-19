"""Research-only NQ ADX bar-confirmed strategy causal, session and contract tests."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import pytest

from warden.strategy_loader import load_module_from_path

mod = load_module_from_path(Path("strategies/candidates/bta_adx_nq_cfd_30m_20261031.py"))
CHI = ZoneInfo("America/Chicago")


def contract(cid=mod.CONTRACT_ID, delay=1):
    return SimpleNamespace(contract_id=cid, execution_delay_bars=delay)


def bars(n=440, rising=True):
    start = datetime(2026, 10, 25, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        ts = start + timedelta(minutes=30 * i)
        for sym in ("ES", "NQ"):
            mid = (100.0 + i * 0.13) if rising and sym == "NQ" else 100.0
            rows.append({"timestamp": ts, "asset": sym,
                         "open": mid - 0.03, "high": mid + 0.08,
                         "low": mid - 0.09, "close": mid,
                         "volume": 150.0})
    return pl.DataFrame(rows).sort("timestamp", "asset")


def test_full_prefix_identical_and_no_es_allocation():
    b = bars()
    small = b.filter(pl.col("timestamp") < datetime(2026, 10, 29, tzinfo=timezone.utc))
    full = mod.generate_positions(b, contract(), ["NQ"])["positions"]
    prefix = mod.generate_positions(small, contract(), ["NQ"])["positions"]
    assert full.shape == (440, 2)
    assert np.array_equal(full[:len(prefix)], prefix)
    assert np.count_nonzero(full[:, 0]) == 0
    assert np.any(full[:, 1] == 1)
    stamps = sorted(b["timestamp"].unique().to_list())
    assert all(full[i, 1] == 0 for i,t in enumerate(stamps)
               if t.astimezone(CHI).weekday() >= 5 or
               (t.astimezone(CHI).hour * 60 + t.astimezone(CHI).minute) >= 14 * 60 + 30)


def test_closed_bar_must_break_previous_close():
    b = bars(rising=False)
    p = mod.generate_positions(b, contract(), ["NQ"])["positions"]
    assert np.count_nonzero(p) == 0


def test_indicator_prefix_invariant():
    b = bars()
    cut = datetime(2026, 10, 29, tzinfo=timezone.utc)
    prefix = b.filter(pl.col("timestamp") < cut)
    full = mod.pipeline.compute(b, contract())
    part = mod.pipeline.compute(prefix, contract())
    count = len(part)
    assert np.allclose(full["adx14"].to_numpy()[:count], part["adx14"].to_numpy(),
                       equal_nan=True)


@pytest.mark.parametrize("cid,delay,target", [
    ("INDICES_30M_V1", 1, ["NQ"]),
    (mod.CONTRACT_ID, 0, ["NQ"]),
    (mod.CONTRACT_ID, 1, ["ES"]),
])
def test_invalid_invocations_fail(cid, delay, target):
    with pytest.raises(ValueError):
        mod.generate_positions(bars(), contract(cid, delay), target)


def test_rejects_unregistered_parameters():
    with pytest.raises(ValueError, match="Unreviewed"):
        mod.generate_positions(bars(), contract(), ["NQ"], adx_period=10)
