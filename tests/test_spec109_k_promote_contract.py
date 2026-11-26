"""Spec #109 lane K (#118) — human-only promotion of a compiled sandbox contract.

The claim this file makes:

* promotion is a **human-only** step: it needs the operator token a human types, it is
  refused under ``WARDEN_NON_INTERACTIVE=1``, and the request itself (token, unknown
  contract, re-promotion) is refused before a byte is copied;
* the compiled-artifact contract is re-validated **in the order**
  ``warden.compile.ARTIFACT_CONTRACT`` states — paths, bytes, raw Merkle root,
  spec/contract identity, policy, fence, lake provenance, namespace — and every refusal is
  named, non-zero and atomic (nothing copied, no registry mutation);
* on success the contract and its dataset manifest are copied with **identical bytes**, a
  promotion receipt records who/what/when and every digest, and the command tells the human
  to re-sign the manifest — it never signs it, never generates it, never opens a holdout;
* promotion alone is **not** holdout eligibility: the sandbox original stays refused by
  name, the promoted copy is still ``unknown`` provenance (no signed admission), and
  ``qualify`` keeps refusing without a claim.

Every fixture is synthetic: a scratch repo, a fixture-owned signed manifest, a Hive minute
lake, a fixture strategy module.  No production registry, holdout byte, real signature or
lake write.
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

from warden.compile import compile_data_requirement

START = datetime(2021, 1, 1, tzinfo=UTC)
FENCE = datetime(2021, 1, 4, tzinfo=UTC)
POLICY_REL = "contracts/policy/venue_policy.yaml"
MANIFEST_FILE = ".warden_manifest.sha256"
FIXTURE_KEY = "fixture-only-lane-k"
TOKEN = "YES_PROMOTE"

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
"""


# ---------------------------------------------------------------------------
# fixtures (same synthetic lake/policy as lane J, so the artifact is the real shape)
# ---------------------------------------------------------------------------


def _rows(total_minutes: int, base_cents: int = 10_000) -> list[dict]:
    closes = [base_cents]
    for index in range(1, total_minutes):
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


