"""Spec #109 lane J (#117) — compile a requirement into a write-once sandbox artifact.

The claim this file makes:

* a ``compile_required`` requirement becomes one fenced Parquet, one sandbox contract and
  one dataset manifest, and the fence is *proved*: no emitted bar closes after
  ``holdout_start``, and a fence that would straddle a bar is refused instead of truncated;
* intraday bars come from the canonical minute store by left-closed, left-labelled
  aggregation, so the next bucket can never leak into a bar's OHLCV;
* costs, leverage, fences and ``selection_group_id`` are injected from the signed venue
  policy, and an agent-supplied override is refused before anything is written;
* the artifact is reproducible: the same requirement twice reuses the identical bytes, a
  changed lake refuses write-once;
* the artifact runs the ordinary pipeline (register → verify → advance) without production
  signing, two sandbox contracts in one group share search debt, and ``qualify`` refuses a
  sandbox contract outright.

Every fixture is synthetic: a scratch repo, a fixture-owned signed manifest, a Hive
minute lake.  No production registry, holdout byte, real signature or lake write.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
import yaml

from warden.compile import (
    ARTIFACT_CONTRACT,
    CompileFenceError,
    CompileGap,
    CompileRefused,
    CompileWriteOnce,
    compile_data_requirement,
)

START = datetime(2021, 1, 1, tzinfo=UTC)
FENCE = datetime(2021, 1, 4, tzinfo=UTC)
POLICY_REL = "contracts/policy/venue_policy.yaml"
MANIFEST_FILE = ".warden_manifest.sha256"
FIXTURE_KEY = "fixture-only-lane-j"

#: A compact but complete policy: the fences are the only time authority a compile reads,
#: and 2021-01-04 (a 1h/4h/daily grid point) is the holdout fence for every fixture here.
POLICY = """\
version: 1
train_start: '2021-01-01T00:00:00Z'
val_start: '2021-01-02T00:00:00Z'
val_end: '2021-01-04T00:00:00Z'
holdout_start: '2021-01-04T00:00:00Z'
holdout_end: '2021-02-01T00:00:00Z'
asset_classes:
  fx:
    taker_fee_bps: 0.6
    slippage_bps: 0.7
    funding_bps_per_8h: 0
    allow_leverage: false
    max_gross_leverage: 0.9
    selection_groups:
      intraday: TEST_FX_INTRADAY
      daily: TEST_FX_DAILY
  crypto_perps:
    taker_fee_bps: 4.0
    slippage_bps: 2.0
    funding_bps_per_8h: 1.0
    allow_leverage: true
    max_gross_leverage: 3.0
    selection_groups:
      intraday: TEST_CRYPTO_INTRADAY
"""


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _rows(total_minutes: int, base_cents: int = 10_000) -> list[dict]:
    """A deterministic integer-cent walk over *absolute* minute indices.

    Dispersed enough that the MAD return gate is meaningful (a near-constant series makes
    every z-score infinite) and large enough that a two-bar holding clears the fixture's cost
    hurdle, and hand-checkable: ``open_i == close_{i-1}``, high/low are the bar's own
    extremes and volume cycles 1..3.
    """
    closes = [base_cents]
    for index in range(1, total_minutes):
        # +/-7 cents on a 100.00 price is +/-7bp per minute: dispersed enough for the MAD gate
        # and large enough that a two-bar holding clears the fixture's cost hurdle.
        step = ((index * 1103515245 + 12345) >> 8) % 15 - 7
        closes.append(closes[-1] + (step if step else (1 if index % 2 else -1)))
    rows = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else close
        rows.append(
            {
                "open": previous / 100.0,
                "high": (max(previous, close) + 1) / 100.0,
                "low": (min(previous, close) - 1) / 100.0,
                "close": close / 100.0,
                "volume": float(1 + index % 3),
            }
        )
    return rows


def _minute_frame(
    symbol: str, first: datetime, minutes: int, *, first_index: int = 0, base_cents: int = 10_000
) -> pl.DataFrame:
    rows = _rows(first_index + minutes, base_cents)[first_index: first_index + minutes]
    return pl.DataFrame(
        [{"timestamp": first + timedelta(minutes=index), "asset": symbol, **row}
         for index, row in enumerate(rows)]
    )


def _expected_bar(symbol: str, label: datetime, minutes: int = 60, *, base_cents: int = 10_000) -> dict:
    """The 1h bar an independent reader computes from the same per-minute rows."""
    offset = int((label - START).total_seconds() // 60)
    window = _rows(offset + minutes, base_cents)[offset: offset + minutes]
    return {
        "open": window[0]["open"],
        "high": max(row["high"] for row in window),
        "low": min(row["low"] for row in window),
        "close": window[-1]["close"],
        "volume": sum(row["volume"] for row in window),
        "ticks": float(len(window)),
    }


def _lake(
    root: Path,
    *,
    symbols: tuple[str, ...] = ("EURUSD", "GBPUSD"),
    days: tuple[str, ...] = ("01", "02", "03"),
    per_day: int = 60 * 24,
    partitions: tuple[str, ...] | None = None,
    asset_class: str = "fx",
    cadence: str = "minute",
    acceptance: bool = True,
) -> Path:
    """A synthetic minute lake: ``<key>/year=/month=/day=/part-0.parquet`` plus metadata."""
    (root / ".catalog").mkdir(parents=True, exist_ok=True)
    (root / ".metadata").mkdir(parents=True, exist_ok=True)
    entries = []
    for symbol in symbols:
        key = f"forex_{cadence}_{symbol}" if asset_class == "fx" else f"crypto_futures_ohlcv_{cadence}_HYPERLIQUID_{symbol}_PERP"
        entry_partitions: list[str] = []
        cursor = START
        if partitions is not None:
            for partition in partitions:
                path = root / key / partition / "part-0.parquet"
                path.parent.mkdir(parents=True, exist_ok=True)
                _minute_frame(symbol, START, per_day).write_parquet(path)
                entry_partitions.append(f"{key}/{partition}")
        else:
            for offset, day in enumerate(days):
                relative = f"year=2021/month=01/day={day}"
                path = root / key / relative / "part-0.parquet"
                path.parent.mkdir(parents=True, exist_ok=True)
                _minute_frame(symbol, cursor, per_day, first_index=offset * per_day).write_parquet(path)
                entry_partitions.append(f"{key}/{relative}")
                cursor += timedelta(days=1)
        if partitions is not None:
            cursor = START + timedelta(days=len(partitions))
        entries.append(
            {
                "dataset_key": key,
                "asset_class": asset_class,
                "sub_family": "g10_fx" if asset_class == "fx" else "majors",
                "frequency": cadence,
                "entry_identity": hashlib.sha256(f"{key}:entry".encode()).hexdigest(),
                "consumer_identity": hashlib.sha256(f"{key}:consumer".encode()).hexdigest(),
                "acceptance": {
                    "present": acceptance,
                    "relative_path": f".acceptance/{key}/dataset.json",
                    "sha256": hashlib.sha256(f"{key}:acceptance".encode()).hexdigest() if acceptance else None,
                },
            }
        )
        (root / ".metadata" / f"{key}.json").write_text(
            json.dumps(
                {
                    "dataset_key": key,
                    "first_timestamp": START.isoformat(),
                    "last_timestamp": (cursor - timedelta(minutes=1)).isoformat(),
                    "partitions": entry_partitions,
                }
            ),
            encoding="utf-8",
        )
    (root / ".catalog" / "research-dataset-catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "catalog_identity": "lane-j-fixture-catalog",
                "consumer_contract": "fixture",
                "policy_identity": "fixture",
                "resolutions": [],
                "entries": entries,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return root


def _sign_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture-owned integrity: hash the protected set, sign it with a fixture key."""
    from warden_ops.integrity import generate_manifest

    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", FIXTURE_KEY)
    content = generate_manifest(repo)
    digest = hmac.new(FIXTURE_KEY.encode(), content.encode(), hashlib.sha256).hexdigest()
    (repo / f"{MANIFEST_FILE}.sig").write_text(digest + "\n", encoding="ascii")


