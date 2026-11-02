"""Deterministic controls for the pre-registered 6h crypto trend adaptation."""

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from strategies.candidates.arxiv_six_hour_crypto_trend import (
    SixHourTrendPipeline,
    _allocation,
    generate_positions,
)
from warden.contracts import load_contract
from warden.leakage import verify_prefix_invariance


def _frame(n: int = 1100) -> pl.DataFrame:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    records = []
    for j, sym in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT")):
        for i in range(n):
            p = 100 * np.exp((0.00003 * (j + 1) * i) + 0.009 * np.sin(i / 11 + j))
            records.append({
                "timestamp": start + timedelta(hours=i),
                "asset": sym, "open": p, "high": p * 1.004,
                "low": p * 0.996, "close": p * 1.0001,
                "volume": 300.0 + j * 40 + i % 10, "funding_rate": 0.0,
            })
    return pl.DataFrame(records).sort("timestamp", "asset")


def test_six_hour_trend_features_are_prefix_invariant() -> None:
    contract = load_contract("contracts/crypto_majors_1h_v1.yaml")
    verify_prefix_invariance(SixHourTrendPipeline(), _frame(), contract)


def test_liquidity_ranking_uses_only_eligible_assets() -> None:
    mom = np.array([0.1, -0.3, 0.2, 0.4])
    vol = np.array([0.02, 0.01, 0.04, 0.08])
    liq = np.array([10.0, 20.0, 40.0, 1000.0])
    available = np.array([True, True, True, False])
    pos = _allocation(mom, vol, liq, available, risk_scaled=False,
                      universe_size=2, long_budget=0.7, short_budget=0.3)
    assert pos[3] == 0
    assert pos[0] == 0
    assert pos[1] == -0.2  # concentration cap applies even to a single short
    assert pos[2] == 0.2  # single long name gets clipped at 20%
    assert np.sum(np.abs(pos)) <= 1.0


def test_volatility_scaling_changes_weights_without_raising_risk_budget() -> None:
    mom = np.array([0.1, 0.2, -0.1, -0.2])
    vol = np.array([0.02, 0.06, 0.04, 0.08])
    liq = np.array([50.0, 60.0, 70.0, 80.0])
    seen = np.ones(4, dtype=bool)
    fixed = _allocation(mom, vol, liq, seen, risk_scaled=False, universe_size=4,
                        long_budget=0.7, short_budget=0.3)
    adaptive = _allocation(mom, vol, liq, seen, risk_scaled=True, universe_size=4,
                           long_budget=0.7, short_budget=0.3)
    assert not np.array_equal(fixed, adaptive)
    assert np.all(np.abs(adaptive) <= 0.2 + 1e-12)
    assert np.sum(adaptive[adaptive > 0]) <= 0.7 + 1e-12
    assert np.abs(np.sum(adaptive[adaptive < 0])) <= 0.3 + 1e-12


def test_positions_are_six_hour_cadence_only() -> None:
    contract = load_contract("contracts/crypto_majors_1h_v1.yaml")
    df = _frame()
    for scaled in (True, False):
        pos = generate_positions(df, contract, risk_scaled=scaled)["positions"]
        assert pos.shape == (1100, 3)
        assert np.all(pos[:720] == 0)
        assert np.all(np.isfinite(pos))
        assert np.all(np.abs(pos) <= 0.2 + 1e-12)
        changed = np.where(np.any(np.abs(np.diff(pos, axis=0)) > 1e-12, axis=1))[0] + 1
        timestamps = sorted(df["timestamp"].unique().to_list())
        assert all(timestamps[i].hour % 6 == 5 for i in changed)
