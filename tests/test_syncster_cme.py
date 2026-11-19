"""Research-only Syncster CME ingestion: source pin, cutoff, provenance and immutability."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from scripts import prepare_syncster_cme as cme
from warden.contracts import load_contract
from warden.provenance import resolve_dataset


def fixture_source(tmp_path: Path, symbol: str = "ES") -> Path:
    rows = []
    start = datetime(2016, 6, 1, tzinfo=UTC)
    val = datetime(2025, 6, 30, 23, 45, tzinfo=UTC)
    for stamp in [*(start + timedelta(minutes=i) for i in range(18)),
                  *(val + timedelta(minutes=i) for i in range(15)),
                  datetime(2025, 7, 1, 0, 0, tzinfo=UTC)]:
        rows.append({"ts": stamp, "symbol": symbol, "open": 100.0,
                     "high": 101.0, "low": 99.0, "close": 100.5, "volume": 2.0})
    path = tmp_path / f"{symbol}.parquet"
    pl.DataFrame(rows).write_parquet(path)
    return path


def test_panel_causal_cutoff_ohlcv_resampling_and_missing_minutes(tmp_path: Path) -> None:
    source = fixture_source(tmp_path)
    panel, audit = cme.prepare_panel(source, "ES", "15m", expected_sha=cme.sha256(source))
    assert panel["timestamp"].min() == datetime(2016, 6, 1, tzinfo=UTC)
    assert panel["timestamp"].max() == datetime(2025, 6, 30, 23, 45, tzinfo=UTC)
    assert len(panel) == 3  # Two 15m buckets at inception plus the final historical bucket.
    assert panel["observed_minutes"].to_list() == [15, 3, 15]
    assert audit.observed_rows == 33
    assert audit.gaps_over_60m >= 1
    assert audit.sha256 == cme.sha256(source)
    assert "2025-07-01" not in str(panel["timestamp"].to_list())


def test_mismatched_source_digest_fails_closed(tmp_path: Path) -> None:
    source = fixture_source(tmp_path)
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        cme.prepare_panel(source, "ES", "15m")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        cme.prepare_panel(source, "ES", "15m", expected_sha="f" * 64)


@pytest.mark.parametrize("issue", ["duplicate", "null", "nan", "bad_ohlc", "zero_volume"])
def test_invalid_source_fails_without_silent_repair(tmp_path: Path, issue: str) -> None:
    source = fixture_source(tmp_path)
    df = pl.read_parquet(source)
    if issue == "duplicate":
        df = pl.concat([df, df.head(1)])
    elif issue == "null":
        df = df.with_columns(pl.when(pl.int_range(pl.len()) == 0)
                             .then(None).otherwise(pl.col("close")).alias("close"))
    elif issue == "nan":
        df = df.with_columns(pl.when(pl.int_range(pl.len()) == 0)
                             .then(pl.lit(float("nan"))).otherwise(pl.col("close")).alias("close"))
    elif issue == "bad_ohlc":
        df = df.with_columns(pl.when(pl.int_range(pl.len()) == 0)
                             .then(pl.lit(98.0)).otherwise(pl.col("high")).alias("high"))
    else:
        df = df.with_columns(pl.when(pl.int_range(pl.len()) == 0)
                             .then(pl.lit(0.0)).otherwise(pl.col("volume")).alias("volume"))
    df.write_parquet(source)
    with pytest.raises(ValueError, match="Duplicate|Null|Non-finite|Invalid"):
        cme.prepare_panel(source, "ES", "15m", expected_sha=cme.sha256(source))


def test_write_once_and_warden_provenance(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "upstream"
    source_root.joinpath("data/futures").mkdir(parents=True)
    source = fixture_source(source_root / "data/futures")
    monkeypatch.setitem(cme.SOURCE_SHA256, "ES", cme.sha256(source))
    repo = tmp_path / "quant"
    repo.mkdir()
    with pytest.raises(ValueError, match="Only the integrity-pinned 15m"):
        cme.stage("ES", "1h", repo, source_root, commit_files=True)
    result = cme.stage("ES", "15m", repo, source_root, commit_files=True)
    contract = load_contract(repo / result["contract_path"])
    record = json.loads((repo / result["manifest_path"]).read_text())
    resolution = resolve_dataset(contract, repo)
    assert resolution.eligible_market is True
    assert resolution.sha256 == result["derived_sha256"]
    assert record["source"] == "syncster_market_data"
    assert record["market_type"] == "cme_futures"
    assert record["raw_source_sha256"] == cme.sha256(source)
    assert record["roll_method"] == "provider_continuous_unverified"
    assert not (repo / "data/holdout").exists()
    assert cme.stage("ES", "15m", repo, source_root, commit_files=True) == result

    # Changing the already frozen panel cannot change contract/manifest or overwrite the data.
    frozen = repo / result["data_path"]
    before = hashlib.sha256(frozen.read_bytes()).hexdigest()
    frozen.write_bytes(b"tampered")
    with pytest.raises(FileExistsError, match="Conflicting pinned"):
        cme.stage("ES", "15m", repo, source_root, commit_files=True)
    assert before != hashlib.sha256(frozen.read_bytes()).hexdigest()
    assert record["sha256"] == before


def test_new_contracts_and_manifests_are_integrity_pinned() -> None:
    repo = Path(__file__).resolve().parents[1]
    manifest_lines = (repo / ".warden_manifest.sha256").read_text().splitlines()
    hashes = {line.split(maxsplit=1)[1]: line.split(maxsplit=1)[0]
              for line in manifest_lines if line.strip()}
    for symbol in ("es", "nq"):
        for rel in (f"contracts/cme_{symbol}_15m_syncster_v1.yaml",
                    f"contracts/datasets/CME_{symbol.upper()}_15M_SYNCSTER_V1.json"):
            assert rel in hashes
            assert cme.sha256(repo / rel) == hashes[rel]


def test_invalid_contract_basis_and_fallback_order(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "upstream"
    root.joinpath("data/futures").mkdir(parents=True)
    source = fixture_source(root / "data/futures")
    monkeypatch.setitem(cme.SOURCE_SHA256, "ES", cme.sha256(source))
    repo = tmp_path / "quant"
    fallback = repo / "data/exploratory/cme_es_1m.parquet"
    fallback.parent.mkdir(parents=True)
    fallback.write_bytes(b"bad")
    # Existing canonical source is authoritative, never silently fallback to an untrusted copy.
    assert cme.source_path("ES", repo, root) == source
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        cme.prepare_panel(fallback, "ES", "15m")