def _policy(repo: Path, monkeypatch: pytest.MonkeyPatch, document: str = POLICY) -> Path:
    policy = repo / POLICY_REL
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(document, encoding="utf-8")
    _sign_repo(repo, monkeypatch)
    return policy


def _requirement(**overrides) -> dict:
    document = {
        "asset_class": "fx",
        "frequency": "1h",
        "symbols": ["EURUSD"],
        "min_start": START.isoformat(),
        "price_basis": "ohlcv",
        "execution_mode": "NEXT_BAR",
    }
    document.update(overrides)
    return document


def _compile(repo: Path, lake: Path, requirement: dict | None = None, **kwargs):
    from warden.market_data import read_parquet_bytes

    return compile_data_requirement(
        requirement or _requirement(),
        repo_root=repo,
        lake_root=lake,
        parquet_reader=read_parquet_bytes,
        **kwargs,
    )


def _at(text: str) -> datetime:
    """Parse either ISO spelling a manifest may carry (``T`` or space, ``Z`` or ``+00:00``)."""
    cleaned = str(text).strip().removesuffix(" UTC")
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    parsed = datetime.fromisoformat(cleaned)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _tree(root: Path) -> dict[str, bytes]:
    """Every file in the fixture repo except the CLI's own audit trail of the attempt."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.relative_to(root).as_posix().startswith(".workspace/audit/")
    }


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A scratch repo (signed policy) plus a fenced synthetic minute lake."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lake = _lake(tmp_path / "lake")
    _policy(repo, monkeypatch)
    return repo, lake


# ---------------------------------------------------------------------------
# 1. identity: three artifacts, one fence, a manifest that binds every digest
# ---------------------------------------------------------------------------


def test_compile_writes_one_parquet_one_contract_and_one_manifest(workspace) -> None:
    repo, lake = workspace
    artifact = _compile(repo, lake)

    parquet = repo / artifact.parquet_path
    contract_path = repo / artifact.contract_path
    manifest_path = repo / artifact.manifest_path
    assert parquet.is_file() and contract_path.is_file() and manifest_path.is_file()
    assert artifact.parquet_path.startswith("data/exploratory/")
    assert artifact.contract_path.startswith("contracts/sandbox/")
    assert artifact.manifest_path.startswith("contracts/sandbox/datasets/")
    # One Parquet, one contract, one manifest — nothing else was published.
    assert len(list((repo / "data/exploratory").rglob("*.parquet"))) == 1
    assert len(list((repo / "contracts/sandbox").glob("*.yaml"))) == 1
    assert len(list((repo / "contracts/sandbox/datasets").glob("*.json"))) == 1
    # The namespace documents its own non-qualifying boundary, once.
    readme = (repo / "contracts/sandbox/README.md")
    assert readme.is_file() and "qualify" in readme.read_text(encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(parquet.read_bytes()).hexdigest()
    assert manifest["sha256"] == digest == artifact.parquet_sha256
    assert manifest["raw_inputs_merkle_sha256"] == artifact.raw_inputs_merkle_sha256
    assert manifest["spec_sha256"] == artifact.spec_sha256
    assert manifest["dataset"]["price_basis"] == "ohlcv"
    assert manifest["dataset"]["assets"] == ["EURUSD"]
    assert _at(manifest["dataset"]["first_bar"]) == START
    assert _at(manifest["dataset"]["last_bar"]) == FENCE - timedelta(hours=1)

    sandbox = manifest["sandbox"]
    assert sandbox["schema"] == ARTIFACT_CONTRACT["schema"]
    assert sandbox["policy"]["sha256"] == artifact.policy_sha256
    assert sandbox["policy"]["selection_group_id"] == "TEST_FX_INTRADAY"
    assert _at(sandbox["fence"]["holdout_start"]) == FENCE
    assert sandbox["lake"]["catalog_identity"] == "lane-j-fixture-catalog"
    assert sandbox["lake"]["acceptance"]["forex_minute_EURUSD"]["present"] is True
    # The raw Merkle covers exactly the input files that were read.
    assert {record["path"] for record in manifest["raw_inputs"]} == {
        f"forex_minute_EURUSD/year=2021/month=01/day={day}/part-0.parquet"
        for day in ("01", "02", "03")
    }


def test_the_compiled_contract_is_v2_policy_governed_and_carries_the_artifact_pin(workspace) -> None:
    from warden.contracts import load_contract

    repo, lake = workspace
    artifact = _compile(repo, lake)
    contract = load_contract(repo / artifact.contract_path)

    assert contract.contract_id == artifact.contract_id
    assert contract.frequency == "1h"
    assert contract.execution_mode == "NEXT_BAR"
    assert contract.governance.spec_version == 2
    assert contract.selection_group_id == "TEST_FX_INTRADAY"
    # Cost, leverage and fences are the signed policy's, not the compiler's or an agent's.
    assert (contract.taker_fee_bps, contract.slippage_bps) == (0.6, 0.7)
    assert contract.governance.allow_leverage is False
    assert contract.governance.max_gross_leverage == 0.9
    assert contract.holdout_start == FENCE
    assert contract.val_start == datetime(2021, 1, 2, tzinfo=UTC)
    assert contract.exploratory_data_path == artifact.parquet_path
    # The dataset pin is the artifact's own digest, and its provenance is *unattested*.
    assert contract.data_snapshot_sha256 == artifact.parquet_sha256
    assert contract.data_provenance == "unknown"
    assert contract.spec_hash() == artifact.contract_spec_hash


# ---------------------------------------------------------------------------
# 2. the fence: no bar closes after holdout_start, and a straddle is refused
# ---------------------------------------------------------------------------


def test_every_emitted_bar_closes_at_or_before_the_fence(workspace) -> None:
    repo, lake = workspace
    artifact = _compile(repo, lake)
    frame = pl.read_parquet(repo / artifact.parquet_path)

    interval = timedelta(hours=1)
    labels = frame["timestamp"].unique().sort().to_list()
    assert max(labels) + interval == FENCE
    assert _at(artifact.last_bar_close) == FENCE
    # Nothing is emitted at or after the fence, and every bar is a whole 1h bin.
    assert all(label + interval <= FENCE for label in labels)
    assert set(frame["observed_sub_bars"].to_list()) == {60}


def test_a_fence_that_straddles_one_bar_is_refused_not_truncated(workspace, monkeypatch) -> None:
    """2021-01-04T00:30 is not on the 1h grid: the last bar would reach past the fence."""
    repo, lake = workspace
    _policy(repo, monkeypatch, POLICY.replace("holdout_start: '2021-01-04T00:00:00Z'",
                                             "holdout_start: '2021-01-04T00:30:00Z'"))
    before = _tree(repo)
    with pytest.raises(CompileFenceError) as caught:
        _compile(repo, lake)
    assert "straddle" in str(caught.value)
    assert "2021-01-04T00:30:00+00:00" in str(caught.value)
    # The refusal is total: no contract, no manifest, no Parquet.
    assert _tree(repo) == before


def test_a_store_partition_that_straddles_the_fence_is_refused(tmp_path, monkeypatch) -> None:
    """A monthly partition covering the fence cannot be sliced: the compile refuses."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lake = _lake(tmp_path / "lake", partitions=("year=2021/month=01",))
    _policy(repo, monkeypatch)
    before = _tree(repo)
    with pytest.raises(CompileFenceError) as caught:
        _compile(repo, lake)
    assert "year=2021/month=01" in str(caught.value)
    assert _tree(repo) == before


