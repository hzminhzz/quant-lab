"""Adversarial tests for causal Top-100 crypto cross-sectional pressure screening."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from strategies.candidates.arxiv_price_pressure_xs_1h import (
    FEATURES,
    PricePressurePipeline,
    _forward_label,
    generate_positions,
)
from warden.contracts import load_contract
from warden.leakage import purged_walk_forward_splits, verify_prefix_invariance


def _bars(n: int = 740, n_assets: int = 8) -> pl.DataFrame:
    rng = np.random.default_rng(20261102)
    start = datetime(2023, 1, 1, tzinfo=UTC)
    rows = []
    for a in range(n_assets):
        prices = 100 * np.exp(np.cumsum(rng.normal(0.0, 0.0025, n)))
        for t, value in enumerate(prices):
            rows.append({
                "timestamp": start + timedelta(hours=t),
                "asset": f"X{a:02d}USDT",
                "open": float(value),
                "close": float(value),
                "high": float(value * 1.008),
                "low": float(value * 0.992),
                "volume": float(1300 + a * 40 + (t % 11)),
                "funding_rate": 0.0,
            })
    return pl.DataFrame(rows).sort("timestamp", "asset")


def _contract():
    start = datetime(2023, 1, 1, tzinfo=UTC)
    c = load_contract("contracts/crypto_perps_top100_1h_v1.yaml")
    return replace(
        c,
        train_start=start,
        val_start=start + timedelta(hours=500),
        val_end=start + timedelta(hours=720),
        holdout_start=start + timedelta(hours=720),
        holdout_end=start + timedelta(hours=740),
        n_folds=4,
    )


def test_labels_use_executable_open_after_delay_and_no_future_fill() -> None:
    opens = np.array([[100., 100.], [102., 100.], [104., 100.],
                      [106., 100.], [108., 100.], [110., 100.], [112., 100.]])
    label = _forward_label(opens, delay=1, horizon=4)
    np.testing.assert_allclose(label[0, 0], np.log(110 / 102))
    assert np.isnan(label[-5:, 0]).all()
    opens[3, 0] = np.nan  # one missing intermediate hour must mask label.
    masked = _forward_label(opens, delay=1, horizon=4)
    assert np.isnan(masked[0, 0])


def test_features_require_hourly_contiguity() -> None:
    df = _bars(210)
    broken = df.filter(~((pl.col("asset") == "X00USDT")
                         & (pl.col("timestamp") == datetime(2023, 1, 4, 10, tzinfo=UTC))))
    got = PricePressurePipeline().compute(broken, _contract())
    after_gap = got.filter(
        (pl.col("asset") == "X00USDT")
        & (pl.col("timestamp") == datetime(2023, 1, 4, 11, tzinfo=UTC))
    )
    assert after_gap.height == 1
    assert after_gap["relative_return_1h"][0] is None


def test_prefix_invariance_on_market_style_data() -> None:
    verify_prefix_invariance(PricePressurePipeline(), _bars(), _contract())


def test_purged_model_has_no_prevalidation_positions() -> None:
    df, contract = _bars(n_assets=32), _contract()
    result = generate_positions(
        df, contract, ridge_alpha=100, rebalance_every=4, top_k=2, min_liquidity_usd=10000,
    )
    positions = result["positions"]
    assert positions.shape == (740, 32)
    assert np.all(positions[:500] == 0)
    assert np.all(positions[720:] == 0)
    assert np.isfinite(positions).all()
    assert np.max(np.abs(positions)) <= .25 + 1e-12
    assert len(result["ic_series"]) > 100
    assert set(FEATURES).issubset(PricePressurePipeline().compute(df, contract).columns)


def test_no_train_label_reaches_next_test_fold() -> None:
    contract = _contract()
    folds = purged_walk_forward_splits(
        n_timestamps=720, n_folds=contract.n_folds, min_train_timestamps=500,
        label_horizon=contract.label_horizon_bars,
        embargo=contract.embargo_bars, execution_delay=contract.execution_delay_bars,
    )
    assert len(folds) == 4
    for train, test in folds:
        assert train[-1] + contract.label_horizon_bars + contract.execution_delay_bars < test[0]
