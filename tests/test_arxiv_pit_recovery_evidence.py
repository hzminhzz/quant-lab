"""Preserved diagnostics must match independent SHA-256 receipts and disclosures."""

import hashlib
import json
from pathlib import Path

PREFIX=Path("docs/evidence/arxiv_stage1_20261102")
SHAS={
    "execution_gap_audit_20261102.json":"7671bc237fa57da1721bd91893e80fd7b83eaafdca95ff5b6ec448d6bb8c61b5",
    "terminal_flat_20261102.json":"f61049062e3738235e5f00db324bc71dda13b670ec4cb53f1c10f8bbc71de0d3",
    "event_funding_stress_20261102.json":"19cb1cbc97ebfee30dc5c78a052fd517189bf65edf4b41199a5af3eacdd7c4c1",
    "pit_funding_risks_20261102.json":"4e02114c56bed0aa32a9198d43ac08911fd6b2d1aedfd41229ca4260a3c9f9d1",
    "pit_lowvol_train_preflight_20261102.json":"c4a984035c12b2153a64f16becefb01e404df7d2eccb64bb5663616643d91cfe",
}


def _read(key):
    path=PREFIX/key
    assert hashlib.sha256(path.read_bytes()).hexdigest()==SHAS[key]
    return json.loads(path.read_text())


def test_terminal_gap_is_not_interior() -> None:
    report=_read("execution_gap_audit_20261102.json")
    gap=report["executable_gap_audit"]
    assert gap["counts"]=={"terminal_no_next_exploratory_open":20}
    assert gap["total"]==20
    assert report["holdout_read"] is False


def test_terminal_flat_accounting_pays_exit_fee() -> None:
    report=_read("terminal_flat_20261102.json")
    assert report["protected_period_accessed"] is False
    for scenario in report["scenarios"].values():
        assert scenario["terminal_flat"]["end_exposure"]==0
        assert scenario["terminal_flat"]["terminal_bar_turnover"]==1.0


def test_event_funding_stress_is_not_claimed_as_actual_missing_data() -> None:
    report=_read("event_funding_stress_20261102.json")
    assert report["protected_data_accessed"] is False
    assert report["status"]=="READ_ONLY_NOT_STAGE3"
    assert report["event_classification"]["event_intervals_hours"]["4"] > 0
    assert report["diagnostics"]["double_cost"]["observed_events_only_net_sharpe"] < 0.5


def test_pit_funding_missingness_and_train_only_receipt() -> None:
    funding=_read("pit_funding_risks_20261102.json")
    assert funding["no_zero_funding_imputation"] is True
    assert funding["missing_funding_rows"]==314653
    training=_read("pit_lowvol_train_preflight_20261102.json")
    assert training["status"]=="TRAINING_ONLY_PREFLIGHT_NOT_WARDEN_VALIDATION"
    assert training["train_end_exclusive"].startswith("2024-07-01")
    assert all(item["mean_spearman_ic"] > 0.05 for item in training["preflight_halves"])