def test_a_raw_row_at_or_after_the_fence_is_counted_and_refused(workspace) -> None:
    """The raw-frame guard is the first fence proof: a partition *name* inside the window
    is not proof of its rows.

    ``day=03`` is a lawful input (its name ends exactly at the fence), but a real leak looks
    like rows that reach past it.  The guard counts those rows and refuses before the gate
    aggregates anything.
    """
    repo, lake = workspace
    part = lake / "forex_minute_EURUSD" / "year=2021/month=01/day=03" / "part-0.parquet"
    frame = pl.read_parquet(part)
    last = frame.tail(1)
    spilled = pl.concat(
        [
            last.with_columns(pl.col("timestamp") + timedelta(minutes=1)),  # exactly the fence
            last.with_columns(pl.col("timestamp") + timedelta(minutes=6)),  # past it
        ]
    )
    assert spilled["timestamp"].to_list() == [FENCE, FENCE + timedelta(minutes=5)]
    pl.concat([frame, spilled]).write_parquet(part)

    with pytest.raises(CompileFenceError) as caught:
        _compile(repo, lake)
    message = str(caught.value)
    assert "2 raw input rows" in message
    assert FENCE.isoformat() in message
    # Refused before publication: no Parquet, no contract, no manifest.
    assert not list((repo / "data" / "exploratory").rglob("*.parquet"))
    assert not list((repo / "contracts" / "sandbox").glob("*.yaml"))
    assert not list((repo / "contracts" / "sandbox" / "datasets").glob("*.json"))


