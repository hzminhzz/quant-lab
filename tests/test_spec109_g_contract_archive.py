"""Spec #109 lane G (#114) — deprecated contracts archived without losing replay.

Scope of the evidence in this file (all local, all synthetic except the *read-only*
assertions against the shipped contract archive):

* the archive namespace holds exactly the contracts the reviewed lifecycle document
  closes, with their dataset manifests, and no live copy survives next to them;
* the archived bytes and ``spec_hash`` values are the **pre-move** ones: the pinned
  table below was captured from the integration base commit before any file moved, and
  one test re-derives the same bytes from git history to show the table is not a
  description of the post-move layout;
* *active* resolution (new work) refuses an archived id and names the archive location,
  while *recorded* resolution (historical replay) still loads the same contract object
  and the same digest;
* the registration gate refuses an archived contract even when the lifecycle document is
  absent, so the refusal is a property of the archive, not a coincidence of one file;
* ``warden data fence`` still credits an exploratory file that an archived contract owns;
* the archive document fails closed on corruption or digest drift and the move is
  mechanically reversible (archive path -> original path).

No production registry, holdout, manifest signature or signing key is touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
import yaml

from warden.cli import main as cli_main
from warden.contract_source import (
    ARCHIVE_DIR,
    ARCHIVE_MAP_NAME,
    ArchivedContractRefused,
    ContractArchive,
    ContractArchiveError,
    active_contract_paths,
    all_contract_paths,
    dataset_manifest_path,
    load_active_contract,
    load_contract_archive,
    load_recorded_contract,
    resolve_contract_source,
)
from warden.contracts import EvaluationContract, load_contract
from warden.lifecycle import ContractLifecycleBlocked, load_lifecycle_registry
from warden.market_data import read_parquet_bytes
from warden.provenance import DatasetProvenanceError, resolve_dataset
from warden.runner import ExperimentRunner
from warden_evidence.exploratory_fence import audit_exploratory_storage

REPO_ROOT = Path(__file__).resolve().parents[1]

#: ``contract_id -> (yaml name, dataset manifest name, spec_hash, yaml sha256, json sha256)``
#: Captured from the integration base commit *before* the files moved.  A digest here is a
#: real recorded value, never recomputed from the archive layout under test.
PRE_MOVE = {
    "INDICES_1H_V1": (
        "indices_1h_v1.yaml", "INDICES_1H_V1.json", "5f676e527a4f69ab",
        "8d4798b9e75207da98a4ac7e016b87f81e19f009fc2fe2997804d3a0dc3b375e",
        "cf1a4139c304ae8fbd17196a7db2daaf59a6664a74da7fdfff673ad9091fcc79",
    ),
    "FX_MAJORS_1H_V1": (
        "fx_majors_1h_v1.yaml", "FX_MAJORS_1H_V1.json", "629bae1d62dbfd17",
        "82d25d9468c8b292756d1c20ba4800c0d991ff3f42d83e8fab0c34a996db5626",
        "456953931acae689480e60b3bec218d2f12c857a9cbee0e2c5a2db27ce22a9d4",
    ),
    "FX_MAJORS_1D_V1": (
        "fx_majors_1d_v1.yaml", "FX_MAJORS_1D_V1.json", "bb01d60a9ea90527",
        "e18028a6a6f408f66a2c041bdb21c4f08a40211c56da3c50716231ac5f5e35c7",
        "c6acde7d19592655e14653644516b9794f56925343066f0475f5adbe0f133337",
    ),
    "FX_MAJORS_30M_V1": (
        "fx_majors_30m_v1.yaml", "FX_MAJORS_30M_V1.json", "bcd88fa42c5313cc",
        "931070d3d01a9436f9abb11034ebeff31846b2dd7d2185d12523da500deae581",
        "d946c5277fe84894868a472581c0f6b69b0772ec01175ccc04e721bf42991d57",
    ),
    "FX_MAJORS_15M_V1": (
        "fx_majors_15m_v1.yaml", "FX_MAJORS_15M_V1.json", "5778652f86feb4a6",
        "c7ed5c04faa21cd9b0f2be77c28c651a1b11224c268ac5a174642a245e5d76b2",
        "38a03c0a1d2aa6a6287d857cd2a3e0ddab78a5ef4a9e50bced75f5b022847137",
    ),
    "FX_MAJORS_4H_V1": (
        "fx_majors_4h_v1.yaml", "FX_MAJORS_4H_V1.json", "170c2dadb4e1cd13",
        "420731ff6329bdd073fa657ae2584591ea45c41b59ca88563caa3339ade81f79",
        "93eb1164bd81f4053dad33a471e30a20eddd24ab6866877aea8ff3ab4e413a29",
    ),
    "FX_MAJORS_5M_V1": (
        "fx_majors_5m_v1.yaml", "FX_MAJORS_5M_V1.json", "43a04a1c9aa153b1",
        "20c28b6b4e606f6c13f73e446d917c14491664fa7cb46812b7e8db17e0214e33",
        "2f40894a919f3c0719032ed6072d443099f62c4ab773f0450a9f37371b41f317",
    ),
    "FX_MAJORS_1M_V1": (
        "fx_majors_1m_v1.yaml", "FX_MAJORS_1M_V1.json", "e159945eb3ddef88",
        "700a544d52e0d6312297a138653b1a47e386dfac5c225571af7105af2b40bcda",
        "2ec519b10092b36376cc46d6217a0a79169b3c9bee1947122736395e8b65411f",
    ),
    "CRYPTO_PERPS_1H_V1": (
        "crypto_perps_1h_v1.yaml", "CRYPTO_PERPS_1H_V1.json", "f24108d3be8852e1",
        "6243c74550573da1fe55d3bee5a1fddb54aff3df06ffd503ea491fa60783ae24",
        "27aae9c64ae6da150b1d6a66bdaf1dc7367a5a0ad2e6c281411722a982e545e2",
    ),
    "METALS_1M_V1": (
        "metals_1m_v1.yaml", "METALS_1M_V1.json", "6710b49de3886263",
        "204ac130a603b011a3a328b13cf4b9bcd9ae9e49017e076de41a68e4a98df054",
        "d1c2ac2a208b9462aea74720cd9d91b2be54356f72ee128079fcffe39ddf1b4b",
    ),
    "CRYPTO_MAJORS_1H_V1": (
        "crypto_majors_1h_v1.yaml", "CRYPTO_MAJORS_1H_V1.json", "b298b53e3654d455",
        "b11e9298e1dd6812a5dc00d8334d162dc2e1635ea807d03c0375872e1299b336",
        "c85f16d51feaf9b49351731ab63056ad7a371a5fa28b95f785c4a0d3b34fc312",
    ),
    "CRYPTO_PERPS_TOP100_1H_V1": (
        "crypto_perps_top100_1h_v1.yaml", "CRYPTO_PERPS_TOP100_1H_V1.json", "9f138c29cd8c4778",
        "eb76ea6107090c72bb9dd7c879d6791d3a967deec9f089f48c175669a5155e19",
        "0685548fc288684537ef73d99ef30b3cf07b212876bab443f30b6a2488390123",
    ),
    "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1": (
        "auto_crypto_perps_top100_1h_v1_h24_e24_f45_s20_v1.yaml",
        "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1.json", "cfb99eb95b5a2d6e",
        "cb3d69251e005c78d567be61883c3e73902c4aeee70602e32280726da87341c2",
        "caac717d21033230602fdb7b967bc0a352c5705cebff7c0aee9fdd48a27b0017",
    ),
}

ARCHIVED = frozenset(PRE_MOVE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 1. the archive namespace and its mapping
# ---------------------------------------------------------------------------


def test_archive_holds_exactly_the_contracts_the_lifecycle_document_closes() -> None:
    archive = load_contract_archive(REPO_ROOT)
    lifecycle = load_lifecycle_registry(REPO_ROOT, strict=True)
    blocking = set(lifecycle.blocking_entries())
    assert set(archive.entries) == blocking == ARCHIVED
    assert len(archive.entries) == 13
    # Nothing the archive holds is still reachable as an active contract document.
    active_ids = {load_contract(p).contract_id for p in active_contract_paths(REPO_ROOT)}
    assert active_ids.isdisjoint(ARCHIVED)
    assert not (REPO_ROOT / "contracts" / "archive_map.json").exists()
    for contract_id, (yaml_name, json_name, *_rest) in PRE_MOVE.items():
        assert not (REPO_ROOT / "contracts" / yaml_name).exists(), f"live copy of {contract_id} survived"
        assert not (REPO_ROOT / "contracts" / "datasets" / json_name).exists()
        entry = archive.entry(contract_id)
        assert entry is not None
        assert entry.original_path == f"contracts/{yaml_name}"
        assert entry.archive_path == f"contracts/archive/{yaml_name}"
        assert entry.dataset_original_path == f"contracts/datasets/{json_name}"
        assert entry.dataset_archive_path == f"contracts/archive/datasets/{json_name}"
    # Every contract document in the tree is still reachable: 36 legacy contracts, 13 of
    # them from the archive and 23 from the live directory.
    assert len(all_contract_paths(REPO_ROOT)) == 36
    assert len(active_contract_paths(REPO_ROOT)) == 23


def test_archived_bytes_and_digests_are_the_pre_move_record() -> None:
    archive = load_contract_archive(REPO_ROOT)
    for contract_id, (yaml_name, json_name, spec_hash, yaml_sha, json_sha) in PRE_MOVE.items():
        entry = archive.entry(contract_id)
        assert entry is not None
        yaml_path = REPO_ROOT / entry.archive_path
        json_path = REPO_ROOT / entry.dataset_archive_path
        assert yaml_path.is_file() and json_path.is_file()
        assert _sha256(yaml_path) == entry.sha256 == yaml_sha
        assert _sha256(json_path) == entry.dataset_sha256 == json_sha
        archived = load_recorded_contract(REPO_ROOT, contract_id)
        # Same object shape, same identity, same digest: nothing about the move is visible.
        assert archived.contract_id == contract_id
        assert archived.spec_hash() == entry.spec_hash == spec_hash
        source = resolve_contract_source(REPO_ROOT, contract_id, allow_archive=True)
        assert source.location == "archive"
        assert source.path.resolve() == yaml_path.resolve()


def test_pre_move_bytes_are_recoverable_from_the_recorded_commit() -> None:
    """The pinned table is not a description of the post-move layout."""
    archive = load_contract_archive(REPO_ROOT)
    assert archive.moved_from_commit
    for contract_id in ("FX_MAJORS_1H_V1", "CRYPTO_PERPS_1H_V1", "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1"):
        entry = archive.entry(contract_id)
        assert entry is not None
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "cat-file", "-p", f"{archive.moved_from_commit}:{entry.original_path}"],
            capture_output=True,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        if proc.returncode != 0:
            pytest.skip(f"pre-move commit {archive.moved_from_commit} is not available in this checkout")
        before = hashlib.sha256(proc.stdout).hexdigest()
        assert before == _sha256(REPO_ROOT / entry.archive_path)


def test_the_move_is_mechanically_reversible() -> None:
    archive = load_contract_archive(REPO_ROOT)
    plan = archive.reverse_plan()
    assert len(plan) == 26  # 13 contract documents + 13 dataset manifests
    seen_sources: set[str] = set()
    for archive_rel, original_rel in plan:
        assert archive_rel not in seen_sources
        seen_sources.add(archive_rel)
        assert (REPO_ROOT / archive_rel).is_file()
        assert not (REPO_ROOT / original_rel).exists(), f"reverse target {original_rel} already occupied"
    assert {(src, dst) for src, dst in plan} == {
        (f"contracts/archive/{yaml_name}", f"contracts/{yaml_name}") for yaml_name, *_ in PRE_MOVE.values()
    } | {
        (f"contracts/archive/datasets/{json_name}", f"contracts/datasets/{json_name}")
        for _yaml, json_name, *_ in PRE_MOVE.values()
    }


# ---------------------------------------------------------------------------
# 2. active resolution refuses, recorded resolution replays
# ---------------------------------------------------------------------------


def test_active_lookup_refuses_an_archived_contract_and_names_the_archive() -> None:
    with pytest.raises(ArchivedContractRefused) as caught:
        load_active_contract(REPO_ROOT, "FX_MAJORS_1H_V1")
    message = str(caught.value)
    assert "contracts/archive/fx_majors_1h_v1.yaml" in message
    assert "FX_MAJORS_1H_V1" in message
    assert "DEPRECATED_UNEXECUTABLE" in message
    assert isinstance(caught.value, FileNotFoundError)  # existing callers catch FileNotFoundError

    runner = ExperimentRunner(repo_root=REPO_ROOT)
    with pytest.raises(ArchivedContractRefused):
        runner.resolve_active_contract("CRYPTO_PERPS_1H_V1")
    # The live id is still active and resolves normally.
    assert runner.resolve_active_contract("CRYPTO_PERPS_1H_V2").contract_id == "CRYPTO_PERPS_1H_V2"


def test_active_lookup_never_falls_back_to_a_live_copy(tmp_path: Path) -> None:
    repo = _archive_repo(tmp_path, "no_fallback")
    # A decoy live copy of the same contract id: the archive is authoritative, so an active
    # lookup must refuse instead of quietly resolving the live document.
    (repo / "contracts" / "FX_MAJORS_1H_V1.yaml".lower()).write_bytes(
        (repo / ARCHIVE_DIR / "fx_majors_1h_v1.yaml").read_bytes()
    )
    with pytest.raises(ArchivedContractRefused):
        load_active_contract(repo, "FX_MAJORS_1H_V1")
    assert load_recorded_contract(repo, "FX_MAJORS_1H_V1").contract_id == "FX_MAJORS_1H_V1"


def test_recorded_lookup_refuses_a_live_copy_that_does_not_match_the_recorded_digest(
    tmp_path: Path,
) -> None:
    """Review A2: the recorded digest is checked whichever document resolves.

    A live ``contracts/<id>.yaml`` for an archived id used to win with its bytes unverified,
    so the same id resolved to two different byte sets depending on the lookup — a 10x
    looser cost floor replaying as if it were recorded evidence.
    """
    repo = _archive_repo(tmp_path, "decoy_digest")
    archived = (repo / ARCHIVE_DIR / "fx_majors_1h_v1.yaml").read_text(encoding="utf-8")
    decoy = archived.replace("taker_fee_bps: 0.2", "taker_fee_bps: 0.02")
    assert decoy != archived
    live = repo / "contracts" / "fx_majors_1h_v1.yaml"
    live.write_text(decoy, encoding="utf-8")

    recorded_sha = PRE_MOVE["FX_MAJORS_1H_V1"][3]
    decoy_sha = hashlib.sha256(decoy.encode()).hexdigest()
    with pytest.raises(ContractArchiveError) as caught:
        load_recorded_contract(repo, "FX_MAJORS_1H_V1")
    message = str(caught.value)
    # The refusal names both digests and the document it refused, so an operator can tell
    # a substituted live copy from a drifted archive.
    assert recorded_sha[:12] in message and decoy_sha[:12] in message
    assert "contracts/fx_majors_1h_v1.yaml" in message

    # Control: with the decoy gone the recorded bytes still replay, at the recorded cost.
    live.unlink()
    assert load_recorded_contract(repo, "FX_MAJORS_1H_V1").taker_fee_bps == 0.2


def test_recorded_lookup_replays_a_trial_recorded_against_an_archived_contract() -> None:
    runner = ExperimentRunner(repo_root=REPO_ROOT)
    for contract_id in ("FX_MAJORS_1D_V1", "CRYPTO_MAJORS_1H_V1"):
        contract = runner.resolve_contract(contract_id)
        assert contract.contract_id == contract_id
        assert contract.spec_hash() == PRE_MOVE[contract_id][2]
        source = resolve_contract_source(REPO_ROOT, contract_id, allow_archive=True)
        assert source.location == "archive" and source.path.is_file()


def test_active_data_resolution_refuses_an_archived_contract() -> None:
    contract = load_recorded_contract(REPO_ROOT, "FX_MAJORS_1H_V1")
    with pytest.raises(DatasetProvenanceError) as caught:
        resolve_dataset(contract, REPO_ROOT)
    assert "archived" in str(caught.value).lower()
    assert "contracts/archive/fx_majors_1h_v1.yaml" in str(caught.value)


def test_a_live_contract_is_still_resolved_as_active() -> None:
    # Control: the archive gate must not turn every lookup into a refusal.
    source = resolve_contract_source(REPO_ROOT, "CRYPTO_PERPS_1H_V2", allow_archive=False)
    assert source.location == "active" and source.path.is_file()


def test_dataset_manifest_is_resolvable_from_the_archive() -> None:
    manifest = dataset_manifest_path(REPO_ROOT, "FX_MAJORS_1H_V1")
    assert manifest is not None
    assert manifest.as_posix().endswith("contracts/archive/datasets/FX_MAJORS_1H_V1.json")
    assert _sha256(manifest) == PRE_MOVE["FX_MAJORS_1H_V1"][4]
    live = dataset_manifest_path(REPO_ROOT, "CRYPTO_PERPS_1H_V2")
    assert live is not None and live.as_posix().endswith("contracts/datasets/CRYPTO_PERPS_1H_V2.json")


# ---------------------------------------------------------------------------
# 3. new-hypothesis registration refuses an archived contract
# ---------------------------------------------------------------------------


def test_registration_gate_names_the_archive_location() -> None:
    lifecycle = load_lifecycle_registry(REPO_ROOT, strict=True)
    with pytest.raises(ContractLifecycleBlocked) as caught:
        lifecycle.assert_open("FX_MAJORS_1H_V1")
    message = str(caught.value)
    assert "DEPRECATED_UNEXECUTABLE" in message
    assert "contracts/archive/fx_majors_1h_v1.yaml" in message


def test_registration_gate_refuses_an_archived_contract_even_without_a_lifecycle_entry(
    tmp_path: Path,
) -> None:
    repo = _archive_repo(tmp_path, "no_lifecycle_entry")
    lifecycle = load_lifecycle_registry(repo)
    assert lifecycle.entry("FX_MAJORS_1H_V1") is None
    with pytest.raises(ContractLifecycleBlocked) as caught:
        lifecycle.assert_open("FX_MAJORS_1H_V1")
    assert "contracts/archive/fx_majors_1h_v1.yaml" in str(caught.value)
    # An unrelated contract is unaffected.
    lifecycle.assert_open("SOME_OTHER_V1")


def test_register_hypothesis_cli_refuses_an_archived_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _registry_repo(tmp_path, "cli_register")
    hyp_file = repo / ".workspace" / "hypotheses" / "HYP-G-ARCH.yaml"
    hyp_file.write_text(
        yaml.safe_dump(
            {
                "hypothesis_id": "HYP-G-ARCH",
                "contract_id": "FX_MAJORS_1H_V1",
                "title": "Archived contract fixture",
                "strategy_family": "rules_ts",
                "economic_mechanism": "Mechanism explanation of twenty chars",
                "counterparty_or_friction": "Friction explanation of twenty chars",
                "expected_holding_bars": 8,
                "falsification_criteria": "Reject net Sharpe below one",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    assert cli_main(["register-hypothesis", str(hyp_file), "--json"]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "contracts/archive/fx_majors_1h_v1.yaml" in err
    import sqlite3

    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 4. the exploratory fence still credits archived contracts
# ---------------------------------------------------------------------------


def _fence_repo(tmp_path: Path, *, archived: bool) -> Path:
    repo = tmp_path / ("fence_archive" if archived else "fence_live")
    for rel in ("contracts/datasets", "contracts/archive/datasets", "data/exploratory"):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    contract_dir = repo / "contracts" / "archive" if archived else repo / "contracts"
    manifest_dir = repo / "contracts" / "archive" / "datasets" if archived else repo / "contracts" / "datasets"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    contract = _panel_contract("G_FENCE_V1", start + timedelta(days=3))
    (contract_dir / "g_fence_v1.yaml").write_text(yaml.safe_dump(contract.to_dict()), encoding="utf-8")
    panel = _panel(start, 12)
    parquet = repo / "data" / "exploratory" / "panel.parquet"
    panel.write_parquet(parquet)
    (manifest_dir / "G_FENCE_V1.json").write_text(
        json.dumps(
            {
                "contract_id": "G_FENCE_V1",
                "path": "data/exploratory/panel.parquet",
                "sha256": _sha256(parquet),
                "provenance": "synthetic",
                "source": "g114_fixture",
            }
        ),
        encoding="utf-8",
    )
    if archived:
        (repo / ARCHIVE_DIR / ARCHIVE_MAP_NAME).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "moved_from_commit": "",
                    "contracts": [
                        {
                            "contract_id": "G_FENCE_V1",
                            "original_path": "contracts/g_fence_v1.yaml",
                            "archive_path": "contracts/archive/g_fence_v1.yaml",
                            "dataset_original_path": "contracts/datasets/G_FENCE_V1.json",
                            "dataset_archive_path": "contracts/archive/datasets/G_FENCE_V1.json",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
    return repo


@pytest.mark.parametrize("archived", [True, False])
def test_fence_credits_a_contract_from_the_archive_namespace(tmp_path: Path, archived: bool) -> None:
    repo = _fence_repo(tmp_path, archived=archived)
    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert report.ok is True, report.to_dict()
    assert not report.failures
    assert [f.contract_id for f in report.files] == ["G_FENCE_V1"]


# ---------------------------------------------------------------------------
# 5. fail closed on corruption, digest drift and an unusable mapping
# ---------------------------------------------------------------------------


def _archive_repo(tmp_path: Path, name: str) -> Path:
    """A minimal repo carrying one real archived contract document and its manifest."""
    repo = tmp_path / name
    (repo / "contracts" / "archive" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    yaml_name, json_name, spec_hash, yaml_sha, json_sha = PRE_MOVE["FX_MAJORS_1H_V1"]
    shutil.copyfile(REPO_ROOT / "contracts" / "archive" / yaml_name, repo / "contracts" / "archive" / yaml_name)
    shutil.copyfile(
        REPO_ROOT / "contracts" / "archive" / "datasets" / json_name,
        repo / "contracts" / "archive" / "datasets" / json_name,
    )
    (repo / ARCHIVE_DIR / ARCHIVE_MAP_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-11-27T00:00:00Z",
                "moved_from_commit": "0" * 40,
                "contracts": [
                    {
                        "contract_id": "FX_MAJORS_1H_V1",
                        "status": "DEPRECATED_UNEXECUTABLE",
                        "original_path": f"contracts/{yaml_name}",
                        "archive_path": f"contracts/archive/{yaml_name}",
                        "dataset_original_path": f"contracts/datasets/{json_name}",
                        "dataset_archive_path": f"contracts/archive/datasets/{json_name}",
                        "spec_hash": spec_hash,
                        "sha256": yaml_sha,
                        "dataset_sha256": json_sha,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return repo


def test_a_corrupt_archive_map_fails_closed(tmp_path: Path) -> None:
    repo = _archive_repo(tmp_path, "corrupt")
    (repo / ARCHIVE_DIR / ARCHIVE_MAP_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ContractArchiveError):
        load_contract_archive(repo)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.update({"archive_root": "elsewhere"}),
        lambda doc: doc["contracts"][0].update({"archive_path": "contracts/elsewhere.yaml"}),
        lambda doc: doc["contracts"][0].update({"contract_id": "ACTIVE_V2_ID"}),
        lambda doc: doc["contracts"][0].update({"status": "ACTIVE_V2"}),
        lambda doc: doc["contracts"].append(dict(doc["contracts"][0])),
    ],
    ids=["unknown-key", "path-escapes-archive", "invalid-contract-id", "active-contract-archived", "duplicate"],
)
def test_an_unusable_archive_map_is_refused(tmp_path: Path, mutate) -> None:
    repo = _archive_repo(tmp_path, "unusable")
    path = repo / ARCHIVE_DIR / ARCHIVE_MAP_NAME
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractArchiveError):
        load_contract_archive(repo)


def test_digest_drift_on_an_archived_contract_fails_closed(tmp_path: Path) -> None:
    repo = _archive_repo(tmp_path, "drift")
    archived = repo / "contracts" / "archive" / "fx_majors_1h_v1.yaml"
    archived.write_bytes(archived.read_bytes() + b"\n# edited\n")
    with pytest.raises(ContractArchiveError):
        load_recorded_contract(repo, "FX_MAJORS_1H_V1")
    # The mapping itself is untouched, so the drift is the archive's, not the reader's.
    archive = load_contract_archive(repo)
    assert archive.entry("FX_MAJORS_1H_V1") is not None


def test_an_absent_archive_is_an_empty_namespace(tmp_path: Path) -> None:
    archive = load_contract_archive(tmp_path)
    assert isinstance(archive, ContractArchive)
    assert archive.entries == {} and archive.source == "absent"


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------


def _panel_contract(contract_id: str, holdout_start: datetime) -> EvaluationContract:
    start = holdout_start - timedelta(days=3)
    return EvaluationContract(
        contract_id=contract_id,
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="data/exploratory/panel.parquet",
        holdout_data_path="data/holdout/panel_holdout.parquet",
        train_start=start,
        val_start=start + timedelta(hours=12),
        val_end=start + timedelta(hours=23),
        holdout_start=holdout_start,
        holdout_end=holdout_start + timedelta(days=10),
        n_folds=2,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=5.0,
        slippage_bps=1.0,
        execution_mode="NEXT_BAR",
        data_provenance="synthetic",
        data_snapshot_sha256="0" * 64,
    )


def _panel(start: datetime, bars: int) -> pl.DataFrame:
    stamps = [start + timedelta(hours=i) for i in range(bars)]
    return (
        pl.DataFrame(
            {
                "timestamp": stamps,
                "asset": ["BTCUSDT"] * bars,
                "open": [100.0 + i for i in range(bars)],
                "high": [101.0 + i for i in range(bars)],
                "low": [99.0 + i for i in range(bars)],
                "close": [100.5 + i for i in range(bars)],
                "volume": [10.0] * bars,
            }
        )
        .sort("timestamp", "asset")
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    )


def _registry_repo(tmp_path: Path, name: str) -> Path:
    """The archive plus the minimum a registration gate needs to run the CLI."""
    from warden_ops.integrity import generate_manifest

    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    for rel in (
        "contracts/datasets",
        "contracts/archive/datasets",
        "registry",
        ".workspace/hypotheses",
        "experiments/active",
        "strategies/candidates",
        "cache/features",
        "src/warden",
    ):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / "AGENTS.md").write_text("# g114 fixture\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='g114'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version=1\n", encoding="utf-8")
    for yaml_name, json_name in ((v[0], v[1]) for v in PRE_MOVE.values()):
        shutil.copyfile(REPO_ROOT / "contracts" / "archive" / yaml_name, repo / "contracts" / "archive" / yaml_name)
        shutil.copyfile(
            REPO_ROOT / "contracts" / "archive" / "datasets" / json_name,
            repo / "contracts" / "archive" / "datasets" / json_name,
        )
    shutil.copyfile(REPO_ROOT / ARCHIVE_DIR / ARCHIVE_MAP_NAME, repo / ARCHIVE_DIR / ARCHIVE_MAP_NAME)
    generate_manifest(repo)
    return repo


# ---------------------------------------------------------------------------
# 4. a build may not overwrite a dataset any recorded contract pins
# ---------------------------------------------------------------------------


def test_pinned_dataset_paths_include_the_archive_namespace(tmp_path: Path) -> None:
    """`warden data build` must not create a file an archived contract pins.

    The archive is unexecutable for *new work*, but its datasets are historical
    evidence; dropping archived contracts from the pinned-path inventory would let
    a build overwrite a file that a recorded trial still points at.
    """
    from warden.cli import _pinned_dataset_paths

    repo = tmp_path / "pins"
    (repo / "contracts" / "archive").mkdir(parents=True)
    (repo / "contracts" / "live_v1.yaml").write_text(
        "contract_id: LIVE_V1\nexploratory_data_path: data/exploratory/live.parquet\n",
        encoding="utf-8",
    )
    (repo / "contracts" / "archive" / "closed_v1.yaml").write_text(
        "contract_id: CLOSED_V1\nexploratory_data_path: data/exploratory/closed.parquet\n",
        encoding="utf-8",
    )

    pinned = _pinned_dataset_paths(repo)

    assert pinned["data/exploratory/live.parquet"] == "LIVE_V1"
    assert pinned["data/exploratory/closed.parquet"] == "CLOSED_V1"
