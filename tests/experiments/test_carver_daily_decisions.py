"""Decision records must be derived, fail closed, and be byte-identical on rerun."""

from __future__ import annotations

import json
import runpy
import sqlite3
from pathlib import Path

from warden.registry import ResearchRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER = runpy.run_path(str(REPO_ROOT / "scripts" / "build_carver_daily_decisions.py"))
collect = BUILDER["collect"]
write_all = BUILDER["write_all"]
stage1_failures = BUILDER["stage1_failures"]
stage3_failures = BUILDER["stage3_failures"]

HYP = "HYP-2026-095"
TRIAL = {"run_id": "RUN-HYP095-001", "hypothesis_id": HYP, "contract_id": "MULTI_ASSET_1D_V1",
         "trial_number_in_hyp": 1, "net_sharpe": 1.317, "stress_2x_cost_sharpe": 1.317,
         "delay_plus1_sharpe": 1.317, "deflated_sharpe_prob": 0.663, "total_trials_n": 29,
         "effective_trials_keff": 11.61, "expected_max_sharpe_noise": 1.0567,
         "diagnostics_json": json.dumps({"selection": {"unvectorized_attempts": 17}}),
         "breakeven_cost_bps": 2728.6, "annualized_turnover": 0.39,
         "param_neighbor_stability": 1.0, "stage_reached": "STAGE_3_STRESS", "verdict": "REVISE"}
SCREEN = {"attempt_id": "ATT-1", "attempt_base": "base-1", "hypothesis_id": HYP,
          "contract_id": "MULTI_ASSET_1D_V1", "kind": "standalone_screen",
          "stage": "STAGE_1_SIGNAL", "outcome": "REJECT", "created_at": 1.0,
          "diagnostics_json": json.dumps({"profit_factor": 1.03, "conditional_edge": 4.4e-05,
                                          "trade_edge": 1.5e-04, "time_in_market": 1.0,
                                          "bootstrap_p_value": 0.31, "avg_holding_bars": 35.1})}


def build_registry(tmp_path: Path, *, with_trial: bool = True, with_screen: bool = True,
                   screen_metrics: dict[str, float] | None = None) -> Path:
    db = tmp_path / "registry.db"
    ResearchRegistry(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO contract_search_state VALUES (?,?,?,?)",
                ("MULTI_ASSET_1D_V1", 11.61, 1.057, 0.0))
    con.execute(
        "INSERT INTO hypotheses (hypothesis_id,contract_id,title,strategy_family,economic_mechanism,"
        "counterparty_or_friction,expected_holding_bars,falsification_criteria,trials_used,status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (HYP, "MULTI_ASSET_1D_V1", "AF30 S01 Buy and Hold", "rules_ts", "mechanism", "friction",
         312, "profit factor < 1.15", 1 if with_trial else 0, "ACTIVE"))
    if with_trial:
        con.execute(f"INSERT INTO trials ({','.join(TRIAL)}) VALUES ({','.join('?' * len(TRIAL))})",
                    tuple(TRIAL.values()))
    if with_screen:
        screen = dict(SCREEN)
        if screen_metrics:
            screen["diagnostics_json"] = json.dumps(screen_metrics)
        con.execute(f"INSERT INTO selection_attempts ({','.join(screen)}) VALUES ({','.join('?' * len(screen))})",
                    tuple(screen.values()))
    con.commit()
    con.close()
    return db


def audit_dir(tmp_path: Path, *, run_id: str = "RUN-HYP095-001") -> Path:
    d = tmp_path / "audits"
    d.mkdir(exist_ok=True)
    (d / f"AUDIT_{run_id}.md").write_text(
        f"# 6-Check Audit: {run_id}\nVerdict: REJECTED\n"
        "- [PASS] 1_target_rebalance_alignment\n- [FAIL] 2_outlier_sensitivity\n"
        "- [PASS] 5_cost_and_latency_survival\n- [FAIL] 6_selection_bias_accounting\n", encoding="utf-8")
    return d


def render_one(tmp_path: Path, db: Path, audits: Path) -> str:
    records = collect(db, audits, tmp_path / "revisions", (HYP,))
    return next(text for path, text in records if path.parent.name == HYP)