def test_a_published_bar_that_closes_after_the_fence_is_withdrawn(workspace, monkeypatch) -> None:
    """Backstop proof for the post-aggregate fence check, including its withdrawal.

    A lawful raw frame cannot reach this branch: the fence is required to sit on the bar
    grid and buckets are left-closed and epoch-aligned, so the last bucket that starts
    before the fence closes at or before it.  The builder seam is therefore replaced by one
    that publishes exactly what a leak would publish — one Parquet, one manifest and a bar
    *labelled at* the fence — and the compile must withdraw both files and refuse.
    """
    from types import SimpleNamespace

    import warden.compile as compile_module

    repo, lake = workspace
    published: list[Path] = []

    def leaking_build(spec, **kwargs):
        parquet = Path(kwargs["repo_root"]) / kwargs["dataset_dir"] / f"{spec.dataset_id}.parquet"
        manifest = (
            Path(kwargs["repo_root"]) / kwargs["manifest_dir"] / f"{kwargs['manifest_name']}.json"
        )
        compile_module._create_exclusive(parquet, b"leaked-parquet-bytes")
        compile_module._create_exclusive(
            manifest,
            json.dumps(
                {
                    "sha256": hashlib.sha256(b"leaked-parquet-bytes").hexdigest(),
                    "spec_sha256": spec.spec_sha256(),
                    "raw_inputs_merkle_sha256": "0" * 64,
                    "dataset": {
                        "last_bar": FENCE.isoformat(),
                        "first_bar": START.isoformat(),
                        "rows": 24,
                        "assets": ["EURUSD"],
                        "frequency": "1h",
                        "price_basis": "ohlcv",
                    },
                    "sandbox": {
                        "policy": {"sha256": "0" * 64, "selection_group_id": "TEST_FX_INTRADAY"},
                        "fence": {"holdout_start": FENCE.isoformat()},
                        "window": {"compiled_start_inclusive": START.isoformat()},
                        "resampling": {"source_frequency": "1m"},
                        "lake": {},
                    },
                }
            ).encode("utf-8"),
        )
        published.extend([parquet, manifest])
        return SimpleNamespace(
            parquet_sha256=hashlib.sha256(b"leaked-parquet-bytes").hexdigest(),
            rows=24,
            first_bar=START.isoformat(),
            last_bar=FENCE.isoformat(),
        )

    monkeypatch.setattr(compile_module, "build_dataset", leaking_build)
    with pytest.raises(CompileFenceError) as caught:
        _compile(repo, lake)
    message = str(caught.value)
    assert "closes at" in message and "after the holdout fence" in message
    assert len(published) == 2
    # The just-written evidence is withdrawn: a refused compile leaves no artifact behind.
    assert [path.exists() for path in published] == [False, False]
    assert not list((repo / "contracts" / "sandbox").glob("*.yaml"))


def test_no_input_partition_at_or_after_the_fence_is_ever_opened(tmp_path, monkeypatch) -> None:
    """Partitions beyond the fence are excluded by name, before any byte is read."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lake = _lake(tmp_path / "lake")
    # A holdout-side partition exists and must never be opened.
    holdout_part = lake / "forex_minute_EURUSD" / "year=2021/month=01/day=04" / "part-0.parquet"
    holdout_part.parent.mkdir(parents=True, exist_ok=True)
    _minute_frame("EURUSD", FENCE, 60 * 24).write_parquet(holdout_part)
    _policy(repo, monkeypatch)
    artifact = _compile(repo, lake)
    assert artifact.raw_inputs_merkle_sha256
    recorded = json.loads((repo / artifact.manifest_path).read_text(encoding="utf-8"))
    assert all("day=04" not in record["path"] for record in recorded["raw_inputs"])
    assert recorded["sandbox"]["lake"]["excluded_partitions"]


# ---------------------------------------------------------------------------
# 3. resampling: left-closed, left-labelled, and the next bucket cannot leak
# ---------------------------------------------------------------------------


def test_intraday_bars_are_hand_computed_left_closed_aggregates(workspace) -> None:
    repo, lake = workspace
    artifact = _compile(repo, lake)
    frame = pl.read_parquet(repo / artifact.parquet_path).sort("timestamp")

    for label in (START + timedelta(hours=5), START + timedelta(hours=29)):
        row = frame.filter(pl.col("timestamp") == label).row(0, named=True)
        assert row["open"] == pytest.approx(_expected_bar("EURUSD", label)["open"])
        assert row["high"] == pytest.approx(_expected_bar("EURUSD", label)["high"])
        assert row["low"] == pytest.approx(_expected_bar("EURUSD", label)["low"])
        assert row["close"] == pytest.approx(_expected_bar("EURUSD", label)["close"])
        # An FX lake minute store carries tick counts, so the declared semantics publish the
        # number of sub-bars — not the sum of a column the venue never reported as lots.
        assert row["volume"] == pytest.approx(_expected_bar("EURUSD", label)["ticks"])
        assert row["volume"] != pytest.approx(_expected_bar("EURUSD", label)["volume"])
        assert row["observed_sub_bars"] == 60


def test_the_next_bucket_never_leaks_into_a_bar(tmp_path, monkeypatch) -> None:
    """Hour 05 holds one minute only: the 04:00 bar must be exactly 04:00..04:59.

    A right-closed or right-labelled bucket would have carried minute 05:00 into the 04:00
    bar (61 sub-bars, wrong close/high).  Its own bar is dropped by the declared sub-bar
    minimum, and the drop is accounted for in the manifest.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    lake = _lake(tmp_path / "lake")
    part = lake / "forex_minute_EURUSD" / "year=2021/month=01/day=01" / "part-0.parquet"
    frame = pl.read_parquet(part)
    spilled = (START + timedelta(hours=5)) + timedelta(minutes=1)
    frame = frame.filter(
        (pl.col("timestamp") < spilled) | (pl.col("timestamp") >= START + timedelta(hours=6))
    )
    frame.write_parquet(part)
    _policy(repo, monkeypatch)

    artifact = _compile(repo, lake)
    bars = pl.read_parquet(repo / artifact.parquet_path).sort("timestamp")

    bar_04 = bars.filter(pl.col("timestamp") == START + timedelta(hours=4)).row(0, named=True)
    expected = _expected_bar("EURUSD", START + timedelta(hours=4))
    assert bar_04["observed_sub_bars"] == 60
    assert bar_04["close"] == pytest.approx(expected["close"])
    assert bar_04["high"] == pytest.approx(expected["high"])
    # The single-minute hour 05 produced no bar at all...
    assert bars.filter(pl.col("timestamp") == START + timedelta(hours=5)).height == 0
    # ...and the manifest says so rather than losing it silently.
    manifest = json.loads((repo / artifact.manifest_path).read_text(encoding="utf-8"))
    resampling = manifest["quality_gate_report"]["resampling"]
    assert resampling["dropped_bins"] == 1
    assert resampling["sub_bars_min"] == 60 and resampling["sub_bars_max"] == 60


