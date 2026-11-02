"""Immutable confirmation of the real PIT Warden Stage 1 attempt.

The test reads a canonical saved Warden receipt; it NEVER invokes screen()
or opens the protected holdout.
"""

import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from warden.gates import compute_hac_tstat

RECEIPT = Path("docs/evidence/arxiv_stage1_20261102/pit100_lowvol_stage1_receipt.json.gz")
RECEIPT_SHA = "28a1fe8de2574be4b7222eb0936f4397cbdc75d84cc2dadbc83db240d001a341"
VECTOR_SHA = "289f194ea46e42b23da64137f923b6dbf702c1bb557fd49759e7a83eb13a0720"


def _receipt() -> dict:
    assert hashlib.sha256(RECEIPT.read_bytes()).hexdigest() == RECEIPT_SHA
    with gzip.open(RECEIPT, "rt") as fd:
        return json.load(fd)


def test_pit_stage1_is_exactly_one_real_warden_pass() -> None:
    doc = _receipt()
    rec = doc["attempt"]
    assert doc["schema"] == "quantlab-pit-lowvol-stage1-receipt-v1"
    assert rec["attempt_id"] == "ATT-480df2ef5d8788f2-001"
    assert rec["hypothesis_id"] == "HYP-ARXIV-XS-LOWVOL-PIT100-20261102"
    assert rec["contract_id"] == "CRYPTO_PERPS_PIT100_1H_V1"
    assert rec["strategy_family"] == "cross_sectional_ml"
    assert rec["stage"] == "STAGE_1_SIGNAL"
    assert rec["kind"] == "standalone_screen"
    assert rec["outcome"] == "PASS"
    assert rec["returns_sha256"] == VECTOR_SHA
    assert rec["returns_observed"] == 1
    assert doc["recorded_observations"] == 8755
    assert doc["data_sha256"] == "c76266d4c3bf2e37ce904bedd182cd6e54b9cfd3e6bc9fe1f337925e4a818a8a"


def test_pit_ic_vector_recomputes_same_hac_and_nonoverlapping_phases() -> None:
    doc = _receipt()
    record = doc["attempt"]
    blob = record["returns_json"]
    assert hashlib.sha256(blob.encode()).hexdigest() == VECTOR_SHA
    vec = np.asarray(json.loads(blob), dtype=np.float64)
    assert len(vec) == 8755
    assert np.isfinite(vec).all()
    stats = json.loads(record["diagnostics_json"])
    assert math.isclose(float(np.mean(vec)), stats["mean_ic"], abs_tol=1e-12)
    assert math.isclose(float(compute_hac_tstat(vec)), stats["ic_hac_tstat"], abs_tol=1e-10)
    assert math.isclose(stats["mean_ic"], 0.05742364202055246, abs_tol=1e-12)
    assert math.isclose(stats["ic_hac_tstat"], 11.551318322648376, abs_tol=1e-10)

    for phase in doc["nonoverlapping_four_hour_ic_phases"]:
        series = vec[phase["phase"]::4]
        assert phase["samples"] == len(series)
        assert math.isclose(float(np.mean(series)), phase["mean_ic"], abs_tol=1e-12)
        assert math.isclose(float(compute_hac_tstat(series)), phase["hac_t"], abs_tol=1e-10)
        assert phase["mean_ic"] > 0.015
        assert phase["hac_t"] > 2.0


def test_all_historical_attempts_preserved_and_no_stage3_trial_spent() -> None:
    report = _receipt()
    counts = report["research_snapshot_counts_after"]
    assert counts == {
        "hypotheses": 153,
        "trials": 75,
        "selection_attempts": 234,
    }
    assert len(report["historical_archive_only_attempts"]) == 4
    assert report["combined_distinct_research_attempts"] == 238
    assert report["hypothesis_is_descendant_of"] == "HYP-ARXIV-XS-LOWVOL-20261102"


def test_manifest_records_interactive_approval_not_hmac_signing() -> None:
    m = json.loads(Path(".warden_manifest.sha256.meta.json").read_text())
    assert m["mode"] == "interactive"
    assert m["approver"] == "human-operator"
    assert hashlib.sha256(Path(".warden_manifest.sha256").read_bytes()).hexdigest() == m["manifest_sha256"]
    assert m["manifest_sha256"] == "4c68afda13b0c0f894b79a913d06ea2c8462edfac3f9200aafb3c087ef0927ab"
