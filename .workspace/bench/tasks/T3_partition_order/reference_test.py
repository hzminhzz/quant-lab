"""Hidden reference test for T3. Copied into the graded worktree at grade time and removed
afterwards, so the agent cannot see or edit it.

It pins the contract the task asks for, without assuming how the fix is implemented:
chronological output, no row loss, and the two existing validations still firing.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

SCRIPT = Path("scripts/build_fx_majors_1d_v2.py")
PAIR = "EURUSD"


def _module():
    spec = importlib.util.spec_from_file_location("bench_build_fx", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _store(root: Path, months: list[tuple[str, list[datetime]]], symbol: str = PAIR) -> Path:
    store = root / "store"
    for label, days in months:
        d = store / f"forex_daily_{PAIR}" / "year=2025" / f"month={label}"
        d.mkdir(parents=True)
        pl.DataFrame(
            {
                "timestamp": [t for t in days],
                "symbol": [symbol] * len(days),
                "open": [1.10] * len(days),
                "high": [1.11] * len(days),
                "low": [1.09] * len(days),
                "close": [1.105] * len(days),
                "volume": [100.0] * len(days),
            },
            schema_overrides={"timestamp": pl.Datetime("us", "UTC")},
        ).write_parquet(d / "data.parquet")
    return store


def _days(month: int, n: int) -> list[datetime]:
    return [datetime(2025, month, 1, tzinfo=UTC) + timedelta(days=i) for i in range(n)]


def test_load_pair_returns_chronological_rows_for_unpadded_months(tmp_path):
    # written the way the Hive writer does: month=9 sorts after month=10 as a string
    store = _store(tmp_path, [("12", _days(12, 3)), ("9", _days(9, 3)), ("11", _days(11, 3)),
                              ("10", _days(10, 3))])
    df, labels = _module().load_pair(store, PAIR)

    stamps = df["timestamp"].to_list()
    assert stamps == sorted(stamps), f"load_pair returned unordered rows: {stamps[:6]}"
    assert df.height == 12, f"row loss: {df.height}"
    assert labels == []


def test_load_pair_still_rejects_implausible_years(tmp_path):
    store = _store(tmp_path, [("9", _days(9, 2))])
    bad = store / f"forex_daily_{PAIR}" / "year=1999" / "month=12"
    bad.mkdir(parents=True)
    pl.DataFrame(
        {"timestamp": [datetime(1999, 12, 1, tzinfo=UTC)], "symbol": [PAIR], "open": [1.1],
         "high": [1.1], "low": [1.1], "close": [1.1], "volume": [1.0]},
        schema_overrides={"timestamp": pl.Datetime("us", "UTC")},
    ).write_parquet(bad / "data.parquet")
    with pytest.raises(ValueError):
        _module().load_pair(store, PAIR)


def test_load_pair_still_flags_symbol_label_mismatch(tmp_path):
    # one real store labels its rows by the quote currency only; that is recorded, not raised
    store = _store(tmp_path, [("9", _days(9, 2))], symbol="USD")
    df, labels = _module().load_pair(store, PAIR)
    assert df.height == 2
    assert labels == ["USD"]
