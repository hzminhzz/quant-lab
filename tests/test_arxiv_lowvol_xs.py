"""Causal low-vol factor adaptation and fold-local Ridge controls."""

import numpy as np
import polars as pl

from strategies.candidates.arxiv_lowvol_xs_1h import LowVolatilityPipeline, generate_positions
from tests.test_arxiv_price_pressure_xs import _bars, _contract
from warden.leakage import verify_prefix_invariance


def test_lowvol_pipeline_is_prefix_invariant() -> None:
    verify_prefix_invariance(LowVolatilityPipeline(), _bars(), _contract())


def test_hourly_gap_is_not_a_one_hour_volatility_sample() -> None:
    bars = _bars(n=740)
    truncated = bars.filter(
        ~((pl.col("asset") == "X00USDT")
          & (pl.col("timestamp") == bars["timestamp"][50]))
    )
    features = LowVolatilityPipeline().compute(truncated, _contract())
    feature = features.filter((pl.col("asset") == "X00USDT")
                              & (pl.col("timestamp") == bars["timestamp"][58]))
    # Missing one intermediate hour contaminates the 24h rolling window.
    assert feature["realized_volatility_24h"][0] is None


def test_lowvol_ridge_uses_purged_oos_only() -> None:
    bars, contract = _bars(n_assets=32), _contract()
    output = generate_positions(
        bars, contract,
        ridge_alpha=100.0, rebalance_every=4, top_k=2,
        min_liquidity_usd=10000.0,
    )
    p = output["positions"]
    assert p.shape == (740, 32)
    assert np.all(p[:500] == 0)
    assert np.all(p[720:] == 0)
    assert np.all(np.isfinite(p))
    assert np.max(np.abs(p)) <= 0.25 + 1e-12
    assert len(output["ic_series"]) > 100
    assert len(output["oos_fold_ridge_coefficients"]) == 4
