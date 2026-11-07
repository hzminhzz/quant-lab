"""Golden lifecycle tapes for reviewed, unintegrated liquidation-reversal source.

Pinned original source: sha256 385618ac9f404774ba15790d493d36eae868386f75b3058aa036786bc17bd6b1.
No production registry, research run or external market data is accessed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.runner import load_module_from_path

MODULE = load_module_from_path(Path(__file__).resolve().parent / "fixtures" /
                               "untracked_review" / "ts_liquidation_reversal_1h.py")


def _panel() -> pl.DataFrame:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    for t in range(60):
        price = 80.0 if 30 <= t <= 35 else 75.0 if 40 <= t <= 45 else 100.0
        volume = 5000.0 if t in (30, 40) else 100.0
        rows.append({"timestamp": start + timedelta(hours=t), "asset": "TEST",
                     "open": price, "high": price + 0.1, "low": price - 0.1,
                     "close": price, "volume": volume, "funding_rate": 0.0})
    return pl.DataFrame(rows)


def test_liquidation_reversal_timeout_and_reentry_are_deterministic(tiny_repo) -> None:
    fixture = tiny_repo()
    panel = _panel()
    kwargs = {"bollinger_window": 5, "bollinger_k": 1.0,
              "volume_lookback": 5, "volume_mult": 2.0, "holding_bars": 3}
    first = MODULE.generate_positions(panel, fixture.contract, **kwargs)["positions"][:, 0]
    second = MODULE.generate_positions(panel, fixture.contract, **kwargs)["positions"][:, 0]
    np.testing.assert_array_equal(first, second)
    assert first[29] == 0.0
    assert first[30] == 1.0
    assert first[31] == 1.0 and first[32] == 1.0
    assert first[33] == 0.0  # expiry at the third held bar
    assert first[40] == 1.0  # independent later volume/cascade event
    assert first[43] == 0.0
    assert np.all(first[46:] == 0.0)


@pytest.mark.xfail(strict=True, reason="Reviewed untracked candidate counts inactive union bars as holding bars; scientific change requires approval")
def test_sparse_union_must_preserve_inactive_position(tiny_repo) -> None:
    fixture = tiny_repo()
    rows = _panel().filter(pl.col("timestamp") != datetime(2025, 1, 1, tzinfo=UTC)
                            + timedelta(hours=31))
    # Retain the union timestamp with another asset while TEST has no bar.
    b = pl.DataFrame([{ "timestamp": datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=31),
                       "asset": "B", "open": 40.0, "high": 40.1, "low": 39.9,
                       "close": 40.0, "volume": 100.0, "funding_rate": 0.0 }])
    rows = pl.concat([rows, b]).sort("timestamp", "asset")
    data = MODULE.generate_positions(rows, fixture.contract, bollinger_window=5, bollinger_k=1.0,
                                     volume_lookback=5, volume_mult=2.0, holding_bars=3)["positions"]
    # The reviewed source erroneously exits on TEST's missing bar; this guard
    # is intentionally xfailed and must NOT be reported as T17 acceptance.
    assert data[30, 1] > 0.0
    assert data[31, 1] == data[30, 1]
