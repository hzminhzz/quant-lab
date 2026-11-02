"""Checksum-verified PIT source membership evidence and governance boundary."""

import gzip
import hashlib
import json
from pathlib import Path

import yaml

ARCHIVE = Path("docs/evidence/arxiv_stage1_20261102/pit_universe_membership_20261102.json.gz")
SHA = "7672cb880aaef849df12312192b93942bd4c653476915a66baa3f3e4205f75d2"
PROPOSAL = Path("docs/contract-proposals/CRYPTO_PERPS_PIT100_1H_V1.yaml")
MANIFEST = Path("docs/contract-proposals/CRYPTO_PERPS_PIT100_1H_V1.dataset.json")
PIT_PANEL_SHA = "c76266d4c3bf2e37ce904bedd182cd6e54b9cfd3e6bc9fe1f337925e4a818a8a"


def test_pit_rank_archive_is_deterministic_and_pre_holdout_only() -> None:
    assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() == SHA
    with gzip.open(ARCHIVE,"rt") as f:
        doc = json.load(f)
    assert doc["schema"] == "quantlab-asof-binance-top100-membership-v1"
    assert doc["window"]["holdout_start_exclusive"].startswith("2025-07-01")
    assert doc["source_shard_count"] == 8795
    assert len(doc["months"]) == 30
    assert doc["distinct_asof_selected_symbols"] == 364
    assert len(doc["asof_selected_not_in_hindsight_panel"]) == 264
    for row in doc["source_shards"]:
        rel=row["path"]
        assert "year=2025/month=7" not in rel
        assert "year=2026/" not in rel
    for month in doc["months"]:
        assert len(month["symbols"]) == month["eligible_selected"]
        assert month["eligible_selected"] <= 100
    overlap = [row["hindsight_top100_overlap"] for row in doc["months"]]
    assert min(overlap) == 33 and max(overlap) == 74


def test_pit_contract_adopted_as_a_separately_versioned_research_contract() -> None:
    proposal = yaml.safe_load(PROPOSAL.read_text())
    proposal_manifest = json.loads(MANIFEST.read_text())
    installed_path = Path("contracts/crypto_perps_pit100_1h_v1.yaml")
    installed_manifest_path = Path("contracts/datasets/CRYPTO_PERPS_PIT100_1H_V1.json")
    installed = yaml.safe_load(installed_path.read_text())
    installed_manifest = json.loads(installed_manifest_path.read_text())
    assert proposal["contract_id"] == installed["contract_id"] == "CRYPTO_PERPS_PIT100_1H_V1"
    assert proposal == installed  # no silent parameter or validation-window changes
    assert installed["data_snapshot_sha256"] == PIT_PANEL_SHA
    assert proposal_manifest["sha256"] == installed_manifest["sha256"] == PIT_PANEL_SHA
    assert proposal_manifest["status"] == "UNAPPROVED_CONTRACT_PROPOSAL"
    assert installed_manifest["status"] == "APPROVED_RESEARCH_STAGE1_ONLY"
    assert "Stage 1" in installed_manifest["approval_scope"]
    assert "No human-held HMAC" in installed_manifest["cryptographic_attestation"]
    assert installed["holdout_data_path"].startswith("data/holdout/")
    assert installed["exploratory_data_path"] != "data/exploratory/crypto_perps_top100_1h.parquet"
    # Original contract remains untouched, preserving all old rejections and passes.
    original = yaml.safe_load(Path("contracts/crypto_perps_top100_1h_v1.yaml").read_text())
    assert original["data_snapshot_sha256"] == "a7e13ed71f8ac09a11f98bdb6d711d93f91a4021105141a2070fd79696af99bc"
    digest_text = Path(".warden_manifest.sha256").read_text()
    assert "contracts/crypto_perps_pit100_1h_v1.yaml" in digest_text
    assert "contracts/datasets/CRYPTO_PERPS_PIT100_1H_V1.json" in digest_text
