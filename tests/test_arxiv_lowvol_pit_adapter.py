"""Frozen low-vol PIT adapter is only a feature-view, not a new model."""

from datetime import UTC, datetime

import numpy as np
import polars as pl

from strategies.candidates.arxiv_lowvol_pit100_adapter import (
    baseline,
    generate_positions,
    pipeline,
)
from tests.test_arxiv_price_pressure_xs import _bars, _contract
from warden.leakage import verify_prefix_invariance


def _with_source_provenance() -> pl.DataFrame:
    bars = _bars(n_assets=32)
    return bars.with_columns(
        pl.lit(datetime(2023, 1, 1, tzinfo=UTC)).alias("funding_event_time"),
        pl.lit(True).alias("funding_observed"),
        pl.lit(0.0).alias("funding_age_hours"),
    )


def test_pit_numeric_feature_view_is_prefix_invariant() -> None:
    contract = _contract()
    panel = _with_source_provenance()
    result = pipeline.compute(panel, contract)
    assert result.columns == [
        "timestamp",
        "asset",
        "realized_volatility_24h",
        "liquidity_per_hour",
    ]
    verify_prefix_invariance(pipeline, panel, contract)


def test_pit_adapter_preserves_same_frozen_oos_ic_and_positions() -> None:
    contract = _contract()
    panel = _with_source_provenance()
    params = {
        "ridge_alpha": 100.0,
        "rebalance_every": 4,
        "top_k": 2,
        "min_liquidity_usd": 10_000.0,
    }
    old = baseline.generate_positions(panel, contract, **params)
    new = generate_positions(panel, contract, **params)
    np.testing.assert_array_equal(old["positions"], new["positions"])
    np.testing.assert_array_equal(old["ic_series"], new["ic_series"])
    assert old["oos_fold_ridge_coefficients"] == new["oos_fold_ridge_coefficients"]


def test_funding_provenance_is_not_a_model_feature() -> None:
    panel = _with_source_provenance()
    contract = _contract()
    before = pipeline.compute(panel, contract)
    after = pipeline.compute(
        panel.with_columns(
            pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("funding_event_time"),
            pl.lit(False).alias("funding_observed"),
            pl.lit(None, dtype=pl.Float64).alias("funding_age_hours"),
        ),
        contract,
    )
    assert before.equals(after, null_equal=True)
