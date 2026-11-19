"""ES daily down-day rule must be closed-bar and prefix invariant."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from warden.strategy_loader import load_module_from_path

mod = load_module_from_path(Path("strategies/candidates/bta_index_down_es_cfd_1d_20261031.py"))


def contract(cid=mod.CONTRACT_ID, delay=1):
    return SimpleNamespace(contract_id=cid, execution_delay_bars=delay)


def frame():
    rows = []
    for i in range(40):
        for sym in ("ES", "NQ"):
            price = 100 + i
            rows.append({
                "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
                "asset": sym, "open": float(price),
                "high": float(price + 2), "low": float(price - 2),
                "close": float(price - 1 if i % 3 == 0 else price + 1),
                "volume": 120.0,
            })
    return pl.DataFrame(rows).sort("timestamp", "asset")


def test_red_bar_signal_and_prefix_invariance():
    x = frame()
    full = mod.generate_positions(x, contract(), ["ES"])["positions"]
    partial = x.filter(pl.col("timestamp") <= datetime(2026, 1, 20, tzinfo=timezone.utc))
    prefix = mod.generate_positions(partial, contract(), ["ES"])["positions"]
    assert np.array_equal(full[:len(prefix)], prefix)
    assert full.shape == (40, 2)
    assert np.array_equal(full[:, 0], (np.arange(40) % 3 == 0).astype(float))
    assert np.count_nonzero(full[:, 1]) == 0


def test_illegal_asset_or_delay():
    with pytest.raises(ValueError):
        mod.generate_positions(frame(), contract(mod.CONTRACT_ID, 0), ["ES"])
    with pytest.raises(ValueError):
        mod.generate_positions(frame(), contract(), ["NQ"])
    with pytest.raises(ValueError, match="Unreviewed"):
        mod.generate_positions(frame(), contract(), ["ES"], lookback=5)
