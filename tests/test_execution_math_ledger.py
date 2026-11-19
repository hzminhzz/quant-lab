"""Hand-computed fill, funding and turnover economics used by both Warden stages."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from warden.runner import _funding_settlement_mask, execution_returns, simulate_positions


@pytest.mark.parametrize("mode", ["NEXT_BAR", "QUOTE_SIDE"])
def test_exact_hand_computed_long_short_funding_and_latency(tiny_repo, mode: str) -> None:
    f = tiny_repo()
    contract = replace(f.contract, execution_mode=mode, execution_delay_bars=1)
    start = datetime(2025, 1, 1, 7, tzinfo=UTC)
    opens = [100.0, 110.0, 121.0, 133.1, 146.41]
    rows = [
        {"timestamp": start + timedelta(hours=t), "asset": "A", "open": o,
         "high": o + 0.5, "low": o - 0.5, "close": o + 0.2,
         "bid": o - 0.5, "ask": o + 0.5, "volume": 100.0, "funding_rate": 0.002}
        for t, o in enumerate(opens)
    ]
    panel = pl.DataFrame(rows)
    raw_positions = np.array([[1.0], [1.0], [-1.0], [-1.0], [0.0]])
    returns = execution_returns(panel, contract, ["A"])
    mask = _funding_settlement_mask([x["timestamp"] for x in rows], contract)
    sim = simulate_positions(raw_positions, returns["long"],
                             np.full((5, 1), 0.002), contract, slice(0, 5),
                             funding_mask=mask, quote_returns=returns)
    assert sim["positions"][:, 0].tolist() == [0.0, 1.0, 1.0, -1.0, -1.0]
    assert sim["turnover"].tolist() == [0.0, 1.0, 0.0, 2.0, 0.0]
    assert mask.tolist() == [False, True, False, False, False]
    if mode == "NEXT_BAR":
        gross = np.array([0.0, 121 / 110 - 1, 133.1 / 121 - 1,
                          -(146.41 / 133.1 - 1), 0.0])
    else:
        gross = np.array([0.0, 120.5 / 110.5 - 1, 132.6 / 121.5 - 1,
                          -(146.91 / 132.6 - 1), 0.0])
    fee = contract.total_one_way_cost_decimal()
    expected = gross - np.array([0.0, fee, 0.0, 2 * fee, 0.0]) - np.array([0.0, 0.002, 0, 0, 0])
    np.testing.assert_allclose(sim["gross_returns"], gross, atol=1e-12)
    np.testing.assert_allclose(sim["net_returns"], expected, atol=1e-12)
    # A validation-only slice must not grant artificial funding rebates.
    assert sim["net_returns"][1] == pytest.approx(gross[1] - fee - 0.002)
