"""Versioned preservation of Warden Stage 1 return-vector evidence."""

import gzip
import hashlib
import json
from pathlib import Path

ARCHIVE = Path("docs/evidence/arxiv_stage1_20261102/selection_attempts.json.gz")
EXPECTED_SHA256 = "4723cc14b70938de1ed04fbad279432f8b4f01971e9506fe905ff483ba31d772"


def test_stage1_receipts_are_complete_and_byte_verified() -> None:
    assert ARCHIVE.is_file()
    assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() == EXPECTED_SHA256
    with gzip.open(ARCHIVE, "rt") as f:
        payload = json.load(f)
    assert payload["schema"] == "quant-lab-arxiv-stage1-receipts-v1"
    rows = payload["records"]
    assert len(rows) == 7
    assert sum(r["outcome"] == "PASS" for r in rows) == 2
    assert sum(r["outcome"] == "REJECT" for r in rows) == 5
    for row in rows:
        assert row["returns_observed"] == 1
        assert row["stage"] == "STAGE_1_SIGNAL"
        assert row["kind"] == "standalone_screen"
        assert hashlib.sha256(row["returns_json"].encode()).hexdigest() == row["returns_sha256"]
        assert json.loads(row["returns_json"])

    passes = {r["hypothesis_id"]: r for r in rows if r["outcome"] == "PASS"}
    lowvol = passes["HYP-ARXIV-XS-LOWVOL-20261102"]
    daily = passes["HYP-ARXIV-XS-LOWVOL-24H-20261102"]
    assert lowvol["returns_sha256"] == daily["returns_sha256"]
    assert lowvol["returns_json"] == daily["returns_json"]
