"""Hermetic multi-asset salvage tests (live dataset pins live in tests/integration)."""

from __future__ import annotations

import runpy

import polars as pl
import pytest

API = runpy.run_path("scripts/salvage_multi_asset.py")
normalize_panel = API["normalize_panel"]


def test_normalize_panel_validation():
    # Valid synthetic sample
    df = pl.DataFrame({
        "timestamp": ["2025-01-01T00:00:00Z", "2025-01-01T01:00:00Z"],
        "symbol": ["EURUSD", "EURUSD"],
        "open": [1.10, 1.11],
        "high": [1.12, 1.13],
        "low": [1.09, 1.10],
        "close": [1.11, 1.12],
        "volume": [100.0, 150.0],
    })
    normalized = normalize_panel(df)
    assert "asset" in normalized.columns
    assert "funding_rate" in normalized.columns
    assert normalized["funding_rate"].to_list() == [0.0, 0.0]

    # Reject duplicate timestamp/asset
    dup_df = pl.concat([df, df[:1]])
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_panel(dup_df)

    # Reject negative price
    neg_df = df.with_columns(close=pl.lit(-1.0))
    with pytest.raises(ValueError, match="Invalid"):
        normalize_panel(neg_df)
