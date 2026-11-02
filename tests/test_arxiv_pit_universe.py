"""As-of universe ranking and protected-data exclusion controls."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from scripts.audit_pit_top100_universe import (
    CUTOFF,
    asof_liquidity_rank,
    eligible_source_files,
    month_starts,
)


def _daily() -> pl.DataFrame:
    start = datetime(2024,5,20,tzinfo=UTC)
    rows = []
    for day in range(50):
        for asset, volume in (("BTCUSDT", 2_000_000), ("ALTOUSDT", 400_000), ("NEWUSDT", 150_000)):
            # NEWUSDT has only 10 historical days available before June 1.
            if asset == "NEWUSDT" and day < 3:
                continue
            rows.append({
                "day": start + timedelta(days=day),
                "asset": asset,
                "quote_usd": volume,
                "hours": 24,
            })
    return pl.DataFrame(rows)


def test_rank_never_uses_any_volume_on_or_after_selection_time() -> None:
    original = _daily()
    asof = datetime(2024,6,1,tzinfo=UTC)
    first = asof_liquidity_rank(original,asof,top_n=3,min_hours=1)
    mutated = pl.concat([
        original, pl.DataFrame({
            "day": [asof],
            "asset": ["NEWUSDT"],
            "quote_usd": [10**16],
            "hours": [24],
        })
    ])
    second = asof_liquidity_rank(mutated,asof,top_n=3,min_hours=1)
    assert first.to_dicts() == second.to_dicts()


def test_min_history_and_liquidity_excludes_new_listing() -> None:
    daily = _daily()
    asof = datetime(2024,6,15,tzinfo=UTC)
    res = asof_liquidity_rank(daily,asof,top_n=3,min_hours=24*26,min_avg_quote_usd=25000)
    # NEWUSDT existed since May 23, so 23 days at June 15 and is ineligible.
    assert "NEWUSDT" not in res["asset"].to_list()
    assert "BTCUSDT" in res["asset"].to_list()


def test_source_enumeration_excludes_protected_year_and_month(tmp_path: Path) -> None:
    store = tmp_path
    for year,month in ((2022,12),(2023,1),(2025,6),(2025,7),(2026,1)):
        p=store / "symbol=BTCUSDT" / f"year={year}" / f"month={month}"
        p.mkdir(parents=True)
        (p / "data.parquet").write_bytes(b"fixture")
    files=eligible_source_files(store)
    assert len(files) == 3
    assert all("year=2025/month=7" not in str(f) for f in files)
    assert all("year=2026" not in str(f) for f in files)
    assert max(month_starts()) == datetime(2025,6,1,tzinfo=UTC)
    assert all(ts < CUTOFF for ts in month_starts())
