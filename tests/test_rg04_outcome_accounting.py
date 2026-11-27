"""RG-04 synthetic public runner and durable evidence lifecycle regressions."""

import pytest

from tests.test_rg03_candidate_admission import bound_identity, reserve, variant
from warden.contracts import ENGINE_SIMULATION_EPOCH
from warden.registry import ResearchRegistry


def test_interrupted_screen_is_selection_debt_before_any_outcome(tiny_repo):
    fixture = tiny_repo(provenance="synthetic")
    identity = bound_identity(fixture, variant(fixture, 1))
    reserve(fixture.registry, identity)
    reopened = ResearchRegistry(fixture.registry.db_path)
    history = reopened.selection_history(
        fixture.contract.contract_id, 14, simulation_epoch=ENGINE_SIMULATION_EPOCH,
    )
    assert history["raw_attempt_count"] == 1
    assert history["unvectorized_attempts"] == 1
    assert history["streams"] == []
    assert reopened.candidate_budget("HYP-TINY")["used"] == 1


def test_fixed_stress_evidence_is_finalized_before_trial_returns(tiny_repo):
    from warden.runner import ExperimentRunner

    fixture = tiny_repo(provenance="synthetic")
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    runner.run_stage1_screen(fixture.spec)
    result = runner.run_spec(fixture.spec)
    receipts = fixture.registry.robustness_status(result.run_id)
    assert {row["scenario_key"] for row in receipts} == {"cost_1p5", "cost_2x", "latency_plus1"}
    assert all(row["state"] == "FINALIZED" and row["evidence_sha256"] for row in receipts)
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 1
    assert fixture.registry.validation_attempt_count(fixture.contract.contract_id) == 5
    assert result.total_trials_n == 1


def test_grid_frozen_before_screen_and_variant_promotion_charges_slot(tiny_repo):
    import yaml

    from warden.registry import HypothesisBudgetExhaustedError
    from warden.runner import ExperimentRunner

    f = tiny_repo(provenance="synthetic")
    spec = variant(f, 4)
    payload = yaml.safe_load(spec.read_text())
    payload["param_grid"] = [{"candidate_variant": 3}, {"candidate_variant": 5}]
    original = yaml.safe_dump(payload)
    spec.write_text(original)
    runner = ExperimentRunner(registry=f.registry, repo_root=f.repo)
    runner.run_stage1_screen(spec)
    payload["param_grid"].append({"candidate_variant": 4})
    spec.write_text(yaml.safe_dump(payload))
    with pytest.raises(ValueError, match="robustness plan changed"):
        runner.run_spec(spec)
    spec.write_text(original)
    result = runner.run_spec(spec)
    assert len(f.registry.robustness_status(result.run_id)) == 5
    assert f.registry.validation_attempt_count(f.contract.contract_id) == 7
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1
    assert result.total_trials_n == 1
    runner.run_stage1_screen(variant(f, 3))
    runner.run_stage1_screen(variant(f, 5))
    with pytest.raises(HypothesisBudgetExhaustedError):
        runner.run_stage1_screen(variant(f, 6))
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 3


@pytest.mark.parametrize("grid", [
    [{"candidate_variant": 1}, {"candidate_variant": 2}, {"candidate_variant": 3}],
    [{"target_symbols": ["OTHER"]}],
])
def test_selectable_grid_refused_before_admission(tiny_repo, grid):
    import yaml

    from tests.test_rg03_candidate_admission import count
    from warden.runner import ExperimentRunner

    f = tiny_repo(provenance="synthetic")
    spec = variant(f, 4)
    payload = yaml.safe_load(spec.read_text())
    payload["param_grid"] = grid
    spec.write_text(yaml.safe_dump(payload))
    runner = ExperimentRunner(registry=f.registry, repo_root=f.repo)
    with pytest.raises(ValueError, match="separately admitted candidate"):
        runner.run_stage1_screen(spec)
    assert count(f.registry, "candidate_admissions") == 0
    assert count(f.registry, "robustness_receipts") == 0