def _lake(
    root: Path,
    *,
    symbols: tuple[str, ...] = ("EURUSD",),
    days: tuple[str, ...] = ("01", "02", "03"),
    per_day: int = 60 * 24,
) -> Path:
    """A synthetic minute lake: ``<key>/year=/month=/day=/part-0.parquet`` plus metadata."""
    (root / ".catalog").mkdir(parents=True, exist_ok=True)
    (root / ".metadata").mkdir(parents=True, exist_ok=True)
    entries = []
    for symbol in symbols:
        key = f"forex_minute_{symbol}"
        entry_partitions: list[str] = []
        cursor = START
        for offset, day in enumerate(days):
            relative = f"year=2021/month=01/day={day}"
            path = root / key / relative / "part-0.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            _minute_frame(symbol, cursor, per_day, first_index=offset * per_day).write_parquet(path)
            entry_partitions.append(f"{key}/{relative}")
            cursor += timedelta(days=1)
        entries.append(
            {
                "dataset_key": key,
                "asset_class": "fx",
                "sub_family": "g10_fx",
                "frequency": "minute",
                "entry_identity": hashlib.sha256(f"{key}:entry".encode()).hexdigest(),
                "consumer_identity": hashlib.sha256(f"{key}:consumer".encode()).hexdigest(),
                "acceptance": {
                    "present": True,
                    "relative_path": f".acceptance/{key}/dataset.json",
                    "sha256": hashlib.sha256(f"{key}:acceptance".encode()).hexdigest(),
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
                "catalog_identity": "lane-k-fixture-catalog",
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


def _compile(repo: Path, lake: Path, requirement: dict | None = None):
    from warden.market_data import read_parquet_bytes

    return compile_data_requirement(
        requirement or _requirement(),
        repo_root=repo,
        lake_root=lake,
        parquet_reader=read_parquet_bytes,
    )


def _tree(root: Path) -> dict[str, bytes]:
    """Every file in the fixture repo except the CLI's own audit trail of the attempt."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.relative_to(root).as_posix().startswith(".workspace/audit/")
    }


def _last_json(capsys) -> dict:
    """The one JSON envelope a ``--json`` run prints (sentinels and prose are not JSON)."""
    out = capsys.readouterr().out
    start = out.find("{")
    if start < 0:
        raise AssertionError(f"no JSON envelope in output: {out!r}")
    payload, _end = json.JSONDecoder().raw_decode(out[start:])
    return payload


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A scratch repo (signed policy) plus a fenced synthetic minute lake."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lake = _lake(tmp_path / "lake")
    _policy(repo, monkeypatch)
    return repo, lake


@pytest.fixture
def compiled(workspace):
    """The compiled sandbox artifact this file promotes."""
    repo, lake = workspace
    artifact = _compile(repo, lake)
    return repo, lake, artifact


def _promote(repo: Path, contract_id: str, **kwargs) -> int:
    from warden.cli import main as cli_main

    argv = ["promote-contract", "--contract-id", contract_id,
            "--human-confirm-promote", TOKEN, "--operator", "fixture-operator", "--json"]
    return cli_main(argv, **kwargs)


def _production_paths(contract_id: str) -> tuple[str, str, str]:
    return (
        f"contracts/{contract_id.lower()}.yaml",
        f"contracts/datasets/{contract_id}.json",
        f"contracts/promotions/{contract_id}.json",
    )


# ---------------------------------------------------------------------------
# 1. the human gate: token, non-interactive, and the request itself
# ---------------------------------------------------------------------------


def test_promotion_is_a_human_only_policy_gate() -> None:
    from warden.policy import APPROVAL, authorize

    decision = authorize("promote-contract")
    assert decision.allow and decision.approval_required
    assert "promote-contract" in APPROVAL


def test_the_cli_refuses_without_the_operator_token(compiled, monkeypatch, capsys) -> None:
    from warden.cli import main as cli_main

    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    before = _tree(repo)
    code = cli_main(["promote-contract", "--contract-id", artifact.contract_id, "--json"])
    assert code == 2
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err and "--human-confirm-promote" in captured.err
    assert captured.out.strip() == ""
    assert _tree(repo) == before


def test_the_cli_refuses_a_wrong_token_by_name(compiled, monkeypatch, capsys) -> None:
    from warden.cli import main as cli_main

    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    before = _tree(repo)
    code = cli_main(["promote-contract", "--contract-id", artifact.contract_id,
                     "--human-confirm-promote", "yes", "--json"])
    assert code == 2
    captured = capsys.readouterr()
    assert "not_authorized" in captured.err and TOKEN in captured.err
    assert "WARDEN_PROMOTE_CONTRACT_FAILED" in captured.err
    assert _tree(repo) == before


def test_a_non_interactive_caller_is_refused_even_with_the_token(
    compiled, monkeypatch, capsys
) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    monkeypatch.setenv("WARDEN_NON_INTERACTIVE", "1")
    before = _tree(repo)
    assert _promote(repo, artifact.contract_id) == 2
    captured = capsys.readouterr()
    assert "not_authorized" in captured.err
    assert "WARDEN_NON_INTERACTIVE=1" in captured.err
    assert _tree(repo) == before


def test_an_unknown_contract_is_refused_before_anything_is_copied(
    compiled, monkeypatch, capsys
) -> None:
    repo, _lake_root, _artifact = compiled
    monkeypatch.chdir(repo)
    before = _tree(repo)
    assert _promote(repo, "SANDBOX_FX_1H_NOT_A_CONTRACT") == 2
    captured = capsys.readouterr()
    assert "unknown_contract" in captured.err
    assert "contracts/sandbox" in captured.err
    assert _tree(repo) == before


def test_a_live_contract_is_not_a_promotion_candidate(workspace, monkeypatch, capsys) -> None:
    """Only a *sandbox* artifact is promotable; a live contract id is left untouched."""
    repo, _lake = workspace
    live = repo / "contracts" / "fx_majors_1h_v1.yaml"
    live.write_text("contract_id: FX_MAJORS_1H_V1\n", encoding="utf-8")
    _sign_repo(repo, monkeypatch)  # the tree stays clean: this is a request refusal
    monkeypatch.chdir(repo)
    before = _tree(repo)
    assert _promote(repo, "FX_MAJORS_1H_V1") == 2
    assert "unknown_contract" in capsys.readouterr().err
    assert _tree(repo) == before


# ---------------------------------------------------------------------------
# 2. the happy path: identical bytes, a receipt, and a human re-sign obligation
# ---------------------------------------------------------------------------


def test_promotion_copies_identical_bytes_and_writes_a_receipt(
    compiled, monkeypatch, capsys
) -> None:
    from warden_ops.integrity import check_worktree

    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    assert check_worktree(repo), "the compiled artifact is not a protected write"
    before = _tree(repo)

    assert _promote(repo, artifact.contract_id) == 0
    envelope = _last_json(capsys)
    assert envelope["ok"] is True and envelope["source"] == "warden.promote-contract"
    value = envelope["value"]
    contract_rel, manifest_rel, receipt_rel = _production_paths(artifact.contract_id)
    assert value["contract_id"] == artifact.contract_id
    assert value["target"]["contract"] == contract_rel
    assert value["target"]["manifest"] == manifest_rel
    assert value["receipt_path"] == receipt_rel

    # Byte-identical copies, and the sandbox sources are untouched.
    after = _tree(repo)
    assert after[contract_rel] == before[artifact.contract_path]
    assert after[manifest_rel] == before[artifact.manifest_path]
    assert before[artifact.contract_path] == after[artifact.contract_path]
    assert before[artifact.manifest_path] == after[artifact.manifest_path]
    assert sorted(set(after) - set(before)) == sorted([contract_rel, manifest_rel, receipt_rel])
    assert all(after[name] == payload for name, payload in before.items())

    # The receipt says who/what/when and every digest, and never claims a signature.
    receipt = json.loads(after[receipt_rel].decode("utf-8"))
    assert receipt["schema"] == "spec109.contract-promotion.v1"
    assert receipt["contract_id"] == artifact.contract_id
    assert receipt["dataset_id"] == artifact.dataset_id
    assert receipt["operator"] == "fixture-operator"
    assert receipt["promoted_at"].endswith("+00:00")
    assert receipt["digests"] == {
        "contract_sha256": hashlib.sha256(after[contract_rel]).hexdigest(),
        "contract_spec_hash": artifact.contract_spec_hash,
        "manifest_sha256": hashlib.sha256(after[manifest_rel]).hexdigest(),
        "parquet_sha256": artifact.parquet_sha256,
        "policy_sha256": artifact.policy_sha256,
        "raw_inputs_merkle_sha256": artifact.raw_inputs_merkle_sha256,
        "spec_sha256": artifact.spec_sha256,
    }
    assert receipt["source"] == {
        "contract": artifact.contract_path,
        "manifest": artifact.manifest_path,
        "parquet": artifact.parquet_path,
    }
    assert receipt["qualification_eligible"] is False
    assert receipt["manifest_signed"] is False
    assert receipt["manifest_generated"] is False
    assert receipt["holdout_opened"] is False
    assert receipt["requires_human_resign"] == [manifest_rel]

    # Promotion is a *protected* write: the human manifest is now stale by design.
    assert check_worktree(repo) is False
    assert receipt["requires_human_resign"]


def test_promotion_never_signs_or_regenerates_the_manifest(compiled, monkeypatch, capsys) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    manifest_before = (repo / MANIFEST_FILE).read_bytes()
    signature_before = (repo / f"{MANIFEST_FILE}.sig").read_bytes()

    assert _promote(repo, artifact.contract_id) == 0
    capsys.readouterr()
    assert (repo / MANIFEST_FILE).read_bytes() == manifest_before
    assert (repo / f"{MANIFEST_FILE}.sig").read_bytes() == signature_before


def test_promotion_prints_the_human_resign_instruction(compiled, monkeypatch, capsys) -> None:
    from warden.cli import main as cli_main

    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    assert cli_main(["promote-contract", "--contract-id", artifact.contract_id,
                     "--human-confirm-promote", TOKEN, "--operator", "fixture-operator"]) == 0
    out = capsys.readouterr().out
    assert "WARDEN_PROMOTE_CONTRACT_COMPLETE" in out
    assert "generate-manifest --human-approve-manifest" in out
    assert "no holdout" in out.lower()


# ---------------------------------------------------------------------------
# 3. the refusal matrix: every named reason, atomic, through the real CLI
# ---------------------------------------------------------------------------


def _edit_manifest(repo: Path, artifact, mutate) -> None:
    """Rewrite the compiled manifest with one field changed (nothing pins its bytes)."""
    path = repo / artifact.manifest_path
    record = json.loads(path.read_text(encoding="utf-8"))
    mutate(record)
    path.write_text(json.dumps(record), encoding="utf-8")


def _edit_contract(repo: Path, artifact, **fields) -> None:
    """Rewrite one field of the compiled sandbox contract."""
    path = repo / artifact.contract_path
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document.update(fields)
    path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")


def _assert_refusal(repo: Path, capsys, contract_id: str, code: str, exit_code: int) -> str:
    """One refusal: non-zero, named, and nothing copied (no registry, no holdout either)."""
    before = _tree(repo)
    assert _promote(repo, contract_id) == exit_code
    captured = capsys.readouterr()
    assert code in captured.err, captured.err
    assert "WARDEN_PROMOTE_CONTRACT_FAILED" in captured.err
    assert captured.out.strip() == ""
    assert _tree(repo) == before
    assert not (repo / "registry").exists(), "a refusal never opens the registry"
    assert not (repo / "data" / "holdout").exists(), "a refusal never creates a holdout root"
    return captured.err


def test_a_tampered_parquet_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    (repo / artifact.parquet_path).write_bytes(b"PAR1 not the compiled bytes")
    _assert_refusal(repo, capsys, artifact.contract_id, "tampered_parquet", 1)


def test_an_edited_contract_pin_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    _edit_contract(repo, artifact, data_snapshot_sha256="f" * 64)
    _assert_refusal(repo, capsys, artifact.contract_id, "mismatched_pin", 1)


def test_an_unpinned_compiled_contract_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    _edit_contract(repo, artifact, data_snapshot_sha256="")
    _assert_refusal(repo, capsys, artifact.contract_id, "unpinned_dataset", 1)


def test_a_changed_lake_breaks_the_raw_input_merkle_root(compiled, monkeypatch, capsys) -> None:
    """The lake is the other half of the provenance: new raw bytes, same Parquet, refused."""
    repo, lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    partition = lake_root / artifact.lake["input_files"][-1]
    frame = pl.read_parquet(partition)
    frame.with_columns((pl.col("close") * 1.01).alias("close")).write_parquet(partition)
    detail = _assert_refusal(repo, capsys, artifact.contract_id, "mismatched_merkle", 1)
    assert "day=03" in detail


def test_a_missing_raw_input_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    (lake_root / artifact.lake["input_files"][0]).unlink()
    _assert_refusal(repo, capsys, artifact.contract_id, "mismatched_merkle", 1)


def test_a_stale_policy_is_refused(compiled, monkeypatch, capsys) -> None:
    """A *re-signed* policy with new costs is still a stale artifact: the digest moved."""
    repo, _lake_root, artifact = compiled
    (repo / POLICY_REL).write_text(POLICY.replace("taker_fee_bps: 0.6", "taker_fee_bps: 0.9"),
                                   encoding="utf-8")
    _sign_repo(repo, monkeypatch)
    monkeypatch.chdir(repo)
    _assert_refusal(repo, capsys, artifact.contract_id, "stale_policy", 1)


def test_a_moved_policy_fence_is_refused_as_a_stale_policy(compiled, monkeypatch, capsys) -> None:
    """A re-signed policy that moves the fence is a new policy: step 5 refuses it first."""
    repo, _lake_root, artifact = compiled
    moved = POLICY.replace("holdout_start: '2021-01-04T00:00:00Z'", "holdout_start: '2021-01-05T00:00:00Z'")
    (repo / POLICY_REL).write_text(moved, encoding="utf-8")
    _sign_repo(repo, monkeypatch)
    monkeypatch.chdir(repo)
    _assert_refusal(repo, capsys, artifact.contract_id, "stale_policy", 1)


def test_a_moved_artifact_fence_is_refused(compiled, monkeypatch, capsys) -> None:
    """The compiled fence, the contract and the policy must still name one instant."""
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)

    def moved(record):
        record["sandbox"]["fence"]["holdout_start"] = "2021-01-05T00:00:00+00:00"

    _edit_manifest(repo, artifact, moved)
    _assert_refusal(repo, capsys, artifact.contract_id, "stale_fence", 1)


def test_a_last_bar_at_the_fence_is_refused(compiled, monkeypatch, capsys) -> None:
    """The fence is re-proved from the manifest, not trusted from ``fence_satisfied``."""
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)

    def at_the_fence(record):
        record["dataset"]["last_bar"] = "2021-01-04 00:00:00+00:00"

    _edit_manifest(repo, artifact, at_the_fence)
    _assert_refusal(repo, capsys, artifact.contract_id, "fence_violated", 1)


def test_a_moved_lake_acceptance_identity_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    catalogue = lake_root / ".catalog" / "research-dataset-catalog.json"
    document = json.loads(catalogue.read_text(encoding="utf-8"))
    document["entries"][0]["acceptance"]["sha256"] = "a" * 64
    catalogue.write_text(json.dumps(document), encoding="utf-8")
    detail = _assert_refusal(repo, capsys, artifact.contract_id, "lake_provenance", 1)
    assert "forex_minute_EURUSD" in detail


def test_a_shrunk_excluded_partition_set_is_refused(compiled, monkeypatch, capsys) -> None:
    """An excluded partition must never silently become an input: the fence bookkeeping moved."""
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)

    def overlap(record):
        lake = record["sandbox"]["lake"]
        lake["excluded_partitions"] = [lake["input_files"][0]]

    _edit_manifest(repo, artifact, overlap)
    _assert_refusal(repo, capsys, artifact.contract_id, "lake_inputs", 1)


def test_a_manifest_for_another_contract_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)

    def republished(record):
        record["dataset"]["dataset_id"] = "sandbox_fx_1h_someone_elses_r1"

    _edit_manifest(repo, artifact, republished)
    _assert_refusal(repo, capsys, artifact.contract_id, "mismatched_identity", 1)


def test_a_contract_pointing_outside_the_sandbox_dataset_is_refused(
    compiled, monkeypatch, capsys
) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    _edit_contract(repo, artifact, exploratory_data_path="data/exploratory/sandbox/other.parquet")
    _assert_refusal(repo, capsys, artifact.contract_id, "mismatched_identity", 1)


def test_a_missing_dataset_manifest_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    (repo / artifact.manifest_path).unlink()
    _assert_refusal(repo, capsys, artifact.contract_id, "missing_manifest", 1)


def test_a_missing_parquet_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    (repo / artifact.parquet_path).unlink()
    _assert_refusal(repo, capsys, artifact.contract_id, "missing_dataset", 1)


def test_a_foreign_artifact_schema_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)

    def foreign(record):
        record["sandbox"]["schema"] = "spec109.sandbox-artifact.v0"

    _edit_manifest(repo, artifact, foreign)
    _assert_refusal(repo, capsys, artifact.contract_id, "wrong_namespace", 2)


def test_a_retired_contract_is_refused(compiled, monkeypatch, capsys) -> None:
    """A contract the reviewed lifecycle document closes never becomes a production candidate."""
    repo, _lake_root, artifact = compiled
    lifecycle = repo / "contracts" / "lifecycle" / "contract_lifecycle.json"
    lifecycle.parent.mkdir(parents=True, exist_ok=True)
    lifecycle.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2021-01-01T00:00:00+00:00",
                "note": "lane K fixture lifecycle document",
                "contracts": [
                    {
                        "contract_id": artifact.contract_id,
                        "status": "DEPRECATED_UNEXECUTABLE",
                        "reason": "fixture: this id is retired",
                    }
                ],
                "legacy_scripts": [],
            }
        ),
        encoding="utf-8",
    )
    _sign_repo(repo, monkeypatch)
    monkeypatch.chdir(repo)
    assert _promote(repo, artifact.contract_id) == 2
    captured = capsys.readouterr()
    assert "retired_contract" in captured.err
    assert artifact.contract_id in captured.err
    assert not (repo / "contracts" / f"{artifact.contract_id.lower()}.yaml").exists()


def test_a_second_promotion_of_the_same_contract_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    assert _promote(repo, artifact.contract_id) == 0
    capsys.readouterr()
    # The promotion made the protected inventory stale; a human re-sign restores the gate, and
    # the re-promotion is then refused on its own evidence: one promotion per contract id.
    _sign_repo(repo, monkeypatch)
    before = _tree(repo)
    assert _promote(repo, artifact.contract_id) == 2
    captured = capsys.readouterr()
    assert "already_promoted" in captured.err
    assert _tree(repo) == before
    receipt = json.loads((repo / _production_paths(artifact.contract_id)[2]).read_text())
    assert receipt["promoted_at"]


def test_a_half_promoted_target_is_refused(compiled, monkeypatch, capsys) -> None:
    """A manifest copy without its contract and receipt is a stranded promotion, not a redo."""
    repo, _lake_root, artifact = compiled
    target_manifest = repo / _production_paths(artifact.contract_id)[1]
    target_manifest.parent.mkdir(parents=True, exist_ok=True)
    target_manifest.write_bytes((repo / artifact.manifest_path).read_bytes())
    _sign_repo(repo, monkeypatch)
    monkeypatch.chdir(repo)
    before = _tree(repo)
    assert _promote(repo, artifact.contract_id) == 2
    captured = capsys.readouterr()
    assert "half_promoted" in captured.err
    assert _tree(repo) == before


def test_a_broken_contract_document_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, _lake_root, artifact = compiled
    (repo / artifact.contract_path).write_text("contract_id: [not, a, contract]\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    _assert_refusal(repo, capsys, artifact.contract_id, "invalid_contract", 2)


# ---------------------------------------------------------------------------
# 4. promotion is not eligibility: the sandbox original, the promoted copy, the fence
# ---------------------------------------------------------------------------

STRATEGY_MODULE = """
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor


def required_symbols(params):
    return None


class _PromotionStrategy(Strategy):
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
    return _PromotionStrategy()
"""


def _trading_workspace(tmp_path, monkeypatch):
    """A scratch repo with the compiled artifact, a strategy module and an outcome index."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lake = _lake(tmp_path / "lake")
    _policy(repo, monkeypatch)
    (repo / "strategies").mkdir()
    (repo / "strategies" / "promotion_fixture.py").write_text(STRATEGY_MODULE, encoding="utf-8")
    index = repo / "docs" / "research-outcomes" / "branch-result-index-v1.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "as_of_utc": "2021-01-01",
                "purpose": "lane-K fixture replay-suppression index",
                "source_refs": [{"pr": 1, "commit": "abcdef0"}],
                "notes": [],
                "outcomes": [],
                "original_unattempted": [],
            }
        ),
        encoding="utf-8",
    )
    artifact = _compile(repo, lake)
    _sign_repo(repo, monkeypatch)
    return repo, lake, artifact


