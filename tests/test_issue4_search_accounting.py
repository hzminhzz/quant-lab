"""Issue #4 tickets 06/07/08: every attempt counted, gate never relaxed, lineage capped."""

from __future__ import annotations

import json

import numpy as np
import pytest

from warden.contracts import HypothesisSpec, load_contract
from warden.registry import (
    HypothesisBudgetExhaustedError,
    ResearchRegistry,
    SelectionHistoryError,
    compute_selection_report,
)
from warden.runner import ExperimentRunner

REPO = __import__("pathlib").Path(".")
CONTRACT = load_contract(REPO / "contracts" / "crypto_perps_1h_v1.yaml")


def _hyp(hyp_id, family="rules_ts", module="", parent=None):
    return HypothesisSpec(hyp_id, CONTRACT.contract_id, "T", family,
                          "Mechanism explanation of twenty chars",
                          "Friction explanation of twenty chars", 8, "Reject net Sharpe below one",
                          parent_hypothesis_id=parent, strategy_module=module)


def test_screens_grids_and_failed_reservations_are_all_counted(tiny_repo) -> None:
    fixture = tiny_repo()
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    runner.run_stage1_screen(fixture.spec)                     # standalone screen -> one attempt
    with fixture.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM selection_attempts").fetchone()[0] == 1
        row = conn.execute("SELECT kind, returns_observed, returns_json FROM selection_attempts").fetchone()
    assert row[0] == "standalone_screen" and row[1] == 1 and row[2] not in ("", "[]")

    spec = fixture.spec.read_text() + "param_grid:\n- {lookback_bars: 3}\n- {lookback_bars: 6}\n"
    fixture.spec.write_text(spec)
    runner.run_spec(fixture.spec)
    with fixture.registry._connect() as conn:
        kinds = [r[0] for r in conn.execute("SELECT kind FROM selection_attempts ORDER BY created_at")]
        assert kinds.count("param_grid") == 2
        raw = runner.registry.validation_attempt_count(fixture.contract.contract_id)
    assert raw == 4  # 1 trial + 1 screen + 2 grid configurations

    # A reservation that dies before any result is still an attempt, with no fake zero returns.
    broken = tiny_repo(module_source="def generate_positions(*a, **k):\n    raise RuntimeError('boom')\n",
                       module_name="broken")
    broken_runner = ExperimentRunner(registry=broken.registry, repo_root=broken.repo)
    with pytest.raises(RuntimeError):
        broken_runner.run_spec(broken.spec)
    with broken.registry._connect() as conn:
        trial = conn.execute("SELECT verdict, returns_json, returns_parquet_path FROM trials").fetchone()
    assert trial[0] == "FAILED" and trial[1] == "[]" and trial[2] == ""


def test_duplicate_and_collinear_trials_never_relax_the_gate() -> None:
    rng = np.random.default_rng(11)
    x = rng.normal(0.0004, 0.01, 500)
    other = rng.normal(0.0001, 0.01, 500)
    base = compute_selection_report(x, [other], 8760)
    assert base["k_eff_dedup"] > 1.5 and base["expected_max_sharpe_noise"] > 0

    # Adding an exact duplicate, a rescaled near-duplicate and a losing collinear copy of an
    # existing trial must never make the same candidate's hurdle easier.
    for added in ([x], [x * 0.2 - 0.002], [0.99 * x * -1.0]):
        extended = compute_selection_report(x, [other, *added], 8760)
        assert extended["k_eff_dedup"] == pytest.approx(base["k_eff_dedup"], abs=1e-9)
        assert extended["k_eff_gate"] >= base["k_eff_gate"]
        assert extended["probability"] <= base["probability"] + 1e-12

    # With the recorded historical floor the same candidate cannot improve at all.
    floored = compute_selection_report(x, [other, x * 0.2 - 0.002], 8760,
                                       prior_max_noise=base["expected_max_sharpe_noise"])
    assert floored["probability"] <= base["probability"] + 1e-12
    assert floored["expected_max_sharpe_noise"] >= base["expected_max_sharpe_noise"]
    binding = compute_selection_report(x, [other], 8760, prior_max_noise=10.0)
    assert binding["noise_floor_applied"] is True and binding["expected_max_sharpe_noise"] == pytest.approx(10.0)
    assert binding["probability"] < base["probability"]

    orthogonal = rng.normal(0, 0.01, (500, 3))
    ranks = [compute_selection_report(orthogonal[:, n], [orthogonal[:, j] for j in range(n)], 8760)
             for n in range(3)]
    assert [r["raw_attempt_count"] for r in ranks] == [1, 2, 3]
    assert ranks[1]["k_eff_dedup"] > ranks[0]["k_eff_dedup"]
    assert ranks[1]["probability"] < ranks[0]["probability"]


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


def test_lineage_cap_and_module_alias_are_enforced(tmp_path) -> None:
    reg = ResearchRegistry(tmp_path / "reg.db")
    reg.register_contract(CONTRACT)
    module = "strategies/candidates/shared.py"
    reg.register_hypothesis(_hyp("HYP-ROOT", module=module))
    assert reg.register_trial_start("HYP-ROOT", "RUN-R1") == 1
    # A renamed ID reusing the exhausted module must declare its parent.
    with pytest.raises(ValueError, match="already registered to HYP-ROOT"):
        reg.register_hypothesis(_hyp("HYP-RENAMED", module=module))
    reg.register_hypothesis(_hyp("HYP-CHILD", module=module, parent="HYP-ROOT"))
    for run in ("RUN-R2", "RUN-R3"):
        reg.register_trial_start("HYP-ROOT", run)
    with pytest.raises(HypothesisBudgetExhaustedError):
        reg.register_trial_start("HYP-ROOT", "RUN-R4")
    for run in ("RUN-C1", "RUN-C2", "RUN-C3"):
        reg.register_trial_start("HYP-CHILD", run)
    reg.register_hypothesis(_hyp("HYP-GRAND", module=module, parent="HYP-CHILD"))
    with pytest.raises(HypothesisBudgetExhaustedError, match="lineage HYP-ROOT exhausted"):
        reg.register_trial_start("HYP-GRAND", "RUN-G1")


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
    assert row[0] == "[]" and row[1] == ""
    diagnostics = json.loads(row[2])
    assert len(diagnostics["stage1_signal"]["vector"]) == 30
    assert not all(v == 0 for v in diagnostics["stage1_signal"]["vector"])
    assert row[3] == 1
