"""Fold-local ML training and rebalance continuity, using synthetic OOS tapes."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from warden.contracts import load_contract
from warden.leakage import purged_walk_forward_splits
from warden.runner import load_module_from_path

MODULE = load_module_from_path(Path("strategies/baselines/cs_ridge_1h.py"))


def _panel() -> pl.DataFrame:
    rng = np.random.default_rng(37)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    for asset in range(8):
        price = 60.0 + asset * 10
        for bar in range(420):
            open_price = price
            close_price = open_price * (1 + float(rng.normal(0, 0.0015)))
            rows.append({"timestamp": start + timedelta(hours=bar), "asset": f"A{asset}",
                         "open": open_price, "high": max(open_price, close_price) * 1.001,
                         "low": min(open_price, close_price) * 0.999, "close": close_price,
                         "volume": 100.0, "funding_rate": 0.0001 + asset * 0.00001})
            price = close_price
    return pl.DataFrame(rows).sort("timestamp", "asset")


def test_ml_fold_purge_and_carry_forward_invariant_under_future_shocks(monkeypatch) -> None:
    panel = _panel()
    start = datetime(2025, 1, 1, tzinfo=UTC)
    contract = replace(load_contract(Path("contracts/crypto_perps_1h_v1.yaml")),
                       train_start=start - timedelta(hours=1),
                       val_start=start + timedelta(hours=120),
                       val_end=start + timedelta(hours=380),
                       holdout_start=start + timedelta(hours=381),
                       holdout_end=start + timedelta(hours=440),
                       n_folds=3, label_horizon_bars=4, execution_delay_bars=1, embargo_bars=2)
    actual_splits = purged_walk_forward_splits(380, 3, 120, 4, 2, execution_delay=1)
    for train, val in actual_splits:
        assert train.max() + 1 + 4 + 2 < val.min()
    fit_rows = []
    original = MODULE.Ridge

    class ObservedRidge(original):
        def fit(self, x, y, **kwargs):
            fit_rows.append((len(x), len(y)))
            return super().fit(x, y, **kwargs)

    monkeypatch.setattr(MODULE, "Ridge", ObservedRidge)
    first = MODULE.generate_positions(panel, contract, rebalance_every=8)
    # Validation-censored decision tape must ignore modified inaccessible future.
    shocked = panel.with_columns(
        pl.when(pl.col("timestamp") >= start + timedelta(hours=381))
        .then(pl.col("open") * 2.0).otherwise(pl.col("open")).alias("open"),
        pl.when(pl.col("timestamp") >= start + timedelta(hours=381))
        .then(pl.col("close") * 0.4).otherwise(pl.col("close")).alias("close"),
    )
    second = MODULE.generate_positions(shocked, contract, rebalance_every=8)
    np.testing.assert_array_equal(first["positions"][:380], second["positions"][:380])
    assert len(fit_rows) == 2 * len(actual_splits)
    expected_rows = [(len(t[t >= 12]) * 8, len(t[t >= 12]) * 8) for t, _ in actual_splits]
    assert fit_rows[:len(actual_splits)] == expected_rows
    assert fit_rows[len(actual_splits):] == expected_rows
    pos = first["positions"]
    assert np.all(pos[:120] == 0.0) and np.all(pos[380:] == 0.0)
    for t in range(121, 380):
        if t % 8 != 0:
            np.testing.assert_array_equal(pos[t], pos[t - 1])