def _hypothesis_file(repo, contract_id: str, hypothesis_id: str) -> Path:
    from warden.contracts import HypothesisSpec

    spec = HypothesisSpec(
        hypothesis_id=hypothesis_id,
        contract_id=contract_id,
        title=f"Promotion fixture {contract_id}",
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
                "strategy_name": "Promotion fixture",
                "strategy_family": "rules_ts",
                "model_type": "rules",
                "strategy_module": "strategies/promotion_fixture.py",
                "parameters": {},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_promotion_alone_never_makes_the_sandbox_original_holdout_eligible(
    tmp_path, monkeypatch, capsys
) -> None:
    """The ticket's core claim: promotion moves bytes, it does not unseal anything."""
    from warden.cli import main as cli_main
    from warden.registry import ResearchRegistry

    repo, _lake_root, artifact = _trading_workspace(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    hypothesis = _hypothesis_file(repo, artifact.contract_id, "HYP-PROMOTION-1")
    assert cli_main(["register-hypothesis", str(hypothesis), "--json"]) == 0
    spec = _experiment_spec(repo, artifact.contract_id, "HYP-PROMOTION-1")
    assert cli_main(["backtest", str(spec), "--json"]) == 0
    capsys.readouterr()

    registry = ResearchRegistry(repo / "registry" / "registry.db")
    with registry._connect() as conn:
        run_id = conn.execute("SELECT run_id FROM trials LIMIT 1").fetchone()[0]
        assert conn.execute("SELECT COUNT(*) FROM holdout_claims").fetchone()[0] == 0

    # 1. Before promotion the namespace refusal names the sandbox contract.
    with pytest.raises(PermissionError) as before:
        cli_main(["qualify", "--run-id", run_id, "--unseal-holdout",
                  "--human-confirm-unseal", "YES_UNSEAL_HOLDOUT", "--json"])
    assert "sandbox" in str(before.value).lower()

    # 2. Promotion succeeds (the human gate), and is not a signature.
    assert _promote(repo, artifact.contract_id) == 0
    capsys.readouterr()
    _sign_repo(repo, monkeypatch)

    # 3. The original trial is still refused -- now by the ordinary gates, on provenance.
    with pytest.raises((ValueError, PermissionError)) as after:
        cli_main(["qualify", "--run-id", run_id, "--unseal-holdout",
                  "--human-confirm-unseal", "YES_UNSEAL_HOLDOUT", "--json"])
    message = str(after.value).lower()
    assert "sandbox" not in message, "the namespace refusal is gone: the copy is production now"
    assert ("provenance" in message) or ("synthetic" in message) or ("eligible" in message), message

    eligibility = registry.effective_eligibility(run_id)
    assert eligibility["eligible"] is False
    assert "provenance" in eligibility["reason"] or "eligible" in eligibility["reason"]

    with registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM holdout_claims").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0] == 0
    assert not (repo / "data" / "holdout").exists()


def test_the_production_copy_is_what_resolution_sees_after_promotion(
    compiled, monkeypatch, capsys
) -> None:
    from warden.contract_source import ACTIVE, SANDBOX, resolve_contract_source
    from warden.provenance import resolve_dataset

    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    cid = artifact.contract_id

    before = resolve_contract_source(repo, cid, allow_archive=True, allow_sandbox=True)
    assert before.location == SANDBOX

    assert _promote(repo, cid) == 0
    capsys.readouterr()

    after = resolve_contract_source(repo, cid, allow_archive=True, allow_sandbox=True)
    assert after.location == ACTIVE
    assert after.path.as_posix().endswith(f"contracts/{cid.lower()}.yaml")

    from warden.contract_source import load_active_contract

    promoted = load_active_contract(repo, cid)
    exploratory = resolve_dataset(promoted, repo)
    assert exploratory.eligible_market is False
    assert exploratory.provenance == "unknown"
    holdout = resolve_dataset(promoted, repo, role="holdout")
    assert holdout.eligible_market is False
    assert "holdout pin" in holdout.reason
    assert not (repo / "data" / "holdout").exists()


def test_the_fence_audit_still_owns_a_promoted_dataset(compiled, monkeypatch, capsys) -> None:
    """Two byte-identical manifests (sandbox + promoted copy) are one owner, not an ambiguity."""
    from warden.market_data import read_parquet_bytes
    from warden_evidence.exploratory_fence import AMBIGUOUS, FENCED, audit_exploratory_storage

    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    assert _promote(repo, artifact.contract_id) == 0
    capsys.readouterr()

    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert report.ok, report.to_dict()
    assert [item.path for item in report.files] == [artifact.parquet_path]
    assert report.files[0].state == FENCED
    assert report.files[0].contract_id == artifact.contract_id

    # A *different* manifest claiming the same bytes is still ambiguous: only the promoted
    # byte-identical copy collapses into one owner.
    conflicting = repo / "contracts" / "sandbox" / "datasets" / "SANDBOX_FX_1H_CONFLICT.json"
    record = json.loads((repo / artifact.manifest_path).read_text(encoding="utf-8"))
    record["contract_id"] = "SANDBOX_FX_1H_CONFLICT"
    conflicting.write_text(json.dumps(record), encoding="utf-8")
    report = audit_exploratory_storage(repo, parquet_reader=read_parquet_bytes)
    assert not report.ok
    assert report.failures[0].state == AMBIGUOUS


# ---------------------------------------------------------------------------
# 5. review K-fix: the gate re-derives its facts from the published bytes
# ---------------------------------------------------------------------------


def _re_pin_everything(repo: Path, artifact, digest: str) -> None:
    """What a falsifier can do: re-pin the manifest and the contract to new Parquet bytes.

    ``dataset.last_bar`` is deliberately left alone — the whole point of the attack is that the
    manifest's copy of that fact is not re-derived by the gate.
    """
    contract_path = repo / artifact.contract_path
    document = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    document["data_snapshot_sha256"] = digest
    contract_path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")

    manifest_path = repo / artifact.manifest_path
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    record["sha256"] = digest
    record["sandbox"]["contract_sha256"] = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(record), encoding="utf-8")


def test_a_falsified_last_bar_cannot_smuggle_a_bar_at_the_fence(
    compiled, monkeypatch, capsys
) -> None:
    """probe1: the fence is derived from the Parquet, and the manifest is only a cross-check."""
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)

    path = repo / artifact.parquet_path
    frame = pl.read_parquet(path)
    extra = pl.DataFrame(
        [{"timestamp": FENCE, "asset": "EURUSD", "open": 100.0, "high": 101.0, "low": 99.0,
          "close": 100.5, "volume": 1.0, "observed_sub_bars": 60}]
    ).select([pl.col(column).cast(frame.schema[column]) for column in frame.columns])
    leaky = frame.vstack(extra)
    leaky.write_parquet(path)
    assert leaky["timestamp"].max() == FENCE
    _re_pin_everything(repo, artifact, hashlib.sha256(path.read_bytes()).hexdigest())
    assert json.loads((repo / artifact.manifest_path).read_text())["dataset"]["last_bar"] != str(FENCE)

    detail = _assert_refusal(repo, capsys, artifact.contract_id, "fence_violated", 1)
    # Both values are named: the manifest's claim and the Parquet's own last bar.
    assert "2021-01-03T23:00:00+00:00" in detail
    assert "2021-01-04T00:00:00+00:00" in detail


