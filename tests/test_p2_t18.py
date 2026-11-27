"""Issue #104 tests: point-in-time ``binance_um_perp`` builds with forward funding.

Every fixture here is a synthetic lake written under ``tmp_path``: a listing catalog, one
Parquet partition per symbol per month (including a post-cutoff month that must never be
opened), and a funding feed whose settlement instants are deliberately jittered.  No market
file, network call, live registry or protected holdout is touched.

The acceptance is deliberately *behavioural*: a delisted symbol survives, the post-cutoff
partitions are never opened, a ``04:00`` settlement and an ``08:00:00.005`` settlement land
on ``00:00`` and ``08:00`` respectively with no backward lookahead, and missing funding
either fails the build or is made good by the exact audited rate.
"""

from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
import yaml

from warden.cli import main as cli_main
from warden.market_data import read_parquet_bytes
from warden_evidence.dataset_builder import (
    AdapterError,
    DatasetSpecError,
    QualityGateError,
    SourceInput,
    TemporalFenceError,
    WriteOnceError,
    build_dataset,
    crypto_perp,
    load_dataset_spec,
    parse_dataset_spec,
    run_quality_gate,
    source_adapter_names,
)
from warden_evidence.dataset_builder.containment import LocalRawFileSystem, canonical_source_path
from warden_ops.integrity import generate_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "p2_t18"
PACKAGE = REPO_ROOT / "src" / "warden_evidence" / "dataset_builder"
DATASET_ID = "synth_crypto_perp_pit_1h_funding_r1"

