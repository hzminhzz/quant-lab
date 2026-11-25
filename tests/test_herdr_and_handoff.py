"""Tests for Ticket TKT-10: Herdr dispatch, 6-check audit, and session transitions."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from warden.cli import check_worktree, generate_manifest
from warden.runner import ExperimentRunner


def _make_isolated_cli_repo(tmp_path: Path) -> Path:
    """Create an isolated repo copy in tmp_path so tests never mutate repo-root .workspace/."""
    src_root = Path.cwd()
    work = tmp_path / "repo"
    work.mkdir(parents=True, exist_ok=True)
    for rel in ("src", "contracts", "scripts", "AGENTS.md", "pyproject.toml", "uv.lock"):
        s = src_root / rel
        if s.exists():
            os.symlink(s, work / rel)
    (work / "registry").mkdir(parents=True, exist_ok=True)
    src_db = src_root / "registry" / "registry.db"
    if src_db.is_file():
        src_conn = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
        dst_conn = sqlite3.connect(work / "registry" / "registry.db")
        src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@quant-lab.local"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init", "-q"], cwd=work, check=True)
    generate_manifest(work)
    return work


def _seed_run_ml_001_ledger(root: Path) -> Path:
    """Seeded RUN-ML-001 synthetic trial at ``root/registry/registry.db``, inside ``tmp_path``.

    The test used to query the checkout's ``registry/registry.db`` directly: on a clean tree
    that raised ``OperationalError``, and when a sibling test had just created the file it hit
    ``no such table: trials`` (DDL not committed) or skipped when the row was absent -- three
    different outcomes for one test.  The seed states the denial case explicitly, mirroring the
    recorded legacy disposition of RUN-ML-001 (a synthetic run with no pinned market digest,
    quarantined as such), so the refusal is exercised on every checkout in any order.
    """
    from warden.contracts import ExperimentResult, HypothesisSpec, load_contract
    from warden.registry import ResearchRegistry

    reg = ResearchRegistry(root / "registry" / "registry.db")
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    reg.register_contract(contract)
    reg.register_hypothesis(
        HypothesisSpec(
            "HYP-ML-001",
            contract.contract_id,
            "Legacy RUN-ML-001 baseline",
            "rules_ts",
            "Economic mechanism here",
            "Counterparty friction here",
            8,
            "Criteria",
        )
    )
    reg.record_trial(
        ExperimentResult(
            run_id="RUN-ML-001",
            hypothesis_id="HYP-ML-001",
            contract_id=contract.contract_id,
            strategy_name="RidgeBaseline",
            strategy_family="rules_ts",
            config_hash="cfg-run-ml-001",
            stage_reached="STAGE_3_STRESS",
            verdict="CANDIDATE_FOR_QUALIFICATION",
            gross_sharpe=1.2,
            net_sharpe=1.1,
            deflated_sharpe_prob=0.96,
            expected_max_sharpe_noise=0.2,
            total_trials_n=1,
            effective_trials_keff=1.0,
            mean_ic=None,
            ic_hac_tstat=None,
            hit_rate=0.55,
            profit_factor=1.3,
            max_drawdown_pct=3.0,
            annualized_turnover=60.0,
            breakeven_cost_bps=12.0,
            stress_2x_cost_sharpe=0.9,
            param_neighbor_stability=0.9,
            notes="seeded legacy synthetic baseline",
            synthetic=1,
            data_snapshot_sha256="",
            strategy_module="strategies/candidates/ts_breakout_20_1h.py",
            model_type="rules",
            data_provenance="synthetic",
        ),
        {},
        np.linspace(0.01, -0.004, 120),
    )
    reg.record_disposition(
        "RUN-ML-001",
        "QUARANTINED_SYNTHETIC",
        "Legacy RUN-ML-001 recorded synthetic=0 with no dataset digest; treated as synthetic.",
        "warden:test",
    )
    return reg.db_path


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


def test_audit_check_refuses_effective_approval_for_synthetic_run_ml_001(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #4 tickets 02/03: historical checks may pass, effective verdict may not approve."""
    # The trial and its return artifact live inside the isolated repo, so the audit reads the
    # seeded evidence through the registry/returns fence -- never the checkout's ledger.
    work = _make_isolated_cli_repo(tmp_path)
    seed_db = _seed_run_ml_001_ledger(work)
    with sqlite3.connect(seed_db) as conn:
        historic = conn.execute("SELECT 1 FROM trials WHERE run_id='RUN-ML-001'").fetchone()
    assert historic is not None, "the seeded ledger must carry the RUN-ML-001 trial"
    monkeypatch.chdir(work)
    assert check_worktree(work) is True

    cmd = [sys.executable, "-m", "warden.cli", "audit-check", "--run-id", "RUN-ML-001", "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 1, f"RUN-ML-001 must not be approved: {proc.stderr or proc.stdout}"

    data = json.loads(proc.stdout)
    assert data["run_id"] == "RUN-ML-001"
    assert data["historical_checks_passed"] is False or data["effective_eligibility"]["eligible"] is False
    assert len(data["checks"]) == 6
    assert data["audit_passed"] is False
    assert data["verdict"] in {"REJECTED", "REVOKED_INELIGIBLE"}
    assert data["effective_eligibility"]["eligible"] is False

    report = work / ".workspace" / "hypotheses" / "AUDIT_RUN-ML-001.md"
    assert report.is_file()
    assert any(f"Verdict: {v}" in report.read_text() for v in ("REJECTED", "REVOKED_INELIGIBLE"))
    assert "Verdict: APPROVED" not in report.read_text()


def test_audit_check_planted_violation_fails_closed(tmp_path: Path) -> None:
    # Plant an artificial run with failing delay survival in a temporary registry
    from warden.contracts import ExperimentResult
    from warden.registry import ResearchRegistry

    (tmp_path / "contracts").mkdir(parents=True, exist_ok=True)
    shutil.copy2("contracts/crypto_perps_1h_v1.yaml", tmp_path / "contracts" / "crypto_perps_1h_v1.yaml")
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

    runner = ExperimentRunner(registry=reg, repo_root=tmp_path)
    # Must register contract first
    from warden.contracts import load_contract
    reg.register_contract(load_contract(tmp_path / "contracts" / "crypto_perps_1h_v1.yaml"))

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


def test_handoff_and_continue_drift_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = _make_isolated_cli_repo(tmp_path)
    monkeypatch.chdir(work)
    # Generate clean handoff
    cmd_h = [sys.executable, "-m", "warden.cli", "handoff", "--ticket", "TKT-10", "--notes", "Testing drift"]
    proc_h = subprocess.run(cmd_h, capture_output=True, text=True, check=False)
    assert proc_h.returncode == 0

    # Typed state artifact: schema, timestamped evidence, replayable trace, explicit gates
    state = json.loads((work / ".workspace" / "state" / "current.json").read_text())
    assert state["schema"] == 1
    assert {"created_at", "evidence", "tool_trace", "quality_gates"} <= set(state)
    assert all("observed_at" in e for e in state["evidence"])
    assert all({"op", "expect"} <= set(t) for t in state["tool_trace"])
    assert (work / ".workspace" / "state" / "history.jsonl").read_text().strip()

    # Continue on clean state passes (zero drift)
    cmd_c = [sys.executable, "-m", "warden.cli", "continue"]
    proc_c = subprocess.run(cmd_c, capture_output=True, text=True, check=False)
    assert proc_c.returncode == 0
    assert "zero drift" in proc_c.stdout

    # Tamper the recorded trials count downward (the real "a trial was appended" case) -> drift fails closed
    tampered = json.loads(json.dumps(state))
    for entry in tampered["tool_trace"]:
        if entry.get("op") == "registry_count":
            entry["expect"] = str(int(entry["expect"]) + 1)
    tampered_file = tmp_path / "tampered_state.json"
    tampered_file.write_text(json.dumps(tampered))

    proc_drift = subprocess.run([sys.executable, "-m", "warden.cli", "continue", str(tampered_file)], capture_output=True, text=True, check=False)
    assert proc_drift.returncode == 1
    assert "DRIFT" in proc_drift.stderr or "DRIFT" in proc_drift.stdout
    assert "expected" in proc_drift.stderr

    # Unsupported schema fails closed instead of being replayed
    tampered["schema"] = 99
    tampered_file.write_text(json.dumps(tampered))
    proc_schema = subprocess.run([sys.executable, "-m", "warden.cli", "continue", str(tampered_file)], capture_output=True, text=True, check=False)
    assert proc_schema.returncode == 1
    assert "schema" in proc_schema.stderr
