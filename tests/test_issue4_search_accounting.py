"""Issue #4 tickets 06/07/08/12: every attempt counted, gate never relaxed, three-attempt lineage."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from warden import trust_root
from warden.contracts import ExperimentResult, HypothesisSpec, canonical_json, load_contract
from warden.registry import (
    HypothesisBudgetExhaustedError,
    ResearchRegistry,
    SelectionHistoryError,
    compute_selection_report,
)
from warden.runner import ExperimentRunner

REPO = Path(".")
CONTRACT = load_contract(REPO / "contracts" / "crypto_perps_1h_v1.yaml")


def _hyp(hyp_id, family="rules_ts", module="", parent=None, contract_id=CONTRACT.contract_id):
    return HypothesisSpec(hyp_id, contract_id, "T", family,
                          "Mechanism explanation of twenty chars",
                          "Friction explanation of twenty chars", 8, "Reject net Sharpe below one",
                          parent_hypothesis_id=parent, strategy_module=module)


def _grid_spec(fixture, grids: str) -> None:
    fixture.spec.write_text(fixture.spec.read_text() + grids)


def test_candidate_and_grids_are_registered_before_the_gate(tiny_repo) -> None:
    fixture = tiny_repo(module_source="""
import numpy as np


def generate_positions(panel_df, contract, **kwargs):
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    pos = np.zeros((len(ts), len(assets)))
    step = int(kwargs.get("lookback_bars", 4))
    pos[::step, 0] = 0.1
    pos[1::max(step, 2), 1] = -0.05
    return {"positions": pos}
""")
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    _grid_spec(fixture, "param_grid:\n- {lookback_bars: 3}\n- {lookback_bars: 6}\n")
    res = runner.run_spec(fixture.spec)

    with fixture.registry._connect() as conn:
        attempts = [dict(r) for r in conn.execute(
            "SELECT kind,outcome,returns_observed,created_at FROM selection_attempts ORDER BY created_at")]
        trial = dict(conn.execute("SELECT total_trials_n,effective_trials_keff,created_at,diagnostics_json "
                                 "FROM trials WHERE run_id=?", (res.run_id,)).fetchone())
    assert [a["kind"] for a in attempts] == ["standalone_backtest", "param_grid", "param_grid"]
    assert all(a["returns_observed"] == 1 for a in attempts)
    # The gate snapshot happened after every same-run grid was durable ...
    assert trial["created_at"] > max(a["created_at"] for a in attempts)
    # ... and the recorded raw N includes the candidate and both grid configurations.
    assert trial["total_trials_n"] == 3
    assert trial["effective_trials_keff"] >= 1.999
    diagnosis = json.loads(trial["diagnostics_json"])["selection"]
    assert diagnosis["raw_attempt_count"] == 3 and diagnosis["streams_used"] == 3
    assert diagnosis["unvectorized_attempts"] == 0
    assert "0.50" not in diagnosis["correction_policy"]


def test_failed_grid_is_recorded_and_still_counts(tiny_repo) -> None:
    fixture = tiny_repo(module_source="""
import numpy as np


def generate_positions(panel_df, contract, **kwargs):
    if kwargs.get("lookback_bars") == 99:
        raise RuntimeError("grid boom")
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    pos = np.zeros((len(ts), len(assets)))
    pos[::4] = 0.1
    return {"positions": pos}
