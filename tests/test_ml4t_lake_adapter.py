"""Behavior of the Warden lake reader against synthetic Hive partitions only."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from warden.market_data import read_parquet_bytes
from warden_evidence.dataset_builder import build_dataset, parse_dataset_spec
from warden_evidence.dataset_builder.adapters import get_source_adapter
from warden_evidence.dataset_builder.containment import AdapterError, SourceContainmentError


@pytest.fixture
def lake_spec():
    return parse_dataset_spec(
        {
            "schema_version": 1,
            "dataset_id": "dukascopy_fx_test_1d_ohlcv_r1",
            "asset_class": "fx",
            "evidence_tier": "research_proxy",
            "venue": "dukascopy",
            "frequency": "1d",
            "price_basis": "ohlcv",
            "volume_semantics": "tick_count_proxy",
            "time_window": {
                "start_inclusive": "2024-01-01T00:00:00Z",
                "exploratory_end_exclusive": "2025-01-01T00:00:00Z",
                "holdout_start_inclusive": "2025-01-01T00:00:00Z",
                "holdout_end_exclusive": "2026-01-01T00:00:00Z",
            },
            "time_contract": {
                "timezone": "UTC",
                "bar_timestamp_semantics": "open_left_closed_left",
                "session_calendar": "24/5",
            },
            "source": {
                "kind": "lake_raw",
                "adapter": "ml4t_lake",
                "inputs": {
                    "eurusd": {
                        "path": "forex_daily_EURUSD",
                        "format": "parquet",
                        "entity_column": "symbol",
                    },
                },
            },
            "universe": {
                "selection_mode": "explicit_symbols",
                "symbols": ["EURUSD"],
                "retain_delisted": True,
            },
            "resampling": {"enabled": True},
            "quality_gate": {
                "max_abs_bar_return": 0.25,
                "mad_outlier_threshold": 15.0,
                "allow_zero_volume": True,
                "explicit_anomaly_filters": [],
            },
            "parquet_write": {
                "compression": "zstd",
                "compression_level": 3,
                "statistics": True,
                "row_group_size": 100000,
                "sort_keys": ["timestamp", "asset"],
            },
        }
    )


def _write_lake(root: Path, close: float) -> Path:
    partition = root / "forex_daily_EURUSD" / "year=2024" / "month=01" / "data.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "timestamp": [datetime(2024, 1, 2, tzinfo=UTC)],
            "symbol": ["EURUSD"],
            "open": [1.2],
            "high": [1.4],
            "low": [1.1],
            "close": [close],
            "volume": [100.0],
        }
    ).write_parquet(partition)
    catalog = root / ".catalog" / "research-dataset-catalog.json"
    catalog.parent.mkdir()
    catalog.write_text(
        json.dumps(
            {
                "catalog_identity": "fixture-catalog",
                "entries": [
                    {
                        "dataset_key": "forex_daily_EURUSD",
                        "asset_class": "fx",
                        "provider": "dukascopy",
                        "tier": "maintained_canonical",
                        "qualification": "accepted",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return partition


def test_configured_store_overrides_sibling(tmp_path, monkeypatch, lake_spec):
    workspace = tmp_path / "quant-lab"
    workspace.mkdir()
    _write_lake(tmp_path / "ml4t-data" / ".data" / "store", 1.30)
    configured = tmp_path / "fixture-store"
    partition = _write_lake(configured, 1.25)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("ML4T_DATA_STORE", str(configured))

    adapter = get_source_adapter("ml4t_lake", parquet_reader=read_parquet_bytes)
    bundle = adapter.load(lake_spec)
    assert bundle.frame.select("asset", "close").rows() == [("EURUSD", 1.25)]
    assert bundle.inputs[0].to_dict()["sha256"] == hashlib.sha256(partition.read_bytes()).hexdigest()
    assert bundle.audit["catalog_identity"] == "fixture-catalog"
    assert bundle.audit["stores"]["forex_daily_EURUSD"]["provider"] == "dukascopy"


@pytest.mark.parametrize("configured", ["missing-store", ""])
def test_invalid_configured_store_never_uses_sibling(tmp_path, monkeypatch, configured):
    workspace = tmp_path / "quant-lab"
    workspace.mkdir()
    _write_lake(tmp_path / "ml4t-data" / ".data" / "store", 1.3)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("ML4T_DATA_STORE", configured)
    with pytest.raises(FileNotFoundError, match="ML4T_DATA_STORE"):
        get_source_adapter("ml4t_lake", parquet_reader=read_parquet_bytes)


def test_unset_store_uses_sibling(tmp_path, monkeypatch, lake_spec):
    workspace = tmp_path / "quant-lab"
    workspace.mkdir()
    _write_lake(tmp_path / "ml4t-data" / ".data" / "store", 1.30)
    monkeypatch.chdir(workspace)
    monkeypatch.delenv("ML4T_DATA_STORE", raising=False)
    adapter = get_source_adapter("ml4t_lake", parquet_reader=read_parquet_bytes)
    assert adapter.load(lake_spec).frame["close"].to_list() == [1.30]


def test_lake_parquet_reader_must_be_injected(tmp_path, monkeypatch):
    monkeypatch.setenv("ML4T_DATA_STORE", str(tmp_path))
    with pytest.raises(AdapterError, match="requires an injected parquet_reader"):
        get_source_adapter("ml4t_lake")


def test_build_reports_missing_configured_lake_before_publishing(tmp_path, monkeypatch, lake_spec):
    monkeypatch.setenv("ML4T_DATA_STORE", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError, match="ML4T_DATA_STORE"):
        build_dataset(lake_spec, repo_root=tmp_path, parquet_reader=read_parquet_bytes)

    assert not list(tmp_path.iterdir())


def test_configured_store_refuses_protected_root(tmp_path, monkeypatch):
    protected = tmp_path / "data" / "holdout"
    protected.mkdir(parents=True)
    link = tmp_path / "fixture-store"
    link.symlink_to(protected, target_is_directory=True)
    for path in (protected, link):
        monkeypatch.setenv("ML4T_DATA_STORE", str(path))
        with pytest.raises(SourceContainmentError, match="ML4T_DATA_STORE.*protected"):
            get_source_adapter("ml4t_lake", parquet_reader=read_parquet_bytes)