def test_a_manifest_that_declares_a_holdout_pin_is_refused(compiled, monkeypatch, capsys) -> None:
    """probe7: a compiled manifest has no holdout pin, so a declared one is outside the shape."""
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)

    def pin(record):
        record["holdout_path"] = f"data/holdout/{artifact.dataset_id}.parquet"
        record["holdout_sha256"] = "a" * 64

    _edit_manifest(repo, artifact, pin)
    detail = _assert_refusal(repo, capsys, artifact.contract_id, "holdout_declared", 2)
    assert "holdout_sha256" in detail or "holdout_path" in detail


def test_a_manifest_that_claims_verified_provenance_is_refused(compiled, monkeypatch, capsys) -> None:
    """probe7: `provenance` is what holdout eligibility reads; a compile never claims one."""
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)

    def claim(record):
        record["provenance"] = "verified_market"
        record["source"] = "agent-declared"

    _edit_manifest(repo, artifact, claim)
    _assert_refusal(repo, capsys, artifact.contract_id, "claimed_provenance", 2)


def test_a_contract_that_claims_verified_provenance_is_refused(compiled, monkeypatch, capsys) -> None:
    """The contract's own `data_provenance` is a second declaration, refused on its own."""
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    _edit_contract(repo, artifact, data_provenance="verified_market")
    _assert_refusal(repo, capsys, artifact.contract_id, "claimed_provenance", 2)


