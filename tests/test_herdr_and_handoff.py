"""Tests for Ticket TKT-10: Herdr dispatch, 6-check audit, and session transitions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from warden.cli import check_worktree, generate_manifest
from warden.runner import ExperimentRunner


def test_herdr_dispatch_fallback_and_execution(tmp_path: Path) -> None:
    dispatch_script = Path("scripts/herdr_dispatch.sh")
    assert dispatch_script.is_file(), "scripts/herdr_dispatch.sh must exist"

    # 1. Fallback mode: HERDR_ENV not set -> runs synchronously
    test_out = tmp_path / "sync_out.txt"
    cmd = [str(dispatch_script), f"echo 'SYNC_PASS' > {test_out}"]
    env = {k: v for k, v in os.environ.items() if k != "HERDR_ENV"}
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert test_out.read_text().strip() == "SYNC_PASS"

    # 2. Mock herdr CLI mode: HERDR_ENV=1 with fake herdr in PATH
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    fake_herdr = mock_bin / "herdr"
    fake_herdr.write_text("""#!/usr/bin/env bash
echo "MOCK_HERDR: $@"
if [[ "$*" == *"wait-output"* ]]; then
    echo "WARDEN_RUN_COMPLETE"
fi
""")
    fake_herdr.chmod(0o755)

    env_herdr = {**env, "HERDR_ENV": "1", "PATH": f"{mock_bin}:{os.environ['PATH']}"}
    proc_herdr = subprocess.run(
        [str(dispatch_script), "--pane", "w1:p2", "uv run warden status"],
        env=env_herdr, capture_output=True, text=True, check=False
    )
    assert proc_herdr.returncode == 0
    assert "[herdr_dispatch]" in proc_herdr.stdout
    assert "MOCK_HERDR: pane run w1:p2 uv run warden status" in proc_herdr.stdout


def test_audit_check_evaluates_all_six_checks_on_candidate() -> None:
    # Make sure manifest and worktree check are clean
    generate_manifest()
    assert check_worktree() is True

    # Run audit-check via CLI on RUN-ML-001
    cmd = [sys.executable, "-m", "warden.cli", "audit-check", "--run-id", "RUN-ML-001", "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"Audit check failed: {proc.stderr or proc.stdout}"

    import json
    data = json.loads(proc.stdout)
    assert data["run_id"] == "RUN-ML-001"
    assert data["audit_passed"] is True
    checks = data["checks"]
    assert len(checks) == 6
    for c_name, c_res in checks.items():
        assert c_res["passed"] is True, f"Check '{c_name}' failed for RUN-ML-001"

    # Verify report written
    report = Path(".workspace/hypotheses/AUDIT_RUN-ML-001.md")
    assert report.is_file()
    assert "Verdict: APPROVED" in report.read_text()


def test_audit_check_planted_violation_fails_closed(tmp_path: Path) -> None:
    # Plant an artificial run with failing delay survival in a temporary registry
    from warden.contracts import ExperimentResult
    from warden.registry import ResearchRegistry

    fake_db = tmp_path / "reg.db"
    reg = ResearchRegistry(fake_db)

    # Insert a failing trial with delay_plus1_sharpe = -0.5
    failing_result = ExperimentResult(
        run_id="RUN-FAIL-AUDIT",
        hypothesis_id="HYP-2026-002",
        contract_id="CRYPTO_PERPS_1H_V1",
        strategy_name="FailingStrategy",
        strategy_family="cross_sectional_ml",
        config_hash="abc",
        stage_reached="STAGE_3_STRESS",
        verdict="REVISE",
        gross_sharpe=1.0,
        net_sharpe=0.5,
        deflated_sharpe_prob=0.85,
        expected_max_sharpe_noise=0.2,
        total_trials_n=1,
        effective_trials_keff=1.0,
        mean_ic=0.03,
        ic_hac_tstat=2.5,
        hit_rate=0.5,
        profit_factor=1.2,
        max_drawdown_pct=5.0,
        annualized_turnover=100.0,
        breakeven_cost_bps=10.0,
        stress_2x_cost_sharpe=0.8,
        param_neighbor_stability=0.9,
        notes="Failing delay test",
        delay_plus1_sharpe=-0.5,  # FAILS check 5!
    )

    runner = ExperimentRunner(registry=reg, repo_root=Path("."))
    # Must register contract first
    from warden.contracts import load_contract
    reg.register_contract(load_contract(Path("contracts/crypto_perps_1h_v1.yaml")))

    from warden.contracts import HypothesisSpec
    reg.register_hypothesis(HypothesisSpec(
        "HYP-2026-002", "CRYPTO_PERPS_1H_V1", "Title", "cross_sectional_ml",
        "Economic mechanism here", "Counterparty friction here", 8, "Criteria"
    ))

    reg.record_trial(failing_result, {}, np.linspace(0.01, -0.01, 100))

    import argparse

    from warden.cli import cmd_audit_check
    args = argparse.Namespace(run_id="RUN-FAIL-AUDIT", json=True)
    ret = cmd_audit_check(args, runner)
    # Must return 1 (falsified/rejected)
    assert ret == 1


def test_handoff_and_continue_drift_detection(tmp_path: Path) -> None:
    # Generate clean handoff
    cmd_h = [sys.executable, "-m", "warden.cli", "handoff", "--ticket", "TKT-10", "--notes", "Testing drift"]
    proc_h = subprocess.run(cmd_h, capture_output=True, text=True, check=False)
    assert proc_h.returncode == 0

    # Typed state artifact: schema, timestamped evidence, replayable trace, explicit gates
    state = json.loads(Path(".workspace/state/current.json").read_text())
    assert state["schema"] == 1
    assert {"created_at", "evidence", "tool_trace", "quality_gates"} <= set(state)
    assert all("observed_at" in e for e in state["evidence"])
    assert all({"op", "expect"} <= set(t) for t in state["tool_trace"])
    assert Path(".workspace/state/history.jsonl").read_text().strip()

    # Continue on clean state passes (zero drift)
    cmd_c = [sys.executable, "-m", "warden.cli", "continue"]
    proc_c = subprocess.run(cmd_c, capture_output=True, text=True, check=False)
    assert proc_c.returncode == 0
    assert "zero drift" in proc_c.stdout

    # Tamper the recorded trials count downward (the real "a trial was appended" case) -> drift fails closed
    tampered = json.loads(json.dumps(state))
    for entry in tampered["tool_trace"]:
        if entry.get("op") == "registry_count":
            entry["expect"] = "0"
    tampered_file = tmp_path / "tampered_state.json"
    tampered_file.write_text(json.dumps(tampered))

    proc_drift = subprocess.run([sys.executable, "-m", "warden.cli", "continue", str(tampered_file)], capture_output=True, text=True, check=False)
    assert proc_drift.returncode == 1
    assert "DRIFT" in proc_drift.stderr or "DRIFT" in proc_drift.stdout
    assert "state advanced" in proc_drift.stderr

    # Unsupported schema fails closed instead of being replayed
    tampered["schema"] = 99
    tampered_file.write_text(json.dumps(tampered))
    proc_schema = subprocess.run([sys.executable, "-m", "warden.cli", "continue", str(tampered_file)], capture_output=True, text=True, check=False)
    assert proc_schema.returncode == 1
    assert "schema" in proc_schema.stderr
