"""Recovered dataset staging never becomes verified origin from its digest alone."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from pathlib import Path

from warden_evidence.data_admission import classify_recovered_dataset


def _signed_attestation(tmp_path: Path, f, key: bytes) -> Path:
    row = {"schema": 1, "contract_id": f.contract.contract_id,
           "source_id": "synthetic-test-provider", "data_sha256": f.sha,
           "raw_source_sha256": "1" * 64, "observed_at": "2025-01-03T00:00:00Z"}
    payload = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    row["signature"] = hmac.new(key, payload, hashlib.sha256).hexdigest()
    dest = tmp_path / "external-source-attestation.json"
    dest.write_text(json.dumps(row))
    return dest


def test_matching_digest_without_origin_is_staged_not_promotable(tiny_repo, tmp_path: Path) -> None:
    f = tiny_repo()
    state = classify_recovered_dataset(f.contract, f.repo)
    assert state.state == "STAGED" and not state.promotable
    assert state.data_sha256 == f.sha


def test_human_attested_origin_then_contract_pin_are_separate_states(tiny_repo, tmp_path: Path) -> None:
    f = tiny_repo()
    key = b"fixture-only-test-key-not-production"
    receipt = _signed_attestation(tmp_path, f, key)
    pinned = classify_recovered_dataset(f.contract, f.repo, receipt, key)
    assert pinned.state == "CONTRACT_ADMITTED" and pinned.promotable
    missing_approval = classify_recovered_dataset(replace(f.contract, data_snapshot_sha256="f" * 64),
                                                   f.repo, receipt, key)
    assert missing_approval.state == "PROVENANCE_VERIFIED" and not missing_approval.promotable
    with_bad_key = classify_recovered_dataset(f.contract, f.repo, receipt, b"wrong-key")
    assert with_bad_key.state == "STAGED" and not with_bad_key.promotable


def test_modified_data_and_holdout_paths_fail_closed(tiny_repo, tmp_path: Path) -> None:
    f = tiny_repo()
    key = b"fixture-only-test-key-not-production"
    receipt = _signed_attestation(tmp_path, f, key)
    path = f.repo / f.contract.exploratory_data_path
    path.write_bytes(b"unexpected source revision")
    changed = classify_recovered_dataset(f.contract, f.repo, receipt, key)
    assert changed.state == "STAGED" and not changed.promotable
    protected = classify_recovered_dataset(replace(f.contract, exploratory_data_path="data/holdout/other.parquet"),
                                           f.repo, receipt, key)
    assert protected.state == "BLOCKED" and not protected.promotable
