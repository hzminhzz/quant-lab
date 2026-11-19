"""Warden's transactional research admission, receipts, and no-retry recovery."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from warden.contracts import ExperimentResult, HypothesisSpec, canonical_json, load_contract
from warden.registry import ResearchRegistry
from warden.runner import default_run_id


def _registry(tmp_path: Path) -> ResearchRegistry:
    registry = ResearchRegistry(tmp_path / "registry.db")
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    registry.register_contract(contract)
    registry.register_hypothesis(HypothesisSpec(
        "HYP-A", contract.contract_id, "Crash-safe research", "rules_ts",
        "Observed inventory pressure could cause short-term continuation",
        "Fees and adverse selection may exhaust all apparent edge", 8,
        "Reject when executable net returns are not robust",
    ))
    return registry


def _result() -> ExperimentResult:
    return ExperimentResult(
        run_id="RUN-A", hypothesis_id="HYP-A", contract_id="CRYPTO_PERPS_1H_V1",
        strategy_name="Fixture", strategy_family="rules_ts", config_hash="abc",
        stage_reached="STAGE_3_STRESS", verdict="REVISE", gross_sharpe=1.0,
        net_sharpe=0.5, deflated_sharpe_prob=0.6,
        expected_max_sharpe_noise=0.1, total_trials_n=1, effective_trials_keff=1.0,
        mean_ic=None, ic_hac_tstat=None, hit_rate=0.5, profit_factor=1.1,
        max_drawdown_pct=2.0, annualized_turnover=10.0, breakeven_cost_bps=5.0,
        stress_2x_cost_sharpe=0.3, param_neighbor_stability=0.8,
        notes="Synthetic acceptance fixture only",
    )


def test_default_run_ids_cannot_collide_across_same_family_hypotheses(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    registry.register_hypothesis(HypothesisSpec(
        "HYP-OTHER", contract.contract_id, "Other mechanism", "rules_ts",
        "A different economic supply-demand mechanism may produce a signal",
        "Liquidity frictions may eliminate the effect before execution", 8,
        "Reject when the effect cannot exceed executable costs",
    ))
    one = default_run_id("HYP-A", 1)
    two = default_run_id("HYP-OTHER", 1)
    assert one != two
    assert one == default_run_id("HYP-A", 1)
    assert registry.register_trial_start("HYP-A", one) == 1
    assert registry.register_trial_start("HYP-OTHER", two) == 1


def test_finalized_early_rejection_remains_stage1_not_stage3(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    result = replace(_result(), stage_reached="STAGE_1_SIGNAL", verdict="REJECTED_EARLY",
                     strategy_module="strategies/flat.py",
                     config_hash=hashlib.sha256(canonical_json({}).encode()).hexdigest()[:12])
    registry.record_trial(result, {}, np.zeros(0))
    state = registry.spec_status("HYP-A", "CRYPTO_PERPS_1H_V1", "strategies/flat.py", {})
    assert state["state"] == "STAGE1_REJECT"
    assert state["verdict"] == "REJECTED_EARLY"
    assert state["run_id"] == "RUN-A"
    assert registry.reconcile_attempt("RUN-A")["state"] == "FINALIZED"


def test_reservation_survives_restart_and_interruption_is_never_refunded(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    assert registry.register_trial_start("HYP-A", "RUN-A") == 1
    assert registry.reconcile_attempt("RUN-A")["state"] == "RESERVED"
    reopened = ResearchRegistry(registry.db_path)
    assert reopened.reconcile_attempt("RUN-A", confirm_interrupted=True)["state"] == "INTERRUPTED_REVIEW"
    assert reopened.reconcile_attempt("RUN-A", confirm_interrupted=True)["state"] == "INTERRUPTED_REVIEW"
    with pytest.raises(ValueError, match="Duplicate"):
        reopened.register_trial_start("HYP-A", "RUN-A")
    with reopened._connect() as conn:
        assert conn.execute("SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-A'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM attempt_receipts WHERE run_id='RUN-A'").fetchone()[0] == 1


def test_completed_receipt_hashes_parquet_and_detects_tamper(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    data = np.linspace(-0.002, 0.003, 80)
    registry.record_trial(_result(), {"parameters": {"fast": 8}}, data)
    receipt = registry.reconcile_attempt("RUN-A")
    assert receipt["state"] == "FINALIZED" and receipt["eligible"] is True
    assert len(receipt["artifact_sha256"]) == 64
    with registry._connect() as conn:
        target = Path(conn.execute("SELECT returns_parquet_path FROM trials WHERE run_id='RUN-A'").fetchone()[0])
        assert conn.execute("SELECT state,trial_number FROM attempt_receipts WHERE run_id='RUN-A'").fetchone()[:] == ("FINALIZED", 1)
    target.write_bytes(b"tampered fixture")
    assert registry.reconcile_attempt("RUN-A")["state"] == "EVIDENCE_DRIFT"
    assert registry.reconcile_attempt("RUN-A")["eligible"] is False


def test_uncertain_artifact_remains_reviewable_after_db_insert_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import warden.registry as module
    registry = _registry(tmp_path)
    registry.register_trial_start("HYP-A", "RUN-A")
    original = module._insert

    def fail_on_trial(conn, table, values):
        if table == "trials":
            raise RuntimeError("fault after atomic artifact publication")
        return original(conn, table, values)

    monkeypatch.setattr(module, "_insert", fail_on_trial)
    with pytest.raises(RuntimeError, match="fault after"):
        registry.record_trial(_result(), {}, np.linspace(-0.001, 0.001, 30), trial_number=1)
    orphan = registry.db_path.parent / "returns" / "CRYPTO_PERPS_1H_V1" / "RUN-A.parquet"
    assert orphan.is_file() and not list(orphan.parent.glob("*.pending"))
    assert registry.reconcile_attempt("RUN-A", confirm_interrupted=True)["state"] == "INTERRUPTED_REVIEW"
    with registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 0
        assert conn.execute("SELECT trials_used FROM hypotheses").fetchone()[0] == 1


def test_lineage_budget_rechecked_inside_same_reservation_transaction(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    with registry._connect() as conn:
        contract_id = conn.execute("SELECT contract_id FROM hypotheses LIMIT 1").fetchone()[0]
    registry.register_hypothesis(HypothesisSpec(
        "HYP-B", contract_id, "Child hypothesis", "rules_ts",
        "The alternative retains the same underlying economic mechanism",
        "Fees and market impact remain under the same lineage constraints", 8,
        "Reject if the executable edge is absent", parent_hypothesis_id="HYP-A",
    ))
    for i in range(3):
        assert registry.register_trial_start("HYP-A", f"RUN-ROOT-{i}") == i + 1
    for i in range(2):
        assert registry.register_trial_start("HYP-B", f"RUN-CHILD-{i}") == i + 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(registry.register_trial_start, "HYP-B", f"RUN-RACE-{n}") for n in range(2)]
        outcomes = []
        for f in futures:
            try:
                outcomes.append(f.result())
            except ValueError:
                outcomes.append("BLOCKED")
    assert sorted(map(str, outcomes)) == ["3", "BLOCKED"]
    with registry._connect() as conn:
        assert conn.execute("SELECT SUM(trials_used) FROM hypotheses").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM attempt_receipts").fetchone()[0] == 6


def test_legacy_consumed_attempt_migrates_without_rewriting_history(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    with registry._connect() as conn:
        conn.execute("UPDATE hypotheses SET trials_used=1,attempts_json=? WHERE hypothesis_id='HYP-A'",
                     ('{"RUN-LEGACY":1}',))
    ResearchRegistry(registry.db_path)
    ResearchRegistry(registry.db_path)
    with registry._connect() as conn:
        assert conn.execute("SELECT state FROM attempt_receipts WHERE run_id='RUN-LEGACY'").fetchone()[0] == "INTERRUPTED_REVIEW"
        assert conn.execute("SELECT COUNT(*) FROM attempt_receipts WHERE run_id='RUN-LEGACY'").fetchone()[0] == 1
        assert conn.execute("SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-A'").fetchone()[0] == 1
