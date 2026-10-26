"""Issue #4 tickets 02/03: one effective eligibility policy and append-only corrections."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from warden.cli import _build_state, cmd_audit_check, cmd_status
from warden.contracts import ExperimentResult, HypothesisSpec, load_contract
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner

REPO = Path(".")
CONTRACT = load_contract(REPO / "contracts" / "crypto_perps_1h_v1.yaml")


def _registry(tmp_path: Path) -> ResearchRegistry:
    reg = ResearchRegistry(tmp_path / "registry.db")
    reg.register_contract(CONTRACT)
    reg.register_hypothesis(HypothesisSpec("HYP-E", CONTRACT.contract_id, "T", "rules_ts",
                                           "Mechanism explanation of twenty chars",
                                           "Friction explanation of twenty chars", 8, "Criteria"))
    return reg


def _candidate(run_id: str, provenance: str, sha: str = CONTRACT.data_snapshot_sha256,
               verdict: str = "CANDIDATE_FOR_QUALIFICATION") -> ExperimentResult:
    return ExperimentResult(
        run_id, "HYP-E", CONTRACT.contract_id, "S", "rules_ts", "cfg", "STAGE_3_STRESS", verdict,
        1.0, 1.5, 0.95, 0.2, 1, 1.0, None, None, 0.55, 1.4, 3.0, 60.0, 12.0, 0.9, 0.9,
        "fixture", 0.1, 1.0, 0, sha, "strategies/candidates/vol_breakout_1h.py", "rules", "",
        provenance, "{}")


def _with_returns(tmp_path: Path, reg: ResearchRegistry, result: ExperimentResult, values=None) -> Path:
    vector = np.asarray(values if values is not None else np.linspace(0.01, -0.004, 120))
    reg.record_trial(result, {}, vector)
    with reg._connect() as conn:
        return Path(conn.execute("SELECT returns_parquet_path FROM trials WHERE run_id=?",
                                 (result.run_id,)).fetchone()[0])


def test_run_ml_001_regression_case_is_never_effectively_approved(tmp_path: Path, signed_repo) -> None:
    reg = _registry(tmp_path)
    _with_returns(tmp_path, reg, _candidate("RUN-ML-001", "synthetic"))
    with reg._connect() as conn:
        conn.execute("INSERT INTO feature_sets VALUES ('fh','CRYPTO_PERPS_1H_V1','[]','0.2.0',1,'p',1.0)")
    reg.record_disposition("RUN-ML-001", "QUARANTINED_SYNTHETIC",
                           "Legacy RUN-ML-001 recorded synthetic=0 with no dataset digest; treated as synthetic.",
                           "warden:test")
    elig = reg.effective_eligibility("RUN-ML-001")
    assert elig["eligible"] is False
    assert elig["historical_verdict"] == "CANDIDATE_FOR_QUALIFICATION"
    assert "QUARANTINED_SYNTHETIC" in elig["reason"]
    assert [d["disposition"] for d in elig["dispositions"]] == ["QUARANTINED_SYNTHETIC"]

    runner = ExperimentRunner(registry=reg, repo_root=signed_repo)
    ret = cmd_audit_check(argparse.Namespace(run_id="RUN-ML-001", json=True), runner)
    assert ret == 1
    with reg._connect() as conn:
        verdict, reason = conn.execute("SELECT verdict, reason FROM audit_evidence WHERE run_id='RUN-ML-001'").fetchone()
    assert verdict == "REVOKED_INELIGIBLE" and "QUARANTINED_SYNTHETIC" in reason


def test_effective_eligibility_agrees_across_status_and_handoff(tmp_path: Path, signed_repo, monkeypatch) -> None:
    reg = _registry(tmp_path)
    _with_returns(tmp_path, reg, _candidate("RUN-OK", "verified_market"))
    _with_returns(tmp_path, reg, _candidate("RUN-UNPINNED", "verified_market", sha=""))
    runner = ExperimentRunner(registry=reg, repo_root=REPO)

    import contextlib
    import io
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert cmd_status(argparse.Namespace(json=True), runner) == 0
    status = json.loads(buffer.getvalue())["value"]["recent_trials"]
    by_run = {row["run_id"]: row for row in status}
    assert by_run["RUN-OK"]["eligible"] is True
    assert by_run["RUN-UNPINNED"]["eligible"] is False
    assert "no pinned dataset digest" in by_run["RUN-UNPINNED"]["eligibility_reason"]

    monkeypatch.setenv("WARDEN_REGISTRY", str(reg.db_path))
    state = _build_state(signed_repo, "issue-4", "eligibility")  # isolated registry: additive evidence
    assert any(e["kind"] == "effective_eligibility" for e in state["evidence"])
    assert "eligibility_surface_reachable" in state["quality_gates"]


def test_migration_is_idempotent_and_history_is_immutable(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    _with_returns(tmp_path, reg, _candidate("RUN-ML-001", "synthetic"))
    with reg._connect() as conn:
        before_trial = dict(conn.execute("SELECT * FROM trials WHERE run_id='RUN-ML-001'").fetchone())
        before_audit = [dict(r) for r in conn.execute("SELECT * FROM audit_reports")]
    first = reg.effective_eligibility("RUN-ML-001")
    reopened = ResearchRegistry(reg.db_path)          # second migration pass
    second = reopened.effective_eligibility("RUN-ML-001")
    assert first["eligible"] == second["eligible"]
    with reopened._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trial_dispositions WHERE run_id='RUN-ML-001'").fetchone()[0] == 1
        assert dict(conn.execute("SELECT * FROM trials WHERE run_id='RUN-ML-001'").fetchone()) == before_trial
        assert [dict(r) for r in conn.execute("SELECT * FROM audit_reports")] == before_audit
        with pytest.raises(sqlite3.IntegrityError, match="Append-only"):
            conn.execute("UPDATE trial_dispositions SET reason='rewritten'")
        with pytest.raises(sqlite3.IntegrityError, match="Append-only"):
            conn.execute("DELETE FROM trial_dispositions")
        conn.execute("INSERT OR IGNORE INTO audit_evidence VALUES ('AUD-X','RUN-ML-001','C','REJECTED','{}','',1.0)")
        with pytest.raises(sqlite3.IntegrityError, match="Append-only"):
            conn.execute("UPDATE audit_evidence SET verdict='APPROVED'")


def test_verified_market_candidate_keeps_the_normal_lifecycle(tmp_path: Path, signed_repo) -> None:
    reg = _registry(tmp_path)
    _with_returns(tmp_path, reg, _candidate("RUN-MARKET", "verified_market"))
    with reg._connect() as conn:
        conn.execute("INSERT INTO feature_sets VALUES ('fh','CRYPTO_PERPS_1H_V1','[]','0.2.0',1,'p',1.0)")
    runner = ExperimentRunner(registry=reg, repo_root=signed_repo)
    assert cmd_audit_check(argparse.Namespace(run_id="RUN-MARKET", json=True), runner) == 0
    with reg._connect() as conn:
        assert conn.execute("SELECT verdict FROM audit_evidence WHERE run_id='RUN-MARKET'").fetchone()[0] == "APPROVED"
    assert reg.effective_eligibility("RUN-MARKET")["eligible"] is True


def test_corrections_require_attribution_and_unknown_runs_are_ineligible(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    _with_returns(tmp_path, reg, _candidate("RUN-X", "verified_market"))
    with pytest.raises(ValueError, match="substantive reason"):
        reg.record_disposition("RUN-X", "REVOKED", "short", "warden:test")
    with pytest.raises(ValueError, match="not found"):
        reg.record_disposition("RUN-MISSING", "REVOKED", "A sufficiently long reason for the record", "warden:test")
    assert reg.effective_eligibility("RUN-NOPE")["eligible"] is False
    reg.record_disposition("RUN-X", "REVOKED", "Superseding decision recorded for the regression fixture", "warden:test")
    assert reg.effective_eligibility("RUN-X")["eligible"] is False
    assert hashlib.sha256(b"x").hexdigest() != ""