def _plant_holdout(repo: Path, artifact) -> tuple[Path, int]:
    holdout = repo / "data" / "holdout" / f"{artifact.dataset_id}.parquet"
    holdout.parent.mkdir(parents=True, exist_ok=True)
    holdout.write_bytes(b"HOLDOUT-SECRET" * 100)
    return holdout, holdout.stat().st_size


def _refused_without_copying(repo: Path, capsys, artifact, code: str, exit_code: int) -> str:
    """A refusal that leaves the tree alone except for what this test planted (no holdout root check)."""
    before = _tree(repo)
    assert _promote(repo, artifact.contract_id) == exit_code
    captured = capsys.readouterr()
    assert code in captured.err, captured.err
    assert "WARDEN_PROMOTE_CONTRACT_FAILED" in captured.err
    assert captured.out.strip() == ""
    assert _tree(repo) == before
    assert not (repo / "contracts" / f"{artifact.contract_id.lower()}.yaml").exists()
    return captured.err


def test_a_raw_input_that_escapes_the_lake_is_refused_without_a_size(
    compiled, monkeypatch, capsys
) -> None:
    """probe3a: a manifest-supplied path is not a licence to open (or measure) any file."""
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    holdout, size = _plant_holdout(repo, artifact)
    relative = f"../repo/data/holdout/{holdout.name}"

    def escape(record):
        record["raw_inputs"].append({"path": relative, "bytes": 1, "sha256": "0" * 64})

    _edit_manifest(repo, artifact, escape)
    detail = _refused_without_copying(repo, capsys, artifact, "unconfined_raw_input", 1)
    assert relative in detail
    assert f"{size} bytes" not in detail
    assert str(size) not in detail


