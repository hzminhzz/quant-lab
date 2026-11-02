"""Pin exact PIT exit evidence without requiring the private market lake."""

import hashlib
import json
from pathlib import Path

PATH = Path("docs/evidence/arxiv_stage1_20261102/pit_monthly_exit_source_audit_20261102.json")
SHA256 = "28bc41728a746c2571386baa2ce7da4cdd2272a28ae680900998456060e96aa8"


def test_all_pit_monthly_exit_prices_are_source_sha_verified() -> None:
    raw = PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA256
    r = json.loads(raw)
    assert r["status"] == "READ_ONLY_EXECUTION_SOURCE_BRIDGE_NOT_STAGE3"
    assert r["no_post_cutoff_prices_read"] is True
    assert r["no_holdout_access"] is True
    assert r["no_fills_or_stage3"] is True
    assert r["sparse_panel"]["cross_gap_next_observed_bar_pairs"] == 359
    assert r["sparse_panel"]["max_gap_hours"] == 15337
    out = r["exit_coverage"]
    assert out["monthly_position_boundary_pairs"] == 30
    assert out["monthly_exit_quote_source"] == {"SOURCE_OBSERVED_NEXT_OPEN": 30}
    assert out["terminal_positions_without_legal_next_price"] == 20
    assert all(
        x["status"] == "SOURCE_OBSERVED_NEXT_OPEN"
        and x["actual_fill_verified"] is False
        and len(x["source_digest"]) == 64
        for x in out["transition_evidence"]
    )
