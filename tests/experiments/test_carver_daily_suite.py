"""Unit tests for the Carver AF30 daily suite shared primitives (carver_common)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from warden.runner import load_module_from_path

common = load_module_from_path(Path("strategies/candidates/carver_daily/carver_common.py"))
ASSET_CLASSES = common.ASSET_CLASSES
ann_vol = common.ann_vol
apply_buffer = common.apply_buffer
cash_vol_positions = common.cash_vol_positions
ewmac = common.ewmac
forecast_from_trend = common.forecast_from_trend
observation_mask = common.observation_mask
price_vol = common.price_vol
value_matrix = common.value_matrix


def _bars(closes: list[float], asset: str = "AAA") -> pl.DataFrame:
    dates = [datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(len(closes))]
    return pl.DataFrame({"timestamp": dates, "asset": [asset] * len(closes), "close": closes})


def test_ewmac_step_response_matches_closed_form():
    """span=1 means alpha=1, so EMA_1 is the price and EMA_2 is hand-computable."""
    # EMA_2 at the jump = (200 + 100/3 + 100/9 + 100/27) / (1 + 1/3 + 1/9 + 1/27) = 167.5;
    # ewmac = (200 - 167.5) / 200 = 13/80.
    df = _bars([100.0, 100.0, 100.0, 200.0])
    got = df.select(ewmac(pl.col("close"), 1, 2).over("asset").alias("e"))["e"].to_numpy()

    assert np.allclose(got[:3], 0.0, atol=1e-12)
    assert math.isclose(got[3], 13.0 / 80.0, rel_tol=0.0, abs_tol=1e-12)


def test_ewmac_is_computed_per_asset():
    df = pl.concat([_bars([100.0, 100.0, 100.0, 200.0], "AAA"), _bars([200.0] * 4, "BBB")])
    got = df.sort("timestamp", "asset").with_columns(e=ewmac(pl.col("close"), 1, 2).over("asset"))

    aaa = got.filter(pl.col("asset") == "AAA")["e"].to_numpy()
    bbb = got.filter(pl.col("asset") == "BBB")["e"].to_numpy()
    assert math.isclose(aaa[-1], 13.0 / 80.0, abs_tol=1e-12)
    assert np.allclose(bbb, 0.0, atol=1e-12)


def test_ann_vol_matches_hand_value_and_price_vol_is_lagged():
    df = _bars([100.0, 102.0, 102.0, 102.0])
    out = df.select(
        ann_vol(pl.col("close"), 2, 312).alias("ann"),
        price_vol(pl.col("close"), 2, 312).alias("pv"),
    )

    # Returns are [null, 0.02, 0.0, 0.0]; sample std of [0.02, 0.0] is 0.02/sqrt(2).
    assert math.isclose(out["ann"][2], 0.02 * math.sqrt(156.0), rel_tol=0.0, abs_tol=1e-12)
    assert out["ann"][3] == 0.0
    assert out["pv"][3] == out["ann"][2]


def test_forecast_from_trend_scales_and_clips():
    df = pl.DataFrame({"trend": [0.1, 1.0, -1.0], "vol": [0.2, 0.001, 0.001]})
    got = df.select(forecast_from_trend(pl.col("trend"), pl.col("vol"), 1.91).alias("f"))["f"].to_numpy()

    assert math.isclose(got[0], 0.1 / 0.2 * 1.91, rel_tol=0.0, abs_tol=1e-12)
    assert got[1] == 20.0
    assert got[2] == -20.0


def test_apply_buffer_deadband():
    # width = 0.10 * weight(0.1) = 0.01
    assert apply_buffer(0.055, 0.05, 0.10, 0.1) == 0.05
    assert apply_buffer(0.07, 0.05, 0.10, 0.1) == 0.07
    assert apply_buffer(-0.055, -0.05, 0.10, 0.1) == -0.05
    assert apply_buffer(-0.07, -0.05, 0.10, 0.1) == -0.07


def test_cash_vol_positions_formula_and_cap():
    assert math.isclose(cash_vol_positions(10.0, 0.64, 1.0, 0.16), 0.25, rel_tol=0.0, abs_tol=1e-12)
    assert math.isclose(cash_vol_positions(10.0, 0.16, 0.5, 0.16), 0.5, rel_tol=0.0, abs_tol=1e-12)
    assert cash_vol_positions(10.0, 0.16, 1.0, 0.16) == 0.5  # clipped by the per-instrument cap
    assert cash_vol_positions(-10.0, 0.16, 1.0, 0.16) == -0.5


def test_asset_classes_cover_the_17_instrument_panel():
    assert ASSET_CLASSES == {
        "ES": "equity_us", "NQ": "equity_us",
        "DAX": "equity_eu", "EUSIDXEUR": "equity_eu",
        "HK33": "equity_asia", "NIKKEI225": "equity_asia",
        "AUDUSD": "fx", "CADJPY": "fx", "EURUSD": "fx", "GBPJPY": "fx",
        "GBPUSD": "fx", "USDCAD": "fx", "USDCHF": "fx", "USDJPY": "fx",
        "XAGUSD": "metals", "XAUUSD": "metals", "CL": "energy",
    }


def test_value_matrix_and_observation_mask_fill_closed_bars():
    rows = [
        {"timestamp": datetime(2020, 1, 1, tzinfo=UTC), "asset": "AAA", "close": 10.0},
        {"timestamp": datetime(2020, 1, 1, tzinfo=UTC), "asset": "BBB", "close": 20.0},
        {"timestamp": datetime(2020, 1, 2, tzinfo=UTC), "asset": "AAA", "close": 11.0},
    ]
    df = pl.DataFrame(rows)
    values = value_matrix(df, "close", ["AAA", "BBB"])
    mask = observation_mask(df, ["AAA", "BBB"])

    assert values.shape == (2, 2)
    assert np.allclose(values, np.array([[10.0, 20.0], [11.0, 0.0]]))
    assert mask.dtype == bool
    assert mask.tolist() == [[True, True], [True, False]]
