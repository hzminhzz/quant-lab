"""Spec #109 lane I (#116) — resolve a declarative DataRequirement before strategy code.

The claim this file makes is narrow and entirely local:

* a requirement is a *strict* value object — an agent may say what data it needs and
  may never say what it costs, when the holdout starts, or which debt group to charge;
* resolution picks a signed contract only when its evidence is present, verified and
  actually covers the requirement (exact, or a superset that is filtered explicitly);
* everything else is an actionable answer — ``compile_required`` when the lake can
  serve the domain, ``fail_closed`` with the exact gap when it cannot;
* an archived contract is never an active match, and a near miss is never silently
  accepted;
* resolution writes nothing: no contract, no dataset manifest, no Parquet, no registry.

Every fixture here is synthetic (a small lake catalogue + ``.metadata`` records, a
hand-written contract, a tiny Parquet panel).  No production registry, holdout, lake
byte, manifest signature or signing key is touched.
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
from pydantic import ValidationError

from warden.cli import main as cli_main
from warden.contracts import EvaluationContract
from warden.resolve import DataRequirement, resolve_data_requirement

START = datetime(2021, 1, 1, tzinfo=UTC)
MIN_START = "2022-01-01T00:00:00Z"
POLICY_REL = "contracts/policy/venue_policy.yaml"
MANIFEST_FILE = ".warden_manifest.sha256"

SYNTHETIC_POLICY = """\
version: 1
train_start: '2021-01-01T00:00:00Z'
val_start: '2022-01-01T00:00:00Z'
val_end: '2023-01-01T00:00:00Z'
holdout_start: '2023-01-01T00:00:00Z'
holdout_end: '2024-01-01T00:00:00Z'
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
  metals:
    taker_fee_bps: 0.9
    slippage_bps: 1.1
    funding_bps_per_8h: 0
    allow_leverage: false
    max_gross_leverage: 0.5
    selection_groups:
      intraday: TEST_METALS_INTRADAY
"""


# ---------------------------------------------------------------------------
# fixtures: a synthetic repo (contract + dataset manifest + Parquet) and lake
# ---------------------------------------------------------------------------


def _frame(assets: tuple[str, ...], first_bar: datetime, bars: int) -> pl.DataFrame:
    rows = []
    for asset in assets:
        for i in range(bars):
            ts = first_bar + timedelta(hours=i)
            rows.append(
                {
                    "timestamp": ts,
                    "asset": asset,
                    "open": 1.0 + i / 1000,
                    "high": 1.1 + i / 1000,
                    "low": 0.9 + i / 1000,
                    "close": 1.05 + i / 1000,
                    "volume": 10.0 + i,
                }
            )
    return pl.DataFrame(rows)


def _contract(
    contract_id: str,
    *,
    asset_class: str = "fx",
    frequency: str = "1h",
    execution_mode: str = "NEXT_BAR",
    first_bar: datetime = START,
    assets: tuple[str, ...] = ("EURUSD", "GBPUSD"),
    declared_group: str | None = None,
) -> EvaluationContract:
    return EvaluationContract(
        contract_id=contract_id,
        asset_class=asset_class,
        frequency=frequency,
        bars_per_year=6260 if frequency != "1d" else 313,
        exploratory_data_path=f"data/exploratory/{contract_id.lower()}.parquet",
        holdout_data_path=f"data/holdout/{contract_id.lower()}.parquet",
        train_start=first_bar,
        val_start=first_bar + timedelta(days=365),
        val_end=first_bar + timedelta(days=730),
        holdout_start=first_bar + timedelta(days=730),
        holdout_end=first_bar + timedelta(days=1095),
        n_folds=4,
        label_horizon_bars=5,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=0.2,
        slippage_bps=0.5,
        execution_mode=execution_mode,
        data_snapshot_sha256="0" * 64,
        data_provenance="verified_market",
        policy=(
            {"spec_version": 2, "selection_group_id": declared_group}
            if declared_group is not None
            else None
        ),
    )


def _install_contract(
    repo: Path,
    contract: EvaluationContract,
    *,
    assets: tuple[str, ...] = ("EURUSD", "GBPUSD"),
    first_bar: datetime = START,
    bars: int = 48,
    price_basis: str | None = "ohlcv",
    manifest_price_basis: bool = True,
) -> EvaluationContract:
    """Write a contract, its dataset manifest and its Parquet bytes into ``repo``."""

    parquet = repo / contract.exploratory_data_path
    parquet.parent.mkdir(parents=True, exist_ok=True)
    _frame(assets, first_bar, bars).write_parquet(parquet)
    digest = hashlib.sha256(parquet.read_bytes()).hexdigest()
    contract = EvaluationContract(
        **{
            **{
                name: getattr(contract, name)
                for name in contract.__dataclass_fields__
                if name != "policy"
            },
            "data_snapshot_sha256": digest,
            "policy": contract.policy,
        }
    )
    (repo / "contracts").mkdir(parents=True, exist_ok=True)
    (repo / "contracts" / f"{contract.contract_id.lower()}.yaml").write_text(
        yaml.safe_dump(contract.to_dict()), encoding="utf-8"
    )
    manifest: dict = {
        "contract_id": contract.contract_id,
        "path": contract.exploratory_data_path,
        "sha256": digest,
        "provenance": "verified_market",
        "source": "synthetic lane-I fixture lake",
        "coverage": {
            "rows": bars * len(assets),
            "assets": len(assets),
            "min": (first_bar).isoformat(),
            "max": (first_bar + timedelta(hours=bars - 1)).isoformat(),
            "per_asset": {asset: bars for asset in assets},
        },
    }
    if manifest_price_basis and price_basis is not None:
        manifest["price_basis"] = price_basis
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "contracts" / "datasets" / f"{contract.contract_id}.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8"
    )
    return contract


def _archive(repo: Path, contract: EvaluationContract) -> None:
    """Move a contract into the read-only archive namespace (#114 layout)."""

    archive_dir = repo / "contracts" / "archive"
    (archive_dir / "datasets").mkdir(parents=True, exist_ok=True)
    live = repo / "contracts" / f"{contract.contract_id.lower()}.yaml"
    archived = archive_dir / live.name
    archived.write_bytes(live.read_bytes())
    live.unlink()
    live_manifest = repo / "contracts" / "datasets" / f"{contract.contract_id}.json"
    archived_manifest = archive_dir / "datasets" / live_manifest.name
    archived_manifest.write_bytes(live_manifest.read_bytes())
    live_manifest.unlink()
    (archive_dir / "archive_map.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "moved_from_commit": "",
                "contracts": [
                    {
                        "contract_id": contract.contract_id,
                        "status": "DEPRECATED_UNEXECUTABLE",
                        "original_path": f"contracts/{live.name}",
                        "archive_path": f"contracts/archive/{live.name}",
                        "dataset_original_path": f"contracts/datasets/{live_manifest.name}",
                        "dataset_archive_path": f"contracts/archive/datasets/{live_manifest.name}",
                    }
                ],
            },
            indent=1,
        ),
        encoding="utf-8",
    )


