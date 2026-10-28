"""Thesis preflight: pure-logic tests for the validation gate and the next legal command."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from warden.runner import load_module_from_path

_pf = load_module_from_path(Path("scripts/preflight_thesis.py"))
next_step = _pf.next_step
validation_gap_covered = _pf.validation_gap_covered


def _ok_state() -> dict:
    return {"ok": True, "unsatisfied": []}


def test_healthy_window_is_covered_within_one_bar():
    covered, gap = validation_gap_covered(
        datetime(2025, 7, 1, tzinfo=UTC), datetime(2025, 6, 30, 23, tzinfo=UTC), "1h")
    assert covered is True
    assert gap == pytest.approx(1.0 / 24, abs=0.01)  # gap is in days: one 1h bar


def test_truncated_data_leaves_the_validation_window_uncovered():
    covered, gap = validation_gap_covered(
        datetime(2025, 7, 1, tzinfo=UTC), datetime(2025, 3, 25, tzinfo=UTC), "1h")
    assert covered is False
    assert gap == pytest.approx(98.0, abs=1.0)


def test_data_beyond_val_end_overshoots_with_a_negative_gap():
    covered, gap = validation_gap_covered(
        datetime(2025, 7, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC), "1h")
    assert covered is True
    assert gap < 0


def test_unknown_frequency_cannot_be_judged_so_it_passes():
    assert validation_gap_covered(
        datetime(2025, 7, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC), "7h") == (True, 0.0)


def test_missing_last_timestamp_cannot_be_judged_so_it_passes():
    assert validation_gap_covered(datetime(2025, 7, 1, tzinfo=UTC), None, "1h") == (True, 0.0)


def test_uncovered_validation_window_stops_and_names_the_gap():
    contract = {"contract_id": "A", "validation_window_covered": False,
                "validation_gap_days": 98.0}
    step = next_step(_ok_state(), contract, None)
    assert step["command"] == "STOP"
    assert "98" in step["why"]


def test_contract_hypothesis_mismatch_stops():
    contract = {"contract_id": "A", "execution_columns_ok": True,
                "validation_window_covered": True}
    hypothesis = {"hypothesis_id": "HYP-X", "registered": True, "contract_id": "B",
                  "trials_remaining": 2}
    step = next_step(_ok_state(), contract, hypothesis)
    assert step["command"] == "STOP"
    assert "mismatch" in step["why"]


def test_failed_worktree_check_asks_for_the_human_manifest_resign():
    step = next_step({"ok": False, "unsatisfied": []}, None, None)
    assert "generate-manifest" in step["command"]


def test_next_step_prefers_the_newest_spec_and_finds_trial_suffixes(tmp_path, monkeypatch):
    active = tmp_path / "experiments" / "active"
    active.mkdir(parents=True)
    (active / "hyp_2026_095.yaml").write_text("x")
    (active / "hyp_2026_095_t2.yaml").write_text("x")
    monkeypatch.chdir(tmp_path)
    contract = {"contract_id": "C", "execution_columns_ok": True, "validation_window_covered": True}
    hypothesis = {"hypothesis_id": "HYP-2026-095", "registered": True, "contract_id": "C",
                  "trials_remaining": 1}
    step = next_step(_ok_state(), contract, hypothesis)
    assert "hyp_2026_095_t2.yaml" in step["command"]


def test_next_step_asks_for_the_spec_when_none_exists(tmp_path, monkeypatch):
    (tmp_path / "experiments" / "active").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    contract = {"contract_id": "C", "execution_columns_ok": True, "validation_window_covered": True}
    hypothesis = {"hypothesis_id": "HYP-2026-777", "registered": True, "contract_id": "C",
                  "trials_remaining": 1}
    step = next_step(_ok_state(), contract, hypothesis)
    assert "hyp_2026_777.yaml" in step["command"]
    assert "no experiment spec" in step["why"]


def test_dataset_error_stops():
    contract = {"contract_id": "C", "dataset_error": "FileNotFoundError: missing exploratory dataset"}
    step = next_step(_ok_state(), contract, None)
    assert step["command"] == "STOP"
    assert "dataset unusable" in step["why"]


def test_ineligible_dataset_with_digest_mismatch_stops():
    contract = {"contract_id": "C", "dataset_eligible": False, "dataset_provenance": "verified_market",
                "dataset_reason": "dataset digest differs from manifest (file changed or substituted)"}
    step = next_step(_ok_state(), contract, None)
    assert step["command"] == "STOP"
    assert "digest differs" in step["why"]


def test_synthetic_dataset_is_reported_but_not_a_hard_stop():
    contract = {"contract_id": "C", "dataset_eligible": False, "dataset_provenance": "synthetic",
                "dataset_reason": "not market eligible"}
    hypothesis = {"hypothesis_id": "HYP-2026-777", "registered": True, "contract_id": "C",
                  "trials_remaining": 1}
    step = next_step(_ok_state(), contract, hypothesis)
    assert step["command"] != "STOP"
