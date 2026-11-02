"""Immutable PIT funding recovery evidence receipt tests (no market files required)."""

import hashlib
import json
from pathlib import Path

ROOT = Path("docs/evidence/arxiv_stage1_20261102")
HASHES = {
    "pit_funding_archive_recovery_20261102.json":
        "64656affc7e0abb4808ffdc55c2ab905f3359c101b6001c4cb16211afc1bfee5",
    "pit_funding_overlay_v2_20261102.json":
        "c558de1ae21ae333f0e43d9163b9f8ebcf1ef8e76394a9437cec2a7807bca7e3",
    "pit_funding_overlay_v3_20261102.json":
        "c04c3f431b1c0c6ea5b65592857ec799a6cf08fcd1885807aa96de05c5a46640",
}


def _read(filename):
    raw = (ROOT / filename).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == HASHES[filename]
    return json.loads(raw)


def test_archive_source_receipt_matches_official_checksums() -> None:
    d = _read("pit_funding_archive_recovery_20261102.json")
    assert d["original"]["original_missing_rows"] == 314653
    assert d["original"]["source_missing_assets"] == 77
    assert d["original"]["archive_requests"] == 590
    assert d["downloaded_archive_status"] == {"VERIFIED": 588, "NOT_PUBLISHED": 2}
    good = [x for x in d["archives"] if x["status"] == "VERIFIED"]
    missing = [x for x in d["archives"] if x["status"] == "NOT_PUBLISHED"]
    assert len(good) == 588
    assert len({(x["asset"], x["month"]) for x in d["archives"]}) == 590
    assert all(x["official_checksum_status"] == "MATCH" and len(x["sha256"]) == 64
               for x in good)
    assert {(x["asset"], x["month"]) for x in missing} == {
        ("BNXUSDT", "2022-12"), ("BNXUSDT", "2023-01"),
    }
    assert d["coverage"]["recovered_rows"] == 313908
    assert d["coverage"]["remaining_unknown_rows"] == 745
    assert d["protected_holdout_read"] is False
    assert d["original_source_modified"] is False


def test_separate_v2_version_preserved_and_not_overwritten() -> None:
    d = _read("pit_funding_overlay_v2_20261102.json")
    assert d["status"] == "RESEARCH_ONLY_UNSIGNED_NOT_WARDEN_STAGE3"
    assert d["event_ledger"]["event_count"] == 758641
    assert d["panel_overlay"]["input_rows"] == 2187277
    assert d["panel_overlay"]["after_overlay_missing_rows"] == 745
    assert d["panel_overlay"]["OHLCV_unchanged"] is True
    assert d["panel_overlay"]["remaining_missing_by_asset"][0]["asset"] == "BNXUSDT"
    assert d["protected_holdout_read"] is False
    assert d["source_approval"] is False


def test_v3_source_remains_complete_without_claiming_cashflow_completeness() -> None:
    d = _read("pit_funding_overlay_v3_20261102.json")
    assert d["status"] == "RESEARCH_ONLY_UNSIGNED_NOT_WARDEN"
    assert d["sources"]["official_rest"]["source_events"] == 93
    assert d["sources"]["official_rest"]["interval_metadata_present"] is False
    assert d["sources"]["official_rest"]["source_kind"] == (
        "OFFICIAL_HISTORICAL_REST_NOT_MONTHLY_ARCHIVE"
    )
    assert d["sources"]["official_rest"]["not_published_monthly_archives"] == [
        "BNXUSDT:2022-12", "BNXUSDT:2023-01",
    ]
    assert d["data"]["panel_rows"] == 2187277
    assert d["data"]["event_ledger_rows"] == 758734
    assert d["data"]["rest_interval_metadata_missing_events"] == 93
    assert d["data"]["after_overlay_missing_rows"] == 0
    assert d["data"]["coverage_pct"] == 100
    assert d["data"]["OHLCV_unchanged"] is True
    assert d["funding_execution_qualified"] is False
    assert d["holdout_read"] is False
    assert d["original_data_modified"] is False
    assert d["unapproved_8h_warden_model_not_used"] is True