""")
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    _grid_spec(fixture, "param_grid:\n- {lookback_bars: 3}\n- {lookback_bars: 99}\n")
    with pytest.raises(RuntimeError, match="grid boom"):
        runner.run_spec(fixture.spec)
    with fixture.registry._connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT kind,outcome,returns_observed FROM selection_attempts ORDER BY created_at")]
        failed = dict(conn.execute("SELECT total_trials_n FROM trials").fetchone())
        raw = runner.registry.validation_attempt_count(fixture.contract.contract_id)
    assert [r["outcome"] for r in rows] == ["PENDING", "GRID", "GRID_ERROR"]
    assert rows[-1]["returns_observed"] == 0  # recorded as unobserved, never as fabricated zeros
    assert failed["total_trials_n"] == 3 == raw


def test_fourth_validation_attempt_is_blocked_for_the_whole_lineage(tmp_path) -> None:
    reg = ResearchRegistry(tmp_path / "reg.db")
    reg.register_contract(CONTRACT)
    module = "strategies/candidates/shared.py"
    reg.register_hypothesis(_hyp("HYP-ROOT", module=module))
    for run in ("RUN-R1", "RUN-R2", "RUN-R3"):
        reg.register_trial_start("HYP-ROOT", run)
    with pytest.raises(HypothesisBudgetExhaustedError):
        reg.register_trial_start("HYP-ROOT", "RUN-R4")

    # A child cannot replenish the shared lineage: three attempts total, not three each.
    reg.register_hypothesis(_hyp("HYP-CHILD", module=module, parent="HYP-ROOT"))
    with pytest.raises(HypothesisBudgetExhaustedError, match="lineage HYP-ROOT exhausted"):
        reg.register_trial_start("HYP-CHILD", "RUN-C1")
    with pytest.raises(ValueError, match="already registered to HYP-ROOT"):
        reg.register_hypothesis(_hyp("HYP-RENAMED", module=module))

    # A new contract with the equivalent module cannot silently start a fresh budget.
    v2 = load_contract(REPO / "contracts" / "crypto_perps_1h_v1.yaml")
    from dataclasses import replace
    v2 = replace(v2, contract_id="CRYPTO_PERPS_1H_VX")
    reg.register_contract(v2)
    reg.register_hypothesis(_hyp("HYP-XCONTRACT", module=module, parent="HYP-ROOT", contract_id=v2.contract_id))
    with pytest.raises(HypothesisBudgetExhaustedError, match="lineage"):
        reg.register_trial_start("HYP-XCONTRACT", "RUN-X1")


def test_validation_attempts_consume_the_same_three_attempt_budget(tiny_repo) -> None:
    fixture = tiny_repo()
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    runner.run_stage1_screen(fixture.spec)  # attempt 1: standalone screen
    _grid_spec(fixture, "param_grid:\n- {lookback_bars: 3}\n")
    runner.run_spec(fixture.spec)  # attempt 2: baseline, attempt 3: the first grid
    with pytest.raises(HypothesisBudgetExhaustedError):
        runner.run_spec(fixture.spec)
    assert runner.registry.validation_attempt_count(fixture.contract.contract_id) == 3


def test_duplicate_and_collinear_trials_never_relax_the_gate() -> None:
    rng = np.random.default_rng(11)
    x = rng.normal(0.0004, 0.01, 500)
    other = rng.normal(0.0001, 0.01, 500)
    base = compute_selection_report(x, [other], 8760)
    assert base["k_eff_dedup"] > 1.5 and base["expected_max_sharpe_noise"] > 0

    # Exact duplicates, affine copies and near-collinear copies of a losing attempt must never
    # make the same candidate's hurdle easier (raw N still rises).
    for added in ([x], [x * 0.2 - 0.002], [0.99 * x + rng.normal(0, 0.001, 500)], [-0.8 * x]):
        extended = compute_selection_report(x, [other, *added], 8760)
        assert extended["k_eff_dedup"] == pytest.approx(base["k_eff_dedup"], abs=1e-12)
        assert extended["k_eff_gate"] >= base["k_eff_gate"]
        assert extended["probability"] <= base["probability"] + 1e-12

    # A distinct losing attempt raises the hurdle and the raw count.
    losing = rng.normal(-0.003, 0.02, 500)
    tougher = compute_selection_report(x, [other, losing], 8760)
    assert tougher["raw_attempt_count"] == 3
    assert tougher["probability"] < base["probability"]

    # With the persisted immutable floor the same candidate cannot improve at all.
    floored = compute_selection_report(x, [other, x * 0.2 - 0.002], 8760,
                                       prior_max_noise=base["expected_max_sharpe_noise"])
    assert floored["probability"] <= base["probability"] + 1e-12
    assert floored["expected_max_sharpe_noise"] >= base["expected_max_sharpe_noise"]
    binding = compute_selection_report(x, [other], 8760, prior_max_noise=10.0)
    assert binding["noise_floor_applied"] is True and binding["expected_max_sharpe_noise"] == pytest.approx(10.0)
    assert binding["probability"] < base["probability"]

    # A persisted gate floor makes the applied rank non-decreasing across calls.
    orth = rng.normal(0, 0.01, (500, 3))
    reports = []
    for n in range(3):
        previous = reports[-1] if reports else None
        reports.append(compute_selection_report(
            orth[:, 0], [orth[:, j] for j in range(1, n + 1)], 8760,
            prior_max_keff=1.0 if previous is None else previous["k_eff_gate"],
            prior_max_noise=0.0 if previous is None else previous["expected_max_sharpe_noise"]))
    assert [r["raw_attempt_count"] for r in reports] == [1, 2, 3]
    assert reports[1]["k_eff_gate"] > reports[0]["k_eff_gate"]
    assert reports[1]["probability"] <= reports[0]["probability"]
    assert reports[2]["k_eff_gate"] >= reports[1]["k_eff_gate"]
    assert reports[2]["probability"] <= reports[1]["probability"]


def test_unvectorised_attempts_keep_the_raw_count_and_fail_closed() -> None:
    rng = np.random.default_rng(5)
    x = rng.normal(0.0003, 0.01, 400)
    other = rng.normal(0.0001, 0.01, 400)
    report = compute_selection_report(x, [other], 8760, raw_attempt_count=4)
    assert report["raw_attempt_count"] == 4 and report["unvectorized_attempts"] == 2
    assert "no comparable return stream" in report["non_promotable_reason"]
    # No invented numeric penalty: the gate is the reference effective rank, not a magic constant.
    clean = compute_selection_report(x, [other], 8760)
    assert report["k_eff_gate"] == pytest.approx(clean["k_eff_gate"])
    assert "non_promotable_reason" not in clean or clean["non_promotable_reason"] == ""


def test_unvectorised_attempts_count_without_fake_streams(tiny_repo) -> None:
    fixture = tiny_repo()
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    res = runner.run_spec(fixture.spec)
    fixture.registry.record_selection_attempt(hypothesis_id="HYP-TINY", contract_id=fixture.contract.contract_id,
                                              kind="failed_reservation", stage="DATA", outcome="ERROR",
                                              diagnostics={"error": "missing dataset"})
    with fixture.registry._connect() as conn:
        observed_len = len(json.loads(conn.execute(
            "SELECT returns_json FROM trials WHERE run_id=?", (res.run_id,)).fetchone()[0]))
    history = fixture.registry.selection_history(fixture.contract.contract_id, observed_len)
    assert len(history["streams"]) == 1 and history["raw_attempt_count"] == 2
    assert history["unvectorized_attempts"] == 1
    with pytest.raises(SelectionHistoryError, match="refusing to pad"):
        fixture.registry.selection_history(fixture.contract.contract_id, observed_len + 1)
    with pytest.raises(ValueError, match="finite and temporally aligned"):
        compute_selection_report(np.zeros(50), [np.zeros(49)], 8760)
    # A malformed vector is rejected, never silently reclassified as unobserved.
    with pytest.raises(ValueError, match="non-empty finite 1-D"):
        fixture.registry.record_selection_attempt(hypothesis_id="HYP-TINY",
                                                  contract_id=fixture.contract.contract_id, kind="grid",
                                                  stage="S", outcome="GRID", returns=np.zeros((2, 2)))


def _signed_scope(reg, monkeypatch, *, root="HYP-ROOT", hypothesis="HYP-NEW", contract_id=None,
                  module="strategies/candidates/shared.py"):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(trust_root, "MANIFEST_PUBLIC_KEY_HEX",
                        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex())
    payload = {"root_hypothesis_id": root, "hypothesis_id": hypothesis,
               "contract_id": contract_id or CONTRACT.contract_id,
               "strategy_module": module, "rationale": "New economic mechanism, reviewed by the operator.",
               "approver": "human-operator", "evidence_sha256": "a" * 64}
    return reg.record_scope_authorization(**payload,
                                          signature=key.sign(canonical_json(payload).encode()).hex())


def test_new_scope_requires_a_signed_human_authorization(tmp_path, monkeypatch) -> None:
    reg = ResearchRegistry(tmp_path / "reg.db")
    reg.register_contract(CONTRACT)
    module = "strategies/candidates/shared.py"
    reg.register_hypothesis(_hyp("HYP-ROOT", module=module))
    for run in ("RUN-R1", "RUN-R2", "RUN-R3"):
        reg.register_trial_start("HYP-ROOT", run)
    reg.register_hypothesis(_hyp("HYP-NEW", module=module, parent="HYP-ROOT"))

    # Without a signed authorization the shared lineage stays exhausted: fail closed.
    with pytest.raises(HypothesisBudgetExhaustedError):
        reg.register_trial_start("HYP-NEW", "RUN-N1")
    assert reg.scope_authorized("HYP-NEW") is False

    # A forged/unsigned authorization is refused before any budget change.
    with pytest.raises(PermissionError, match="signature"):
        reg.record_scope_authorization(
            root_hypothesis_id="HYP-ROOT", hypothesis_id="HYP-NEW", contract_id=CONTRACT.contract_id,
            strategy_module=module, rationale="A sufficiently long operator rationale for a new scope.",
            approver="human-operator", evidence_sha256="b" * 64, signature="00" * 64)
    with pytest.raises(HypothesisBudgetExhaustedError):
        reg.register_trial_start("HYP-NEW", "RUN-N1")

    _signed_scope(reg, monkeypatch)
    assert reg.scope_authorized("HYP-NEW") is True
    assert reg.register_trial_start("HYP-NEW", "RUN-N1") == 1
    for run in ("RUN-N2", "RUN-N3"):
        reg.register_trial_start("HYP-NEW", run)
    with pytest.raises(HypothesisBudgetExhaustedError):
        reg.register_trial_start("HYP-NEW", "RUN-N4")


def test_scope_authorization_is_append_only(tmp_path, monkeypatch) -> None:
    import sqlite3
    reg = ResearchRegistry(tmp_path / "reg.db")
    reg.register_contract(CONTRACT)
    reg.register_hypothesis(_hyp("HYP-ROOT", module="strategies/candidates/shared.py"))
    _signed_scope(reg, monkeypatch)
    with reg._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="Append-only"):
        conn.execute("UPDATE scope_authorizations SET approver='agent'")


def test_new_contract_version_inherits_the_historical_floor(tmp_path) -> None:
    from dataclasses import replace
    reg = ResearchRegistry(tmp_path / "reg.db")
    v1 = CONTRACT
    v2 = replace(v1, contract_id="CRYPTO_PERPS_1H_V2")
    for contract in (v1, v2):
        reg.register_contract(contract)
    reg.update_search_floor(v1.contract_id, 3.5, 0.02)
    floor, noise, family = reg.prior_search_floor(v2)
    assert floor == pytest.approx(3.5) and noise == pytest.approx(0.02)
    assert family == "crypto_perps:1h"
    with reg._connect() as conn, pytest.raises(Exception, match="may not be relaxed"):
        conn.execute("UPDATE contract_search_state SET max_keff_gate=1.0 WHERE contract_id=?", (v1.contract_id,))


def test_stage1_rejection_stores_the_observed_signal(tiny_repo) -> None:
    fixture = tiny_repo(module_source="""