def test_concurrent_reservation_and_interrupted_scenario(tiny_repo):
    from concurrent.futures import ThreadPoolExecutor

    f = tiny_repo(provenance="synthetic")
    candidate = bound_identity(f, f.spec)
    run = "RUN-RG04-INTERRUPTED"
    f.registry.register_trial_start("HYP-TINY", run, candidate=candidate)

    def reserve_one(_):
        try:
            return f.registry.reserve_robustness(run, "cost_2x")
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = [x for x in pool.map(reserve_one, range(8)) if x]
    assert len(admitted) == 1
    with pytest.raises(ValueError, match="declared clock"):
        f.registry.finalize_robustness(admitted[0], {"net_sharpe": 1.0}, returns=[0.1])
    assert f.registry.robustness_status(run)[0]["state"] == "RESERVED"
    assert f.registry.validation_attempt_count(f.contract.contract_id) == 2
    assert f.registry.selection_attempt_count(f.contract.contract_id) == 1
    reopened = ResearchRegistry(f.registry.db_path)
    assert reopened.reconcile_attempt(run, confirm_interrupted=True)["state"] == "INTERRUPTED_REVIEW"
    assert reopened.robustness_status(run)[0]["state"] == "INTERRUPTED_REVIEW"
    with pytest.raises(ValueError, match="active admitted full trial"):
        reopened.reserve_robustness(run, "cost_2x")
    assert reopened.candidate_budget("HYP-TINY")["used"] == 1


def test_failed_finalization_exposes_no_cli_performance(tiny_repo, monkeypatch, capsys):
    import sqlite3
    from types import SimpleNamespace

    from warden import cli
    from warden.runner import ExperimentRunner

    f = tiny_repo(provenance="synthetic")
    with sqlite3.connect(f.registry.db_path) as conn:
        conn.execute("CREATE TRIGGER fault BEFORE INSERT ON robustness_outcomes "
                     "BEGIN SELECT RAISE(ABORT,'injected finalization fault'); END")
    runner = ExperimentRunner(registry=f.registry, repo_root=f.repo)
    monkeypatch.setattr(cli, "check_worktree", lambda: True)
    rc = cli.cmd_backtest(SimpleNamespace(spec_yaml=str(f.spec), json=True), runner)
    captured = capsys.readouterr()
    assert rc == 1
    assert "WARDEN_RUN_COMPLETE" not in captured.out
    assert "injected finalization fault" in captured.err
    with sqlite3.connect(f.registry.db_path) as conn:
        run = conn.execute("SELECT run_id FROM attempt_receipts").fetchone()[0]
        conn.execute("DROP TRIGGER fault")
    assert f.registry.robustness_status(run)[0]["state"] == "RESERVED"
    assert f.registry.reconcile_attempt(run, confirm_interrupted=True)["robustness"][0]["state"] == "INTERRUPTED_REVIEW"
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1
    assert f.registry.spec_status("HYP-TINY", f.contract.contract_id, "strategies/flat.py", {})["state"] == "REQUIRES_RECONCILIATION"


def test_scheduler_stops_exhausted_candidate_before_verify(tiny_repo, monkeypatch):
    from warden import cli
    from warden.runner import ExperimentRunner

    f = tiny_repo(provenance="synthetic")
    runner = ExperimentRunner(registry=f.registry, repo_root=f.repo)
    for i in range(3):
        runner.run_stage1_screen(variant(f, i))
    monkeypatch.setattr(cli, "cmd_verify", lambda *a: pytest.fail("verification must not start"))
    result = cli._advance_one_spec(variant(f, 4), runner, check_outcome_memory=False, acquire_lease=False)
    assert result["action"] == "SKIP_TERMINAL"
    assert "budget exhausted" in result["reason"]
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 3


@pytest.mark.parametrize("script", ["af30_book_report.py", "xs_panel_diagnostics.py",
                                  "xs_residual_momentum_diag.py", "xs_residual_reversal_1h_diag.py"])
def test_retired_diagnostics_refuse_execution(tmp_path, script):
    import subprocess
    import sys
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "scripts" / script
    result = subprocess.run([sys.executable, str(source), "--execute"],
                            cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "REFUSED" in result.stderr
    assert result.stdout == ""
    assert list(tmp_path.iterdir()) == []


def test_v4_migration_does_not_erase_recorded_selection_debt(tiny_repo):
    import sqlite3

    from warden.runner import ExperimentRunner

    f = tiny_repo(provenance="synthetic")
    runner = ExperimentRunner(registry=f.registry, repo_root=f.repo)
    runner.run_stage1_screen(f.spec)
    runner.run_spec(f.spec)
    cid = f.contract.contract_id
    assert f.registry.selection_attempt_count(cid) == 1
    # Emulate a v4 ledger, whose screen and trial had separate search debt.
    with sqlite3.connect(f.registry.db_path) as conn:
        for table in ("robustness_outcomes", "robustness_receipts", "robustness_plans"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("PRAGMA user_version=4")
    reopened = ResearchRegistry(f.registry.db_path)
    assert reopened.selection_attempt_count(cid) == 2
    assert reopened.validation_attempt_count(cid) == 2
    assert reopened.candidate_budget("HYP-TINY")["used"] == 1
