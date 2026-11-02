"""PIT dataset builder tests: future exclusion and non-imputed funding."""

import hashlib
from datetime import UTC, datetime, timedelta

import polars as pl

from scripts.build_pit_top100_proposal import (
    assemble_panel,
    funding_paths,
    selected_ohlcv_paths,
)


def _price_rows() -> pl.DataFrame:
    base = datetime(2024, 7, 1, tzinfo=UTC)
    return pl.DataFrame({
        "timestamp": [base + timedelta(hours=i) for i in range(4)],
        "symbol": ["BTCUSDT"] * 4,
        "open": [10.0, 10.1, 10.2, 10.3],
        "high": [10.2, 10.3, 10.4, 10.5],
        "low": [9.9, 10.0, 10.1, 10.2],
        "close": [10.1, 10.2, 10.3, 10.4],
        "volume": [100.,100.,100.,100.],
    })


def test_selected_month_data_is_sha_checked_and_no_future_month(tmp_path) -> None:
    folder=tmp_path/"symbol=BTCUSDT"/"year=2024"/"month=7"
    folder.mkdir(parents=True)
    path=folder/"data.parquet"
    path.write_bytes(b"frozen source fixture")
    expected=hashlib.sha256(path.read_bytes()).hexdigest()
    audit={
        "source_shards":[{
            "path":"symbol=BTCUSDT/year=2024/month=7/data.parquet",
            "sha256":expected,
        }],
        "months":[{"asof_utc":"2024-07-01T00:00:00+00:00","symbols":["BTCUSDT"]}],
    }
    paths,missing,selected=selected_ohlcv_paths(audit,tmp_path)
    assert paths == [path]
    assert missing == []
    assert selected[0]["source_sha256"] == expected
    path.write_bytes(b"tampered")
    import pytest
    with pytest.raises(ValueError,match="source changed"):
        selected_ohlcv_paths(audit,tmp_path)


def test_missing_funding_remains_null_not_zero(tmp_path) -> None:
    path=tmp_path/"price.parquet"
    _price_rows().write_parquet(path)
    funding=pl.DataFrame({
        "timestamp":[datetime(2024,7,1,0,tzinfo=UTC)],
        "asset":["BTCUSDT"],
        "funding_rate":[0.0001],
        "funding_event_time":[datetime(2024,7,1,0,tzinfo=UTC)],
    })
    panel,audit=assemble_panel([path],funding)
    assert panel.height == 4
    assert panel["funding_observed"].to_list() == [True]*4
    assert audit["strict_ohlcv_dropped"] == 0
    no_funding=funding.head(0)
    panel2,audit2=assemble_panel([path],no_funding)
    assert panel2["funding_rate"].null_count() == 4
    assert panel2["funding_observed"].to_list() == [False]*4
    assert audit2["funding_missing_rows"] == 4


def test_funding_path_discovery_does_not_touch_protected_months(tmp_path) -> None:
    base=tmp_path/"crypto_futures_funding_BTCUSDT_PERP"
    for year,month in [(2022,12),(2023,1),(2025,6),(2025,7),(2026,2)]:
        d=base/f"year={year}"/f"month={month}"
        d.mkdir(parents=True)
        (d/"data.parquet").write_bytes(b"mock")
    paths=funding_paths(tmp_path,{"BTCUSDT"})
    assert len(paths)==3
    assert all("year=2025/month=7" not in str(p) for p in paths)
    assert all("year=2026" not in str(p) for p in paths)
