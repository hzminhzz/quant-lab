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
    if not exp_path.is_file():
        pytest.skip(f"FX exploratory dataset not mounted at {exp_path}; source-only checkout cannot validate market bytes")

    # Verify pinned SHA-256
    actual_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()
    assert actual_sha == contract.data_snapshot_sha256

    df = pl.read_parquet(exp_path)
    assert len(df) == 298694
    assert df.schema["timestamp"] == pl.Datetime("us", "UTC")
    assert df.schema["asset"] == pl.String
    assert set(df["asset"].unique()) == {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "CADJPY", "GBPJPY"}
    assert df.null_count().row(0) == (0,) * 8
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start


def test_fx_majors_1m_exploratory_dataset():
    contract_path = Path("contracts/fx_majors_1m_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "fx"
    assert contract.frequency == "1m"
    assert contract.execution_mode == "QUOTE_SIDE"
    assert contract.label_horizon_bars == 240
    assert contract.embargo_bars == 60

    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file(), f"FX 1m exploratory dataset not found at {exp_path}"

    actual_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()
    assert actual_sha == contract.data_snapshot_sha256

    df = pl.read_parquet(exp_path)
    assert len(df) == 4465397
    assert df.schema["timestamp"] == pl.Datetime("us", "UTC")
    assert df.schema["asset"] == pl.String
    assert set(df["asset"].unique()) == {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "CADJPY", "GBPJPY"}
    assert df.null_count().row(0) == (0,) * 8
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start


def test_indices_exploratory_dataset():
    contract_path = Path("contracts/indices_1h_v2.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "indices"
    assert contract.frequency == "1h"

    exp_path = Path(contract.exploratory_data_path)
    if not exp_path.is_file():
        pytest.skip(f"Indices exploratory dataset not mounted at {exp_path}; source-only checkout cannot validate market bytes")

    # Verify pinned SHA-256
    actual_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()
    assert actual_sha == contract.data_snapshot_sha256

    df = pl.read_parquet(exp_path)
    assert len(df) == 302036
    assert df["timestamp"].min().year == 2015
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
    if not exp_path.is_file():
        pytest.skip(f"Indices 1m exploratory dataset not mounted at {exp_path}; source-only checkout cannot validate market bytes")

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
    if not exp_path.is_file():
        pytest.skip(f"Metals 1m exploratory dataset not mounted at {exp_path}; source-only checkout cannot validate market bytes")

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
def test_commodities_1m_exploratory_dataset():
    contract_path = Path("contracts/commodities_1m_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "commodities"
    assert contract.frequency == "1m"

    exp_path = Path(contract.exploratory_data_path)
    if not exp_path.is_file():
        pytest.skip(f"Commodities 1m exploratory dataset not mounted at {exp_path}; source-only checkout cannot validate market bytes")

    actual_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()
    assert actual_sha == contract.data_snapshot_sha256

    df = pl.read_parquet(exp_path)
    assert len(df) == 497180
    assert df.schema["timestamp"] == pl.Datetime("us", "UTC")
    assert df.schema["asset"] == pl.String
    assert set(df["asset"].unique()) == {"CL"}
    assert df.null_count().row(0) == (0,) * 8
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start
def test_crypto_perps_1m_exploratory_dataset():
    contract_path = Path("contracts/crypto_perps_1m_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "crypto_perps"
    assert contract.frequency == "1m"

    exp_path = Path(contract.exploratory_data_path)
    if not exp_path.is_file():
        pytest.skip(f"Crypto 1m exploratory dataset not mounted at {exp_path}; source-only checkout cannot validate market bytes")

    actual_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()
    assert actual_sha == contract.data_snapshot_sha256

    df = pl.read_parquet(exp_path)
    assert len(df) == 6301440
    assert df.schema["timestamp"] == pl.Datetime("us", "UTC")
    assert df.schema["asset"] == pl.String
    expected_assets = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT"}
    assert set(df["asset"].unique()) == expected_assets
    assert df.null_count().row(0) == (0,) * 8
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start

def test_multi_asset_1h_exploratory_dataset():
    contract_path = Path("contracts/multi_asset_1h_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "multi_asset"
    assert contract.frequency == "1h"

    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file(), f"Multi-asset exploratory dataset not found at {exp_path}"

    actual_sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()
    assert actual_sha == contract.data_snapshot_sha256

    df = pl.read_parquet(exp_path)
    assert len(df) == 1004384
    assert df.schema["timestamp"] == pl.Datetime("us", "UTC")
    assert df.schema["asset"] == pl.String
    assert sorted(df["asset"].unique().to_list()) == sorted(API["MULTI_ASSET_SYMBOLS"])
    assert len(df["asset"].unique()) == 17
    assert df.null_count().row(0) == (0,) * 8
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start

    df_2024 = df.filter(
        (df["timestamp"] >= pl.datetime(2024, 1, 1, time_zone="UTC"))
        & (df["timestamp"] < pl.datetime(2025, 1, 1, time_zone="UTC"))
    )
    assert abs(df_2024["timestamp"].n_unique() - 6240) / 6240 < 0.01

def test_multi_asset_5m_exploratory_dataset():
    contract_path = Path("contracts/multi_asset_5m_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "multi_asset"
    assert contract.frequency == "5m"
    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file()
    assert hashlib.sha256(exp_path.read_bytes()).hexdigest() == contract.data_snapshot_sha256
    df = pl.read_parquet(exp_path)
    assert len(df) == 11873470
    assert sorted(df["asset"].unique().to_list()) == sorted(API["MULTI_ASSET_SYMBOLS"])
    df_2024 = df.filter(
        (df["timestamp"] >= pl.datetime(2024, 1, 1, time_zone="UTC"))
        & (df["timestamp"] < pl.datetime(2025, 1, 1, time_zone="UTC"))
    )
    assert abs(df_2024["timestamp"].n_unique() - contract.bars_per_year) / contract.bars_per_year < 0.01


def test_multi_asset_30m_exploratory_dataset():
    contract_path = Path("contracts/multi_asset_30m_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "multi_asset"
    assert contract.frequency == "30m"
    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file()
    assert hashlib.sha256(exp_path.read_bytes()).hexdigest() == contract.data_snapshot_sha256
    df = pl.read_parquet(exp_path)
    assert len(df) == 1993883
    assert sorted(df["asset"].unique().to_list()) == sorted(API["MULTI_ASSET_SYMBOLS"])
    df_2024 = df.filter(
        (df["timestamp"] >= pl.datetime(2024, 1, 1, time_zone="UTC"))
        & (df["timestamp"] < pl.datetime(2025, 1, 1, time_zone="UTC"))
    )
    assert abs(df_2024["timestamp"].n_unique() - contract.bars_per_year) / contract.bars_per_year < 0.01


def test_multi_asset_4h_exploratory_dataset():
    contract_path = Path("contracts/multi_asset_4h_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "multi_asset"
    assert contract.frequency == "4h"
    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file()
    assert hashlib.sha256(exp_path.read_bytes()).hexdigest() == contract.data_snapshot_sha256
    df = pl.read_parquet(exp_path)
    assert len(df) == 271817
    assert sorted(df["asset"].unique().to_list()) == sorted(API["MULTI_ASSET_SYMBOLS"])
    df_2024 = df.filter(
        (df["timestamp"] >= pl.datetime(2024, 1, 1, time_zone="UTC"))
        & (df["timestamp"] < pl.datetime(2025, 1, 1, time_zone="UTC"))
    )
    assert abs(df_2024["timestamp"].n_unique() - contract.bars_per_year) / contract.bars_per_year < 0.01


def test_multi_asset_1d_exploratory_dataset():
    contract_path = Path("contracts/multi_asset_1d_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "multi_asset"
    assert contract.frequency == "1d"
    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file()
    assert hashlib.sha256(exp_path.read_bytes()).hexdigest() == contract.data_snapshot_sha256
    df = pl.read_parquet(exp_path)
    assert len(df) == 53504
    assert sorted(df["asset"].unique().to_list()) == sorted(API["MULTI_ASSET_SYMBOLS"])
    df_2024 = df.filter(
        (df["timestamp"] >= pl.datetime(2024, 1, 1, time_zone="UTC"))
        & (df["timestamp"] < pl.datetime(2025, 1, 1, time_zone="UTC"))
    )
    assert abs(df_2024["timestamp"].n_unique() - contract.bars_per_year) / contract.bars_per_year < 0.01

@pytest.mark.parametrize("freq,rows", [("5m", 3485714), ("30m", 591361), ("4h", 87963), ("1d", 17697)])
def test_indices_domain_exploratory_datasets(freq, rows):
    contract_path = Path(f"contracts/indices_{freq}_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "indices"
    assert contract.frequency == freq
    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file()
    assert hashlib.sha256(exp_path.read_bytes()).hexdigest() == contract.data_snapshot_sha256
    df = pl.read_parquet(exp_path)
    assert len(df) == rows
    assert sorted(df["asset"].unique().to_list()) == sorted(API["INDEX_SYMBOLS"])
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start
    df_2024 = df.filter(
        (df["timestamp"] >= pl.datetime(2024, 1, 1, time_zone="UTC"))
        & (df["timestamp"] < pl.datetime(2025, 1, 1, time_zone="UTC"))
    )
    assert abs(df_2024["timestamp"].n_unique() - contract.bars_per_year) / contract.bars_per_year < 0.01

    stg_path = Path("data/holdout_staging") / exp_path.name
    assert stg_path.is_file()
    df_stg = pl.read_parquet(stg_path)
    assert df_stg["timestamp"].min() >= contract.holdout_start
    assert df_stg["timestamp"].max() < contract.holdout_end


@pytest.mark.parametrize("freq,rows", [("5m", 6290027), ("30m", 1051876), ("1h", 526720), ("4h", 136390), ("1d", 26576)])
def test_fx_majors_domain_exploratory_datasets(freq, rows):
    contract_path = Path(f"contracts/fx_majors_{freq}_v1.yaml")
    contract = load_contract(contract_path)
    assert contract.asset_class == "fx"
    assert contract.frequency == freq
    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file()
    assert hashlib.sha256(exp_path.read_bytes()).hexdigest() == contract.data_snapshot_sha256
    df = pl.read_parquet(exp_path)
    assert len(df) == rows
    assert sorted(df["asset"].unique().to_list()) == sorted(API["FX_PAIRS"])
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start
    df_2024 = df.filter(
        (df["timestamp"] >= pl.datetime(2024, 1, 1, time_zone="UTC"))
        & (df["timestamp"] < pl.datetime(2025, 1, 1, time_zone="UTC"))
    )
    assert abs(df_2024["timestamp"].n_unique() - contract.bars_per_year) / contract.bars_per_year < 0.01

    stg_path = Path("data/holdout_staging") / exp_path.name
    assert stg_path.is_file()
    df_stg = pl.read_parquet(stg_path)
    assert df_stg["timestamp"].min() >= contract.holdout_start
    assert df_stg["timestamp"].max() < contract.holdout_end

@pytest.mark.parametrize("freq,rows,stg_rows,contract_file", [
    ("5m", 1260288, 1107840, "contracts/crypto_perps_5m_v1.yaml"),
    ("30m", 210048, 184640, "contracts/crypto_perps_30m_v1.yaml"),
    ("1h", 105024, 92320, "contracts/crypto_perps_1h_v2.yaml"),
    ("4h", 26256, 23080, "contracts/crypto_perps_4h_v1.yaml"),
    ("1d", 4376, 3840, "contracts/crypto_perps_1d_v1.yaml"),
])
def test_crypto_perps_domain_exploratory_datasets(freq, rows, stg_rows, contract_file):
    contract_path = Path(contract_file)
    contract = load_contract(contract_path)
    assert contract.asset_class == "crypto_perps"
    assert contract.frequency == freq
    exp_path = Path(contract.exploratory_data_path)
    assert exp_path.is_file()
    assert hashlib.sha256(exp_path.read_bytes()).hexdigest() == contract.data_snapshot_sha256
    df = pl.read_parquet(exp_path)
    assert len(df) == rows
    assert sorted(df["asset"].unique().to_list()) == ["ADAUSDT", "AVAXUSDT", "BNBUSDT", "BTCUSDT", "DOGEUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    assert (df["close"] > 0).all()
    assert df["timestamp"].max() < contract.holdout_start
    df_2024 = df.filter(
        (df["timestamp"] >= pl.datetime(2024, 1, 1, time_zone="UTC"))
        & (df["timestamp"] < pl.datetime(2025, 1, 1, time_zone="UTC"))
    )
    assert abs(df_2024["timestamp"].n_unique() - contract.bars_per_year) / contract.bars_per_year < 0.01

    stg_path = Path("data/holdout_staging") / exp_path.name
    assert stg_path.is_file()
    df_stg = pl.read_parquet(stg_path)
    assert len(df_stg) == stg_rows
    assert df_stg["timestamp"].min() >= contract.holdout_start
    assert df_stg["timestamp"].max() < contract.holdout_end


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