def test_an_absolute_raw_input_path_is_refused(compiled, monkeypatch, capsys) -> None:
    repo, _lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    holdout, size = _plant_holdout(repo, artifact)

    def absolute(record):
        record["raw_inputs"].append(
            {"path": str(holdout), "bytes": 1, "sha256": "0" * 64}
        )

    _edit_manifest(repo, artifact, absolute)
    detail = _refused_without_copying(repo, capsys, artifact, "unconfined_raw_input", 1)
    assert str(size) not in detail


def test_a_consistent_traversal_record_is_still_refused(compiled, monkeypatch, capsys) -> None:
    """probe3b: correct bytes and a recomputed Merkle root do not make an outside file an input."""
    from warden_evidence.dataset_builder.merkle import merkle_sha256

    repo, lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    holdout, _size = _plant_holdout(repo, artifact)
    relative = f"../repo/data/holdout/{holdout.name}"
    payload = holdout.read_bytes()

    def consistent(record):
        record["raw_inputs"].append(
            {"path": relative, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        )
        pairs = []
        for item in record["raw_inputs"]:
            target = lake_root / item["path"]
            pairs.append((item["path"], target.read_bytes()))
        record["raw_inputs_merkle_sha256"] = merkle_sha256(pairs)[0]

    _edit_manifest(repo, artifact, consistent)
    _refused_without_copying(repo, capsys, artifact, "unconfined_raw_input", 1)


def test_a_raw_input_the_manifest_did_not_record_is_refused(compiled, monkeypatch, capsys) -> None:
    """A file inside the lake is still not an input unless `sandbox.lake.input_files` names it."""
    from warden_evidence.dataset_builder.merkle import merkle_sha256

    repo, lake_root, artifact = compiled
    monkeypatch.chdir(repo)
    sneaky = lake_root / "forex_minute_EURUSD" / "sneaky" / "part-0.parquet"
    sneaky.parent.mkdir(parents=True, exist_ok=True)
    sneaky.write_bytes((lake_root / artifact.lake["input_files"][0]).read_bytes())
    payload = sneaky.read_bytes()
    relative = sneaky.relative_to(lake_root).as_posix()

    def extra_input(record):
        record["raw_inputs"].append(
            {"path": relative, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        )
        pairs = [(item["path"], (lake_root / item["path"]).read_bytes()) for item in record["raw_inputs"]]
        record["raw_inputs_merkle_sha256"] = merkle_sha256(pairs)[0]

    _edit_manifest(repo, artifact, extra_input)
    _assert_refusal(repo, capsys, artifact.contract_id, "lake_inputs", 1)
