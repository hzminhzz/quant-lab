"""PIT funding overlay invariants: price identity, source lineage, settlement time."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from scripts.build_pit_funding_overlay import assemble_full_ledger, overlay_prices


def panel() -> pl.DataFrame:
    first = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(5):
        for sym in ("BTCUSDT", "EOSUSDT"):
            old = sym == "BTCUSDT"
            rows.append({
                "timestamp": first + timedelta(hours=i),
                "asset": sym,
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.5 + i,
                "volume": 2000.0,
                "funding_rate": 0.0001 if old else None,
                "funding_event_time": first if old else None,
                "funding_observed": old,
                "funding_age_hours": float(i) if old else None,
            })
    return pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
        pl.col("funding_event_time").cast(pl.Datetime("us", "UTC")),
    )


def sources() -> tuple[pl.DataFrame, pl.DataFrame]:
    first = datetime(2024, 1, 1, tzinfo=UTC)
    old = pl.DataFrame({
        "timestamp": [first],
        "asset": ["BTCUSDT"],
        "funding_rate": [.0001],
        "funding_interval_hours": [8],
    })
    fresh = pl.DataFrame({
        "timestamp": [first + timedelta(hours=2, milliseconds=1)],
        "asset": ["EOSUSDT"],
        "funding_rate": [-.0002],
        "funding_interval_hours": [4],
    })
    return old, fresh


def test_overlay_preserves_all_ohlcv_original_source_and_nulls() -> None:
    original = panel()
    old, fresh = sources()
    ledger, meta = assemble_full_ledger(old, fresh, ["BTCUSDT", "EOSUSDT"])
    assert meta["event_count"] == 2
    result, audit = overlay_prices(original, ledger)
    assert audit["OHLCV_unchanged"]
    assert audit["original_missing_rows"] == 5
    # The 2h + 1ms funding event cannot be used at the 2h open.
    assert audit["recovered_rows"] == 2
    assert audit["after_overlay_missing_rows"] == 3
    nulls = result.filter(
        (pl.col("asset") == "EOSUSDT")
        & (pl.col("timestamp") <= datetime(2024, 1, 1, 2, tzinfo=UTC))
    )
    assert nulls["funding_rate"].null_count() == 3
    updated = result.filter(pl.col("asset") == "EOSUSDT").tail(2)
    assert updated["funding_rate"].to_list() == [-.0002, -.0002]
    assert updated["funding_source"].to_list() == ["BINANCE_VISION_SHA_VERIFIED"] * 2
    btc = result.filter(pl.col("asset") == "BTCUSDT")
    assert btc["funding_source"].unique().to_list() == ["CANONICAL_LOCAL"]
    assert btc["funding_rate"].to_list() == [.0001] * 5


def test_original_and_new_rates_on_same_hour_must_match_exactly() -> None:
    old, fresh = sources()
    # A canonical observed rate that differs from the raw archive must
    # fail: audit parity cannot be silently edited.
    original = panel().with_columns(
        pl.when(pl.col("asset") == "BTCUSDT")
        .then(.0003).otherwise(pl.col("funding_rate")).alias("funding_rate")
    )
    ledger, _ = assemble_full_ledger(old, fresh, ["BTCUSDT", "EOSUSDT"])
    with pytest.raises(ValueError, match="conflicts"):
        overlay_prices(original, ledger)


def test_duplicate_source_settlement_is_not_deduped() -> None:
    old, fresh = sources()
    duplicate = pl.concat([fresh, fresh], how="vertical")
    with pytest.raises(ValueError, match="Duplicate settlement"):
        assemble_full_ledger(old, duplicate, ["BTCUSDT", "EOSUSDT"])


def test_overlap_symbol_only_is_allowed_without_colliding_event() -> None:
    old, fresh = sources()
    first = datetime(2024, 1, 1, tzinfo=UTC)
    old = pl.concat([
        old,
        pl.DataFrame({
            "timestamp": [first - timedelta(hours=8)],
            "asset": ["EOSUSDT"],
            "funding_rate": [.0001],
            "funding_interval_hours": [8],
        }),
    ], how="vertical")
    ledger, audit = assemble_full_ledger(old, fresh, ["BTCUSDT", "EOSUSDT"])
    assert audit["overlapping_source_asset_names"] == 1
    assert ledger.height == 3


def test_null_observed_interval_is_retained_without_fabrication() -> None:
    old, fresh = sources()
    old = old.with_columns(
        pl.lit(None, dtype=pl.Int64).alias("funding_interval_hours")
    )
    ledger, audit = assemble_full_ledger(old, fresh, ["BTCUSDT", "EOSUSDT"])
    assert audit["observed_events_with_unknown_interval"] == 1
    assert ledger["funding_interval_hours"].null_count() == 1


def test_missing_actual_rate_fails_closed() -> None:
    old, fresh = sources()
    old = old.with_columns(
        pl.lit(None, dtype=pl.Float64).alias("funding_rate")
    )
    with pytest.raises(ValueError, match="invalid settlement"):
        assemble_full_ledger(old, fresh, ["BTCUSDT", "EOSUSDT"])


def test_historical_funding_after_val_end_is_rejected() -> None:
    old, fresh = sources()
    fresh = fresh.with_columns(
        pl.lit(datetime(2025, 7, 1, tzinfo=UTC)).alias("timestamp")
    )
    with pytest.raises(ValueError, match="post-validation"):
        assemble_full_ledger(old, fresh, ["BTCUSDT", "EOSUSDT"])


def test_wrong_source_asset_rejected() -> None:
    old, fresh = sources()
    with pytest.raises(ValueError, match="non-PIT"):
        assemble_full_ledger(old, fresh, ["BTCUSDT"])


def test_null_is_not_missing_zero_cost() -> None:
    old, fresh = sources()
    ledger, _ = assemble_full_ledger(old, fresh, ["BTCUSDT", "EOSUSDT"])
    result, _ = overlay_prices(panel(), ledger)
    missing = result.filter(~pl.col("funding_observed"))
    assert missing.height == 3
    assert missing["funding_rate"].null_count() == 3
