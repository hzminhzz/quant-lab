"""Issue #103 tests: spec-driven dataset build, mandatory quality gate, v2 manifest.

Every fixture is synthetic (CSV/Parquet/SQLite in ``tmp_path``); no market file,
network call, production registry or manifest is touched.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from warden.contracts import EvaluationContract
from warden.market_data import MarketDataGateway
from warden_evidence.dataset_builder import (
    AdapterError,
    DatasetBuildError,
    DatasetSpecError,
    LocalRawFileSystem,
    QualityGateError,
    SourceContainmentError,
    SourceInput,
    TemporalFenceError,
    WriteOnceError,
    build_dataset,
    canonical_source_path,
    load_dataset_spec,
    merkle_sha256,
    parse_dataset_spec,
    run_quality_gate,
)
from warden_ops.integrity import check_ast_boundaries

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "p2_t17"
PACKAGE = REPO_ROOT / "src" / "warden_evidence" / "dataset_builder"
START = datetime(2025, 1, 2, tzinfo=UTC)
DATASET_ID = "synth_indices_tracer_15m_ohlcv_r1"


# ---------------------------------------------------------------------------
# fixtures: deterministic synthetic bars only
# ---------------------------------------------------------------------------


def _price_series(n: int, seed_price: float = 5000.0) -> list[float]:
    closes: list[float] = []
    price = seed_price
    for index in range(n):
        price = price * (1.0 + 0.0004 * math.sin(index / 3.0) + 0.00012)
        closes.append(price)
    return closes


def _frame(
    *,
    n: int = 12,
    step_minutes: int = 15,
    assets: tuple[str, ...] = ("ES", "NQ"),
    start: datetime = START,
    tz: str | None = None,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for k, asset in enumerate(assets):
        closes = _price_series(n, 5000.0 + 250.0 * k)
        previous = closes[0]
        for i, close in enumerate(closes):
            rows.append(
                {
                    "timestamp": start + timedelta(minutes=step_minutes * i),
                    "asset": asset,
                    "open": previous,
                    "high": max(previous, close) * 1.0005,
                    "low": min(previous, close) * 0.9995,
                    "close": close,
                    "volume": 100.0,
                }
            )
            previous = close
    frame = pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    if tz:
        frame = frame.with_columns(pl.col("timestamp").dt.convert_time_zone(tz))
    return frame.sort(["timestamp", "asset"])


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_source_csvs(
    root: Path,
    *,
    n: int = 36,
    step_minutes: int = 5,
    assets: tuple[str, ...] = ("ES", "NQ"),
    start: datetime = START,
    offset_hours: int = 0,
) -> dict[str, str]:
    """Write one CSV per asset; returns {key: repo-relative path}."""
    paths: dict[str, str] = {}
    for k, asset in enumerate(assets):
        closes = _price_series(n, 5000.0 + 250.0 * k)
        previous = closes[0]
        rows = []
        for i, close in enumerate(closes):
            rows.append(
                {
                    "timestamp": _stamp(start + timedelta(minutes=step_minutes * i + 60 * offset_hours)),
                    "asset": asset,
                    "open": previous,
                    "high": max(previous, close) * 1.0005,
                    "low": min(previous, close) * 0.9995,
                    "close": close,
                    "volume": 100.0,
                }
            )
            previous = close
        relative = f"raw/{asset.lower()}.csv"
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows).write_csv(target)
        paths[asset.lower()] = relative
    return paths


def _spec_doc(
    *,
    inputs: dict[str, dict[str, object]] | None = None,
    symbols: tuple[str, ...] = ("ES", "NQ"),
    frequency: str = "15m",
    step_minutes: int = 15,
    n: int = 12,
    window_end: datetime | None = None,
    resampling_enabled: bool = False,
    require_min_sub_bars: int | None = None,
    filters: list[dict[str, object]] | None = None,
    max_abs: float = 0.20,
    mad: float = 10.0,
    allow_zero_volume: bool = False,
    min_coverage: float = 1.0,
    dataset_id: str = DATASET_ID,
    evidence_tier: str = "synthetic",
    source_timezone: str | None = None,
    fmt: str = "csv",
    volume_semantics: str = "real_contracts",
    price_basis: str = "ohlcv",
) -> dict[str, object]:
    inputs = inputs or {
        asset.lower(): {"path": f"raw/{asset.lower()}.csv", "format": fmt}
        for asset in symbols
    }
    if source_timezone:
        for item in inputs.values():
            item["source_timezone"] = source_timezone
    end = window_end or (START + timedelta(minutes=step_minutes * n))
    return {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "asset_class": "indices",
        "evidence_tier": evidence_tier,
        "venue": "synth_local",
        "frequency": frequency,
        "price_basis": price_basis,
        "volume_semantics": volume_semantics,
        "time_window": {
            "start_inclusive": _stamp(START),
            "exploratory_end_exclusive": _stamp(end),
            "holdout_start_inclusive": _stamp(end),
            "holdout_end_exclusive": _stamp(end + timedelta(hours=3)),
        },
        "time_contract": {
            "timezone": "UTC",
            "bar_timestamp_semantics": "open_left_closed_left",
            "session_calendar": "CME_Equity",
            "daily_roll_anchor": None,
        },
        "source": {"kind": "lake_raw", "adapter": "local_tracer", "inputs": inputs},
        "universe": {
            "selection_mode": "explicit_symbols",
            "symbols": list(symbols),
            "retain_delisted": True,
        },
        "resampling": {
            "enabled": resampling_enabled,
            "require_min_sub_bars": require_min_sub_bars,
        },
        "quality_gate": {
            "max_abs_bar_return": max_abs,
            "mad_outlier_threshold": mad,
            "allow_zero_volume": allow_zero_volume,
            "explicit_anomaly_filters": filters or [],
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


def _spec(**kwargs):
    return parse_dataset_spec(_spec_doc(**kwargs))


class RecordingFileSystem:
    """Counts every read attempt so "never opened" is observable."""

    def __init__(self, root: Path) -> None:
        self.inner = LocalRawFileSystem(root)
        self.calls: list[str] = []

    def read_bytes(self, relative):
        self.calls.append(relative.as_posix())
        return self.inner.read_bytes(relative)


def _spike_close(frame: pl.DataFrame, asset: str, index: int, factor: float) -> pl.DataFrame:
    ordered = frame.sort(["timestamp", "asset"])
    sub = ordered.filter(pl.col("asset") == asset).sort("timestamp")
    previous = float(sub["close"][index - 1])
    close = previous * factor
    stamp = sub["timestamp"][index]
    later = sub["timestamp"][index + 1]
    same = (pl.col("asset") == asset) & (pl.col("timestamp") == stamp)
    following = (pl.col("asset") == asset) & (pl.col("timestamp") == later)
    out = ordered.with_columns(pl.when(same).then(pl.lit(close)).otherwise(pl.col("close")).alias("close"))
    out = out.with_columns(
        pl.when(same).then(pl.max_horizontal("open", "close")).otherwise(pl.col("high")).alias("high"),
        pl.when(same).then(pl.min_horizontal("open", "close")).otherwise(pl.col("low")).alias("low"),
    )
    out = out.with_columns(pl.when(following).then(pl.lit(close)).otherwise(pl.col("open")).alias("open"))
    return out.with_columns(
        pl.when(following).then(pl.max_horizontal("high", "open")).otherwise(pl.col("high")).alias("high"),
        pl.when(following).then(pl.min_horizontal("low", "open")).otherwise(pl.col("low")).alias("low"),
    )


def _set_volume(frame: pl.DataFrame, asset: str, index: int, volume: float) -> pl.DataFrame:
    sub = frame.filter(pl.col("asset") == asset).sort("timestamp")
    stamp = sub["timestamp"][index]
    mask = (pl.col("asset") == asset) & (pl.col("timestamp") == stamp)
    return frame.with_columns(pl.when(mask).then(pl.lit(volume)).otherwise(pl.col("volume")).alias("volume"))


# ---------------------------------------------------------------------------
# 1. strict specification
# ---------------------------------------------------------------------------


def test_spec_is_strict_and_refuses_unknown_or_missing_keys() -> None:
    document = _spec_doc()
    parse_dataset_spec(document)

    unknown = json.loads(json.dumps(document))
    unknown["quality_gate"]["max_abs_bar_return_pct"] = 0.2
    with pytest.raises(DatasetSpecError, match="Extra inputs are not permitted"):
        parse_dataset_spec(unknown)

    missing = json.loads(json.dumps(document))
    del missing["quality_gate"]
    with pytest.raises(DatasetSpecError, match="Field required"):
        parse_dataset_spec(missing)

    bad_id = json.loads(json.dumps(document))
    bad_id["dataset_id"] = "Not A Dataset"
    with pytest.raises(DatasetSpecError, match="dataset_id must match"):
        parse_dataset_spec(bad_id)


def test_spec_digest_is_canonical_and_content_sensitive() -> None:
    document = _spec_doc()
    reference = parse_dataset_spec(document).spec_sha256()

    reversed_document = {key: document[key] for key in reversed(list(document))}
    assert parse_dataset_spec(reversed_document).spec_sha256() == reference

    # "+00:00" and "Z" are the same declaration; the digest must agree.
    same_instant = json.loads(json.dumps(document))
    same_instant["time_window"]["start_inclusive"] = "2025-01-02T00:00:00+00:00"
    assert parse_dataset_spec(same_instant).spec_sha256() == reference

    changed = json.loads(json.dumps(document))
    changed["quality_gate"]["max_abs_bar_return"] = 0.25
    assert parse_dataset_spec(changed).spec_sha256() != reference


def test_spec_refuses_naive_offgrid_and_overlapping_windows() -> None:
    naive = _spec_doc()
    naive["time_window"]["start_inclusive"] = "2025-01-02T00:00:00"
    with pytest.raises(DatasetSpecError, match="explicit timezone"):
        parse_dataset_spec(naive)

    offgrid = _spec_doc()
    offgrid["time_window"]["start_inclusive"] = "2025-01-02T00:07:00Z"
    with pytest.raises(DatasetSpecError, match="aligned"):
        parse_dataset_spec(offgrid)

    overlap = _spec_doc()
    overlap["time_window"]["holdout_start_inclusive"] = "2025-01-02T02:00:00Z"
    with pytest.raises(DatasetSpecError, match="time_window must satisfy"):
        parse_dataset_spec(overlap)


def test_spec_refuses_tick_count_volume_without_resampling() -> None:
    document = _spec_doc()
    document["volume_semantics"] = "tick_count_proxy"
    with pytest.raises(DatasetSpecError, match="tick_count_proxy"):
        parse_dataset_spec(document)


def test_committed_yaml_fixture_parses() -> None:
    spec = load_dataset_spec(FIXTURES / "spec_tracer.yaml")
    assert spec.dataset_id == DATASET_ID
    assert spec.source.adapter == "local_tracer"
    assert spec.quality_gate.explicit_anomaly_filters[0].id == "drop_volume_spike"
    assert len(spec.spec_sha256()) == 64

    daily = parse_dataset_spec(_spec_doc(frequency="1d", step_minutes=1440, n=3))
    feed = daily.feed_spec_dict()
    assert feed["data_frequency"] == "1d"
    assert feed["bar_label"] == "left"
    assert feed["time_contract_version"] == 2


# ---------------------------------------------------------------------------
# 2. containment before I/O
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["raw/holdout/es.csv", "raw/HOLDOUT_2024/es.csv", "../raw/es.csv", "/raw/es.csv", "raw\\es.csv"],
)
def test_protected_and_escaping_source_paths_are_refused(path: str) -> None:
    with pytest.raises(SourceContainmentError):
        canonical_source_path(path)

    document = _spec_doc()
    document["source"]["inputs"]["es"]["path"] = path
    with pytest.raises(DatasetSpecError):
        parse_dataset_spec(document)


def test_blocked_source_path_is_refused_before_the_filesystem_is_touched(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_source_csvs(source)
    repo = tmp_path / "repo"
    repo.mkdir()

    spec = _spec()
    smuggled = SourceInput.model_construct(
        path="raw/holdout/es.csv",
        format="csv",
        timestamp_column="timestamp",
        entity_column="asset",
        source_timezone=None,
    )
    mutated = spec.model_copy(
        update={"source": spec.source.model_copy(update={"inputs": {"es": smuggled}})}
    )
    recording = RecordingFileSystem(source)
    with pytest.raises(SourceContainmentError, match="protected"):
        build_dataset(mutated, repo_root=repo, fs=recording)
    assert recording.calls == []
    assert not (repo / "data").exists()
    assert not (repo / "contracts").exists()


def test_write_once_is_decided_before_any_raw_read(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_source_csvs(source)
    repo = tmp_path / "repo"
    target = repo / "data" / "exploratory" / f"{DATASET_ID}.parquet"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing")

    recording = RecordingFileSystem(source)
    with pytest.raises(WriteOnceError, match="write-once"):
        build_dataset(_spec(), repo_root=repo, fs=recording)
    assert recording.calls == []
    assert target.read_bytes() == b"existing"


def test_output_directory_naming_protected_data_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_source_csvs(source)
    repo = tmp_path / "repo"
    repo.mkdir()
    recording = RecordingFileSystem(source)
    with pytest.raises(SourceContainmentError):
        build_dataset(_spec(), repo_root=repo, dataset_dir="data/holdout", fs=recording)
    assert recording.calls == []


# ---------------------------------------------------------------------------
# 3. mandatory quality gate
# ---------------------------------------------------------------------------


def test_non_utc_raw_inputs_convert_by_instant_without_relabelling() -> None:
    frame = _frame(tz="Asia/Tokyo")
    assert frame.schema["timestamp"].time_zone == "Asia/Tokyo"
    outcome = run_quality_gate(frame, _spec())
    assert outcome.report["time"]["raw_timezone_converted_from"] == "Asia/Tokyo"
    assert outcome.report["time"]["timezone"] == "UTC"
    assert outcome.frame.schema["timestamp"] == pl.Datetime("us", "UTC")
    assert outcome.frame["timestamp"].min() == START
    assert outcome.frame["timestamp"].max() == START + timedelta(minutes=165)


def test_naive_timestamps_are_refused() -> None:
    frame = _frame().with_columns(pl.col("timestamp").dt.replace_time_zone(None))
    with pytest.raises(QualityGateError, match="timezone"):
        run_quality_gate(frame, _spec())


def test_explicit_filter_records_exact_drops_and_reconciles_every_row() -> None:
    frame = _set_volume(_frame(), "ES", 4, 9999.0)
    spec = _spec(
        filters=[
            {
                "id": "drop_volume_spike",
                "column": "volume",
                "comparison": "gt",
                "value": 5000.0,
                "assets": None,
                "reason": "vendor volume spike",
            }
        ]
    )
    outcome = run_quality_gate(frame, spec)
    (applied,) = outcome.report["filters"]
    assert applied["matched"] == applied["dropped"] == 1
    assert applied["dropped_by_asset"] == {"ES": 1}
    assert outcome.report["rows"] == {
        "input": 24,
        "dropped_unselected_symbols": 0,
        "dropped_by_filters": 1,
        "source_after_filters": 23,
        "dropped_by_sub_bar_requirement": 0,
        "kept_source_rows": 23,
        "output_bars": 23,
        "unexplained": 0,
    }
    assert outcome.frame.height == 23
    assert outcome.report["output_coverage"]["coverage_ratio"] < 1.0


def test_uncovered_extreme_return_fails_closed() -> None:
    frame = _spike_close(_frame(), "ES", 4, 1.40)
    with pytest.raises(QualityGateError, match="max_abs_bar_return"):
        run_quality_gate(frame, _spec())


def test_mad_outlier_blocks_an_uncovered_anomaly() -> None:
    # AnomalyManager needs its 20-sample floor: 24 bars give 23 returns.
    frame = _spike_close(_frame(n=24), "ES", 4, 1.08)
    with pytest.raises(QualityGateError, match="MAD return outliers"):
        run_quality_gate(frame, _spec(n=24))


def test_zero_volume_is_refused_unless_explicitly_allowed() -> None:
    frame = _set_volume(_frame(), "NQ", 2, 0.0)
    with pytest.raises(QualityGateError, match="zero-volume"):
        run_quality_gate(frame, _spec())
    outcome = run_quality_gate(frame, _spec(allow_zero_volume=True))
    assert outcome.report["zero_volume"] == {"zero_volume_rows": 1, "allowed": True}


def test_resampling_is_left_closed_left_labelled_with_observed_sub_bars() -> None:
    frame = _frame(n=36, step_minutes=5)
    spec = _spec(n=36, step_minutes=5, resampling_enabled=True, frequency="15m")
    outcome = run_quality_gate(frame, spec)
    assert outcome.frame.height == 24
    resampling = outcome.report["resampling"]
    assert resampling["closed"] == "left"
    assert resampling["label"] == "left"
    assert resampling["sub_bars_min"] == resampling["sub_bars_max"] == 3
    first = outcome.frame.filter(pl.col("asset") == "ES").sort("timestamp").row(0, named=True)
    raw_es = frame.filter(pl.col("asset") == "ES").sort("timestamp")
    assert first["timestamp"] == START
    assert first["open"] == pytest.approx(float(raw_es["open"][0]))
    assert first["close"] == pytest.approx(float(raw_es["close"][2]))
    assert first["high"] == pytest.approx(float(raw_es["high"][:3].max()))
    assert first["low"] == pytest.approx(float(raw_es["low"][:3].min()))
    assert first["volume"] == pytest.approx(300.0)
    assert first["observed_sub_bars"] == 3


def test_sub_bar_minimum_drops_bins_with_an_audited_count() -> None:
    frame = _frame(n=35, step_minutes=5)
    end = START + timedelta(minutes=15 * 11)
    spec = _spec(
        n=35,
        step_minutes=5,
        window_end=end,
        resampling_enabled=True,
        require_min_sub_bars=3,
    )
    outcome = run_quality_gate(frame, spec)
    resampling = outcome.report["resampling"]
    assert resampling["dropped_bins"] == 2  # one partial bin per asset
    assert resampling["dropped_source_rows"] == 4  # 2 remaining sub-bars per asset
    assert outcome.frame.height == 22
    assert outcome.report["rows"]["unexplained"] == 0
    assert outcome.report["rows"]["dropped_by_sub_bar_requirement"] == 4


def test_temporal_fence_refuses_a_bar_at_the_exclusive_end(tmp_path: Path) -> None:
    # Window ends at 03:00; the 13th bar sits exactly on the closed boundary.
    frame = _frame(n=13, step_minutes=15)
    spec = _spec(n=12)
    with pytest.raises(TemporalFenceError, match="exploratory_end_exclusive"):
        run_quality_gate(frame, spec)

    source = tmp_path / "source"
    _write_source_csvs(source, n=13, step_minutes=15)
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(TemporalFenceError):
        build_dataset(_spec(n=12), repo_root=repo, source_root=source)
    assert not (repo / "data").exists()


def test_a_source_coverage_gap_fails_closed_with_the_missing_bar_named() -> None:
    frame = _frame().filter(
        ~((pl.col("asset") == "ES") & (pl.col("timestamp") == START + timedelta(minutes=15)))
    )
    with pytest.raises(QualityGateError, match="coverage") as excinfo:
        run_quality_gate(frame, _spec())
    report = excinfo.value.report
    assert report["per_asset"]["ES"]["missing_bars"] == 1
    assert report["per_asset"]["NQ"]["missing_bars"] == 0


def test_duplicate_keys_and_negative_values_are_refused() -> None:
    doubled = pl.concat([_frame(), _frame()])
    with pytest.raises(QualityGateError, match="duplicated"):
        run_quality_gate(doubled, _spec())

    negative = _set_volume(_frame(), "ES", 0, -1.0)
    with pytest.raises(QualityGateError, match="negative volume"):
        run_quality_gate(negative, _spec())


def test_unselected_symbols_are_dropped_with_a_recorded_count() -> None:
    frame = _frame(assets=("ES", "NQ", "CL"))
    outcome = run_quality_gate(frame, _spec())
    assert outcome.report["universe"]["dropped_unselected_symbols"] == {"CL": 12}
    assert outcome.report["rows"]["dropped_unselected_symbols"] == 12
    assert outcome.report["rows"]["unexplained"] == 0


def test_tick_count_volume_is_the_observed_sub_bar_count() -> None:
    spec = _spec(
        n=36,
        step_minutes=5,
        resampling_enabled=True,
        volume_semantics="tick_count_proxy",
    )
    outcome = run_quality_gate(_frame(n=36, step_minutes=5), spec)
    assert outcome.report["volume_semantics"] == "tick_count_proxy"
    assert outcome.frame["volume"].to_list() == [3.0] * 24
    assert outcome.frame["observed_sub_bars"].to_list() == [3] * 24


def test_declared_price_basis_is_enforced_on_the_raw_frame() -> None:
    with pytest.raises(QualityGateError, match="funding_rate"):
        run_quality_gate(_frame(), _spec(price_basis="ohlcv_funding"))
    with pytest.raises(QualityGateError, match=r"\['bid', 'ask'\]"):
        run_quality_gate(_frame(), _spec(price_basis="quote_ohlcv"))


def test_a_declared_coverage_tolerance_is_recorded_not_implicit() -> None:
    frame = _frame().filter(
        ~((pl.col("asset") == "ES") & (pl.col("timestamp") == START + timedelta(minutes=15)))
    )
    with pytest.raises(QualityGateError, match="coverage"):
        run_quality_gate(frame, _spec())
    outcome = run_quality_gate(frame, _spec(min_coverage=0.9))
    coverage = outcome.report["coverage"]
    assert coverage["enforced"] is True
    assert coverage["min_coverage_ratio"] == 0.9
    assert coverage["worst_asset_ratio"] == pytest.approx(11 / 12)


def test_adapter_registry_is_pluggable_and_refuses_unknown_names(tmp_path: Path) -> None:
    from warden_evidence.dataset_builder import (
        get_source_adapter,
        register_source_adapter,
        source_adapter_names,
    )

    assert "local_tracer" in source_adapter_names()
    with pytest.raises(AdapterError, match="unknown source adapter"):
        get_source_adapter("not_registered")

    class ToyAdapter:
        name = "toy"

        def __init__(self, *, fs=None) -> None:
            self.fs = fs

        def load(self, spec):  # pragma: no cover - registration only
            raise NotImplementedError

    register_source_adapter("toy")(ToyAdapter)
    assert get_source_adapter("toy", fs=object()).name == "toy"
    # A declared name and the supplied adapter must agree, and the mismatch is
    # refused before any raw read (the tmp repo holds no source at all).
    with pytest.raises(DatasetBuildError, match="does not match declared"):
        build_dataset(parse_dataset_spec(_spec_doc()), repo_root=tmp_path, adapter=ToyAdapter())


# ---------------------------------------------------------------------------
# 4. deterministic write-once publication + v2 manifest
# ---------------------------------------------------------------------------


def _build_into(repo: Path, source: Path, **spec_kwargs):
    # Source CSVs are 5-minute bars; the dataset is the 15-minute resample.
    spec_kwargs.setdefault("n", 36)
    spec_kwargs.setdefault("step_minutes", 5)
    spec_kwargs.setdefault("resampling_enabled", True)
    repo.mkdir(parents=True, exist_ok=True)
    return build_dataset(_spec(**spec_kwargs), repo_root=repo, source_root=source)


def test_two_clean_builds_are_byte_identical_with_a_complete_v2_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_source_csvs(source)
    first = _build_into(tmp_path / "one", source)
    second = _build_into(tmp_path / "two", source)

    assert first.parquet_sha256 == second.parquet_sha256
    assert (tmp_path / "one" / first.dataset_path).read_bytes() == (
        tmp_path / "two" / second.dataset_path
    ).read_bytes()
    assert (tmp_path / "one" / first.manifest_path).read_bytes() == (
        tmp_path / "two" / second.manifest_path
    ).read_bytes()

    manifest = first.manifest
    assert manifest["schema_version"] == 2
    assert manifest["spec_sha256"] == first.spec_sha256
    assert manifest["builder"]["code_closure_sha256"] == first.builder_code_closure_sha256
    assert manifest["raw_inputs_merkle_sha256"] == first.raw_inputs_merkle_sha256
    assert manifest["polars_version"] == pl.__version__
    assert manifest["parquet_write"] == {
        "compression": "zstd",
        "compression_level": 3,
        "statistics": True,
        "row_group_size": 100000,
        "sort_keys": ["timestamp", "asset"],
    }
    assert manifest["quality_gate_report"]["rows"]["unexplained"] == 0
    assert manifest["quality_gate_report"]["validator"]["library"].endswith("OHLCVValidator")
    assert manifest["quality_gate_report"]["anomalies"]["mad"]["method"] == "mad"
    assert manifest["dataset"]["feed_spec"]["time_contract_version"] == 2
    assert manifest["dataset"]["feed_spec"]["bar_label"] == "left"
    assert manifest["dataset"]["volume_semantics"] == "real_contracts"
    assert manifest["provenance"] == "synthetic"
    assert manifest["path"] == first.dataset_path
    assert manifest["sha256"] == first.parquet_sha256

    # The Merkle commitment covers exactly the bytes that were read.
    raw = sorted(
        (("raw/es.csv", (source / "raw/es.csv").read_bytes()),
         ("raw/nq.csv", (source / "raw/nq.csv").read_bytes()))
    )
    expected_merkle, expected_records = merkle_sha256(raw)
    assert manifest["raw_inputs_merkle_sha256"] == expected_merkle
    assert [(item["path"], item["bytes"], item["sha256"]) for item in manifest["raw_inputs"]] == [
        (record.path, record.bytes, record.sha256) for record in expected_records
    ]
    assert hashlib.sha256((tmp_path / "one" / first.dataset_path).read_bytes()).hexdigest() == (
        first.parquet_sha256
    )


def test_a_changed_raw_byte_changes_the_commitment_and_the_output(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_source_csvs(source)
    baseline = _build_into(tmp_path / "a", source)

    edited = source / "raw" / "es.csv"
    lines = edited.read_text().splitlines()
    lines[1] = lines[1].replace(",100.0", ",101.0")
    edited.write_text("\n".join(lines) + "\n")
    changed = _build_into(tmp_path / "b", source)

    assert changed.raw_inputs_merkle_sha256 != baseline.raw_inputs_merkle_sha256
    assert changed.parquet_sha256 != baseline.parquet_sha256
    assert changed.spec_sha256 == baseline.spec_sha256


def test_write_once_refuses_a_modified_spec_and_preserves_the_original(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_source_csvs(source)
    repo = tmp_path / "repo"
    first = _build_into(repo, source)
    original = (repo / first.dataset_path).read_bytes()
    original_manifest = (repo / first.manifest_path).read_bytes()

    with pytest.raises(WriteOnceError):
        _build_into(repo, source, max_abs=0.19)

    assert (repo / first.dataset_path).read_bytes() == original
    assert (repo / first.manifest_path).read_bytes() == original_manifest


def test_built_dataset_resolves_through_market_data_gateway(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_source_csvs(source)
    repo = tmp_path / "repo"
    outcome = _build_into(repo, source)

    contract = EvaluationContract(
        contract_id=DATASET_ID,
        asset_class="indices",
        frequency="15m",
        bars_per_year=35040,
        exploratory_data_path=outcome.dataset_path,
        holdout_data_path=f"data/holdout/{DATASET_ID}.parquet",
        train_start=START,
        val_start=START + timedelta(hours=1),
        val_end=START + timedelta(hours=2),
        holdout_start=START + timedelta(hours=3),
        holdout_end=START + timedelta(hours=6),
        n_folds=2,
        label_horizon_bars=1,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
        data_provenance="synthetic",
        data_snapshot_sha256=outcome.parquet_sha256,
    )
    panel = MarketDataGateway(repo_root=repo).load(contract, role="exploratory")
    assert panel.total_bars == 12  # 12 unique 15m timestamps
    assert panel.eval_bars == 4  # one hour of the validation window
    assert panel.panel.height == 24  # two assets per bar
    assert panel.provenance == "synthetic"
    assert panel.market_eligible is False  # a build never claims verified market origin
    assert panel.ts[-1] < contract.holdout_start


def test_parquet_raw_inputs_require_an_injected_reader(tmp_path: Path) -> None:
    source = tmp_path / "source"
    paths = _write_source_csvs(source)
    for asset, relative in paths.items():
        csv_path = source / relative
        buffer = io.BytesIO()
        pl.read_csv(csv_path).write_parquet(buffer)
        (source / f"raw/{asset}.parquet").write_bytes(buffer.getvalue())
    document = _spec_doc(
        inputs={
            asset: {"path": f"raw/{asset}.parquet", "format": "parquet"} for asset in paths
        },
        n=36,
        step_minutes=5,
        resampling_enabled=True,
    )
    spec = parse_dataset_spec(document)

    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(AdapterError, match="injected parquet_reader"):
        build_dataset(spec, repo_root=repo, source_root=source)
    assert not (repo / "data").exists()

    outcome = build_dataset(
        spec,
        repo_root=repo,
        source_root=source,
        parquet_reader=lambda payload: pl.read_parquet(io.BytesIO(payload)),
    )
    assert outcome.rows == 24


def test_merkle_root_is_domain_separated_and_refuses_duplicates() -> None:
    one, _ = merkle_sha256([("raw/a.csv", b"a")])
    with_node, _ = merkle_sha256([("raw/a.csv", b"a"), ("raw/b.csv", b"b")])
    relabelled, _ = merkle_sha256([("raw/a.csv", b"b")])
    assert len({one, with_node, relabelled}) == 3

    with pytest.raises(ValueError, match="read twice"):
        merkle_sha256([("raw/a.csv", b"a"), ("raw/a.csv", b"b")])
    with pytest.raises(ValueError, match="empty"):
        merkle_sha256([])


def test_builder_package_has_no_direct_parquet_reader_or_network_client() -> None:
    banned_modules = {"socket", "http", "requests", "httpx", "urllib", "aiohttp"}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
                assert not (roots & banned_modules), f"{path.name} imports {roots & banned_modules}"
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert (node.module or "").split(".")[0] not in banned_modules, path.name
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"read_parquet", "scan_parquet"}, (
                    f"{path.name}:{node.lineno} imports a Parquet reader; "
                    "the builder takes an injected reader instead"
                )
    assert [
        violation
        for violation in check_ast_boundaries(REPO_ROOT)
        if "market-data read" in violation and "dataset_builder" in violation
    ] == []