def test_the_window_starts_at_the_first_whole_partition_at_or_after_min_start(tmp_path, monkeypatch) -> None:
    """A whole raw file is never sliced, so a mid-partition min_start moves the window forward."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lake = _lake(tmp_path / "lake")
    document = POLICY.replace("val_start: '2021-01-02T00:00:00Z'", "val_start: '2021-01-03T00:00:00Z'")
    _policy(repo, monkeypatch, document)

    artifact = _compile(repo, lake, _requirement(min_start="2021-01-01T00:20:00Z"))

    assert _at(artifact.window_start) == datetime(2021, 1, 2, tzinfo=UTC)
    frame = pl.read_parquet(repo / artifact.parquet_path)
    assert frame["timestamp"].min() == datetime(2021, 1, 2, tzinfo=UTC)
    manifest = json.loads((repo / artifact.manifest_path).read_text(encoding="utf-8"))
    assert _at(manifest["sandbox"]["window"]["requested_min_start"]) == datetime(2021, 1, 1, 0, 20, tzinfo=UTC)
    assert _at(manifest["sandbox"]["window"]["compiled_start_inclusive"]) == datetime(2021, 1, 2, tzinfo=UTC)
    assert manifest["sandbox"]["window"]["policy"] == "first whole input partition at or after min_start"


# ---------------------------------------------------------------------------
# 4. policy injection: an agent override is refused before any file is written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"taker_fee_bps": 0.1},
        {"slippage_bps": 0.0},
        {"holdout_start": "2022-01-01T00:00:00Z"},
        {"allow_leverage": True},
        {"max_gross_leverage": 10.0},
        {"selection_group_id": "SOME_OTHER_GROUP"},
    ],
)
def test_an_agent_supplied_policy_override_is_refused_before_any_write(workspace, override) -> None:
    repo, lake = workspace
    before = _tree(repo)
    with pytest.raises(CompileRefused) as caught:
        _compile(repo, lake, overrides=override)
    key = next(iter(override))
    assert key in str(caught.value)
    assert "signed venue policy" in str(caught.value)
    assert _tree(repo) == before


def test_a_caller_supplied_policy_object_is_not_a_compile_input(tmp_path) -> None:
    """Review A1: the only policy path is the signed one.

    ``VenuePolicy`` is a plain frozen dataclass with a caller-supplied ``sha256`` and
    unvalidated rules, so a ``policy=`` seam lets an agent publish a contract whose costs,
    leverage and selection group no signed document ever declared.  The seam is gone:
    passing one is a ``TypeError``, never a compile.
    """
    from warden.market_data import read_parquet_bytes
    from warden_ops.venue_policy import VenuePolicy, VenueRules

    repo = tmp_path / "repo"
    repo.mkdir()
    lake = _lake(tmp_path / "lake")
    # No policy exists in this repo: no YAML, no manifest entry, no signature.
    forged = VenuePolicy(
        train_start=START,
        val_start=datetime(2021, 1, 2, tzinfo=UTC),
        val_end=FENCE,
        holdout_start=FENCE,
        holdout_end=datetime(2021, 2, 1, tzinfo=UTC),
        sha256="0" * 64,
        _rules={
            ("fx", "intraday"): VenueRules(
                taker_fee_bps=0.001,
                slippage_bps=0.001,
                funding_bps_per_8h=0.0,
                allow_leverage=True,
                max_gross_leverage=50.0,
                selection_group_id="AGENT_GROUP",
            )
        },
    )
    with pytest.raises(TypeError) as caught:
        compile_data_requirement(
            _requirement(),
            repo_root=repo,
            lake_root=lake,
            parquet_reader=read_parquet_bytes,
            policy=forged,
        )
    assert "policy" in str(caught.value)
    assert not (repo / "contracts").exists() and not (repo / "data").exists()
    # Without any seam the same request is refused: no signed policy, no compile.
    with pytest.raises(CompileRefused) as refused:
        _compile(repo, lake)
    assert "policy" in str(refused.value).lower()
    assert not (repo / "contracts").exists() and not (repo / "data").exists()


def test_an_untrusted_policy_refuses_and_writes_nothing(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    lake = _lake(tmp_path / "lake")
    _policy(repo, monkeypatch)
    (repo / f"{MANIFEST_FILE}.sig").unlink()
    before = _tree(repo)
    with pytest.raises(CompileRefused) as caught:
        _compile(repo, lake)
    assert "policy" in str(caught.value).lower()
    assert _tree(repo) == before


def test_a_gap_fails_closed_without_writing(workspace) -> None:
    repo, lake = workspace
    before = _tree(repo)
    with pytest.raises(CompileGap) as caught:
        _compile(repo, lake, _requirement(symbols=["EURJPY"]))
    assert "EURJPY" in str(caught.value)
    assert _tree(repo) == before


# ---------------------------------------------------------------------------
# 5. write-once and reproducibility
# ---------------------------------------------------------------------------


def test_recompiling_the_same_requirement_reuses_the_identical_artifact(workspace) -> None:
    repo, lake = workspace
    first = _compile(repo, lake)
    before = _tree(repo)
    second = _compile(repo, lake)

    assert second.reused is True
    assert second.contract_id == first.contract_id
    assert second.parquet_sha256 == first.parquet_sha256
    assert second.raw_inputs_merkle_sha256 == first.raw_inputs_merkle_sha256
    assert second.manifest_sha256 == first.manifest_sha256
    assert _tree(repo) == before  # not one byte rewritten


def test_a_changed_lake_refuses_write_once_instead_of_silently_reusing(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    lake = _lake(tmp_path / "lake")
    _policy(repo, monkeypatch)
    _compile(repo, lake)
    before = _tree(repo)
    part = lake / "forex_minute_EURUSD" / "year=2021/month=01/day=02" / "part-0.parquet"
    frame = pl.read_parquet(part)
    frame = frame.with_columns((pl.col("close") * 1.0001).alias("close"))
    frame.write_parquet(part)

    with pytest.raises(CompileWriteOnce) as caught:
        _compile(repo, lake)
    assert "merkle" in str(caught.value).lower()
    assert _tree(repo) == before


def test_a_different_requirement_gets_its_own_artifact(workspace) -> None:
    repo, lake = workspace
    one = _compile(repo, lake, _requirement(symbols=["EURUSD"]))
    two = _compile(repo, lake, _requirement(symbols=["EURUSD", "GBPUSD"]))
    assert one.contract_id != two.contract_id
    assert one.parquet_path != two.parquet_path
    assert one.selection_group_id == two.selection_group_id == "TEST_FX_INTRADAY"


def test_the_artifact_contract_names_every_field_promotion_must_validate() -> None:
    required = ARTIFACT_CONTRACT["manifest_fields"]
    for key in (
        "sha256",
        "raw_inputs_merkle_sha256",
        "spec_sha256",
        "sandbox.policy.sha256",
        "sandbox.policy.rules",
        "sandbox.policy.selection_group_id",
        "sandbox.fence",
        "sandbox.lake.catalog_identity",
        "sandbox.lake.acceptance",
        "sandbox.contract_spec_hash",
    ):
        assert key in required
    assert ARTIFACT_CONTRACT["qualification_eligible"] is False


# ---------------------------------------------------------------------------
# 6. the CLI surface: compile is JSON-first, gated, and refuses before writing
# ---------------------------------------------------------------------------


def _cli(capsys) -> tuple[int, dict | None, str]:
    from warden.cli import main as cli_main  # local: the module under test

    return cli_main, capsys


def _last_json(capsys) -> dict:
    """The one JSON envelope a ``--json`` run prints (sentinels and prose are not JSON)."""
    out = capsys.readouterr().out
    start = out.find("{")
    if start < 0:
        raise AssertionError(f"no JSON envelope in output: {out!r}")
    payload, _end = json.JSONDecoder().raw_decode(out[start:])
    return payload


def test_cli_compile_publishes_and_reports_json(workspace, monkeypatch, capsys) -> None:
    from warden.cli import main as cli_main

    repo, lake = workspace
    monkeypatch.chdir(repo)
    requirement = repo / "requirement.json"
    requirement.write_text(json.dumps(_requirement()), encoding="utf-8")

    code = cli_main(["data", "compile", "--requirement", str(requirement),
                     "--lake-root", str(lake), "--json"])
    assert code == 0
    envelope = _last_json(capsys)
    payload = envelope["value"]
    assert envelope["ok"] is True and envelope["source"] == "warden.data-compile"
    assert payload["reused"] is False and payload["qualification_eligible"] is False
    assert (repo / payload["contract_path"]).is_file()
    # The compile is not a protected write: the human manifest is still valid.
    from warden_ops.integrity import check_worktree

    assert check_worktree(repo)

    before = _tree(repo)
    assert cli_main(["data", "compile", "--requirement", str(requirement),
                     "--lake-root", str(lake), "--json"]) == 0
    assert _last_json(capsys)["value"]["reused"] is True
    assert _tree(repo) == before


def test_cli_compile_refuses_an_override_before_writing(workspace, monkeypatch, capsys) -> None:
    from warden.cli import main as cli_main

    repo, lake = workspace
    monkeypatch.chdir(repo)
    before = _tree(repo)

    code = cli_main(["data", "compile", "--requirement", json.dumps(_requirement()),
                     "--lake-root", str(lake), "--override", "taker_fee_bps=0.05", "--json"])
    assert code == 2
    captured = capsys.readouterr()
    assert "taker_fee_bps" in captured.err
    assert "signed venue policy" in captured.err
    assert "WARDEN_DATA_COMPILE_FAILED" in captured.err
    assert _tree(repo) == before


def test_cli_compile_reports_a_gap_with_exit_three(workspace, monkeypatch, capsys) -> None:
    from warden.cli import main as cli_main

    repo, lake = workspace
    monkeypatch.chdir(repo)
    code = cli_main(["data", "compile", "--requirement", json.dumps(_requirement(symbols=["EURJPY"])),
                     "--lake-root", str(lake), "--json"])
    assert code == 3
    envelope = _last_json(capsys)
    assert envelope["ok"] is False and envelope["policy"] == "deny"
    assert envelope["value"]["gap"]["kind"] == "missing_symbols"


def test_the_worktree_gate_runs_in_front_of_every_compile(workspace, monkeypatch, capsys) -> None:
    """A tampered policy blocks the compile before one byte is read or written."""
    from warden.cli import main as cli_main

    repo, lake = workspace
    (repo / POLICY_REL).write_text(POLICY + "\n# tampered\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    before = _tree(repo)
    with pytest.raises(SystemExit):
        cli_main(["data", "compile", "--requirement", json.dumps(_requirement()),
                  "--lake-root", str(lake), "--json"])
    assert _tree(repo) == before


# ---------------------------------------------------------------------------
# 7. a sandbox artifact runs the ordinary pipeline with no production signing
# ---------------------------------------------------------------------------


STRATEGY_MODULE = """
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor


def required_symbols(params):
    return None


class _SandboxStrategy(Strategy):
    def __init__(self):
        self._bar = 0
        self._exec = TargetWeightExecutor(
            RebalanceConfig(allow_short=True, allow_fractional=True, max_single_weight=0.5,
                            max_gross_leverage=1.0)
        )

    def on_data(self, timestamp, data, context, broker):
        weight = 0.1 if self._bar % 4 == 0 else 0.0
        self._exec.execute({asset: weight for asset in data}, data, broker, timestamp=timestamp)
        self._bar += 1


def configure_strategy(contract, params):
    return _SandboxStrategy()
"""


def _sandbox_workspace(tmp_path, monkeypatch, *, baskets=(("EURUSD",), ("GBPUSD",))):
    """A scratch repo with one compiled artifact per basket, a strategy and specs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lake = _lake(tmp_path / "lake")
    _policy(repo, monkeypatch)
    (repo / "strategies").mkdir()
    (repo / "strategies" / "sandbox.py").write_text(STRATEGY_MODULE, encoding="utf-8")
    # `warden advance` consults the read-only research-outcome index; an empty, well-formed
    # one says "never screened this spec", which is the fixture's situation.
    index = repo / "docs" / "research-outcomes" / "branch-result-index-v1.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "as_of_utc": "2021-01-01",
                "purpose": "lane-J fixture replay-suppression index",
                "source_refs": [{"pr": 1, "commit": "abcdef0"}],
                "notes": [],
                "outcomes": [],
                "original_unattempted": [],
            }
        ),
        encoding="utf-8",
    )
    artifacts = [_compile(repo, lake, _requirement(symbols=list(basket))) for basket in baskets]
    # Sign once, after every protected file exists; the sandbox writes are unprotected.
    _sign_repo(repo, monkeypatch)
    return repo, lake, artifacts