START = datetime(2025, 1, 1, tzinfo=UTC)
CUTOFF = datetime(2025, 1, 9, tzinfo=UTC)
DELISTED_AT = datetime(2025, 1, 5, tzinfo=UTC)
FEB_START = datetime(2025, 2, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- fixtures


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _price_series(n: int, seed: float) -> list[float]:
    closes: list[float] = []
    price = seed
    for index in range(n):
        price = price * (1.0 + 0.0002 * math.sin(index / 3.0) + 0.00005)
        closes.append(price)
    return closes


def _write_bars(
    root: Path,
    *,
    symbol: str,
    month: str,
    start: datetime,
    n: int = 744,
    seed: float = 100.0,
    volume: float = 10.0,
    freq: str = "1h",
    extra: dict[str, list[object]] | None = None,
) -> Path:
    closes = _price_series(n, seed)
    previous = closes[0]
    rows: list[dict[str, object]] = []
    for index, close in enumerate(closes):
        row: dict[str, object] = {
            "timestamp": start + timedelta(hours=index),
            "symbol": symbol,
            "open": previous,
            "high": max(previous, close) * 1.0005,
            "low": min(previous, close) * 0.9995,
            "close": close,
            "volume": volume,
        }
        for column, values in (extra or {}).items():
            row[column] = values[index]
        rows.append(row)
        previous = close
    target = root / "klines" / symbol / f"{symbol}-{freq}-{month}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    ).write_parquet(target)
    return target


def _write_funding(
    root: Path,
    *,
    symbol: str,
    month: str,
    rows: list[tuple[datetime, float]],
    column: str = "fundingRate",
) -> Path:
    target = root / "funding" / symbol / f"{symbol}-funding-{month}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "symbol": [symbol] * len(rows),
            "timestamp": [row[0] for row in rows],
            column: [row[1] for row in rows],
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC"))).write_parquet(target)
    return target


def _settlement_rows(
    days: int,
    *,
    rate_00: float = 0.0001,
    rate_04: float = 0.0002,
    rate_08: float = 0.0003,
    rate_16: float = 0.0004,
    jitter_microseconds: int = 5_000,
    first_day: datetime = START - timedelta(days=1),
) -> list[tuple[datetime, float]]:
    """One day of settlements: 00:00, 04:00, a jittered 08:00 and 16:00."""
    rows: list[tuple[datetime, float]] = []
    day = first_day
    for _ in range(days):
        rows.append((day.replace(hour=0), rate_00))
        rows.append((day.replace(hour=4), rate_04))
        rows.append((day.replace(hour=8) + timedelta(microseconds=jitter_microseconds), rate_08))
        rows.append((day.replace(hour=16), rate_16))
        day += timedelta(days=1)
    return rows


def _catalog_csv(root: Path, rows: list[tuple[str, str, str]]) -> Path:
    target = root / "raw" / "catalog.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["symbol,listed_at,delisted_at"]
    lines += [f"{symbol},{listed},{delisted}" for symbol, listed, delisted in rows]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _spec_doc(**overrides) -> dict:
    document: dict = {
        "schema_version": 1,
        "dataset_id": DATASET_ID,
        "asset_class": "crypto_perps",
        "evidence_tier": "synthetic",
        "venue": "synth_binance_um",
        "frequency": "1h",
        "price_basis": "ohlcv_funding",
        "volume_semantics": "real_contracts",
        "time_window": {
            "start_inclusive": _stamp(START),
            "exploratory_end_exclusive": _stamp(CUTOFF),
            "holdout_start_inclusive": _stamp(CUTOFF),
            "holdout_end_exclusive": _stamp(CUTOFF + timedelta(days=3)),
        },
        "time_contract": {
            "timezone": "UTC",
            "bar_timestamp_semantics": "open_left_closed_left",
            "session_calendar": "24/7",
            "daily_roll_anchor": None,
        },
        "source": {
            "kind": "lake_raw",
            "adapter": "binance_um_perp",
            "inputs": {
                "catalog": {
                    "path": "raw/catalog.csv",
                    "format": "csv",
                    "role": "symbol_catalog",
                }
            },
            "crypto": {
                "symbol_catalog": "catalog",
                "klines_root": "raw/klines",
                "funding_root": "raw/funding",
                "partition_period": "month",
                "entity_column": "symbol",
            },
        },
        "universe": {
            "selection_mode": "explicit_symbols",
            "symbols": ["AAAUSDT", "BBBUSDT"],
            "retain_delisted": True,
        },
        "resampling": {"enabled": False, "require_min_sub_bars": None},
        "quality_gate": {
            "max_abs_bar_return": 0.3,
            "mad_outlier_threshold": 12.0,
            "allow_zero_volume": False,
            "explicit_anomaly_filters": [],
            "min_coverage_ratio": 0.4,
        },
        "parquet_write": {
            "compression": "zstd",
            "compression_level": 3,
            "statistics": True,
            "row_group_size": 100000,
            "sort_keys": ["timestamp", "asset"],
        },
        "funding": {
            "settlement_interval": "8h",
            "snap_tolerance_seconds": 15,
            "missing_policy": "fail",
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(document.get(key), dict):
            document[key] = {**document[key], **value}
        else:
            document[key] = value
    return document


def _spec(**overrides):
    return parse_dataset_spec(_spec_doc(**overrides))


class RecordingLake:
    """A raw filesystem that records every open and every listing attempt."""

    def __init__(self, root: Path) -> None:
        self.inner = LocalRawFileSystem(root)
        self.reads: list[str] = []
        self.listings: list[str] = []

    def read_bytes(self, relative):
        self.reads.append(relative.as_posix())
        return self.inner.read_bytes(relative)

    def list_dir(self, relative, *, missing_ok: bool = False):
        self.listings.append(relative.as_posix())
        return self.inner.list_dir(relative, missing_ok=missing_ok)


def _standard_lake(root: Path, *, bars_for: tuple[str, ...] = ("AAAUSDT", "BBBUSDT")) -> None:
    """AAA delists mid-window; BBB trades through it; both have a post-cutoff February."""
    _catalog_csv(
        root,
        [
            ("AAAUSDT", "2024-06-01T00:00:00Z", _stamp(DELISTED_AT)),
            ("BBBUSDT", "2024-07-01T00:00:00Z", ""),
            ("CCCUSDT", "2026-02-01T00:00:00Z", ""),
        ],
    )
    raw = root / "raw"
    for symbol in bars_for:
        _write_bars(raw, symbol=symbol, month="2025-01", start=START, seed=100.0)
        _write_bars(raw, symbol=symbol, month="2025-02", start=FEB_START, seed=100.0)
        _write_funding(raw, symbol=symbol, month="2025-01", rows=_settlement_rows(days=11))
        _write_funding(raw, symbol=symbol, month="2025-02", rows=[(FEB_START, 0.0009)])


# --------------------------------------------------------------------------- 1. spec surface


def test_spec_binds_funding_to_the_crypto_lake_adapter() -> None:
    parse_dataset_spec(_spec_doc())  # the accepted shape

    no_funding = _spec_doc()
    del no_funding["funding"]
    with pytest.raises(DatasetSpecError, match="declared together"):
        parse_dataset_spec(no_funding)

    no_crypto = _spec_doc()
    no_crypto["source"].pop("crypto")
    no_crypto["source"]["adapter"] = "local_tracer"
    no_crypto["source"]["inputs"]["catalog"].pop("role")
    with pytest.raises(DatasetSpecError, match="declared together"):
        parse_dataset_spec(no_crypto)

    wrong_basis = _spec_doc(price_basis="ohlcv")
    with pytest.raises(DatasetSpecError, match="ohlcv_funding"):
        parse_dataset_spec(wrong_basis)

    wrong_class = _spec_doc(asset_class="indices")
    with pytest.raises(DatasetSpecError, match="crypto_perps"):
        parse_dataset_spec(wrong_class)

    wrong_adapter = _spec_doc()
    wrong_adapter["source"]["adapter"] = "local_tracer"
    with pytest.raises(DatasetSpecError, match="binance_um_perp"):
        parse_dataset_spec(wrong_adapter)


def test_spec_refuses_an_undeclared_or_zero_fallback_rate() -> None:
    document = _spec_doc(funding={"missing_policy": "contract_fallback"})
    with pytest.raises(DatasetSpecError, match="requires an explicit fallback_rate"):
        parse_dataset_spec(document)

    zero = _spec_doc(
        funding={"missing_policy": "contract_fallback", "fallback_rate": 0.0}
    )
    with pytest.raises(DatasetSpecError, match="silent 0.0"):
        parse_dataset_spec(zero)

    unused = _spec_doc(funding={"fallback_rate": 0.001})
    with pytest.raises(DatasetSpecError, match="only meaningful"):
        parse_dataset_spec(unused)

    accepted = parse_dataset_spec(
        _spec_doc(funding={"missing_policy": "contract_fallback", "fallback_rate": 0.0007})
    )
    assert accepted.funding is not None
    assert accepted.funding.fallback_rate == 0.0007


def test_spec_refuses_dropping_a_canonical_column_or_a_parquet_catalog() -> None:
    drop_canonical = _spec_doc()
    drop_canonical["source"]["crypto"]["ignored_columns"] = ["funding_rate"]
    with pytest.raises(DatasetSpecError, match="canonical column"):
        parse_dataset_spec(drop_canonical)

    parquet_catalog = _spec_doc()
    parquet_catalog["source"]["inputs"]["catalog"]["format"] = "parquet"
    with pytest.raises(DatasetSpecError, match="delimited or JSON"):
        parse_dataset_spec(parquet_catalog)

    escaped_root = _spec_doc()
    escaped_root["source"]["crypto"]["klines_root"] = "../lake/klines"
    with pytest.raises(DatasetSpecError, match="escapes the declared root"):
        parse_dataset_spec(escaped_root)


def test_non_crypto_spec_digests_are_unchanged_by_the_crypto_extension() -> None:
    """#104 must not re-point an already-published dataset identity."""
    # Digests captured from the wave-three tree *before* the crypto blocks existed.
    assert (
        load_dataset_spec(FIXTURES.parent / "p2_t17" / "spec_tracer.yaml").spec_sha256()
        == "a2de2871ba24b346aa8e986cf29c5fa826c56fb822c591901c3ab602ef46ffd9"
    )
    tracer = _spec_doc()
    tracer.update(
        {
            "dataset_id": "synth_indices_tracer_15m_ohlcv_r1",
            "asset_class": "indices",
            "venue": "synth_local",
            "frequency": "15m",
            "price_basis": "ohlcv",
            "time_contract": {
                "timezone": "UTC",
                "bar_timestamp_semantics": "open_left_closed_left",
                "session_calendar": "CME_Equity",
                "daily_roll_anchor": None,
            },
            "source": {
                "kind": "lake_raw",
                "adapter": "local_tracer",
                "inputs": {
                    "es": {"path": "raw/es.csv", "format": "csv"},
                    "nq": {"path": "raw/nq.csv", "format": "csv"},
                },
            },
            "universe": {
                "selection_mode": "explicit_symbols",
                "symbols": ["ES", "NQ"],
                "retain_delisted": True,
            },
            "resampling": {"enabled": False, "require_min_sub_bars": None},
            "quality_gate": {
                "max_abs_bar_return": 0.2,
                "mad_outlier_threshold": 10.0,
                "allow_zero_volume": False,
                "explicit_anomaly_filters": [],
                "min_coverage_ratio": 1.0,
            },
        }
    )
    tracer.pop("funding")
    tracer["time_window"] = {
        "start_inclusive": "2025-01-02T00:00:00Z",
        "exploratory_end_exclusive": "2025-01-02T03:00:00Z",
        "holdout_start_inclusive": "2025-01-02T03:00:00Z",
        "holdout_end_exclusive": "2025-01-02T06:00:00Z",
    }
    assert (
        parse_dataset_spec(tracer).spec_sha256()
        == "e588ad506d0aa9c30f582b7693760e9292a2a10ea981372ee3f6efbe32db55c9"
    )


def test_adapter_is_registered_without_a_call_site_import() -> None:
    assert "binance_um_perp" in source_adapter_names()


def test_local_tracer_refuses_a_role_it_does_not_consume(tmp_path: Path) -> None:
    """The spec refuses a lake role without a lake adapter; the adapter also refuses it."""
    root = tmp_path / "lake"
    _catalog_csv(root, [("AAAUSDT", "2024-06-01T00:00:00Z", "")])
    document = _spec_doc()
    document["asset_class"] = "indices"
    document["price_basis"] = "ohlcv"
    document["source"] = {
        "kind": "lake_raw",
        "adapter": "local_tracer",
        "inputs": {
            "catalog": {
                "path": "raw/catalog.csv",
                "format": "csv",
                "role": "symbol_catalog",
            }
        },
    }
    document["universe"] = {
        "selection_mode": "explicit_symbols",
        "symbols": ["AAAUSDT"],
        "retain_delisted": True,
    }
    document.pop("funding")
    with pytest.raises(DatasetSpecError, match="without a lake adapter"):
        parse_dataset_spec(document)

    # Bypassing the spec (as a hand-built object could) still does not reach the reader.
    smuggled = SourceInput.model_construct(
        path="raw/catalog.csv",
        format="csv",
        timestamp_column="timestamp",
        entity_column="asset",
        source_timezone=None,
        role="symbol_catalog",
    )
    accepted = parse_dataset_spec({**document, "source": {
        "kind": "lake_raw", "adapter": "local_tracer",
        "inputs": {"catalog": {"path": "raw/catalog.csv", "format": "csv"}},
    }})
    mutated = accepted.model_copy(
        update={
            "source": accepted.source.model_copy(
                update={"inputs": {"catalog": smuggled}}
            )
        }
    )
    with pytest.raises(AdapterError, match="local_tracer reads every declared input as OHLCV"):
        build_dataset(mutated, repo_root=tmp_path, source_root=root)


# --------------------------------------------------------------------------- 2. partitions


def test_partition_decision_is_pure_and_cutoff_first() -> None:
    windows = [
        crypto_perp.PeriodWindow("k/2024-12.parquet", datetime(2024, 12, 1, tzinfo=UTC),
                                 datetime(2025, 1, 1, tzinfo=UTC)),
        crypto_perp.PeriodWindow("k/2025-01.parquet", datetime(2025, 1, 1, tzinfo=UTC),
                                 datetime(2025, 2, 1, tzinfo=UTC)),
        crypto_perp.PeriodWindow("k/2025-02.parquet", datetime(2025, 2, 1, tzinfo=UTC),
                                 datetime(2025, 3, 1, tzinfo=UTC)),
        crypto_perp.PeriodWindow("k/2026-01.parquet", datetime(2026, 1, 1, tzinfo=UTC),
                                 datetime(2026, 2, 1, tzinfo=UTC)),
    ]
    decision = crypto_perp.decide_partitions(
        windows, earliest=START, exclusive_end=CUTOFF
    )
    assert decision.paths("opened") == ["k/2025-01.parquet"]
    assert decision.paths("skipped_before_window") == ["k/2024-12.parquet"]
    assert decision.paths("skipped_at_or_after_cutoff") == [
        "k/2025-02.parquet",
        "k/2026-01.parquet",
    ]


def test_partition_names_are_parsed_or_refused_never_guessed() -> None:
    window = crypto_perp.parse_period_window(
        canonical_source_path("k/BTCUSDT-1h-2025-03.parquet"),
        symbol="BTCUSDT",
        kind="1h",
        period="month",
    )
    assert window is not None
    assert window.start == datetime(2025, 3, 1, tzinfo=UTC)
    assert window.end == datetime(2025, 4, 1, tzinfo=UTC)

    with pytest.raises(AdapterError, match="period component"):
        crypto_perp.parse_period_window(
            canonical_source_path("k/BTCUSDT-1h-2025.parquet"),
            symbol="BTCUSDT",
            kind="1h",
            period="month",
        )
    with pytest.raises(AdapterError, match="not a Parquet file"):
        crypto_perp.parse_period_window(
            canonical_source_path("k/BTCUSDT-1h-2025-03.csv"),
            symbol="BTCUSDT",
            kind="1h",
            period="month",
        )
    # A metadata marker is ignored; a file for another symbol is not this symbol's business.
    assert (
        crypto_perp.parse_period_window(
            canonical_source_path("k/_SUCCESS"), symbol="BTCUSDT", kind="1h", period="month"
        )
        is None
    )
    assert (
        crypto_perp.parse_period_window(
            canonical_source_path("k/ETHUSDT-1h-2025-03.parquet"),
            symbol="BTCUSDT",
            kind="1h",
            period="month",
        )
        is None
    )


def test_2026_partitions_are_never_opened(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake)
    _write_bars(lake / "raw", symbol="BBBUSDT", month="2026-01",
                start=datetime(2026, 1, 1, tzinfo=UTC))
    _write_funding(
        lake / "raw",
        symbol="BBBUSDT",
        month="2026-01",
        rows=[(datetime(2026, 1, 1, tzinfo=UTC), 0.5)],
    )
    fs = RecordingLake(lake)
    repo = tmp_path / "repo"
    repo.mkdir()
    outcome = build_dataset(
        _spec(), repo_root=repo, source_root=lake, fs=fs, parquet_reader=read_parquet_bytes
    )

    opened = [path for path in fs.reads if path != "raw/catalog.csv"]
    assert opened, "the build must read something"
    assert not [path for path in fs.reads if "2025-02" in path or "2026" in path]
    assert all("2025-01" in path for path in opened)

    audit = outcome.source_audit
    assert audit["partitions"]["klines"]["BBBUSDT"]["skipped_at_or_after_cutoff"] == [
        "raw/klines/BBBUSDT/BBBUSDT-1h-2025-02.parquet",
        "raw/klines/BBBUSDT/BBBUSDT-1h-2026-01.parquet",
    ]
    assert audit["funding"]["ignored_columns"] == []
    assert pl.read_parquet(repo / outcome.dataset_path)["timestamp"].max() < CUTOFF


def test_a_mislabelled_partition_cannot_smuggle_a_post_cutoff_bar(tmp_path: Path) -> None:
    """The partition *name* decides the open; the clipped rows and the gate fence both check."""
    lake = tmp_path / "lake"
    _standard_lake(lake)
    extra = lake / "raw" / "klines" / "BBBUSDT" / "BBBUSDT-1h-2025-01.parquet"
    frame = pl.read_parquet(extra)
    # A row that belongs to March, hidden inside a file whose name says January.
    smuggled = frame.tail(1).with_columns(pl.col("timestamp") + timedelta(days=70))
    pl.concat([frame, smuggled]).write_parquet(extra)

    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    outcome = build_dataset(
        _spec(), repo_root=repo, source_root=lake, parquet_reader=read_parquet_bytes
    )
    assert outcome.source_audit["partitions"]["klines"]["BBBUSDT"][
        "rows_clipped_at_or_after_cutoff"
    ] == 553  # the 552 February-dated rows of the January file, plus the smuggled one
    panel = pl.read_parquet(repo / outcome.dataset_path)
    assert panel["timestamp"].max() < CUTOFF

    # Second line of defence: if a post-cutoff bar ever reached the gate, it fails closed.
    fenced = panel.drop("observed_sub_bars").with_columns(
        pl.when(pl.col("timestamp") == panel["timestamp"].max())
        .then(pl.lit(CUTOFF))
        .otherwise(pl.col("timestamp"))
        .alias("timestamp")
    )
    with pytest.raises(TemporalFenceError):
        run_quality_gate(fenced, _spec())


# --------------------------------------------------------------------------- 3. delisting


def test_delisted_symbol_is_retained_with_last_tradable_ts(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake)
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    outcome = build_dataset(
        _spec(),
        repo_root=repo,
        source_root=lake,
        parquet_reader=read_parquet_bytes,
    )

    manifest = outcome.manifest
    assert manifest["schema_version"] == 2
    audit = manifest["source_audit"]
    assert audit["delisted_symbols_retained"] == 1
    assert audit["universe"]["delisted_symbols_retained"] == ["AAAUSDT"]
    assert audit["universe"]["selection_mode"] == "listed_before_exploratory_end_exclusive"
    assert audit["universe"]["volume_ranked"] is False
    assert audit["universe"]["catalog_symbols_not_declared"] == ["CCCUSDT"]
    assert audit["universe"]["excluded_listed_at_or_after_cutoff"] == ["CCCUSDT"]
    # The delisted symbol's last tradable bar is recorded, not inferred from the window.
    assert audit["last_tradable_ts"]["AAAUSDT"] == "2025-01-04 23:00:00+00:00"
    assert audit["last_tradable_ts"]["BBBUSDT"] == "2025-01-08 23:00:00+00:00"
    assert audit["universe"]["rows_clipped_at_or_after_delisting"] == 648

    panel = pl.read_parquet(repo / outcome.dataset_path)
    assert panel["asset"].unique().sort().to_list() == ["AAAUSDT", "BBBUSDT"]
    assert panel.filter(pl.col("asset") == "AAAUSDT")["timestamp"].max() == DELISTED_AT - timedelta(hours=1)
    coverage = outcome.quality_gate_report["coverage"]["per_asset"]
    assert coverage["AAAUSDT"]["coverage_ratio"] == pytest.approx(0.5)
    assert coverage["AAAUSDT"]["missing_bars"] == 96
    assert coverage["BBBUSDT"]["coverage_ratio"] == pytest.approx(1.0)
    # A retained delisted symbol is not a silently incomplete one: the gate records the gap.
    assert outcome.quality_gate_report["coverage"]["enforced"] is True


def test_symbol_catalog_decides_the_universe_never_volume(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake, bars_for=("AAAUSDT", "BBBUSDT"))
    # A symbol with enormous *post-cutoff* volume and one with tiny volume both behave the
    # same way: inclusion is a listing-date fact, not a ranking.
    _catalog_csv(
        lake,
        [
            ("AAAUSDT", "2024-06-01T00:00:00Z", _stamp(DELISTED_AT)),
            ("BBBUSDT", "2024-07-01T00:00:00Z", ""),
            ("CCCUSDT", "2026-02-01T00:00:00Z", ""),
            ("DDDUSDT", "2024-01-01T00:00:00Z", ""),
        ],
    )
    _write_bars(lake / "raw", symbol="DDDUSDT", month="2025-01", start=START,
                volume=0.000001)
    _write_funding(lake / "raw", symbol="DDDUSDT", month="2025-01",
                   rows=_settlement_rows(days=11))
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)

    # DDDUSDT is listed before the cutoff and every other symbol is declared: omitting it is
    # a silent narrowing, so the adapter refuses instead of quietly building the big names.
    with pytest.raises(AdapterError, match="omits them"):
        build_dataset(
            _spec(), repo_root=repo, source_root=lake, parquet_reader=read_parquet_bytes
        )

    declared = _spec_doc()
    declared["universe"] = {
        "selection_mode": "explicit_symbols",
        "symbols": ["AAAUSDT", "BBBUSDT", "DDDUSDT"],
        "retain_delisted": True,
    }
    outcome = build_dataset(
        parse_dataset_spec(declared),
        repo_root=repo,
        source_root=lake,
        parquet_reader=read_parquet_bytes,
    )
    assert outcome.assets == ("AAAUSDT", "BBBUSDT", "DDDUSDT")


def test_universe_refuses_a_future_symbol_and_a_dropped_delisting(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake)
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)

    future = _spec_doc()
    future["universe"] = {
        "selection_mode": "explicit_symbols",
        "symbols": ["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        "retain_delisted": True,
    }
    with pytest.raises(AdapterError, match="future information"):
        build_dataset(
            parse_dataset_spec(future),
            repo_root=repo,
            source_root=lake,
            parquet_reader=read_parquet_bytes,
        )

    dropped = _spec_doc()
    dropped["universe"] = {
        "selection_mode": "explicit_symbols",
        "symbols": ["AAAUSDT", "BBBUSDT"],
        "retain_delisted": False,
    }
    with pytest.raises(AdapterError, match="retains them"):
        build_dataset(
            parse_dataset_spec(dropped),
            repo_root=repo,
            source_root=lake,
            parquet_reader=read_parquet_bytes,
        )
    assert not (repo / "data").exists()


# --------------------------------------------------------------------------- 4. funding


def test_funding_snaps_jitter_and_aggregates_strictly_forward(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake)
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    outcome = build_dataset(
        _spec(), repo_root=repo, source_root=lake, parquet_reader=read_parquet_bytes
    )
    panel = pl.read_parquet(repo / outcome.dataset_path)
    bbb = panel.filter(pl.col("asset") == "BBBUSDT").sort("timestamp")
    by_stamp = {
        row["timestamp"]: row["funding_rate"]
        for row in bbb.select(["timestamp", "funding_rate"]).iter_rows(named=True)
    }

    # Acceptance: 00:00 maps solely to 00:00 …
    assert by_stamp[START] == pytest.approx(0.0001)
    # … and 08:00 maps to 04:00 + 08:00:00.005 (the jitter is snapped onto the hour).
    assert by_stamp[START.replace(hour=8)] == pytest.approx(0.0002 + 0.0003)
    assert by_stamp[START.replace(hour=4)] == pytest.approx(0.0001 + 0.0002)
    # Each settlement is realized exactly once, at the closing bar of its own 8h bucket:
    # (08:00, 16:00] carries the 16:00 settlement alone, never the earlier ones again.
    assert by_stamp[START.replace(hour=16)] == pytest.approx(0.0004)

    audit = outcome.source_audit["funding"]
    assert audit["aggregation"] == "forward_trailing_window"
    assert audit["max_snap_seconds"] == pytest.approx(0.005)
    assert audit["settlements_without_bar"] == 16  # AAA's settlements after its delisting
    assert audit["settlements_unattributed"] == 18  # + the 16:00 settlement 8h before bar 0
    assert audit["bars_missing_settlement"] == 0
    assert audit["premium_index_close_used"] is False

    # No bar ever carries a settlement older than eight hours: the 16:00 settlement the day
    # before bar 0 is excluded rather than carried, and the first bar is 01-01 00:00.
    assert bbb["timestamp"].min() == START


def test_funding_never_looks_backwards(tmp_path: Path) -> None:
    """A backward window (truncate / asof) would put 04:00's rate on the 00:00 bar."""
    lake = tmp_path / "lake"
    _standard_lake(lake)
    # Make every settlement value distinct so a shifted assignment cannot hide.
    _write_funding(
        lake / "raw",
        symbol="BBBUSDT",
        month="2025-01",
        rows=_settlement_rows(
            days=11, rate_00=0.0001, rate_04=0.0002, rate_08=0.0003, rate_16=0.0004
        ),
    )
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    outcome = build_dataset(
        _spec(), repo_root=repo, source_root=lake, parquet_reader=read_parquet_bytes
    )
    panel = pl.read_parquet(repo / outcome.dataset_path)
    bbb = {
        row["timestamp"]: row["funding_rate"]
        for row in panel.filter(pl.col("asset") == "BBBUSDT")
        .select(["timestamp", "funding_rate"])
        .iter_rows(named=True)
    }
    # 23:00 of the day before the window opens the trailing window of the 01-01 00:00 bar:
    # only the 00:00 settlement itself may appear there.
    assert bbb[START] == pytest.approx(0.0001)
    assert bbb[START + timedelta(hours=1)] == pytest.approx(0.0001)
    assert bbb[START + timedelta(hours=3)] == pytest.approx(0.0001)
    assert bbb[START + timedelta(hours=4)] == pytest.approx(0.0003)
    assert bbb[START + timedelta(hours=7)] == pytest.approx(0.0003)
    assert bbb[START + timedelta(hours=8)] == pytest.approx(0.0005)
    assert bbb[START + timedelta(hours=9)] == pytest.approx(0.0005)


def test_settlement_outside_the_snap_tolerance_is_refused(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake)
    _write_funding(
        lake / "raw",
        symbol="BBBUSDT",
        month="2025-01",
        rows=[(START + timedelta(hours=8, seconds=16), 0.0003)],
    )
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    with pytest.raises(AdapterError, match="exact hour boundary"):
        build_dataset(
            _spec(), repo_root=repo, source_root=lake, parquet_reader=read_parquet_bytes
        )
    assert not (repo / "data").exists()


def test_missing_funding_fails_closed(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake)
    (lake / "raw" / "funding" / "BBBUSDT").rename(lake / "raw" / "funding" / "BBBUSDT-removed")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    with pytest.raises(AdapterError, match="missing funding under missing_policy='fail'"):
        build_dataset(
            _spec(), repo_root=repo, source_root=lake, parquet_reader=read_parquet_bytes
        )
    assert not (repo / "data").exists()


def test_contract_fallback_audits_the_exact_rate_and_affected_symbols(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake)
    (lake / "raw" / "funding" / "BBBUSDT").rename(lake / "raw" / "funding" / "BBBUSDT-removed")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    outcome = build_dataset(
        _spec(
            funding={
                "missing_policy": "contract_fallback",
                "fallback_rate": 0.0007,
            }
        ),
        repo_root=repo,
        source_root=lake,
        parquet_reader=read_parquet_bytes,
    )

    audit = outcome.source_audit["funding"]
    assert audit["missing_policy"] == "contract_fallback"
    assert audit["fallback_rate"] == pytest.approx(0.0007)
    assert audit["affected_symbols"] == ["BBBUSDT"]
    assert audit["affected_symbol_count"] == 1
    assert audit["fallback_rows_filled"] == 192
    assert audit["affected_rows_by_symbol"] == {"BBBUSDT": 192}
    assert audit["premium_index_close_used"] is False
    assert audit["bars_missing_settlement"] == 192
    # The substitution is the declared constant, not a zero and not the other symbol's rate.
    panel = pl.read_parquet(repo / outcome.dataset_path)
    bbb = panel.filter(pl.col("asset") == "BBBUSDT")["funding_rate"]
    assert bbb.unique().to_list() == pytest.approx([0.0007])
    aaa = set(panel.filter(pl.col("asset") == "AAAUSDT")["funding_rate"].unique().to_list())
    assert aaa and 0.0007 not in aaa


def test_premium_index_close_is_never_a_funding_substitute(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake)
    # A funding file that carries the premium index *and* no rate must not be read as funding.
    target = lake / "raw" / "funding" / "BBBUSDT" / "BBBUSDT-funding-2025-01.parquet"
    frame = pl.read_parquet(target).drop("fundingRate")
    frame.with_columns(pl.lit(0.0003).alias("premium_index_close")).write_parquet(target)
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    with pytest.raises(AdapterError, match="premium_index_close"):
        build_dataset(
            _spec(), repo_root=repo, source_root=lake, parquet_reader=read_parquet_bytes
        )

    # Declaring it as an ignored vendor column drops it instead — and then the funding gap
    # is judged by the missing-settlement policy rather than papered over with the index.
    document = _spec_doc()
    document["source"]["crypto"]["ignored_columns"] = ["premium_index_close"]
    with pytest.raises(AdapterError, match="no funding_rate"):
        build_dataset(
            parse_dataset_spec(document),
            repo_root=repo,
            source_root=lake,
            parquet_reader=read_parquet_bytes,
        )


def test_kline_partitions_may_not_smuggle_a_funding_column(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake)
    target = lake / "raw" / "klines" / "BBBUSDT" / "BBBUSDT-1h-2025-01.parquet"
    frame = pl.read_parquet(target)
    frame.with_columns(pl.lit(0.0).alias("funding_rate")).write_parquet(target)
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    with pytest.raises(AdapterError, match="must not carry funding_rate"):
        build_dataset(
            _spec(), repo_root=repo, source_root=lake, parquet_reader=read_parquet_bytes
        )


def test_funding_partition_for_another_symbol_is_refused(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake)
    # BBB's funding partition carrying AAAUSDT rows: a cross-symbol settlement feed.
    target = lake / "raw" / "funding" / "BBBUSDT" / "BBBUSDT-funding-2025-01.parquet"
    pl.read_parquet(target).with_columns(pl.lit("AAAUSDT").alias("symbol")).write_parquet(
        target
    )
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    with pytest.raises(AdapterError, match="carries rows for"):
        build_dataset(
            _spec(), repo_root=repo, source_root=lake, parquet_reader=read_parquet_bytes
        )


# --------------------------------------------------------------------------- 5. constructs


def test_the_adapter_forbids_backward_bucket_and_asof_constructs() -> None:
    """PIT funding is a property of the code: no backward truncate, no backward asof."""
    tree = ast.parse((PACKAGE / "crypto_perp.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "truncate", (
                f"crypto_perp.py:{node.lineno} truncates a timestamp into a bucket"
            )
            if node.func.attr == "join_asof":
                strategies = [
                    keyword.value.value
                    for keyword in node.keywords
                    if keyword.arg == "strategy" and isinstance(keyword.value, ast.Constant)
                ]
                assert strategies == ["forward"], (
                    f"crypto_perp.py:{node.lineno} uses an as-of strategy {strategies}; "
                    "a backward as-of carries a stale settlement"
                )
    source = (PACKAGE / "crypto_perp.py").read_text(encoding="utf-8")
    assert "premium_index_close_used" in source


def test_the_builder_package_still_reads_no_parquet_and_opens_no_socket() -> None:
    banned = {"socket", "http", "requests", "httpx", "urllib", "aiohttp"}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
                assert not (roots & banned), f"{path.name} imports {roots & banned}"
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert (node.module or "").split(".")[0] not in banned, path.name
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"read_parquet", "scan_parquet"}, (
                    f"{path.name}:{node.lineno} reads Parquet directly"
                )


# --------------------------------------------------------------------------- 6. CLI surface


def _cli_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "cli-repo"
    (repo / "contracts" / "datasets").mkdir(parents=True)
    _standard_lake(repo)
    _write_bars(repo / "raw", symbol="BBBUSDT", month="2026-01",
                start=datetime(2026, 1, 1, tzinfo=UTC))
    document = _spec_doc()
    # The spec is a protected-inventory artifact: the CLI only builds from one inside it.
    (repo / "contracts" / "datasets" / "spec_crypto_perp.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return repo


def test_cli_data_build_retains_the_delisted_symbol_and_never_opens_later_partitions(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = _cli_repo(tmp_path)
    generate_manifest(repo)  # the unsigned integrity checksum, in tmp_path only
    monkeypatch.chdir(repo)

    code = cli_main(
        ["data", "build", "contracts/datasets/spec_crypto_perp.yaml", "--json"]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "WARDEN_DATA_BUILD_COMPLETE" in out
    payload = [json.loads(line) for line in out.splitlines() if line.startswith("{")][-1]
    value = payload["value"]

    manifest = json.loads((repo / value["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["source_audit"]["delisted_symbols_retained"] >= 1
    assert manifest["source_audit"]["last_tradable_ts"]["AAAUSDT"] == "2025-01-04 23:00:00+00:00"
    opened = manifest["source_audit"]["partitions"]["klines"]["BBBUSDT"]["opened"]
    skipped = manifest["source_audit"]["partitions"]["klines"]["BBBUSDT"][
        "skipped_at_or_after_cutoff"
    ]
    assert opened == ["raw/klines/BBBUSDT/BBBUSDT-1h-2025-01.parquet"]
    assert "raw/klines/BBBUSDT/BBBUSDT-1h-2026-01.parquet" in skipped
    assert manifest["source_audit"]["funding"]["aggregation"] == "forward_trailing_window"
    # Only the catalog and the opened January partitions are committed to.
    committed = {record["path"] for record in manifest["raw_inputs"]}
    assert committed == {
        "raw/catalog.csv",
        "raw/klines/AAAUSDT/AAAUSDT-1h-2025-01.parquet",
        "raw/klines/BBBUSDT/BBBUSDT-1h-2025-01.parquet",
        "raw/funding/AAAUSDT/AAAUSDT-funding-2025-01.parquet",
        "raw/funding/BBBUSDT/BBBUSDT-funding-2025-01.parquet",
    }
    dataset = pl.read_parquet(repo / value["dataset_path"])
    assert dataset["timestamp"].max() < CUTOFF


def test_cli_data_build_survives_the_real_subprocess_path(tmp_path: Path) -> None:
    """The parser, the parquet reader wiring and the builder agree outside pytest's process."""
    repo = _cli_repo(tmp_path)
    generate_manifest(repo)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "warden.cli",
            "data",
            "build",
            "contracts/datasets/spec_crypto_perp.yaml",
            "--json",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "WARDEN_DATA_BUILD_COMPLETE" in proc.stdout
    payload = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")][-1]
    assert payload["value"]["source_audit"]["delisted_symbols_retained"] >= 1


# --------------------------------------------------------------------------- 7. catalog


def test_catalog_refusals(tmp_path: Path) -> None:
    root = tmp_path / "lake"
    root.mkdir()
    source = crypto_perp.SourceInput(path="raw/catalog.csv", format="csv", role="symbol_catalog")

    naive = root / "raw" / "naive.csv"
    naive.parent.mkdir(parents=True)
    naive.write_text("symbol,listed_at\nAAAUSDT,2024-06-01T00:00:00\n", encoding="utf-8")
    with pytest.raises(AdapterError, match="refusing to assume a zone"):
        crypto_perp.parse_symbol_catalog(naive.read_bytes(), source)

    duplicated = root / "raw" / "dup.csv"
    duplicated.write_text(
        "symbol,listed_at\nAAAUSDT,2024-06-01T00:00:00Z\nAAAUSDT,2024-07-01T00:00:00Z\n",
        encoding="utf-8",
    )
    with pytest.raises(AdapterError, match="listed twice"):
        crypto_perp.parse_symbol_catalog(duplicated.read_bytes(), source)

    inverted = root / "raw" / "inverted.csv"
    inverted.write_text(
        "symbol,listed_at,delisted_at\nAAAUSDT,2024-06-01T00:00:00Z,2024-05-01T00:00:00Z\n",
        encoding="utf-8",
    )
    with pytest.raises(AdapterError, match="delisted before it was listed"):
        crypto_perp.parse_symbol_catalog(inverted.read_bytes(), source)

    unread = root / "raw" / "extra.csv"
    unread.write_text(
        "symbol,listed_at,turnover\nAAAUSDT,2024-06-01T00:00:00Z,1e9\n", encoding="utf-8"
    )
    with pytest.raises(AdapterError, match="unread column"):
        crypto_perp.parse_symbol_catalog(unread.read_bytes(), source)


def test_committed_spec_fixture_parses_and_is_the_accepted_shape() -> None:
    spec = load_dataset_spec(FIXTURES / "spec_crypto_perp.yaml")
    assert spec.dataset_id == DATASET_ID
    assert spec.source.adapter == "binance_um_perp"
    assert spec.source.crypto is not None
    assert spec.source.crypto.partition_period == "month"
    assert spec.funding is not None
    assert spec.funding.missing_policy == "fail"
    assert spec.funding.settlement_window_hours == 8
    assert len(spec.spec_sha256()) == 64


def test_build_is_deterministic_and_write_once(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    _standard_lake(lake)
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first = build_dataset(
        _spec(), repo_root=tmp_path / "one", source_root=lake, parquet_reader=read_parquet_bytes
    )
    second = build_dataset(
        _spec(), repo_root=tmp_path / "two", source_root=lake, parquet_reader=read_parquet_bytes
    )
    assert first.parquet_sha256 == second.parquet_sha256
    assert (
        (tmp_path / "one" / first.dataset_path).read_bytes()
        == (tmp_path / "two" / second.dataset_path).read_bytes()
    )
    assert (
        (tmp_path / "one" / first.manifest_path).read_bytes()
        == (tmp_path / "two" / second.manifest_path).read_bytes()
    )
    with pytest.raises(WriteOnceError, match="write-once"):
        build_dataset(
            _spec(),
            repo_root=tmp_path / "one",
            source_root=lake,
            parquet_reader=read_parquet_bytes,
        )


def test_quality_gate_error_type_is_preserved_for_a_fenced_panel(tmp_path: Path) -> None:
    """A build that cannot satisfy the gate raises a gate error, never a bare ValueError."""
    lake = tmp_path / "lake"
    _standard_lake(lake)
    document = _spec_doc()
    document["quality_gate"]["min_coverage_ratio"] = 0.99
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(QualityGateError, match="coverage"):
        build_dataset(
            parse_dataset_spec(document),
            repo_root=repo,
            source_root=lake,
            parquet_reader=read_parquet_bytes,
        )