def _lake(
    root: Path,
    entries: list[dict],
    *,
    starts: dict[str, str] | None = None,
) -> Path:
    """A synthetic lake: catalogue entries plus the store metadata a resolver reads.

    Entries with the same ``dataset_key`` describe one store; their ``partition`` /
    ``partitions`` values are merged into that store's single metadata record.
    """

    (root / ".catalog").mkdir(parents=True, exist_ok=True)
    (root / ".metadata").mkdir(parents=True, exist_ok=True)
    merged: dict[str, dict] = {}
    for entry in entries:
        key = entry["dataset_key"]
        record = merged.setdefault(
            key,
            {
                "dataset_key": key,
                "asset_class": entry["asset_class"],
                "sub_family": entry.get("sub_family"),
                "frequency": entry.get("frequency"),
                "first_timestamp": entry.get("first_timestamp", "2020-06-01 00:00:00+00:00"),
                "last_timestamp": entry.get("last_timestamp", "2024-12-31 23:00:00+00:00"),
                "partitions": [],
            },
        )
        partitions = entry.get("partitions") or (
            [entry["partition"]] if "partition" in entry else [f"{key}/year=2021/month=01"]
        )
        for partition in partitions:
            if partition not in record["partitions"]:
                record["partitions"].append(partition)
    (root / ".catalog" / "research-dataset-catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "catalog_identity": "lane-i-fixture",
                "entries": [
                    {
                        "dataset_key": key,
                        "asset_class": record["asset_class"],
                        "sub_family": record["sub_family"],
                        "frequency": record["frequency"],
                        "qualification": "accepted",
                        "tier": "maintained_canonical",
                        "store_present": True,
                    }
                    for key, record in merged.items()
                ],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    overrides = starts or {}
    for key, record in merged.items():
        first = overrides.get(key, record["first_timestamp"])
        (root / ".metadata" / f"{key}.json").write_text(
            json.dumps(
                {
                    "first_timestamp": first,
                    "last_timestamp": record["last_timestamp"],
                    "partition_count": len(record["partitions"]),
                    "partitions": list(record["partitions"]),
                }
            ),
            encoding="utf-8",
        )
        for partition in record["partitions"]:
            (root / partition).mkdir(parents=True, exist_ok=True)
    return root


def _sign_policy(repo: Path, monkeypatch: pytest.MonkeyPatch, document: str = SYNTHETIC_POLICY) -> None:
    """Install a fixture-approved venue policy (arbitrary bytes, no production key)."""

    fixture_key = b"fixture-only-lane-i"
    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", fixture_key.decode())
    policy = repo / POLICY_REL
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(document, encoding="utf-8")
    manifest = f"{hashlib.sha256(document.encode()).hexdigest()}  {POLICY_REL}\n".encode()
    (repo / MANIFEST_FILE).write_bytes(manifest)
    (repo / f"{MANIFEST_FILE}.sig").write_text(
        hmac.digest(fixture_key, manifest, "sha256").hex() + "\n", encoding="ascii"
    )


def _requirement(**overrides) -> DataRequirement:
    base = {
        "asset_class": "fx",
        "frequency": "1h",
        "symbols": ("EURUSD",),
        "min_start": MIN_START,
        "price_basis": "ohlcv",
        "execution_mode": "NEXT_BAR",
    }
    base.update(overrides)
    return DataRequirement(**base)


def _fx_lake(root: Path, *, symbols=("EURUSD", "GBPUSD", "USDJPY"), cadence="minute", start="2020-06-01 00:00:00+00:00") -> Path:
    entries = [
        {
            "dataset_key": f"forex_{cadence}_{symbol}",
            "asset_class": "fx",
            "sub_family": "g10_fx",
            "frequency": cadence,
            "first_timestamp": start,
        }
        for symbol in symbols
    ]
    return _lake(root, entries)


def _tree(root: Path) -> dict[str, bytes]:
    """Every file in the fixture repo, so a no-write claim is checkable."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# 1. the requirement is a strict value object
# ---------------------------------------------------------------------------


def test_a_requirement_carries_exactly_the_declared_fields() -> None:
    requirement = _requirement(symbols=("EURUSD", "GBPUSD"), sub_family="g10_fx")
    assert requirement.asset_class == "fx"
    assert requirement.symbols == ("EURUSD", "GBPUSD")
    assert requirement.sub_family == "g10_fx"
    assert requirement.min_start == datetime(2022, 1, 1, tzinfo=UTC)
    as_dict = requirement.model_dump(mode="json")
    assert set(as_dict) == {
        "asset_class",
        "sub_family",
        "symbols",
        "all_in_family",
        "frequency",
        "min_start",
        "price_basis",
        "execution_mode",
    }
    with pytest.raises(ValidationError):
        requirement.all_in_family = True


@pytest.mark.parametrize(
    "policy_key",
    [
        {"taker_fee_bps": 0.1},
        {"slippage_bps": 0.0},
        {"holdout_start": "2023-01-01T00:00:00Z"},
        {"allow_leverage": True},
        {"max_gross_leverage": 4.0},
        {"selection_group_id": "SOME_GROUP"},
        {"simulation_epoch": "legacy"},
    ],
)
def test_a_requirement_refuses_a_key_an_agent_may_not_set(policy_key: dict) -> None:
    document = {
        "asset_class": "fx",
        "frequency": "1h",
        "symbols": ["EURUSD"],
        "min_start": MIN_START,
        "price_basis": "ohlcv",
        "execution_mode": "NEXT_BAR",
        **policy_key,
    }
    with pytest.raises(ValidationError) as caught:
        DataRequirement(**document)
    # The refusal names the key: an ignored key would be a silent policy override.
    assert next(iter(policy_key)) in str(caught.value)


def test_a_requirement_needs_exactly_one_symbol_source() -> None:
    with pytest.raises(ValidationError):
        _requirement(symbols=(), all_in_family=False)
    with pytest.raises(ValidationError):
        _requirement(symbols=("EURUSD",), all_in_family=True)


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_start": "2022-01-01T00:00:00"},          # naive: no timezone
        {"frequency": "2h"},
        {"frequency": "1d", "price_basis": "close"},
        {"symbols": ("eurusd",)},
        {"execution_mode": "NEXT_BAR_OR_BETTER"},
        {"asset_class": "equities"},
    ],
)
def test_a_requirement_fails_closed_on_unusable_input(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        _requirement(**overrides)


def test_a_requirement_reads_json_or_yaml_and_nothing_else(tmp_path: Path) -> None:
    from warden.resolve import load_requirement

    payload = _requirement().model_dump(mode="json")
    json_file = tmp_path / "requirement.json"
    json_file.write_text(json.dumps(payload), encoding="utf-8")
    assert load_requirement(json_file) == _requirement()

    yaml_file = tmp_path / "requirement.yaml"
    yaml_file.write_text(yaml.safe_dump(payload), encoding="utf-8")
    assert load_requirement(yaml_file) == _requirement()

    broken = tmp_path / "requirement.txt"
    broken.write_text("just some prose", encoding="utf-8")
    with pytest.raises(ValueError):
        load_requirement(broken)


# ---------------------------------------------------------------------------
# 2. an exact or safely-filtered signed contract is reused
# ---------------------------------------------------------------------------


def _repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    return repo


def _resolve(repo: Path, requirement: DataRequirement, *, lake_root: Path | None, policy=None,
             allow_superset: bool = False):
    return resolve_data_requirement(
        requirement, repo_root=repo, lake_root=lake_root, policy=policy,
        allow_superset=allow_superset,
    ).to_dict()


def test_an_exact_signed_match_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, "exact")
    contract = _install_contract(repo, _contract("FX_EURUSD_1H_V1"), assets=("EURUSD",))
    lake = _fx_lake(tmp_path / "lake")
    _sign_policy(repo, monkeypatch)

    payload = _resolve(repo, _requirement(), lake_root=lake)

    assert payload["decision"] == "reuse_signed"
    assert payload["reason"] == "exact_signed_match"
    assert payload["matched_contract"]["contract_id"] == "FX_EURUSD_1H_V1"
    assert payload["matched_contract"]["spec_hash"] == contract.spec_hash()
    assert payload["matched_contract"]["provenance"] == "verified_market"
    assert payload["matched_contract"]["dataset_sha256"] == contract.data_snapshot_sha256
    assert payload["selection_group_id"] == "TEST_FX_INTRADAY"
    assert payload["available_symbols"] == ["EURUSD"]
    assert payload["missing_symbols"] == []
    assert payload["shortfall_start"] is None
    assert payload["earliest_common_start"].startswith("2021-01-01")
    assert payload["policy"]["trusted"] is True


def test_a_superset_is_not_reused_without_an_explicit_opt_in(tmp_path: Path) -> None:
    """A superset hands back a wider universe than the requirement declared.

    Default: reported, never reused.  The smallest superset's drop set stays on the report,
    so the caller sees exactly what it would be buying — including the case this rule
    exists for, an ``fx`` requirement whose candidate basket carries a metal.
    """
    repo = _repo(tmp_path, "superset_opt_out")
    _install_contract(repo, _contract("FX_METALS_1H_V1"), assets=("EURUSD", "XAUUSD"))
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(symbols=("EURUSD",)), lake_root=lake)

    assert payload["decision"] == "compile_required"
    assert payload["reason"] == "superset_requires_opt_in"
    assert payload["matched_contract"] is None
    assert payload["available_symbols"] == ["EURUSD"]
    assert payload["symbols_to_drop"] == ["XAUUSD"]
    assert payload["rejected_candidates"] == [
        {
            "contract_id": "FX_METALS_1H_V1",
            "reason": "superset_requires_opt_in: contract carries 2 symbols for a 1-symbol "
            "requirement; re-resolve with allow_superset (--allow-superset) to reuse it and "
            "drop the extras",
            "symbols_to_drop": ["XAUUSD"],
        }
    ]

    opted_in = _resolve(
        repo, _requirement(symbols=("EURUSD",)), lake_root=lake, allow_superset=True
    )
    assert opted_in["decision"] == "reuse_signed"
    assert opted_in["reason"] == "superset_signed_match"
    assert opted_in["matched_contract"]["contract_id"] == "FX_METALS_1H_V1"
    assert opted_in["symbols_to_drop"] == ["XAUUSD"]


def test_a_superset_contract_is_reused_with_an_explicit_filter(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "superset")
    _install_contract(repo, _contract("FX_MAJORS_1H_V1"), assets=("EURUSD", "GBPUSD", "USDJPY"))
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(symbols=("EURUSD",)), lake_root=lake,
                       allow_superset=True)

    assert payload["decision"] == "reuse_signed"
    assert payload["reason"] == "superset_signed_match"
    assert payload["matched_contract"]["contract_id"] == "FX_MAJORS_1H_V1"
    assert payload["available_symbols"] == ["EURUSD", "GBPUSD", "USDJPY"]
    assert payload["symbols_to_drop"] == ["GBPUSD", "USDJPY"]
    assert payload["missing_symbols"] == []


def test_the_smallest_superset_wins_and_the_choice_is_deterministic(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, "smallest")
    _install_contract(repo, _contract("FX_WIDE_1H_V1"), assets=("EURUSD", "GBPUSD", "USDJPY"))
    _install_contract(repo, _contract("FX_TWO_1H_V1"), assets=("EURUSD", "GBPUSD"))
    lake = _fx_lake(tmp_path / "lake")

    first = _resolve(repo, _requirement(), lake_root=lake, allow_superset=True)
    second = _resolve(repo, _requirement(), lake_root=lake, allow_superset=True)
    assert first["matched_contract"]["contract_id"] == "FX_TWO_1H_V1"
    assert second["matched_contract"]["contract_id"] == "FX_TWO_1H_V1"
    assert [c["contract_id"] for c in first["rejected_candidates"]] == []


def test_an_exact_match_beats_a_superset_even_when_the_superset_is_allowed(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, "exact_wins")
    _install_contract(repo, _contract("FX_TWO_1H_V1"), assets=("EURUSD", "GBPUSD"))
    _install_contract(repo, _contract("FX_EURUSD_1H_V1"), assets=("EURUSD",))
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(), lake_root=lake, allow_superset=True)

    assert payload["matched_contract"]["contract_id"] == "FX_EURUSD_1H_V1"
    assert payload["reason"] == "exact_signed_match"
    assert payload["symbols_to_drop"] == []


def test_a_contract_starting_after_min_start_is_never_silently_accepted(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, "late_contract")
    _install_contract(
        repo,
        _contract("FX_LATE_1H_V1", first_bar=datetime(2022, 6, 1, tzinfo=UTC)),
        assets=("EURUSD",),
        first_bar=datetime(2022, 6, 1, tzinfo=UTC),
    )
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(), lake_root=lake)

    assert payload["decision"] == "compile_required"
    assert payload["matched_contract"] is None
    assert payload["rejected_candidates"] == [
        {
            "contract_id": "FX_LATE_1H_V1",
            "reason": "insufficient_history: contract first bar 2022-06-01T00:00:00+00:00 "
            "is after min_start 2022-01-01T00:00:00+00:00",
        }
    ]


def test_a_contract_with_the_wrong_price_basis_is_not_a_match(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "basis")
    _install_contract(repo, _contract("FX_FUNDING_1H_V1"), assets=("EURUSD",), price_basis="ohlcv_funding")
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(), lake_root=lake)

    assert payload["decision"] == "compile_required"
    assert payload["matched_contract"] is None
    assert "price_basis" in payload["rejected_candidates"][0]["reason"]


def test_an_undeclared_price_basis_is_unknown_not_ohlcv(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "basis_missing")
    _install_contract(repo, _contract("FX_BARE_1H_V1"), assets=("EURUSD",), manifest_price_basis=False)
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(), lake_root=lake)

    assert payload["decision"] == "compile_required"
    assert "price_basis" in payload["rejected_candidates"][0]["reason"]


def test_a_contract_without_a_dataset_on_disk_is_not_reused(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "no_dataset")
    contract = _install_contract(repo, _contract("FX_GONE_1H_V1"), assets=("EURUSD",))
    (repo / contract.exploratory_data_path).unlink()
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(), lake_root=lake)

    assert payload["decision"] == "compile_required"
    assert "missing exploratory dataset" in payload["rejected_candidates"][0]["reason"]


# ---------------------------------------------------------------------------
# 3. gaps fail closed, with the gap named and nothing written
# ---------------------------------------------------------------------------


def test_missing_symbols_fail_closed_with_a_gap_report_and_no_writes(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "missing_symbol")
    _install_contract(repo, _contract("FX_EURUSD_1H_V1"), assets=("EURUSD",))
    lake = _fx_lake(tmp_path / "lake", symbols=("EURUSD",))
    before = _tree(repo)

    payload = _resolve(repo, _requirement(symbols=("EURUSD", "USDJPY")), lake_root=lake)

    assert payload["decision"] == "fail_closed"
    assert payload["reason"] == "missing_symbols"
    assert payload["missing_symbols"] == ["USDJPY"]
    assert payload["gap"]["kind"] == "missing_symbols"
    assert payload["matched_contract"] is None
    assert _tree(repo) == before


def test_insufficient_history_everywhere_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "short_lake")
    lake = _fx_lake(tmp_path / "lake", start="2023-03-01 00:00:00+00:00")

    payload = _resolve(repo, _requirement(), lake_root=lake)

    assert payload["decision"] == "fail_closed"
    assert payload["reason"] == "insufficient_history"
    assert payload["gap"]["shortfall_start"].startswith("2023-03-01")
    assert payload["missing_symbols"] == []


def test_an_unavailable_lake_fails_closed_and_says_so(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "no_lake")
    payload = _resolve(repo, _requirement(), lake_root=tmp_path / "absent_lake")

    assert payload["decision"] == "fail_closed"
    assert payload["reason"] == "lake_unavailable"
    assert payload["lake"]["status"] == "unavailable"
    assert "absent_lake" in payload["lake"]["reason"]


def test_a_missing_domain_in_the_lake_is_reported_as_missing_symbols(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "empty_family")
    _lake(tmp_path / "lake", [])

    payload = _resolve(repo, _requirement(symbols=("XAUUSD",)), lake_root=tmp_path / "lake")

    assert payload["decision"] == "fail_closed"
    assert payload["missing_symbols"] == ["XAUUSD"]


# ---------------------------------------------------------------------------
# 4. archive membership and family scope
# ---------------------------------------------------------------------------


def test_an_archived_contract_never_appears_as_an_active_match(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "archived")
    archived = _install_contract(repo, _contract("FX_EURUSD_1H_V1"), assets=("EURUSD",))
    _archive(repo, archived)
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(), lake_root=lake)

    assert payload["decision"] == "compile_required"
    assert payload["matched_contract"] is None
    assert payload["rejected_candidates"] == []
    assert "FX_EURUSD_1H_V1" not in json.dumps(payload)


def test_an_active_contract_is_still_preferred_over_a_compiled_one(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "active_wins")
    live = _install_contract(repo, _contract("FX_EURUSD_1H_V2"), assets=("EURUSD",))
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(), lake_root=lake)

    assert payload["decision"] == "reuse_signed"
    assert payload["matched_contract"]["contract_id"] == "FX_EURUSD_1H_V2"
    assert payload["matched_contract"]["spec_hash"] == live.spec_hash()


def test_all_in_family_enumerates_the_lake_and_needs_a_superset(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "family")
    _install_contract(repo, _contract("FX_ALL_1H_V1"), assets=("EURUSD", "GBPUSD", "USDJPY"))
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(symbols=(), all_in_family=True), lake_root=lake)

    assert payload["decision"] == "reuse_signed"
    assert payload["requirement"]["symbols"] == []
    assert payload["available_symbols"] == ["EURUSD", "GBPUSD", "USDJPY"]
    assert payload["matched_contract"]["contract_id"] == "FX_ALL_1H_V1"


def test_all_in_family_fails_closed_when_the_lake_cannot_name_symbols(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, "family_unattributable")
    # A panel store at the source cadence: the key names no symbol and the partitions
    # carry no `symbol=` part, so the family cannot be enumerated from the catalogue.
    lake = _lake(
        tmp_path / "lake",
        [
            {
                "dataset_key": "aggregate_fx_panel",
                "asset_class": "fx",
                "sub_family": "g10_fx",
                "frequency": "minute",
            }
        ],
    )

    payload = _resolve(repo, _requirement(symbols=(), all_in_family=True), lake_root=lake)

    assert payload["decision"] == "fail_closed"
    assert payload["reason"] == "family_enumeration_unavailable"
    assert payload["lake"]["unattributed_dataset_keys"] == ["aggregate_fx_panel"]


def test_all_in_family_without_a_lake_fails_closed_on_the_lake(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "family_no_lake")
    payload = _resolve(
        repo, _requirement(symbols=(), all_in_family=True), lake_root=tmp_path / "absent"
    )

    assert payload["decision"] == "fail_closed"
    assert payload["reason"] == "lake_unavailable"
    assert payload["lake"]["status"] == "unavailable"


def test_a_symbol_outside_the_requested_sub_family_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "sub_family")
    lake = _lake(
        tmp_path / "lake",
        [
            {
                "dataset_key": "forex_daily_USDMXN",
                "asset_class": "fx",
                "sub_family": None,
                "frequency": "daily",
            }
        ],
    )

    payload = _resolve(
        repo,
        _requirement(frequency="1d", sub_family="g10_fx", symbols=("USDMXN",)),
        lake_root=lake,
    )

    assert payload["decision"] == "fail_closed"
    assert payload["reason"] == "symbol_outside_family"
    assert payload["missing_symbols"] == ["USDMXN"]


# ---------------------------------------------------------------------------
# 5. resampling and the compile path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("frequency", "required", "source"),
    [
        ("1m", False, "1m"),
        ("5m", True, "1m"),
        ("1h", True, "1m"),
        ("4h", True, "1m"),
        ("1d", False, "1d"),
    ],
)
def test_minute_resampling_is_reported_as_a_property_of_the_frequency(
    tmp_path: Path, frequency: str, required: bool, source: str
) -> None:
    repo = _repo(tmp_path, f"resample_{frequency}")
    lake = _fx_lake(tmp_path / "lake", cadence="minute" if frequency != "1d" else "daily")

    payload = _resolve(repo, _requirement(frequency=frequency), lake_root=lake)

    assert payload["resample"]["required"] is required
    assert payload["resample"]["source_frequency"] == source
    assert payload["resample"]["target_frequency"] == frequency


def test_compile_required_names_the_source_cadence_and_symbols(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "compile")
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(symbols=("EURUSD", "GBPUSD")), lake_root=lake)

    assert payload["decision"] == "compile_required"
    assert payload["reason"] == "no_matching_contract"
    assert payload["lake"]["cadence"] == "minute"
    assert payload["available_symbols"] == ["EURUSD", "GBPUSD"]
    assert payload["earliest_common_start"].startswith("2020-06-01")
    assert payload["matched_contract"] is None


def test_a_daily_requirement_reuses_a_daily_contract_without_resampling(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, "daily_reuse")
    _install_contract(
        repo, _contract("FX_DAILY_1D_V1", frequency="1d"), assets=("EURUSD",)
    )
    lake = _fx_lake(tmp_path / "lake", symbols=("EURUSD",), cadence="daily")

    payload = _resolve(repo, _requirement(frequency="1d"), lake_root=lake)

    assert payload["decision"] == "reuse_signed"
    assert payload["matched_contract"]["contract_id"] == "FX_DAILY_1D_V1"
    assert payload["resample"] == {
        "required": False,
        "source_frequency": "1d",
        "target_frequency": "1d",
        "rule": "native cadence; no resampling",
    }


def test_a_symbol_partitioned_store_attributes_symbols_conservatively(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, "partitioned")
    store = "crypto_perp_minute_panel"
    lake = _lake(
        tmp_path / "lake",
        [
            {
                "dataset_key": store,
                "asset_class": "crypto_perps",
                "sub_family": "crypto_perp",
                "frequency": "minute",
                "partition": f"{store}/symbol=BTCUSDT/year=2020/month=11",
            },
            {
                "dataset_key": store,
                "asset_class": "crypto_perps",
                "sub_family": "crypto_perp",
                "frequency": "minute",
                "partition": f"{store}/symbol=ETHUSDT/year=2021/month=03",
            },
        ],
    )

    payload = _resolve(
        repo,
        _requirement(
            asset_class="crypto_perps", frequency="1m", symbols=(), all_in_family=True
        ),
        lake_root=lake,
    )

    assert payload["decision"] == "compile_required"
    assert payload["available_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert payload["lake"]["symbols"]["BTCUSDT"]["precision"] == "month"
    # A month-granular partition proves data *inside* that month, so the bound is its end.
    assert payload["lake"]["symbols"]["BTCUSDT"]["first_bar"].startswith("2020-12-01")
    assert payload["earliest_common_start"].startswith("2021-04-01")


# ---------------------------------------------------------------------------
# 6. the selection group is policy-derived, never an input
# ---------------------------------------------------------------------------


def test_a_contract_group_that_contradicts_the_policy_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, "group_conflict")
    _sign_policy(repo, monkeypatch)
    _install_contract(
        repo, _contract("FX_WRONG_GROUP_1H_V1", declared_group="TEST_METALS_INTRADAY"),
        assets=("EURUSD",),
    )
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(), lake_root=lake)

    assert payload["decision"] == "compile_required"
    assert payload["matched_contract"] is None
    assert "selection_group" in payload["rejected_candidates"][0]["reason"]


def test_an_unmapped_policy_domain_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, "unmapped")
    document = SYNTHETIC_POLICY.replace("      intraday: TEST_FX_INTRADAY\n", "")
    _sign_policy(repo, monkeypatch, document)
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(), lake_root=lake)

    assert payload["decision"] == "fail_closed"
    assert payload["reason"] == "policy_unmapped_domain"
    assert "fx" in payload["policy"]["reason"]


def test_an_untrusted_policy_is_reported_and_no_group_is_invented(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "no_policy")
    _install_contract(repo, _contract("FX_PLAIN_1H_V1"), assets=("EURUSD",))
    lake = _fx_lake(tmp_path / "lake")

    payload = _resolve(repo, _requirement(), lake_root=lake)

    # Reuse of a legacy signed contract does not need the policy; the debt group does,
    # and an absent policy is reported as unknown rather than filled in from the YAML.
    assert payload["decision"] == "reuse_signed"
    assert payload["policy"]["trusted"] is False
    assert payload["policy"]["sha256"] is None
    assert "venue policy" in payload["policy"]["reason"]
    assert payload["selection_group_id"] is None
    assert payload["selection_group_source"] == "none"


# ---------------------------------------------------------------------------
# 7. the real CLI entry point
# ---------------------------------------------------------------------------


def _cli_payload(capsys: pytest.CaptureFixture[str]) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1])


def test_resolve_data_cli_reports_reuse_as_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, "cli_reuse")
    _install_contract(repo, _contract("FX_EURUSD_1H_V1"), assets=("EURUSD",))
    lake = _fx_lake(tmp_path / "lake")
    monkeypatch.setenv("ML4T_DATA_STORE", str(lake))
    requirement = tmp_path / "requirement.json"
    requirement.write_text(json.dumps(_requirement().model_dump(mode="json")), encoding="utf-8")
    monkeypatch.chdir(repo)

    assert cli_main(["resolve-data", "--requirement", str(requirement), "--json"]) == 0
    payload = _cli_payload(capsys)
    assert payload["ok"] is True
    assert payload["source"] == "warden.resolve-data"
    assert payload["policy"] == "allow"
    assert payload["value"]["decision"] == "reuse_signed"
    assert payload["value"]["matched_contract"]["contract_id"] == "FX_EURUSD_1H_V1"


def test_resolve_data_cli_requires_allow_superset_to_reuse_a_wider_basket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review A5: the flag is the opt-in, and the default names the drop set instead."""
    repo = _repo(tmp_path, "cli_superset")
    _install_contract(repo, _contract("FX_MAJORS_1H_V1"), assets=("EURUSD", "GBPUSD"))
    lake = _fx_lake(tmp_path / "lake")
    monkeypatch.setenv("ML4T_DATA_STORE", str(lake))
    requirement = tmp_path / "requirement.json"
    requirement.write_text(json.dumps(_requirement().model_dump(mode="json")), encoding="utf-8")
    monkeypatch.chdir(repo)

    assert cli_main(["resolve-data", "--requirement", str(requirement), "--json"]) == 0
    payload = _cli_payload(capsys)
    assert payload["value"]["decision"] == "compile_required"
    assert payload["value"]["reason"] == "superset_requires_opt_in"
    assert payload["value"]["symbols_to_drop"] == ["GBPUSD"]
    assert payload["value"]["rejected_candidates"][0]["symbols_to_drop"] == ["GBPUSD"]

    assert cli_main(
        ["resolve-data", "--requirement", str(requirement), "--allow-superset", "--json"]
    ) == 0
    payload = _cli_payload(capsys)
    assert payload["value"]["decision"] == "reuse_signed"
    assert payload["value"]["matched_contract"]["contract_id"] == "FX_MAJORS_1H_V1"
    assert payload["value"]["symbols_to_drop"] == ["GBPUSD"]


def test_resolve_data_cli_accepts_inline_json_and_exits_zero_for_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, "cli_inline")
    lake = _fx_lake(tmp_path / "lake")
    monkeypatch.setenv("ML4T_DATA_STORE", str(lake))
    monkeypatch.chdir(repo)

    inline = json.dumps(_requirement().model_dump(mode="json"))
    assert cli_main(["resolve-data", "--requirement", inline, "--json"]) == 0
    payload = _cli_payload(capsys)
    assert payload["value"]["decision"] == "compile_required"


