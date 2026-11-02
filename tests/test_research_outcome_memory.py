"""Persistent research-outcome audit and non-replay regression tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from scripts.research_outcome_memory import check_spec, inventory, load_index


def source_registry(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE hypotheses (
                hypothesis_id TEXT PRIMARY KEY,
                contract_id TEXT, title TEXT, status TEXT,
                strategy_module TEXT, parent_hypothesis_id TEXT
            );
            CREATE TABLE trials (
                hypothesis_id TEXT, stage_reached TEXT, verdict TEXT,
                run_id TEXT
            );
            CREATE TABLE selection_attempts (
                hypothesis_id TEXT, stage TEXT, outcome TEXT,
                attempt_id TEXT
            );
            INSERT INTO hypotheses VALUES
                ('HYP-TEST-FAIL', 'CONTRACT-A', 'Rejected experiment',
                 'ACTIVE', 'strategies/candidates/failed.py', NULL),
                ('HYP-TEST-EARLY', 'CONTRACT-A', 'Rejected screen',
                 'ACTIVE', 'strategies/candidates/early.py', NULL),
                ('HYP-TEST-NEVER', 'CONTRACT-A', 'Unseen strategy',
                 'ACTIVE', NULL, NULL),
                ('HYP-TEST-PASS', 'CONTRACT-A', 'Predictive unqualified',
                 'ACTIVE', NULL, NULL);
            INSERT INTO trials VALUES
                ('HYP-TEST-FAIL','STAGE_3_STRESS','REVISE','RUN-TEST-FAIL');
            INSERT INTO selection_attempts VALUES
                ('HYP-TEST-EARLY','STAGE_1_SIGNAL','REJECT','ATT-TEST-EARLY'),
                ('HYP-TEST-PASS','STAGE_1_SIGNAL','PASS','ATT-TEST-PASS');
        """)


def spec(tmp_path: Path, hypothesis: str, contract: str = "CONTRACT-A",
         module: str = "strategies/candidates/other.py") -> Path:
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump({
        "hypothesis_id": hypothesis,
        "contract_id": contract,
        "strategy_module": module,
    }))
    return path


def test_branch_failure_index_is_complete_and_distinguishes_preflight() -> None:
    manifest = load_index()
    entries = manifest["outcomes"]
    assert len(entries) == 15
    assert len(manifest["original_unattempted"]) == 9
    status = {x["disposition"] for x in entries}
    assert status == {
        "STAGE1_REJECT", "STAGE3_REVISE", "PREFLIGHT_FAIL",
        "STAGE1_PASS_UNQUALIFIED",
    }
    assert sum(x["disposition"] == "STAGE1_PASS_UNQUALIFIED" for x in entries) == 2
    assert {x["source_pr"] for x in entries} == {40, 41, 42}
    r = next(x for x in entries if x["hypothesis_id"] == "HYP-XS4H-SIGN-REV-20261102")
    assert r["disposition"] == "STAGE3_REVISE"
    assert "net Sharpe=-8.1754" in r["summary"]
    assert all(x["auto_retry"] == "DENY_SAME_CONFIG" for x in entries)


@pytest.mark.parametrize("hid", [
    "HYP-XS4H-SIGN-REV-20261102",
    "HYP-CME-NQ-ORB-RTH-20261102",
    "HYP-ARXIV-XS-PRICE-PRESSURE-20261102",
    "HYP-ORDERFLOW-ATS-H72-20261102",
    "HYP-ARXIV-XS-LOWVOL-24H-20261102",
])
def test_open_pr_negative_and_unqualified_studies_deny_replay(tmp_path: Path, hid: str) -> None:
    db = tmp_path / "fresh.db"
    source_registry(db)
    row = next(x for x in load_index()["outcomes"] if x["hypothesis_id"] == hid)
    result = check_spec(spec(tmp_path, hid, row["contract_id"]), db)
    assert not result["eligible"]
    assert result["status"] == "HISTORICAL_RESULT_LOCKED"