def _hypothesis_file(repo, contract_id: str, hypothesis_id: str) -> Path:
    from warden.contracts import HypothesisSpec

    spec = HypothesisSpec(
        hypothesis_id=hypothesis_id,
        contract_id=contract_id,
        title=f"Sandbox {contract_id}",
        strategy_family="rules_ts",
        economic_mechanism="A fixture mechanism statement long enough to pass",
        counterparty_or_friction="A fixture friction statement long enough to pass",
        expected_holding_bars=2,
        falsification_criteria="Reject a net Sharpe below one on the validation window",
    )
    path = repo / ".workspace" / "hypotheses" / f"{hypothesis_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({name: getattr(spec, name) for name in spec.__dataclass_fields__}),
        encoding="utf-8",
    )
    return path


def _experiment_spec(repo, contract_id: str, hypothesis_id: str) -> Path:
    path = repo / "experiments" / "active" / f"{hypothesis_id.lower()}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "contract_id": contract_id,
                "hypothesis_id": hypothesis_id,
                "strategy_name": "Sandbox fixture",
                "strategy_family": "rules_ts",
                "model_type": "rules",
                "strategy_module": "strategies/sandbox.py",
                "parameters": {},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_a_sandbox_contract_registers_verifies_and_advances(
    tmp_path, monkeypatch, capsys
) -> None:
    from warden.cli import main as cli_main

    repo, _lake_root, (artifact,) = _sandbox_workspace(tmp_path, monkeypatch, baskets=(("EURUSD",),))
    monkeypatch.chdir(repo)
    hypothesis = _hypothesis_file(repo, artifact.contract_id, "HYP-SANDBOX-1")

    assert cli_main(["register-hypothesis", str(hypothesis), "--json"]) == 0
    capsys.readouterr()
    spec = _experiment_spec(repo, artifact.contract_id, "HYP-SANDBOX-1")
    assert cli_main(["verify", str(spec), "--json"]) == 0
    verified = _last_json(capsys)
    # Observed market history without attested origin: runnable, never market-eligible.
    assert verified["status"] == "PASS"
    assert verified["market_eligible"] is False
    assert "unknown" in verified["provenance_reason"]
    assert cli_main(["advance", str(spec), "--json"]) == 0
    advanced = _last_json(capsys)
    # The ordinary pipeline consumed the sandbox artifact and reached a real Stage 1 verdict
    # (a rejection is evidence that the funnel ran, not that the plumbing failed).
    assert advanced["value"]["state"] != "NEW"
    assert advanced["value"]["action"] != "REVIEW_REQUIRED"
    assert cli_main(["spec-status", str(spec), "--json"]) == 0
    status = _last_json(capsys)["value"]
    assert status["state"] == "STAGE1_REJECT" and status["screen"]["state"] == "FINALIZED"

    from warden.registry import ResearchRegistry

    with ResearchRegistry(repo / "registry" / "registry.db")._connect() as conn:
        attempts = conn.execute(
            "SELECT contract_id, data_provenance FROM selection_attempts"
        ).fetchall()
    assert attempts and all(row[0] == artifact.contract_id for row in attempts)
    # Unattested origin is recorded, not laundered: the artifact never claims market data.
    assert all(row[1] == "unknown" for row in attempts)


