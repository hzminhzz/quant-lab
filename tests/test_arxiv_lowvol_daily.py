"""Controls for the one allowed daily-rebalance cost-recovery revision."""

import numpy as np

from strategies.candidates.arxiv_lowvol_xs_1h import generate_positions as source_positions
from strategies.candidates.arxiv_lowvol_xs_24h import (
    _daily_positions,
    generate_positions,
)
from tests.test_arxiv_price_pressure_xs import _bars, _contract


def test_daily_positions_only_change_on_frozen_cadence() -> None:
    src = np.zeros((36, 2))
    for t in range(4, 32):
        src[t] = (0.25, -0.25) if t % 3 else (-0.25, 0.25)
    opens = np.ones_like(src)
    out = _daily_positions(src, opens, start=4, end=34, interval=24)
    np.testing.assert_equal(out[:4], 0)
    np.testing.assert_equal(out[4:28], np.tile(src[4], (24, 1)))
    np.testing.assert_equal(out[28:34], np.tile(src[28], (6, 1)))
    np.testing.assert_equal(out[34:], 0)


def test_missing_asset_cannot_reenter_without_new_rebalance() -> None:
    src = np.ones((30, 2)) * 0.1
    opens = np.ones_like(src)
    opens[9, 0] = np.nan
    out = _daily_positions(src, opens, start=0, end=30, interval=24)
    assert out[8, 0] > 0
    assert out[9, 0] == 0
    assert np.all(out[10:24, 0] == 0)
    assert out[24, 0] > 0


def test_daily_revision_preserves_original_oos_predictive_signal() -> None:
    bars, contract = _bars(n_assets=32), _contract()
    params = {"ridge_alpha": 100, "top_k": 2, "min_liquidity_usd": 10000}
    base = source_positions(bars, contract, rebalance_every=4, **params)
    daily = generate_positions(bars, contract, rebalance_every=24, **params)
    np.testing.assert_array_equal(base["ic_series"], daily["ic_series"])
    assert len(daily["ic_series"]) > 100
    assert np.all(daily["positions"][:500] == 0)
    assert np.all(daily["positions"][720:] == 0)
    changes = np.flatnonzero(
        np.any(np.abs(np.diff(daily["positions"], axis=0)) > 1e-10, axis=1)
    ) + 1
    assert all((t - 500) % 24 == 0 or t == 720 for t in changes)
    # t=720 is the validation boundary, where research positions stop.