def test_contract_refresh_cannot_silently_reset_original_study(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    source_registry(db)
    old = next(x for x in load_index()["outcomes"] if x["hypothesis_id"].startswith("HYP-CME-ES"))
    result = check_spec(spec(tmp_path, old["hypothesis_id"], "NEW_CONTRACT"), db)
    assert result["status"] == "CROSS_CONTRACT_REVIEW_REQUIRED"


@pytest.mark.parametrize("hid", ["HYP-2026-035", "HYP-2026-050", "HYP-2026-054",
                                     "HYP-2026-062", "HYP-2026-115"])
def test_original_data_blockers_cannot_be_auto_screened(tmp_path: Path, hid: str) -> None:
    db = tmp_path / "fresh.db"
    source_registry(db)
    result = check_spec(spec(tmp_path, hid), db)
    assert result["status"] == "ORIGINAL_BLOCKER_UNRESOLVED"


@pytest.mark.parametrize("hid,status", [
    ("HYP-TEST-FAIL", "HISTORICAL_TRIAL_ALREADY_RECORDED"),
    ("HYP-TEST-EARLY", "HISTORICAL_SELECTION_ATTEMPT_RECORDED"),
    ("HYP-TEST-PASS", "HISTORICAL_SELECTION_ATTEMPT_RECORDED"),
])
def test_live_warnings_are_independent_of_off_main_manifest(tmp_path: Path,
                                                             hid: str, status: str) -> None:
    db = tmp_path / "source.db"
    source_registry(db)
    assert check_spec(spec(tmp_path, hid), db)["status"] == status


def test_unattempted_new_hypothesis_is_not_misclassified_as_failed(tmp_path: Path) -> None:
    db = tmp_path / "source.db"
    source_registry(db)
    assert check_spec(spec(tmp_path, "HYP-TEST-NEVER"), db)["eligible"]
    assert check_spec(spec(tmp_path, "HYP-TEST-UNREGISTERED"), db)["eligible"]


def test_off_main_failed_module_cannot_be_renamed_to_reset_budget(tmp_path: Path) -> None:
    db = tmp_path / "source.db"
    source_registry(db)
    result = check_spec(
        spec(
            tmp_path, "HYP-NEW-BUT-DUPLICATED",
            module="strategies/candidates/four_xs4h/sign_reversal.py",
        ),
        db,
    )
    assert result["status"] == "CROSS_BRANCH_MODULE_ALIAS_REVIEW_REQUIRED"
    assert "HYP-XS4H-SIGN-REV-20261102" in str(result["evidence"])


def test_reusing_a_failed_module_under_new_id_requires_lineage_review(tmp_path: Path) -> None:
    db = tmp_path / "source.db"
    source_registry(db)
    result = check_spec(
        spec(tmp_path, "HYP-TEST-ALIAS", module="strategies/candidates/failed.py"),
        db,
    )
    assert result["status"] == "MODULE_ALIAS_REVIEW_REQUIRED"


def test_contract_id_mismatch_is_not_a_retry(tmp_path: Path) -> None:
    db = tmp_path / "source.db"
    source_registry(db)
    assert (check_spec(spec(tmp_path, "HYP-TEST-NEVER", "CONTRACT-B"), db)
            ["status"] == "CONTRACT_ID_MISMATCH")


def test_missing_registry_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="registry not present"):
        check_spec(spec(tmp_path, "HYP-SOMETHING"), tmp_path / "missing.db")


def test_inventory_counts_must_exclude_stage1_rejections_from_full_trial_backlog(
    tmp_path: Path,
) -> None:
    db = tmp_path / "source.db"
    source_registry(db)
    report = inventory(db)
    assert report["registry"] == {
        "hypotheses": 4, "trials": 1, "selection_attempts": 2,
    }
    assert report["without_any_trial"] == 3
    assert report["without_stage3"] == 3
    assert report["stage1_rejected_without_stage3"] == 1
    assert report["stage1_passed_without_stage3"] == 1
    assert report["never_attempted_count"] == 1
    assert report["never_attempted"][0]["hypothesis_id"] == "HYP-TEST-NEVER"
    assert report["extra_outcomes_in_open_prs"]["negative"] == 13


def test_hourly_runner_checks_failure_memory_before_screening() -> None:
    source = Path("scripts/run_hourly_pipeline.sh").read_text()
    guard = source.index("python -m scripts.research_outcome_memory --check-spec")
    verify = source.index('uv run warden verify "$spec"')
    screen = source.index('uv run warden screen "$spec"')
    assert guard < verify < screen
    assert 'mv "$spec" experiments/active/failed/' in source
    assert "WARDEN_NON_INTERACTIVE=1" in source


def test_corrupt_sidecar_or_duplicate_evidence_fails_closed(tmp_path: Path) -> None:
    data = json.loads(Path("docs/research-outcomes/branch-result-index-v1.json").read_text())
    data["outcomes"].append(data["outcomes"][0])
    path = tmp_path / "corrupted.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Duplicate"):
        load_index(path)