def test_trial_record_states_gate_audit_and_no_falsification_claim(tmp_path):
    db = build_registry(tmp_path)
    text = render_one(tmp_path, db, audit_dir(tmp_path))
    assert "NOT QUALIFIED AT STAGE 3" in text
    assert "deflated Sharpe probability 0.6630 < 0.9" in text
    assert "Status: `REJECTED` — failed `2_outlier_sensitivity`, `6_selection_bias_accounting`" in text
    assert "not falsified at Stage 1" in text
    assert "falsified at Stage 3" not in text


def test_screen_only_record_cites_the_attempt_and_runs_no_computation(tmp_path):
    db = build_registry(tmp_path, with_trial=False)
    text = render_one(tmp_path, db, audit_dir(tmp_path))
    assert "REJECTED AT STAGE 1" in text
    assert "`ATT-1`" in text
    assert "No new computation was performed" in text
    assert "requires a **new** preregistered hypothesis" in text


def test_stage1_pass_without_a_trial_is_not_reported_as_falsified(tmp_path):
    """S02-S04 passed Stage 1 and were simply not advanced; that is not a falsification."""
    db = build_registry(tmp_path, with_trial=False, screen_metrics={
        "profit_factor": 1.26, "conditional_edge": 3.4e-04, "trade_edge": 0.5457,
        "time_in_market": 1.0, "bootstrap_p_value": 0.0, "avg_holding_bars": 1612.0})
    text = render_one(tmp_path, db, audit_dir(tmp_path))
    assert "STAGE-1 PASS — NOT ADVANCED TO STAGE 3" in text
    assert "criteria were **met**" in text
    assert "not** met" not in text
    assert "falsified at Stage 1" not in text


def test_screen_only_record_estimates_the_next_gate_not_the_stored_one(tmp_path):
    db = build_registry(tmp_path, with_trial=False)
    text = render_one(tmp_path, db, audit_dir(tmp_path))
    assert "`max(k_dedup, 11.6100) + 0.50 x 1 = 12.1100`" in text
    assert "not the stored value" in text


def test_missing_evidence_aborts_before_writing_anything(tmp_path):
    db = build_registry(tmp_path, with_trial=False, with_screen=False)
    out = tmp_path / "decisions"
    try:
        records = collect(db, audit_dir(tmp_path), tmp_path / "revisions", (HYP,))
    except ValueError as exc:
        assert "neither a trial nor a Stage-1 screen" in str(exc)
        assert not out.exists()
    else:  # pragma: no cover - the fixture must fail closed
        raise AssertionError(f"expected refusal, got {len(records)} records")


def test_missing_audit_for_a_trial_aborts(tmp_path):
    db = build_registry(tmp_path)
    try:
        collect(db, tmp_path / "no-audits", tmp_path / "revisions", (HYP,))
    except ValueError as exc:
        assert "no audit report" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected refusal")


def test_generation_is_byte_identical_on_rerun(tmp_path):
    db = build_registry(tmp_path)
    records = collect(db, audit_dir(tmp_path), tmp_path / "revisions", (HYP,))
    first = {p: t for p, t in records}
    second = {p: t for p, t in collect(db, audit_dir(tmp_path), tmp_path / "revisions", (HYP,))}
    assert first == second
    write_all(records, tmp_path / "out")
    write_all(records, tmp_path / "out")
    assert (tmp_path / "out" / HYP / "DECISION.md").read_text() == first[Path(HYP) / "DECISION.md"]


def test_trial_record_quotes_its_own_selection_state(tmp_path):
    """The contract floor moves after the run; the record must quote what the trial faced."""
    db = build_registry(tmp_path)
    con = sqlite3.connect(db)
    con.execute("UPDATE contract_search_state SET max_keff_gate=20.1, max_noise=1.18 "
                "WHERE contract_id='MULTI_ASSET_1D_V1'")
    con.commit()
    con.close()
    text = render_one(tmp_path, db, audit_dir(tmp_path))
    assert "`k_eff_gate` 11.6100" in text and "noise floor 1.0567" in text
    assert "20.1000" not in text


def test_stage_gate_rules_match_the_runner():
    assert stage3_failures(TRIAL) == ["deflated Sharpe probability 0.6630 < 0.9"]
    assert stage1_failures(json.loads(SCREEN["diagnostics_json"])) == [
        "profit factor 1.03 < 1.15", "trade edge 1.50 bps <= 1.5 x one-way cost (1.50 bps)",
        "bootstrap p 0.31 >= 0.05"]
