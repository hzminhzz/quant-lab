from __future__ import annotations

import hashlib
import runpy
from pathlib import Path

import polars as pl
import pytest

API = runpy.run_path("scripts/salvage_multi_asset.py")
normalize_panel = API["normalize_panel"]
from warden.contracts import load_contract


def test_fx_majors_exploratory_dataset():
    contract_path = Path("contracts/fx_majors_15m_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "fx"
    assert contract.frequency == "15m"

    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file(), f"FX exploratory dataset not found at {exp_path}"

    # Verify pinned SHA-256
    actual_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()
    assert actual_sha == contract.data_snapshot_sha256

    df = pl.read_parquet(exp_path)
    assert len(df) == 224115
    assert df.schema["timestamp"] == pl.Datetime("us", "UTC")
    assert df.schema["asset"] == pl.String
    assert set(df["asset"].unique()) == {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"}
    assert df.null_count().row(0) == (0,) * 8
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start


def test_indices_exploratory_dataset():
    contract_path = Path("contracts/indices_1h_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "indices"
    assert contract.frequency == "1h"

    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file(), f"Indices exploratory dataset not found at {exp_path}"

    # Verify pinned SHA-256
    actual_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()
    assert actual_sha == contract.data_snapshot_sha256

    df = pl.read_parquet(exp_path)
    assert len(df) == 46050
    assert df.schema["timestamp"] == pl.Datetime("us", "UTC")
    assert df.schema["asset"] == pl.String
    assert set(df["asset"].unique()) == {"ES", "NQ", "DAX", "NIKKEI225", "EUSIDXEUR", "HK33"}
    assert df.null_count().row(0) == (0,) * 8
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start

def test_indices_1m_exploratory_dataset():
    contract_path = Path("contracts/indices_1m_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "indices"
    assert contract.frequency == "1m"

    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file(), f"Indices 1m exploratory dataset not found at {exp_path}"

    actual_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()
    assert actual_sha == contract.data_snapshot_sha256

    df = pl.read_parquet(exp_path)
    assert len(df) == 2633977
    assert df.schema["timestamp"] == pl.Datetime("us", "UTC")
    assert df.schema["asset"] == pl.String
    assert set(df["asset"].unique()) == {"ES", "NQ", "DAX", "NIKKEI225", "EUSIDXEUR", "HK33"}
    assert df.null_count().row(0) == (0,) * 8
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start
def test_metals_1m_exploratory_dataset():
    contract_path = Path("contracts/metals_1m_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "metals"
    assert contract.frequency == "1m"

    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file(), f"Metals 1m exploratory dataset not found at {exp_path}"

    actual_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()
    assert actual_sha == contract.data_snapshot_sha256

    df = pl.read_parquet(exp_path)
    assert len(df) == 842813
    assert df.schema["timestamp"] == pl.Datetime("us", "UTC")
    assert df.schema["asset"] == pl.String
    assert set(df["asset"].unique()) == {"XAUUSD", "XAGUSD"}
    assert df.null_count().row(0) == (0,) * 8
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start

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
