"""Tests for Ticket TKT-10: Herdr dispatch, 6-check audit, and session transitions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import numpy as np

from warden.registry import ResearchRegistry
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


def _seed_historical_candidate(reg, contract, run_id="RUN-ML-001", synthetic=1):
    import numpy as np

    from warden.contracts import ExperimentResult, HypothesisSpec

    reg.register_contract(contract)
    reg.register_hypothesis(HypothesisSpec("HYP-ML-001", contract.contract_id, "T", "rules_ts",
                                           "Mechanism explanation of twenty chars",
                                           "Friction explanation of twenty chars", 8, "Criteria"))
    result = ExperimentResult(
        run_id, "HYP-ML-001", contract.contract_id, "Legacy", "rules_ts", "cfg", "STAGE_3_STRESS",
        "CANDIDATE_FOR_QUALIFICATION", 1.0, 1.5, 0.95, 0.2, 1, 1.0, None, None, 0.55, 1.4, 3.0,
        60.0, 12.0, 0.9, 0.9, "fixture", 0.1, 1.0, synthetic, contract.data_snapshot_sha256,
        "strategies/flat.py", "rules", "", data_provenance="synthetic" if synthetic else "verified_market")
    reg.record_trial(result, {}, np.linspace(0.01, -0.004, 120))
    with reg._connect() as conn:
        conn.execute(f"INSERT INTO feature_sets VALUES ('fh','{contract.contract_id}','[]','0.2.0',1,'p',1.0)")
    return result


def test_audit_check_refuses_effective_approval_for_synthetic_run_ml_001(signed_repo) -> None:
    """Issue #4 tickets 02/03: historical checks may pass, effective verdict may not approve."""
    from warden.cli import main
    from warden.contracts import load_contract

    runner = ExperimentRunner(repo_root=signed_repo)
    _seed_historical_candidate(runner.registry, load_contract(signed_repo / "contracts" / "crypto_perps_1h_v1.yaml"))
    ResearchRegistry(runner.registry.db_path)  # idempotent migration adds the superseding disposition

    assert main(["audit-check", "--run-id", "RUN-ML-001", "--json"]) == 1
    reports = list((signed_repo / ".workspace" / "hypotheses").glob("AUDIT_RUN-ML-001_*.md"))
    assert len(reports) == 1
    assert "Verdict: REVOKED_INELIGIBLE" in reports[0].read_text()
    assert "Verdict: APPROVED" not in reports[0].read_text()
    assert runner.registry.effective_eligibility("RUN-ML-001")["eligible"] is False


def test_audit_check_planted_violation_fails_closed(signed_repo) -> None:
    from warden.contracts import ExperimentResult

    runner = ExperimentRunner(repo_root=signed_repo)
    contract = runner.resolve_contract("CRYPTO_PERPS_1H_V1")
    _seed_historical_candidate(runner.registry, contract)
    failing = ExperimentResult(
        "RUN-FAIL-AUDIT", "HYP-ML-001", contract.contract_id, "Failing", "rules_ts", "cfg",
        "STAGE_3_STRESS", "CANDIDATE_FOR_QUALIFICATION", 1.0, 0.5, 0.95, 0.2, 2, 1.0, None, None,
        0.5, 1.2, 5.0, 100.0, 10.0, 0.8, 0.9, "fails delay survival", -0.5, 1.0, 0,
        contract.data_snapshot_sha256, "strategies/flat.py", "rules", "", data_provenance="verified_market")
    # A second reservation is fine for the same hypothesis (raw N 2 of 3).
    runner.registry.record_trial(failing, {}, np.linspace(0.01, -0.01, 120))
    from warden.cli import cmd_audit_check
    args = argparse.Namespace(run_id="RUN-FAIL-AUDIT", json=True)
    assert cmd_audit_check(args, runner) == 1
    with runner.registry._connect() as conn:
        assert conn.execute("SELECT verdict FROM audit_evidence WHERE run_id='RUN-FAIL-AUDIT'").fetchone()[0] == "REJECTED"


def test_handoff_and_continue_drift_detection(signed_repo, seeded_env_registry, tmp_path) -> None:
    from warden.cli import main

    # Generate a clean handoff inside the isolated signed repo.
    assert main(["handoff", "--ticket", "TKT-10", "--notes", "Testing drift"]) == 0
    state = json.loads((signed_repo / ".workspace" / "state" / "current.json").read_text())
    assert state["schema"] == 1
    assert {"created_at", "evidence", "tool_trace", "quality_gates"} <= set(state)
    assert all("observed_at" in e for e in state["evidence"])
    assert all({"op", "expect"} <= set(t) for t in state["tool_trace"])
    assert (signed_repo / ".workspace" / "state" / "history.jsonl").read_text().strip()

    assert main(["continue"]) == 0

    # Tamper the recorded trials count downward -> drift fails closed.
    tampered = json.loads(json.dumps(state))
    for entry in tampered["tool_trace"]:
        if entry.get("op") == "registry_count":
            entry["expect"] = "0"
    tampered_file = tmp_path / "tampered_state.json"
    tampered_file.write_text(json.dumps(tampered))
    assert main(["continue", str(tampered_file)]) == 1

    # An empty hand-authored trace cannot report "zero drift".
    tampered["schema"] = 1
    tampered["tool_trace"] = []
    tampered_file.write_text(json.dumps(tampered))
    assert main(["continue", str(tampered_file)]) == 1

    # Unsupported schema fails closed instead of being replayed.
    tampered["schema"] = 99
    tampered_file.write_text(json.dumps(tampered))
    assert main(["continue", str(tampered_file)]) == 1
