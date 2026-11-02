"""Source-verified Binance funding acquisition tests (pure synthetic fixtures)."""

import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from scripts.recover_pit_funding_archives import (
    CUTOFF,
    FIRST,
    archive_plan,
    archive_url,
    asof_coverage,
    checksum_from_text,
    validate_archive,
    write_once,
)


def make_archive(asset: str, month: str, csv_body: str) -> bytes:
    contents = io.BytesIO()
    name = f"{asset}-fundingRate-{month}.csv"
    with zipfile.ZipFile(contents, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, csv_body)
    return contents.getvalue()


def fixture_panel() -> pl.DataFrame:
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    return pl.DataFrame(
        {
            "timestamp": [ts + timedelta(hours=i % 4) for i in range(8)],
            "asset": ["BTCUSDT"] * 4 + ["EOSUSDT"] * 4,
            "funding_observed": [True] * 4 + [False] * 4,
        }, schema={
            "timestamp": pl.Datetime("us", "UTC"),
            "asset": pl.String, "funding_observed": pl.Boolean,
        },
    )


def test_missing_symbol_month_and_prev_month_only() -> None:
    plan, counts = archive_plan(fixture_panel())
    assert len(plan) == 2
    assert plan[0] == ("EOSUSDT", datetime(2023, 12, 1, tzinfo=UTC))
    assert plan[1] == ("EOSUSDT", datetime(2024, 1, 1, tzinfo=UTC))
    assert counts["original_missing_rows"] == 4
    assert counts["source_missing_assets"] == 1
    assert counts["source_available_assets"] == 1


def test_partial_missing_mask_fails_closed() -> None:
    panel = fixture_panel().with_columns(
        pl.when(pl.int_range(pl.len()) == 7).then(True)
        .otherwise(pl.col("funding_observed")).alias("funding_observed")
    )
    with pytest.raises(ValueError, match="intermittent"):
        archive_plan(panel)


def test_archive_urls_exclude_protected_or_invalid_symbols() -> None:
    assert archive_url("EOSUSDT", FIRST).endswith("EOSUSDT-fundingRate-2022-12.zip")
    with pytest.raises(ValueError, match="Protected"):
        archive_url("BTCUSDT", CUTOFF)
    with pytest.raises(ValueError, match="Invalid"):
        archive_url("../../BTCUSDT", FIRST)


def test_official_month_header_checksum_and_values() -> None:
    text = (
        "calc_time,funding_interval_hours,last_funding_rate\n"
        "1704067200000,8,0.00012345\n"
        "1704096000001,8,-0.000024\n"
    )
    blob = make_archive("EOSUSDT", "2024-01", text)
    result = validate_archive(blob, "EOSUSDT", datetime(2024, 1, 1, tzinfo=UTC))
    assert len(result) == 2
    assert result[1]["timestamp"] == datetime(2024, 1, 1, 8, 0, 0, 1000, tzinfo=UTC)
    assert result[1]["funding_rate"] == pytest.approx(-.000024)
    sha = hashlib.sha256(blob).hexdigest()
    assert checksum_from_text(f"{sha}  EOSUSDT-fundingRate-2024-01.zip\n".encode()) == sha


@pytest.mark.parametrize(
    "row,match",
    [
        ("1704067200000,8,NaN", "Invalid funding"),
        ("1704067200000,6,0.0002", "Invalid funding"),
        ("1706745600000,8,0.0002", "outside requested historical"),
    ],
)
def test_corrupt_or_outside_archive_fail_closed(row: str, match: str) -> None:
    blob = make_archive(
        "EOSUSDT", "2024-01",
        "calc_time,funding_interval_hours,last_funding_rate\n" + row + "\n",
    )
    with pytest.raises(ValueError, match=match):
        validate_archive(blob, "EOSUSDT", datetime(2024, 1, 1, tzinfo=UTC))


def test_duplicate_observed_source_timestamp_is_error() -> None:
    row = "1704067200000,8,0.0001\n"
    blob = make_archive(
        "EOSUSDT", "2024-01",
        "calc_time,funding_interval_hours,last_funding_rate\n" + row + row,
    )
    with pytest.raises(ValueError, match="Duplicate funding"):
        validate_archive(blob, "EOSUSDT", datetime(2024, 1, 1, tzinfo=UTC))


def test_asof_join_cannot_use_future_funding_event() -> None:
    original = fixture_panel()
    date = datetime(2024, 1, 1, tzinfo=UTC)
    funding = pl.DataFrame({
        "timestamp": [date + timedelta(hours=2)],
        "asset": ["EOSUSDT"],
        "funding_rate": [.0003],
        "funding_interval_hours": [8],
    })
    report = asof_coverage(original, funding)
    assert report["rows_originally_missing"] == 4
    # The clock has 4 duplicate asset timestamps, so rows 0-3 from
    # missing EOS use the same observed hour as BTC. Two are before
    # event and two after.
    assert report["recovered_rows"] == 2
    assert report["remaining_unknown_rows"] == 2


def test_atomic_source_cache_refuses_replacement(tmp_path) -> None:
    path = tmp_path / "asset" / "month.zip"
    write_once(path, b"source archives are immutable")
    write_once(path, b"source archives are immutable")
    assert path.read_bytes() == b"source archives are immutable"
    with pytest.raises(FileExistsError, match="immutable source differs"):
        write_once(path, b"replacement")
