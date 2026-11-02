"""Immutable source-complete PIT low-vol economic screen evidence."""

import hashlib
import json
from pathlib import Path

P = Path("docs/evidence/arxiv_stage1_20261102/pit_lowvol_event_economics_preflight_20261102.json")
DIGEST = "5de4660add68bc42405f62160645b12a60adeeb8608c06ce0d82752207e45ada"


def test_source_faithful_pit_lowvol_preflight_is_negative_and_no_warden_trial() -> None:
    raw = P.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == DIGEST
    report = json.loads(raw)
    assert report["status"] == "SOURCE_VERIFIED_RESEARCH_DIAGNOSTIC_NOT_WARDEN_STAGE3"
    assert report["original_stage1_attempt"] == "ATT-480df2ef5d8788f2-001"
    assert report["original_contract_sha256"] == (
        "c76266d4c3bf2e37ce904bedd182cd6e54b9cfd3e6bc9fe1f337925e4a818a8a"
    )
    assert report["event_ledger_sha256"] == (
        "6ac0630855a88983cc9576cea0b7f094b50799fc8b0cea6ad0d92312e0e661aa"
    )
    assert report["market_price_bridge"]["source_verified_transition_count"] == 30
    assert report["market_price_bridge"]["raw_verified_source_files"] == 30
    assert report["market_price_bridge"]["bridged_missing_asset_hour_opens"] == 120
    assert report["market_price_bridge"]["fills_observed"] is False
    for name in ("base_cost", "double_cost", "delay_plus_one"):
        study = report["scenarios"][name]
        assert study["portfolio_coverage"]["missing_active_price_events"] == 0
        assert study["portfolio_coverage"]["terminal_position_gross"] == 0
        assert study["event_classification"]["invalid_event"] == 0
        assert study["event_classification"]["events_in_window"] == 419559
        for outcome in study["variants"].values():
            assert outcome["net_sharpe"] < 0
            assert outcome["profit_factor"] < 1
    assert report["scenarios"]["base_cost"]["portfolio_coverage"]["active_asset_hours"] == 175160
    assert report["scenarios"]["base_cost"]["variants"]["settlement_post_open"]["net_sharpe"] < -.2
    assert report["scenarios"]["double_cost"]["variants"]["settlement_post_open"]["net_sharpe"] < -1
    assert report["native_warden_trial_created"] is False
    assert report["sealed_holdout_read"] is False
    assert report["original_sources_modified"] is False
    assert report["user_authorization_to_trade"] is False
