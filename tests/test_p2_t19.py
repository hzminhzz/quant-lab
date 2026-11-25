"""Issue #105 tests: FX session roll, derived/composite adapters, human-only holdout seal.

Every fixture is synthetic: minute CSVs generated in ``tmp_path``, datasets built into a
``tmp_path`` repo, and a holdout directory that is a *toy* ``data/holdout`` under that tmp
repo.  No real holdout path is opened, statted or read; the protected root is only ever
named as a repository-relative string inside a temporary tree.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from scripts.mcp_warden import adapter as mcp_adapter
from warden.cli import _human_approved, build_parser, main
from warden.policy import APPROVAL, authorize
from warden_evidence.dataset_builder import (
    AdapterError,
    DatasetSpecError,
    QualityGateError,
    SessionAnchor,
    SourceContainmentError,
    WriteOnceError,
    build_dataset,
    load_dataset_spec,
    parse_daily_roll_anchor,
    parse_dataset_spec,
    seal_holdout,
    sealed_holdout_target,
    source_adapter_names,
)
from warden_evidence.dataset_builder.quality import _session_open_expr
from warden_evidence.dataset_builder.seal import CONFIRMATION_TOKEN, SealError, SealRefused

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "p2_t19"
FX_FIXTURE = FIXTURES / "fx_daily_session_roll.yaml"
FX_ID = "synth_fx_majors_1d_ohlcv_r1"
NY = ZoneInfo("America/New_York")
START = datetime(2025, 1, 6, tzinfo=UTC)

READER = staticmethod(lambda payload: pl.read_parquet(io.BytesIO(payload)))


def _read_parquet_bytes(payload: bytes) -> pl.DataFrame:
    return pl.read_parquet(io.BytesIO(payload))


# ---------------------------------------------------------------------------
# synthetic raw feeds
# ---------------------------------------------------------------------------


def _session_rows(
    sessions: list[datetime],
    asset: str,
    first_price: float,
    *,
    minutes_per_session: int = 1440,
    funding: float | None = None,
    zero_volume_at: datetime | None = None,
) -> list[dict[str, object]]:
    """One minute bar per minute of every announced session, naive UTC (Dukascopy-shaped)."""
    rows: list[dict[str, object]] = []
    price = first_price
    for index, session in enumerate(sessions):
        for minute in range(minutes_per_session):
            stamp = session + timedelta(minutes=minute)
            price = price * (1.0 + 0.00002 * math.sin((index * 1440 + minute) / 37.0) + 0.000001)
            close = price * (1.0 + 0.00001 * math.cos(minute))
            row: dict[str, object] = {
                "timestamp": stamp.replace(tzinfo=None),
                "asset": asset,
                "open": price,
                "high": max(price, close) * 1.0004,
                "low": min(price, close) * 0.9996,
                "close": close,
                "volume": 0.0 if zero_volume_at is not None and stamp == zero_volume_at else 1.0,
            }
            if funding is not None:
                row["funding_rate"] = funding
            rows.append(row)
    return rows


def _write_fx_sources(
    repo: Path,
    spec,
    *,
    funding: float | None = 0.0,
    holdout_zero_volume_at: datetime | None = None,
) -> tuple[list[datetime], list[datetime]]:
    anchor: SessionAnchor = spec.session_anchor
    exploratory = anchor.session_starts(
        spec.time_window.start_inclusive, spec.time_window.exploratory_end_exclusive
    )
    holdout = anchor.session_starts(
        spec.time_window.holdout_start_inclusive, spec.time_window.holdout_end_exclusive
    )
    (repo / "raw").mkdir(parents=True, exist_ok=True)
    for asset, price in (("EURUSD", 1.10), ("USDJPY", 150.0)):
        pl.DataFrame(_session_rows(exploratory, asset, price, funding=funding)).write_csv(
            repo / "raw" / f"{asset.lower()}_hist.csv"
        )
        pl.DataFrame(
            _session_rows(
                holdout,
                asset,
                price * 1.01,
                funding=funding,
                zero_volume_at=holdout_zero_volume_at,
            )
        ).write_csv(repo / "raw" / f"{asset.lower()}_recent.csv")
    return exploratory, holdout


def _intraday_rows(
    start: datetime,
    count: int,
    step_minutes: int,
    asset: str,
    first_price: float = 5000.0,
    *,
    funding: float | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    price = first_price
    for index in range(count):
        stamp = start + timedelta(minutes=step_minutes * index)
        price = price * (1.0 + 0.0004 * math.sin(index / 3.0) + 0.00012)
        row: dict[str, object] = {
            "timestamp": stamp.replace(tzinfo=None),
            "asset": asset,
            "open": price,
            "high": price * 1.001,
            "low": price * 0.999,
            "close": price * (1.0 + 0.0002 * math.cos(index)),
            "volume": 100.0 + index,
        }
        if funding is not None:
            row["funding_rate"] = funding
        rows.append(row)
    return rows


def _write_intraday_source(root: Path, name: str, rows: list[dict[str, object]]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.csv"
    pl.DataFrame(rows).write_csv(path)
    return path


# ---------------------------------------------------------------------------
# spec documents
# ---------------------------------------------------------------------------


def _dataset_doc(
    *,
    dataset_id: str,
    asset_class: str,
    frequency: str,
    symbols: list[str],
    source: dict[str, object],
    window: tuple[datetime, datetime, datetime, datetime],
    price_basis: str = "ohlcv",
    volume_semantics: str = "real_contracts",
    resampling: bool = False,
    min_sub_bars: int | None = None,
    allow_zero_volume: bool = False,
    calendar: str = "CME_Equity",
    anchor: str | None = None,
    min_coverage: float = 1.0,
) -> dict[str, object]:
    start, exploratory_end, holdout_start, holdout_end = window
    document: dict[str, object] = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "asset_class": asset_class,
        "evidence_tier": "synthetic",
        "venue": "synth_local",
        "frequency": frequency,
        "price_basis": price_basis,
        "volume_semantics": volume_semantics,
        "time_window": {
            "start_inclusive": start.isoformat(),
            "exploratory_end_exclusive": exploratory_end.isoformat(),
            "holdout_start_inclusive": holdout_start.isoformat(),
            "holdout_end_exclusive": holdout_end.isoformat(),
        },
        "time_contract": {
            "timezone": "UTC",
            "bar_timestamp_semantics": "open_left_closed_left",
            "session_calendar": calendar,
            "daily_roll_anchor": anchor,
        },
        "source": source,
        "universe": {
            "selection_mode": "explicit_symbols",
            "symbols": symbols,
            "retain_delisted": True,
        },
        "resampling": {"enabled": resampling, "require_min_sub_bars": min_sub_bars},
        "quality_gate": {
            "max_abs_bar_return": 0.20,
            "mad_outlier_threshold": 10.0,
            "allow_zero_volume": allow_zero_volume,
            "explicit_anomaly_filters": [],
            "min_coverage_ratio": min_coverage,
        },
        "parquet_write": {
            "compression": "zstd",
            "compression_level": 3,
            "statistics": True,
            "row_group_size": 100000,
            "sort_keys": ["timestamp", "asset"],
        },
    }
    return document


def _parent_document(dataset_id: str, symbols: list[str], *, hours: int = 12) -> dict[str, object]:
    start = datetime(2025, 1, 6, tzinfo=UTC)
    return _dataset_doc(
        dataset_id=dataset_id,
        asset_class="indices",
        frequency="15m",
        symbols=symbols,
        source={
            "kind": "lake_raw",
            "adapter": "local_tracer",
            "inputs": {
                symbol.lower(): {
                    "path": f"{symbol.lower()}.csv",
                    "format": "csv",
                    "source_timezone": "UTC",
                }
                for symbol in symbols
            },
        },
        window=(
            start,
            start + timedelta(hours=hours),
            start + timedelta(hours=hours),
            start + timedelta(hours=hours + 3),
        ),
        resampling=True,
        min_sub_bars=3,
    )


def _pinned_input(path: str, sha256: str) -> dict[str, object]:
    return {"path": path, "format": "parquet", "sha256": sha256}


class RecordingFileSystem:
    """Counts read attempts so "refused before any input I/O" is observable."""

    def __init__(self, root: Path) -> None:
        self.calls: list[str] = []

    def read_bytes(self, relative):
        self.calls.append(Path(relative).as_posix())
        raise AssertionError(f"refused path read {relative}")


# ---------------------------------------------------------------------------
# 1. the FX session clock
# ---------------------------------------------------------------------------


def test_daily_roll_anchor_parses_and_refuses_anything_else() -> None:
    anchor = parse_daily_roll_anchor("17:00 America/New_York")
    assert anchor is not None
    assert anchor.text == "17:00 America/New_York"
    assert parse_daily_roll_anchor(None) is None
    for bad in ("17:00", "5pm America/New_York", "17:00 Mars/Olympus", "24:00 UTC", ""):
        with pytest.raises(ValueError):
            parse_daily_roll_anchor(bad)


def test_committed_fx_fixture_declares_the_session_clock() -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    assert spec.session_anchor is not None
    assert spec.session_anchor.text == "17:00 America/New_York"
    assert spec.seal_source is not None and spec.seal_source.adapter == "dukascopy_local"
    assert spec.volume_semantics == "tick_count_proxy"
    # The feed spec announces the session clock to consumers without relabelling timestamps.
    assert spec.feed_spec_dict()["session_start_time"] == "17:00"
    assert spec.feed_spec_dict()["timezone"] == "UTC"
    assert spec.time_contract.model_dump(mode="json")["daily_roll_anchor"] == "17:00 America/New_York"


def test_anchor_is_daily_only_and_the_window_must_sit_on_the_session_clock() -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    document = spec.model_dump(mode="json")
    document["frequency"] = "1h"
    with pytest.raises(DatasetSpecError, match="daily session clock"):
        parse_dataset_spec(document)

    off_clock = load_dataset_spec(FX_FIXTURE).model_dump(mode="json")
    off_clock["time_window"]["start_inclusive"] = "2025-03-02T23:00:00Z"
    with pytest.raises(DatasetSpecError, match="session clock"):
        parse_dataset_spec(off_clock)


def test_daily_roll_merges_the_sunday_reopen_and_follows_dst(tmp_path: Path) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    exploratory_sessions, _ = _write_fx_sources(tmp_path, spec)
    outcome = build_dataset(spec, repo_root=tmp_path, source_root=tmp_path)

    panel = pl.read_parquet(tmp_path / outcome.dataset_path)
    local = panel["timestamp"].dt.convert_time_zone("America/New_York")

    # Every bar is labelled at the venue's session close, never at a UTC day boundary.
    assert set(zip(local.dt.hour().to_list(), local.dt.minute().to_list())) == {(17, 0)}
    # A Sun-Fri week holds five sessions, not seven UTC days: ~260 bars/year, not ~313.
    stamps = sorted(panel["timestamp"].unique().to_list())
    assert len(stamps) == 5
    assert [stamp.astimezone(NY).strftime("%a") for stamp in stamps] == [
        "Sun", "Mon", "Tue", "Wed", "Thu"
    ]
    # The window spans seven UTC days; UTC-day binning would have produced seven bars.
    utc_days = {
        (spec.time_window.start_inclusive + timedelta(days=offset)).date() for offset in range(7)
    }
    assert len(utc_days) == 7 > len(stamps)
    # DST: the same 17:00 New York open is 22:00 UTC in standard time and 21:00 UTC in summer.
    assert {stamp.hour for stamp in stamps} == {22}
    assert all(stamp == exploratory_sessions[index] for index, stamp in enumerate(stamps))

    # A Sunday-evening reopen (18:30 New York) belongs to the session that opens Sunday
    # 17:00 New York: it is binned into that bar instead of forming a two-hour Sunday bar.
    reopen = exploratory_sessions[0] + timedelta(minutes=90)
    assert reopen.astimezone(NY).strftime("%H:%M") == "18:30"
    binned = pl.DataFrame({"timestamp": [reopen]}).with_columns(
        _session_open_expr(spec.session_anchor).alias("session")
    )
    assert binned["session"][0] == exploratory_sessions[0]
    assert binned["session"][0] == panel.filter(pl.col("asset") == "EURUSD")["timestamp"][0]

    first_session = panel.filter(pl.col("asset") == "EURUSD").sort("timestamp").head(1)
    assert first_session["observed_sub_bars"][0] == 1440
    assert reopen not in set(panel["timestamp"].unique().to_list())

    report = outcome.quality_gate_report
    assert report["output_coverage"]["calendar"] == "session_starts"
    assert report["output_coverage"]["expected_bars_per_asset"] == 5
    assert report["output_coverage"]["worst_asset_ratio"] == 1.0
    assert report["time"]["daily_roll_anchor"] == "17:00 America/New_York"
    assert report["time"]["window_role"] == "exploratory"
    assert report["resampling"]["grouping"] == "session_close"


def test_dst_switch_moves_the_session_label_by_one_utc_hour(tmp_path: Path) -> None:
    """The week that crosses the US spring-forward switch keeps one 17:00 New York open."""
    spec = load_dataset_spec(FX_FIXTURE)
    _write_fx_sources(tmp_path, spec)
    outcome = build_dataset(spec, repo_root=tmp_path, source_root=tmp_path)
    exploratory = pl.read_parquet(tmp_path / outcome.dataset_path)
    sealed_window = spec.session_anchor.session_starts(
        spec.time_window.holdout_start_inclusive, spec.time_window.holdout_end_exclusive
    )
    # Before the switch: 22:00 UTC (EST).  After it: 21:00 UTC (EDT).  A fixed offset or a
    # UTC-day binning cannot produce both.
    assert {stamp.hour for stamp in exploratory["timestamp"].unique().to_list()} == {22}
    assert {stamp.hour for stamp in sealed_window} == {21}
    assert all(stamp.astimezone(NY).hour == 17 for stamp in sealed_window)


def test_tick_count_volume_and_non_crypto_funding_omission(tmp_path: Path) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    _write_fx_sources(tmp_path, spec, funding=0.0)
    outcome = build_dataset(spec, repo_root=tmp_path, source_root=tmp_path)

    panel = pl.read_parquet(tmp_path / outcome.dataset_path)
    assert "funding_rate" not in panel.columns
    assert panel["volume"].unique().to_list() == [1440.0]  # one tick per minute, not lots
    summary = outcome.manifest["dataset"]["source_summary"]
    assert summary["volume_semantics"] == "tick_count_proxy"
    dropped = summary["dropped_columns"]
    assert [item["column"] for item in dropped] == ["funding_rate"]
    assert dropped[0]["rows"] == 2 * 5 * 1440
    assert outcome.manifest["dataset"]["volume_semantics"] == "tick_count_proxy"

    # A crypto feed keeps its funding column: the omission is non-crypto specific.
    crypto = tmp_path / "crypto"
    (crypto / "raw").mkdir(parents=True)
    start = datetime(2025, 1, 6, tzinfo=UTC)
    window = (start, start + timedelta(days=2), start + timedelta(days=2), start + timedelta(days=3))
    for asset in ("EURUSD", "USDJPY"):
        for tag, first, count in (("hist", start, 2 * 1440), ("recent", start + timedelta(days=2), 1440)):
            pl.DataFrame(
                _intraday_rows(first, count, 1, asset, 25000.0, funding=0.0001)
            ).write_csv(crypto / "raw" / f"{asset.lower()}_{tag}.csv")
    crypto_spec = parse_dataset_spec(
        _dataset_doc(
            dataset_id="synth_crypto_perps_1d_ohlcv_r1",
            asset_class="crypto_perps",
            frequency="1d",
            symbols=["EURUSD", "USDJPY"],
            source={
                "kind": "lake_raw",
                "adapter": "dukascopy_local",
                "inputs": {
                    asset.lower(): {
                        "path": f"raw/{asset.lower()}_hist.csv",
                        "format": "csv",
                        "source_timezone": "UTC",
                    }
                    for asset in ("EURUSD", "USDJPY")
                },
            },
            window=window,
            price_basis="ohlcv_funding",
            volume_semantics="tick_count_proxy",
            resampling=True,
            min_sub_bars=1400,
            calendar="CRYPTO_24_7",
        )
        | {
            "seal_source": {
                "kind": "lake_raw",
                "adapter": "dukascopy_local",
                "inputs": {
                    asset.lower(): {
                        "path": f"raw/{asset.lower()}_recent.csv",
                        "format": "csv",
                        "source_timezone": "UTC",
                    }
                    for asset in ("EURUSD", "USDJPY")
                },
            }
        }
    )
    crypto_out = build_dataset(crypto_spec, repo_root=crypto, source_root=crypto)
    crypto_panel = pl.read_parquet(crypto / crypto_out.dataset_path)
    assert "funding_rate" in crypto_panel.columns
    assert crypto_panel["funding_rate"].unique().to_list() == [0.0001]
    assert "dropped_columns" not in crypto_out.manifest["dataset"]["source_summary"]


def test_dukascopy_adapter_refuses_declarations_a_vendor_feed_cannot_support(tmp_path: Path) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    _write_fx_sources(tmp_path, spec)

    funding_basis = spec.model_dump(mode="json")
    funding_basis["price_basis"] = "ohlcv_funding"
    with pytest.raises(AdapterError, match="no funding rate"):
        build_dataset(parse_dataset_spec(funding_basis), repo_root=tmp_path, source_root=tmp_path)

    contracts = spec.model_dump(mode="json")
    contracts["volume_semantics"] = "real_contracts"
    with pytest.raises(AdapterError, match="tick counts"):
        build_dataset(parse_dataset_spec(contracts), repo_root=tmp_path, source_root=tmp_path)

    no_anchor = spec.model_dump(mode="json")
    no_anchor["time_contract"]["daily_roll_anchor"] = None
    # Dropping the anchor while keeping the session-clock bounds is refused by the spec
    # itself, before any adapter can run; the adapter's own refusal is proven on a
    # UTC-aligned window below.
    with pytest.raises(DatasetSpecError, match="UTC bar grid"):
        parse_dataset_spec(no_anchor)

    start = datetime(2025, 1, 6, tzinfo=UTC)
    unanchored = parse_dataset_spec(
        _dataset_doc(
            dataset_id="synth_fx_unanchored_1d_ohlcv_r1",
            asset_class="fx",
            frequency="1d",
            symbols=["EURUSD"],
            source={
                "kind": "lake_raw",
                "adapter": "dukascopy_local",
                "inputs": {
                    "eurusd": {
                        "path": "raw/eurusd_hist.csv",
                        "format": "csv",
                        "source_timezone": "UTC",
                    }
                },
            },
            window=(
                start,
                start + timedelta(days=2),
                start + timedelta(days=2),
                start + timedelta(days=3),
            ),
            volume_semantics="tick_count_proxy",
            resampling=True,
            min_sub_bars=1400,
            anchor=None,
        )
    )
    with pytest.raises(AdapterError, match="daily_roll_anchor"):
        build_dataset(unanchored, repo_root=tmp_path, source_root=tmp_path)


def test_a_source_may_not_name_the_sealed_root_even_as_a_seal_source() -> None:
    document = load_dataset_spec(FX_FIXTURE).model_dump(mode="json")
    document["seal_source"]["inputs"]["eurusd"]["path"] = "data/holdout/reuse.csv"
    with pytest.raises(DatasetSpecError, match="protected path"):
        parse_dataset_spec(document)


def test_derived_and_composite_kinds_must_be_fully_pinned() -> None:
    parent = _parent_document("synth_parent_15m_ohlcv_r1", ["ES"])
    digest = "a" * 64

    unpinned = dict(parent)
    unpinned["source"] = {
        "kind": "derived_from_dataset",
        "adapter": "derived_from_dataset",
        "inputs": {"parent": {"path": "data/exploratory/p.parquet", "format": "parquet"}},
    }
    with pytest.raises(DatasetSpecError, match="sha256 pin"):
        parse_dataset_spec(unpinned)

    csv_parent = dict(parent)
    csv_parent["source"] = {
        "kind": "derived_from_dataset",
        "adapter": "derived_from_dataset",
        "inputs": {"parent": _pinned_input("data/exploratory/p.csv", digest) | {"format": "csv"}},
    }
    with pytest.raises(DatasetSpecError, match="format 'parquet'"):
        parse_dataset_spec(csv_parent)

    wrong_adapter = dict(parent)
    wrong_adapter["source"] = {
        "kind": "derived_from_dataset",
        "adapter": "local_tracer",
        "inputs": {"parent": _pinned_input("data/exploratory/p.parquet", digest)},
    }
    with pytest.raises(DatasetSpecError, match="requires adapter"):
        parse_dataset_spec(wrong_adapter)

    single_component = dict(parent)
    single_component["source"] = {
        "kind": "composite_union",
        "adapter": "composite_union",
        "inputs": {"only": _pinned_input("data/exploratory/p.parquet", digest)},
    }
    with pytest.raises(DatasetSpecError, match="at least two component datasets"):
        parse_dataset_spec(single_component)

    raw_pin = dict(parent)
    raw_pin["source"] = {
        "kind": "lake_raw",
        "adapter": "local_tracer",
        "inputs": {"es": _pinned_input("src/es.csv", digest)},
    }
    with pytest.raises(DatasetSpecError, match="declare sha256 under kind"):
        parse_dataset_spec(raw_pin)

    assert {"dukascopy_local", "derived_from_dataset", "composite_union"} <= set(
        source_adapter_names()
    )


# ---------------------------------------------------------------------------
# 2. derived / composite adapters
# ---------------------------------------------------------------------------


def _build_parent(tmp_path: Path, dataset_id: str, symbols: list[str]) -> tuple[Path, dict]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    repository = tmp_path / "source"
    for symbol in symbols:
        # 12 hours of 5-minute bars -> 48 bars at the parent's 15-minute frequency.
        _write_intraday_source(
            repository,
            symbol.lower(),
            _intraday_rows(START, 12 * 12, 5, symbol, 5000.0 if symbol == "ES" else 15000.0),
        )
    spec = parse_dataset_spec(_parent_document(dataset_id, symbols))
    outcome = build_dataset(spec, repo_root=repo, source_root=repository)
    return repo, {"spec": spec, "outcome": outcome, "document": _parent_document(dataset_id, symbols)}


def _derived_document(parent_path: str, parent_sha256: str) -> dict[str, object]:
    start = datetime(2025, 1, 6, tzinfo=UTC)
    return _dataset_doc(
        dataset_id="synth_derived_1h_ohlcv_r1",
        asset_class="indices",
        frequency="1h",
        symbols=["ES"],
        source={
            "kind": "derived_from_dataset",
            "adapter": "derived_from_dataset",
            "inputs": {"parent": _pinned_input(parent_path, parent_sha256)},
        },
        window=(
            start,
            start + timedelta(hours=12),
            start + timedelta(hours=12),
            start + timedelta(hours=15),
        ),
        resampling=True,
        min_sub_bars=4,  # four 15-minute parent bars per 1-hour bin
    )


def test_derived_from_dataset_verifies_the_parent_digest_before_resampling(tmp_path: Path) -> None:
    repo, parent = _build_parent(tmp_path, "synth_parent_15m_ohlcv_r1", ["ES"])
    parent_path = parent["outcome"].dataset_path
    parent_sha = parent["outcome"].parquet_sha256
    assert parent_sha == hashlib.sha256((repo / parent_path).read_bytes()).hexdigest()

    derived = parse_dataset_spec(_derived_document(parent_path, parent_sha))
    outcome = build_dataset(
        derived, repo_root=repo, source_root=repo, parquet_reader=_read_parquet_bytes
    )
    assert outcome.rows == 12  # 144 x 5m bars -> 48 x 15m parent bars -> 12 x 1h bars
    panel = pl.read_parquet(repo / outcome.dataset_path)
    # The parent's own ``observed_sub_bars`` audit column is dropped and re-derived: four
    # 15-minute parent bars per 1-hour bin, not the parent's three 5-minute sub-bars.
    assert panel["observed_sub_bars"].unique().to_list() == [4]
    assert panel["asset"].unique().to_list() == ["ES"]

    lineage = outcome.manifest["lineage"]
    assert lineage["kind"] == "derived_from_dataset"
    assert lineage["parents"] == [
        {
            "key": "parent",
            "path": parent_path,
            "sha256": parent_sha,
            "verified": True,
            "rows": parent["outcome"].rows,
            "first_bar": parent["outcome"].first_bar,
            "last_bar": parent["outcome"].last_bar,
            "assets": ["ES"],
        }
    ]
    assert outcome.manifest["dataset"]["time_contract"]["daily_roll_anchor"] is None
    # The commitment is over the parent bytes that were verified, not over a path list.
    expected_merkle, _ = __import__(
        "warden_evidence.dataset_builder.merkle", fromlist=["merkle_sha256"]
    ).merkle_sha256([(parent_path, (repo / parent_path).read_bytes())])
    assert outcome.manifest["raw_inputs_merkle_sha256"] == expected_merkle


def test_a_tampered_parent_is_refused_at_the_pin_not_at_the_parser(tmp_path: Path) -> None:
    repo, parent = _build_parent(tmp_path, "synth_parent_15m_ohlcv_r1", ["ES"])
    parent_path = parent["outcome"].dataset_path
    parent_sha = parent["outcome"].parquet_sha256
    target = repo / parent_path

    original = target.read_bytes()
    target.write_bytes(b"not a parquet file")
    derived = parse_dataset_spec(_derived_document(parent_path, parent_sha))
    with pytest.raises(AdapterError, match="does not match the declared pin"):
        build_dataset(derived, repo_root=repo, source_root=repo, parquet_reader=_read_parquet_bytes)
    assert not (repo / "data" / "exploratory" / "synth_derived_1h_ohlcv_r1.parquet").exists()
    assert not (repo / "contracts" / "datasets" / "synth_derived_1h_ohlcv_r1.json").exists()

    # A pin that matches the tampered bytes reaches the parser and fails there instead.
    garbage_sha = hashlib.sha256(b"not a parquet file").hexdigest()
    repinned = parse_dataset_spec(_derived_document(parent_path, garbage_sha))
    with pytest.raises(AdapterError, match="cannot parse pinned Parquet dataset"):
        build_dataset(repinned, repo_root=repo, source_root=repo, parquet_reader=_read_parquet_bytes)
    target.write_bytes(original)


def test_derived_refuses_a_parent_coarser_than_the_declared_frequency(tmp_path: Path) -> None:
    repo, parent = _build_parent(tmp_path, "synth_parent_15m_ohlcv_r1", ["ES"])
    document = _derived_document(parent["outcome"].dataset_path, parent["outcome"].parquet_sha256)
    document["frequency"] = "5m"
    document["resampling"] = {"enabled": True, "require_min_sub_bars": 1}
    with pytest.raises(AdapterError, match="coarser than the declared"):
        build_dataset(
            parse_dataset_spec(document),
            repo_root=repo,
            source_root=repo,
            parquet_reader=_read_parquet_bytes,
        )


def test_composite_union_verifies_every_component_and_refuses_asset_overlap(tmp_path: Path) -> None:
    repo, es = _build_parent(tmp_path, "synth_es_15m_ohlcv_r1", ["ES"])
    _, nq = _build_parent(tmp_path, "synth_nq_15m_ohlcv_r1", ["NQ"])
    start = datetime(2025, 1, 6, tzinfo=UTC)
    document = _dataset_doc(
        dataset_id="synth_panel_15m_ohlcv_r1",
        asset_class="multi_asset",
        frequency="15m",
        symbols=["ES", "NQ"],
        source={
            "kind": "composite_union",
            "adapter": "composite_union",
            "inputs": {
                "es": _pinned_input(es["outcome"].dataset_path, es["outcome"].parquet_sha256),
                "nq": _pinned_input(nq["outcome"].dataset_path, nq["outcome"].parquet_sha256),
            },
        },
        window=(
            start,
            start + timedelta(hours=12),
            start + timedelta(hours=12),
            start + timedelta(hours=15),
        ),
        resampling=False,
    )
    outcome = build_dataset(
        parse_dataset_spec(document), repo_root=repo, source_root=repo, parquet_reader=_read_parquet_bytes
    )
    panel = pl.read_parquet(repo / outcome.dataset_path)
    assert sorted(panel["asset"].unique().to_list()) == ["ES", "NQ"]
    assert outcome.rows == 2 * 48
    lineage = outcome.manifest["lineage"]
    assert lineage["kind"] == "composite_union"
    assert [item["key"] for item in lineage["parents"]] == ["es", "nq"]
    assert [item["sha256"] for item in lineage["parents"]] == [
        es["outcome"].parquet_sha256,
        nq["outcome"].parquet_sha256,
    ]
    assert all(item["verified"] is True for item in lineage["parents"])

    # One wrong pin refuses the whole union: every component is verified.
    tampered = {key: dict(value) for key, value in document["source"]["inputs"].items()}
    tampered["nq"] = dict(tampered["nq"], sha256="b" * 64)
    overlapping = dict(
        document,
        dataset_id="synth_panel_badpin_15m_ohlcv_r1",
        source=dict(document["source"], inputs=tampered),
    )
    with pytest.raises(AdapterError, match="does not match the declared pin"):
        build_dataset(
            parse_dataset_spec(overlapping),
            repo_root=repo,
            source_root=repo,
            parquet_reader=_read_parquet_bytes,
        )

    # Two components that both carry ES would duplicate every bar of that asset.
    both, both_outcome = _build_parent(tmp_path / "both", "synth_both_15m_ohlcv_r1", ["ES", "NQ"])
    clash = dict(
        document,
        dataset_id="synth_panel_clash_15m_ohlcv_r1",
        source=dict(
            document["source"],
            inputs={
                "es": _pinned_input(es["outcome"].dataset_path, es["outcome"].parquet_sha256),
                "both": _pinned_input(both_outcome["outcome"].dataset_path, both_outcome["outcome"].parquet_sha256),
            },
        ),
        universe={"selection_mode": "explicit_symbols", "symbols": ["ES", "NQ"], "retain_delisted": True},
    )
    (repo / "data" / "exploratory" / "synth_both_15m_ohlcv_r1.parquet").write_bytes(
        (both / both_outcome["outcome"].dataset_path).read_bytes()
    )
    with pytest.raises(AdapterError, match="both carry asset"):
        build_dataset(
            parse_dataset_spec(clash), repo_root=repo, source_root=repo, parquet_reader=_read_parquet_bytes
        )


# ---------------------------------------------------------------------------
# 3. the human-only seal
# ---------------------------------------------------------------------------


def _built_fx_repo(tmp_path: Path, spec) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fx_sources(repo, spec)
    build_dataset(spec, repo_root=repo, source_root=repo)
    return repo


def test_seal_refuses_non_interactive_and_missing_token_before_any_read(
    tmp_path: Path, monkeypatch
) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    repo = _built_fx_repo(tmp_path, spec)
    fs = RecordingFileSystem(repo)

    monkeypatch.setenv("WARDEN_NON_INTERACTIVE", "1")
    with pytest.raises(SealRefused, match="interactive human-only gate"):
        seal_holdout(spec, repo_root=repo, confirmation=CONFIRMATION_TOKEN, fs=fs)
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE")

    for bad in ("", "yes", "YES_SEAL_HOLDOUT ", "YES_UNSEAL_HOLDOUT"):
        with pytest.raises(SealRefused, match="confirmation token"):
            seal_holdout(spec, repo_root=repo, confirmation=bad, fs=fs)
    assert fs.calls == []  # nothing was read, not even a stat of the source root
    assert not (repo / "data" / "holdout").exists()
    manifest = json.loads((repo / "contracts" / "datasets" / f"{FX_ID}.json").read_text())
    assert manifest["holdout"] == {"sealed": False}


def test_seal_requires_the_build_it_extends(tmp_path: Path) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fx_sources(repo, spec)
    fs = RecordingFileSystem(repo)
    with pytest.raises(SealError, match="no v2 dataset manifest"):
        seal_holdout(spec, repo_root=repo, confirmation=CONFIRMATION_TOKEN, fs=fs)
    assert fs.calls == []


def test_seal_refuses_a_tampered_exploratory_dataset_or_a_foreign_spec(tmp_path: Path) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    repo = _built_fx_repo(tmp_path, spec)
    built = json.loads((repo / "contracts" / "datasets" / f"{FX_ID}.json").read_text())
    target = repo / built["path"]
    original = target.read_bytes()
    target.write_bytes(original[:-8])
    fs = RecordingFileSystem(repo)
    with pytest.raises(SealError, match="does not match the manifest pin"):
        seal_holdout(spec, repo_root=repo, confirmation=CONFIRMATION_TOKEN, fs=fs)
    assert fs.calls == []
    target.write_bytes(original)

    changed = spec.model_dump(mode="json")
    changed["quality_gate"]["max_abs_bar_return"] = 0.19
    with pytest.raises(SealError, match="produced by spec"):
        seal_holdout(
            parse_dataset_spec(changed), repo_root=repo, confirmation=CONFIRMATION_TOKEN, fs=fs
        )
    assert fs.calls == []


def test_seal_runs_the_same_gate_over_the_holdout_window(tmp_path: Path) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    repo = _built_fx_repo(tmp_path, spec)
    before = (repo / "contracts" / "datasets" / f"{FX_ID}.json").read_bytes()
    built = json.loads(before)

    outcome = seal_holdout(
        spec, repo_root=repo, confirmation=CONFIRMATION_TOKEN, parquet_reader=_read_parquet_bytes
    )

    sealed_path = repo / "data" / "holdout" / f"{FX_ID}.parquet"
    assert sealed_path.is_file()
    digest = hashlib.sha256(sealed_path.read_bytes()).hexdigest()
    assert outcome.holdout_sha256 == digest
    panel = pl.read_parquet(sealed_path)
    assert panel["timestamp"].min() >= spec.time_window.holdout_start_inclusive
    assert panel["timestamp"].max() < spec.time_window.holdout_end_exclusive
    assert sorted(panel["asset"].unique().to_list()) == ["EURUSD", "USDJPY"]
    assert len(panel["timestamp"].unique()) == 5  # five sessions, five bars

    report = outcome.quality_gate_report
    assert report["window_role"] == "holdout"
    assert report["time"]["window_role"] == "holdout"
    assert report["coverage"]["window_role"] == "holdout"
    assert report["coverage"]["calendar"] == "session_starts"
    assert report["coverage"]["expected_bars_per_asset"] == 5
    assert report["coverage"]["worst_asset_ratio"] == 1.0
    assert report["validator"]["library"] == "ml4t.data.validation.OHLCVValidator"
    assert report["validator"]["stages"] == ["pre-resample", "post-resample"]
    assert report["anomalies"]["mad"]["method"] == "mad"
    assert report["rows"]["unexplained"] == 0
    assert report["resampling"]["grouping"] == "session_close"

    manifest = json.loads((repo / "contracts" / "datasets" / f"{FX_ID}.json").read_text())
    assert manifest["schema_version"] == 2
    assert manifest["holdout"]["sealed"] is True
    assert manifest["holdout"]["sha256"] == digest
    assert manifest["holdout_sha256"] == manifest["holdout"]["sha256"]
    assert manifest["holdout"]["path"] == f"data/holdout/{FX_ID}.parquet"
    assert manifest["holdout"]["quality_gate_report"]["window_role"] == "holdout"
    assert manifest["holdout"]["window"] == {
        "start_inclusive": spec.time_window.holdout_start_inclusive.isoformat(),
        "end_exclusive": spec.time_window.holdout_end_exclusive.isoformat(),
        "window_role": "holdout",
    }
    # The exploratory side is untouched by the seal.
    assert manifest["sha256"] == built["sha256"]
    assert manifest["path"] == built["path"]
    assert manifest["quality_gate_report"] == built["quality_gate_report"]
    assert manifest["provenance"] == "synthetic"  # a seal never promotes provenance

    # Zero staging: exactly one file under the sealed root, no temporary beside the manifest.
    assert [path.name for path in (repo / "data" / "holdout").iterdir()] == [f"{FX_ID}.parquet"]
    assert [path.name for path in (repo / "contracts" / "datasets").iterdir()] == [f"{FX_ID}.json"]
    leftovers = [
        path.relative_to(repo).as_posix()
        for path in repo.rglob("*")
        if any(token in path.name for token in ("staging", "sealing", ".tmp"))
    ]
    assert leftovers == []


def test_seal_is_write_once_and_uses_the_same_gate_as_the_build(tmp_path: Path) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    repo = _built_fx_repo(tmp_path, spec)
    seal_holdout(
        spec, repo_root=repo, confirmation=CONFIRMATION_TOKEN, parquet_reader=_read_parquet_bytes
    )
    sealed_before = (repo / "data" / "holdout" / f"{FX_ID}.parquet").read_bytes()
    manifest_before = (repo / "contracts" / "datasets" / f"{FX_ID}.json").read_bytes()
    with pytest.raises(WriteOnceError, match="already has a sealed holdout"):
        seal_holdout(
            spec, repo_root=repo, confirmation=CONFIRMATION_TOKEN, parquet_reader=_read_parquet_bytes
        )
    assert (repo / "data" / "holdout" / f"{FX_ID}.parquet").read_bytes() == sealed_before
    assert (repo / "contracts" / "datasets" / f"{FX_ID}.json").read_bytes() == manifest_before


def test_quality_parity_the_seal_refuses_exactly_what_the_build_refuses(tmp_path: Path) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    # A zero-volume minute in the sealed window, with allow_zero_volume false.
    repo = tmp_path / "parse"
    repo.mkdir()
    _write_fx_sources(repo, spec, holdout_zero_volume_at=spec.time_window.holdout_start_inclusive)
    build_dataset(spec, repo_root=repo, source_root=repo)
    manifest_before = (repo / "contracts" / "datasets" / f"{FX_ID}.json").read_bytes()
    with pytest.raises(QualityGateError, match="zero-volume rows by asset"):
        seal_holdout(
            spec, repo_root=repo, confirmation=CONFIRMATION_TOKEN, parquet_reader=_read_parquet_bytes
        )
    assert not (repo / "data" / "holdout").exists()
    assert (repo / "contracts" / "datasets" / f"{FX_ID}.json").read_bytes() == manifest_before

    # The exploratory build reports the identical defect with the identical message.
    broken = tmp_path / "broken"
    broken.mkdir()
    _write_fx_sources(broken, spec, holdout_zero_volume_at=None)
    source = broken / "raw" / "eurusd_hist.csv"
    frame = pl.read_csv(source)
    # A zero-volume *exploratory* row: same gate, same sentence.
    first_stamp = frame["timestamp"][0]
    frame = frame.with_columns(
        pl.when(pl.col("timestamp") == first_stamp).then(pl.lit(0.0)).otherwise(pl.col("volume")).alias("volume")
    )
    frame.write_csv(source)
    with pytest.raises(QualityGateError, match="zero-volume rows by asset"):
        build_dataset(spec, repo_root=broken, source_root=broken)


def test_seal_refuses_a_fence_crossing_source_and_a_foreign_window(tmp_path: Path) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    repo = _built_fx_repo(tmp_path, spec)
    # A holdout source that also carries pre-holdout bars is refused by the same fence.
    sessions = spec.session_anchor.session_starts(
        spec.time_window.holdout_start_inclusive, spec.time_window.holdout_end_exclusive
    )
    one_session_earlier = spec.time_window.holdout_start_inclusive - timedelta(days=1)
    pl.DataFrame(
        _session_rows([one_session_earlier, *sessions], "EURUSD", 1.11, funding=0.0)
    ).write_csv(repo / "raw" / "eurusd_recent.csv")
    with pytest.raises(QualityGateError, match="before holdout_start"):
        seal_holdout(
            spec, repo_root=repo, confirmation=CONFIRMATION_TOKEN, parquet_reader=_read_parquet_bytes
        )
    assert not (repo / "data" / "holdout").exists()

    with pytest.raises(SealError, match="only seals the holdout window"):
        seal_holdout(
            spec,
            repo_root=repo,
            confirmation=CONFIRMATION_TOKEN,
            window_role="exploratory",
            parquet_reader=_read_parquet_bytes,
        )


def test_sealed_target_is_exactly_the_holdout_root(tmp_path: Path) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    relative, absolute = sealed_holdout_target(tmp_path, FX_ID)
    assert relative.as_posix() == f"data/holdout/{FX_ID}.parquet"
    assert absolute == tmp_path.resolve() / "data" / "holdout" / f"{FX_ID}.parquet"
    for bad in ("data/exploratory", "data/holdout/nested", "data/Holdout", "/data/holdout", "../data/holdout"):
        with pytest.raises((SourceContainmentError, SealError)):
            sealed_holdout_target(tmp_path, FX_ID, holdout_dir=bad)
    with pytest.raises(SealError, match="not a valid dataset identifier"):
        sealed_holdout_target(tmp_path, "../../etc/passwd")

    repo = _built_fx_repo(tmp_path, spec)
    with pytest.raises(SourceContainmentError, match="sealed holdout root"):
        seal_holdout(
            spec,
            repo_root=repo,
            confirmation=CONFIRMATION_TOKEN,
            holdout_dir="data/exploratory",
            parquet_reader=_read_parquet_bytes,
        )
    assert not (repo / "data" / "holdout").exists()


# ---------------------------------------------------------------------------
# 4. policy, CLI and MCP surface
# ---------------------------------------------------------------------------


def test_seal_is_a_policy_approval_command_and_build_is_not() -> None:
    assert "data seal-holdout" in APPROVAL
    decision = authorize("data seal-holdout")
    assert decision.allow is True and decision.approval_required is True
    build = authorize("data build")
    assert build.allow is True and build.approval_required is False
    assert authorize("data").allow is True
    assert authorize("data seal").allow is False
    assert authorize("data seal-holdout --force").allow is False

    flags = argparse.Namespace(subcommand="data", action="seal-holdout", seal_holdout=True)
    assert _human_approved(flags, "data seal-holdout") is True
    assert _human_approved(argparse.Namespace(subcommand="data", action="seal-holdout"), "data seal-holdout") is False
    assert _human_approved(argparse.Namespace(subcommand="data", action="build"), "data build") is False


def test_cli_refuses_seal_without_the_human_flag_and_creates_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    spec = load_dataset_spec(FX_FIXTURE)
    repo = _built_fx_repo(tmp_path, spec)
    manifest_before = (repo / "contracts" / "datasets" / f"{FX_ID}.json").read_bytes()
    monkeypatch.chdir(repo)

    code = main(["data", "seal-holdout", "tests/p2_t19.spec.yaml"])
    assert code == 2
    stderr = capsys.readouterr().err
    assert "REFUSED" in stderr and "human-only gate" in stderr
    assert not (repo / "data" / "holdout").exists()
    assert (repo / "contracts" / "datasets" / f"{FX_ID}.json").read_bytes() == manifest_before

    # Even with the flag the CLI is stopped by the worktree gate first: a tree this seal
    # changed is not a signed inventory, so the human re-signs before any seal can run.
    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "data",
                "seal-holdout",
                "contracts/datasets/fx.yaml",
                "--seal-holdout",
                "--human-confirm-seal",
                CONFIRMATION_TOKEN,
            ]
        )
    assert exit_info.value.code == 2
    assert not (repo / "data" / "holdout").exists()


def test_mcp_surface_gains_no_data_or_seal_authority() -> None:
    assert "data" not in mcp_adapter.COMMANDS
    assert set(mcp_adapter.COMMANDS) == {
        "status", "check-worktree", "spec-status", "register-hypothesis", "verify", "screen",
        "backtest", "advance", "audit-check", "verify-causality", "attempt-status", "handoff",
        "continue",
    }
    with pytest.raises(mcp_adapter.RestrictedCommandError):
        mcp_adapter.build_argv("data", ["contracts/datasets/fx.yaml"])
    with pytest.raises(mcp_adapter.RestrictedCommandError):
        mcp_adapter.build_argv("data seal-holdout", ["contracts/datasets/fx.yaml"])

    refused = mcp_adapter._handle(
        {
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "name": "warden_exec",
                "arguments": {"command": "data", "positional": ["contracts/datasets/fx.yaml"]},
            },
        }
    )
    body = json.loads(refused["result"]["content"][0]["text"])
    assert body["status"] == "REFUSED" and "REFUSED" in refused["result"]["content"][0]["text"]
    listed = mcp_adapter._handle({"jsonrpc": "2.0", "id": 22, "method": "tools/list"})
    schema = next(tool for tool in listed["result"]["tools"] if tool["name"] == "warden_exec")
    assert "data" not in schema["inputSchema"]["properties"]["command"]["enum"]

    # The CLI still exposes both data actions, so the refusal above is MCP policy, not a
    # missing command.
    parser = build_parser()
    actions = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ).choices["data"]
    data_actions = next(
        action for action in actions._actions if isinstance(action, argparse._SubParsersAction)
    ).choices
    assert {"build", "seal-holdout", "fence"} == set(data_actions)
