"""Official Binance historical REST gap recovery tests, no real network."""

import json
from datetime import timedelta

import polars as pl
import pytest

from scripts.recover_pit_bnx_rest_gap import (
    FIRST,
    SYMBOL,
    augment_v2,
    decode_official_response,
)


def payload(*events) -> bytes:
    return json.dumps([
        {
            "symbol": row[0],
            "fundingTime": row[1],
            "fundingRate": row[2],
            "markPrice": "",
            "rateType": "Regular",
        }
        for row in events
    ]).encode()


def sample_one(stamp_ms: int = 1672531200000) -> bytes:
    return payload((SYMBOL, stamp_ms, "-0.00050967"))


def test_rest_archive_keeps_unknown_interval_without_invention() -> None:
    raw = decode_official_response(sample_one())
    assert raw.height == 1
    assert raw["timestamp"][0] == FIRST
    assert raw["funding_rate"][0] == pytest.approx(-.00050967)
    assert raw["funding_interval_hours"].null_count() == 1
    assert raw["event_source"][0] == "BINANCE_OFFICIAL_FUNDING_HISTORY_REST"


@pytest.mark.parametrize(
    "body,exception",
    [
        (b"[]", "Unexpected"),
        (b"{}", "Unexpected"),
        (payload(("OTHERUSDT", 1672531200000, "0.0001")), "Wrong asset"),
        (payload((SYMBOL, 1675209600000, "0.0001")), "outside preregistered"),
        (payload((SYMBOL, 1672531200000, "nan")), "Invalid historical"),
        (payload((SYMBOL, 1672531200000, "0.2")), "Invalid historical"),
    ],
)
def test_invalid_or_future_rest_payloads_rejected(body: bytes, exception: str) -> None:
    with pytest.raises(ValueError, match=exception):
        decode_official_response(body)


def test_duplicate_rest_settlement_is_not_deduplicated() -> None:
    raw = payload((SYMBOL, 1672531200000, "0.0001"),
                  (SYMBOL, 1672531200000, "0.0002"))
    with pytest.raises(ValueError, match="Duplicate"):
        decode_official_response(raw)


def fake_v2() -> tuple[pl.DataFrame, pl.DataFrame]:
    first = FIRST
    rows = []
    for i in range(4):
        rows.append({
            "timestamp": first + timedelta(hours=i),
            "asset": SYMBOL,
            "open": 12.0, "high": 12.1, "low": 11.9,
            "close": 12.0, "volume": 30.0,
            "funding_rate": None, "funding_event_time": None,
            "funding_observed": False, "funding_age_hours": None,
            "funding_source": None, "funding_interval_hours": None,
        })
    p = pl.DataFrame(rows).with_columns(
        pl.col("funding_event_time").cast(pl.Datetime("us", "UTC")),
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
        pl.col("funding_rate").cast(pl.Float64),
        pl.col("funding_age_hours").cast(pl.Float64),
        pl.col("funding_interval_hours").cast(pl.Int64),
    )
    ledger = pl.DataFrame(
        {
            "timestamp": [first + timedelta(days=32)],
            "asset": [SYMBOL],
            "funding_rate": [.0001],
            "funding_interval_hours": [8],
            "event_source": ["BINANCE_VISION_SHA_VERIFIED"],
        },
        schema={
            "timestamp": pl.Datetime("us", "UTC"),
            "asset": pl.String, "funding_rate": pl.Float64,
            "funding_interval_hours": pl.Int64, "event_source": pl.String,
        },
    )
    return p, ledger


def test_rest_only_closes_earlier_unknown_without_changing_bars() -> None:
    p, ledger = fake_v2()
    supplement = decode_official_response(sample_one())
    updated, events, audit = augment_v2(p, ledger, supplement)
    assert updated.height == 4
    assert events.height == 2
    assert audit["after_overlay_missing_rows"] == 0
    assert audit["recovered_rows"] == 4
    assert updated["funding_source"].to_list() == [
        "BINANCE_OFFICIAL_FUNDING_HISTORY_REST"
    ] * 4
    assert updated["funding_interval_hours"].null_count() == 4
    assert updated["funding_rate"].to_list() == [-.00050967] * 4
    assert updated["open"].to_list() == p["open"].to_list()


def test_rest_coverage_must_not_promote_missing_rows_to_zero() -> None:
    p, ledger = fake_v2()
    # The first observed January event occurs after the first 2 hours.
    later = decode_official_response(sample_one(1672531200000 + 2 * 3600000))
    with pytest.raises(ValueError, match="does not close"):
        augment_v2(p, ledger, later)


def test_rest_supplement_cannot_overlay_foreign_asset() -> None:
    p, ledger = fake_v2()
    x = decode_official_response(sample_one()).with_columns(
        pl.lit("BTCUSDT").alias("asset")
    )
    with pytest.raises(ValueError, match="BNX January"):
        augment_v2(p, ledger, x)