def test_two_sandbox_contracts_in_one_group_share_persisted_selection_debt(
    tmp_path, monkeypatch, capsys
) -> None:
    from warden.cli import main as cli_main
    from warden.registry import ResearchRegistry

    repo, _lake_root, (first, second) = _sandbox_workspace(tmp_path, monkeypatch)
    assert first.selection_group_id == second.selection_group_id == "TEST_FX_INTRADAY"
    assert first.contract_id != second.contract_id
    monkeypatch.chdir(repo)

    for index, artifact in enumerate((first, second), start=1):
        hypothesis = _hypothesis_file(repo, artifact.contract_id, f"HYP-SANDBOX-{index}")
        assert cli_main(["register-hypothesis", str(hypothesis), "--json"]) == 0
        spec = _experiment_spec(repo, artifact.contract_id, f"HYP-SANDBOX-{index}")
        assert cli_main(["backtest", str(spec), "--json"]) == 0
        capsys.readouterr()

    from warden.contract_source import load_active_contract

    registry = ResearchRegistry(repo / "registry" / "registry.db")
    from warden.contracts import ENGINE_SIMULATION_EPOCH

    debt = registry.selection_group_debt(
        load_active_contract(repo, first.contract_id, allow_sandbox=True),
        simulation_epoch=ENGINE_SIMULATION_EPOCH,
    )
    assert debt["selection_group_id"] == "TEST_FX_INTRADAY"
    assert set(debt["scope"]) >= {first.contract_id, second.contract_id}
    # One attempt per contract id, charged to the *group*: the second id starts from the
    # first one's search debt instead of a fresh budget.
    assert debt["per_contract"] == {first.contract_id: 1, second.contract_id: 1}
    assert debt["shared_attempt_count"] == 2
    # Persisted, not merely in-memory: a fresh registry handle reads the same debt.
    reopened = ResearchRegistry(repo / "registry" / "registry.db")
    assert reopened.selection_group_debt(
        load_active_contract(repo, second.contract_id, allow_sandbox=True),
        simulation_epoch=ENGINE_SIMULATION_EPOCH,
    )["shared_attempt_count"] == 2


# ---------------------------------------------------------------------------
# 8. no holdout: qualify refuses a sandbox contract
# ---------------------------------------------------------------------------


def test_qualify_refuses_a_sandbox_contract_before_any_holdout_claim(
    tmp_path, monkeypatch, capsys
) -> None:
    from warden.cli import main as cli_main
    from warden.registry import ResearchRegistry

    repo, _lake_root, (artifact,) = _sandbox_workspace(tmp_path, monkeypatch, baskets=(("EURUSD",),))
    monkeypatch.chdir(repo)
    hypothesis = _hypothesis_file(repo, artifact.contract_id, "HYP-SANDBOX-Q")
    assert cli_main(["register-hypothesis", str(hypothesis), "--json"]) == 0
    spec = _experiment_spec(repo, artifact.contract_id, "HYP-SANDBOX-Q")
    assert cli_main(["backtest", str(spec), "--json"]) == 0
    capsys.readouterr()

    registry = ResearchRegistry(repo / "registry" / "registry.db")
    with registry._connect() as conn:
        run_id = conn.execute("SELECT run_id FROM trials LIMIT 1").fetchone()[0]
        claims_before = conn.execute("SELECT COUNT(*) FROM holdout_claims").fetchone()[0]

    with pytest.raises(PermissionError) as caught:
        cli_main(["qualify", "--run-id", run_id, "--unseal-holdout",
                  "--human-confirm-unseal", "YES_UNSEAL_HOLDOUT", "--json"])
    assert "sandbox" in str(caught.value).lower()
    assert artifact.contract_id in str(caught.value)

    with registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM holdout_claims").fetchone()[0] == claims_before
        assert conn.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0] == 0
    # The declared holdout path was never created: nothing was read or written there.
    assert not (repo / "data" / "holdout").exists()


# ---------------------------------------------------------------------------
# 9. the artifact is not reusable, not unowned, and never touches a holdout
# ---------------------------------------------------------------------------


def test_a_compiled_artifact_is_never_resolved_as_signed_evidence(workspace) -> None:
    """A second resolve still says ``compile_required``: reuse is for signed contracts only."""
    from warden.resolve import DECISION_COMPILE_REQUIRED, resolve_data_requirement

    repo, lake = workspace
    _compile(repo, lake)
    report = resolve_data_requirement(_requirement(), repo_root=repo, lake_root=lake)
    assert report.decision == DECISION_COMPILE_REQUIRED
    assert report.matched_contract is None


def test_the_compiled_parquet_is_owned_and_fenced_by_the_exploratory_audit(workspace) -> None:
    from warden.market_data import read_parquet_bytes
    from warden_evidence.exploratory_fence import audit_exploratory_storage

    repo, lake = workspace
    artifact = _compile(repo, lake)
    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert report.ok, report.to_dict()
    assert [item.path for item in report.files] == [artifact.parquet_path]
    assert report.files[0].max_timestamp and artifact.contract_id == report.files[0].contract_id


def test_a_compile_declares_a_holdout_path_it_never_creates(workspace) -> None:
    from warden.contracts import load_contract

    repo, lake = workspace
    artifact = _compile(repo, lake)
    contract = load_contract(repo / artifact.contract_path)
    assert contract.holdout_data_path.startswith("data/holdout/sandbox/")
    assert not (repo / "data" / "holdout").exists()
    manifest = json.loads((repo / artifact.manifest_path).read_text(encoding="utf-8"))
    assert manifest["holdout"] == {"sealed": False}