import numpy as np


def generate_positions(panel_df, contract, **kwargs):
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    return {"positions": np.zeros((len(ts), len(assets))), "ic_series": np.linspace(-0.03, 0.01, 30)}
""")
    fixture.registry.register_hypothesis(HypothesisSpec(
        "HYP-ML", fixture.contract.contract_id, "T", "cross_sectional_ml",
        "Mechanism explanation of twenty chars", "Friction explanation of twenty chars", 8, "Criteria"))
    spec = fixture.spec.read_text().replace("HYP-TINY", "HYP-ML").replace("rules_ts", "cross_sectional_ml")
    spec = spec.replace("model_type: rules", "model_type: ridge")
    fixture.spec.write_text(spec)
    res = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo).run_spec(fixture.spec)
    assert res.verdict == "REJECTED_EARLY" and res.stage_reached == "STAGE_1_SIGNAL"
    with fixture.registry._connect() as conn:
        row = conn.execute("SELECT returns_json, returns_parquet_path, diagnostics_json, total_trials_n "
                           "FROM trials WHERE run_id=?", (res.run_id,)).fetchone()
    observed = json.loads(row[0])
    assert len(observed) > 0 and row[1] != ""  # the realized signal stream, not a placeholder
    diagnostics = json.loads(row[2])
    assert len(diagnostics["stage1_signal"]["vector"]) == 30
    assert not all(v == 0 for v in diagnostics["stage1_signal"]["vector"])
    assert row[3] == 1


def test_failed_trial_records_the_real_raw_count(tiny_repo) -> None:
    fixture = tiny_repo(module_source="def generate_positions(*a, **k):\n    raise RuntimeError('boom')\n",
                        module_name="broken")
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    fixture.registry.record_selection_attempt(hypothesis_id="HYP-TINY",
                                              contract_id=fixture.contract.contract_id,
                                              kind="standalone_screen", stage="STAGE_1_SIGNAL",
                                              outcome="REJECT", returns=np.linspace(-0.01, 0.01, 40))
    with pytest.raises(RuntimeError, match="boom"):
        runner.run_spec(fixture.spec)
    with fixture.registry._connect() as conn:
        trial = conn.execute("SELECT total_trials_n, returns_json FROM trials").fetchone()
    assert trial[0] == 2 and trial[1] == "[]"  # prior screen + this reservation
    assert ExperimentResult  # imported for the type contract of recorded trials