def test_resolve_data_cli_exits_non_zero_on_a_gap_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, "cli_gap")
    lake = _fx_lake(tmp_path / "lake", symbols=("EURUSD",))
    monkeypatch.setenv("ML4T_DATA_STORE", str(lake))
    monkeypatch.chdir(repo)
    before = _tree(repo)

    inline = json.dumps(_requirement(symbols=("EURUSD", "EURJPY")).model_dump(mode="json"))
    assert cli_main(["resolve-data", "--requirement", inline, "--json"]) == 3
    payload = _cli_payload(capsys)
    assert payload["ok"] is False
    assert payload["policy"] == "deny"
    assert payload["value"]["decision"] == "fail_closed"
    assert payload["value"]["missing_symbols"] == ["EURJPY"]
    after = _tree(repo)
    assert set(after) - set(before) == {".workspace/audit/toolcalls.jsonl"}
    assert not (repo / "registry").exists()


def test_resolve_data_cli_refuses_policy_keys_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, "cli_refuse")
    monkeypatch.chdir(repo)
    document = {**_requirement().model_dump(mode="json"), "taker_fee_bps": 0.01}

    assert cli_main(["resolve-data", "--requirement", json.dumps(document), "--json"]) == 2
    captured = capsys.readouterr()
    assert "taker_fee_bps" in captured.err
    assert "WARDEN_RESOLVE_DATA_FAILED" in captured.err
    assert not (repo / "registry").exists()


def test_resolve_data_cli_never_opens_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from warden.resolve import resolve_data_requirement as api

    repo = _repo(tmp_path, "cli_no_registry")
    lake = _fx_lake(tmp_path / "lake")
    monkeypatch.setenv("ML4T_DATA_STORE", str(lake))
    monkeypatch.chdir(repo)

    def _boom(*args, **kwargs):
        raise AssertionError("resolve-data must not construct the experiment runner")

    monkeypatch.setattr("warden.cli._get_runner", _boom)
    inline = json.dumps(_requirement().model_dump(mode="json"))
    assert cli_main(["resolve-data", "--requirement", inline, "--json"]) == 0
    capsys.readouterr()
    # The API is the same seam the CLI uses; nothing in a resolve needs the registry.
    assert api(_requirement(), repo_root=repo, lake_root=lake).decision == "compile_required"
    assert not (repo / "registry").exists()
