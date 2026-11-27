"""Real synthetic Warden screens/trials through the governed runner seam."""

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
import yaml

from warden.candidate_admission import CandidateIdentity, freeze_candidate
from warden.codeid import code_closure
from warden.contracts import ENGINE_SIMULATION_EPOCH, HypothesisSpec
from warden.provenance import resolve_dataset
from warden.registry import (
    HypothesisBudgetExhaustedError,
    ResearchRegistry,
    ScreenAlreadyAttemptedError,
)
from warden.runner import ExperimentRunner
from warden.schemas import load_experiment_spec


def variant(fixture, value, *, filename=None):
    payload = yaml.safe_load(fixture.spec.read_text())
    payload["parameters"] = {"candidate_variant": value}
    path = fixture.repo / (filename or f"spec-{value}.yaml")
    path.write_text(yaml.safe_dump(payload))
    return path


def count(registry, table):
    with sqlite3.connect(registry.db_path) as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_fourth_selectable_screen_refused_before_outcome(pre_rg05_policy, tiny_repo):
    fixture = tiny_repo()
    module = fixture.repo / "strategies/flat.py"
    source = module.read_text().replace("def __init__(self):", "def __init__(self, weight):")
    source = source.replace("self._bar = 0", "self._bar = 0\n        self._weight = weight")
    source = source.replace("w = 0.1 if", "w = self._weight if")
    source = source.replace("return _FlatStrategy()", "return _FlatStrategy(0.05 + params['candidate_variant'] * 0.04)")
    module.write_text(source)
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    for i in range(3):
        runner.run_stage1_screen(variant(fixture, i))
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 3
    assert count(fixture.registry, "screen_receipts") == 3
    assert count(fixture.registry, "selection_attempts") == 3
    with sqlite3.connect(fixture.registry.db_path) as conn:
        tapes = conn.execute("SELECT returns_json FROM selection_attempts").fetchall()
        assert len({row[0] for row in tapes}) == 3
    for i in range(3, 8):
        with pytest.raises(HypothesisBudgetExhaustedError):
            runner.run_stage1_screen(variant(fixture, i))
    assert count(fixture.registry, "screen_receipts") == 3
    assert count(fixture.registry, "selection_attempts") == 3



def test_screen_and_full_trial_share_identity_at_cap(pre_rg05_policy, tiny_repo):
    fixture = tiny_repo()
    reg = fixture.registry
    runner = ExperimentRunner(registry=reg, repo_root=fixture.repo)
    for i in range(3):
        runner.run_stage1_screen(variant(fixture, i))
    spec = variant(fixture, 0, filename="renamed-spec.yaml")
    result = runner.run_spec(spec)
    assert reg.candidate_budget("HYP-TINY")["used"] == 3
    assert count(reg, "candidate_admissions") == 3
    assert count(reg, "candidate_operations") == 4
    assert reg.reconcile_attempt(result.run_id)["state"] == "FINALIZED"
    with pytest.raises(ScreenAlreadyAttemptedError, match="already admitted"):
        runner.run_spec(spec, override_run_id="RUN-REPLAY-OTHER-ID")
    with pytest.raises(ScreenAlreadyAttemptedError):
        runner.run_stage1_screen(spec)
    with pytest.raises(HypothesisBudgetExhaustedError):
        runner.run_spec(variant(fixture, 99))
    reopened = ResearchRegistry(reg.db_path)
    assert reopened.candidate_budget("HYP-TINY")["used"] == 3
    assert count(reopened, "screen_receipts") == 3


def bound_identity(fixture, path, contract=None):
    spec = load_experiment_spec(path)
    sha, _ = code_closure(fixture.repo, spec.strategy_module)
    contract = contract or fixture.contract
    return freeze_candidate(
        fixture.repo, spec, contract, resolve_dataset(contract, fixture.repo),
        spec.parameters, sha, ENGINE_SIMULATION_EPOCH, legacy_replay=False,
    )


def reserve(reg, identity: CandidateIdentity):
    return reg.reserve_screen(
        identity.hypothesis_id, identity.contract_id, identity.strategy_module,
        yaml.safe_load(identity.parameters_json), identity.dataset_sha256, candidate=identity,
    )


def test_concurrent_candidate_admission_is_atomic(pre_rg05_policy, tiny_repo):
    fixture = tiny_repo()
    identities = [bound_identity(fixture, variant(fixture, i)) for i in range(8)]

    def attempt(candidate):
        try:
            return reserve(fixture.registry, candidate)
        except HypothesisBudgetExhaustedError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, identities))
    assert sum(result is not None for result in results) == 3
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 3
    assert count(fixture.registry, "screen_receipts") == 3
    assert count(fixture.registry, "selection_attempts") == 0


def register_related(reg, contract, hypothesis_id, module, parent=None):
    reg.register_contract(contract)
    reg.register_hypothesis(HypothesisSpec(
        hypothesis_id, contract.contract_id, "Synthetic related hypothesis", "rules_ts",
        "Observed inventory pressure could cause short term continuation",
        "Transaction costs may erase the effect of inventory imbalances", 8,
        "Reject the effect if not robust to costs", strategy_module=module,
        parent_hypothesis_id=parent,
    ))


def test_code_alias_contract_and_cadence_changes_share_lineage_budget(pre_rg05_policy, tiny_repo):
    fixture = tiny_repo()
    reg = fixture.registry
    runner = ExperimentRunner(registry=reg, repo_root=fixture.repo)
    for i in range(3):
        runner.run_stage1_screen(variant(fixture, i))
    identity = bound_identity(fixture, variant(fixture, 10))
    second_contract = replace(
        fixture.contract, contract_id="TINY_OTHER", frequency="30m", bars_per_year=17520,
    )
    alias_path = fixture.repo / "strategies/renamed.py"
    alias_path.write_bytes((fixture.repo / "strategies/flat.py").read_bytes())
    register_related(reg, second_contract, "HYP-ALIAS", "strategies/renamed.py")
    path = fixture.repo / "alias-spec.yaml"
    payload = yaml.safe_load(fixture.spec.read_text())
    payload.update(hypothesis_id="HYP-ALIAS", contract_id=second_contract.contract_id,
                   strategy_module="strategies/renamed.py")
    for i in range(3):
        payload["parameters"] = {"candidate_variant": i}
        path.write_text(yaml.safe_dump(payload))
        alias = bound_identity(fixture, path, second_contract)
        assert alias.code_content_sha256 == identity.code_content_sha256
        assert alias.strategy_code_sha256 != identity.strategy_code_sha256
        reserve(reg, alias)
    child_path = fixture.repo / "strategies/child.py"
    child_path.write_text(alias_path.read_text() + "\n# new implementation\n")
    register_related(reg, fixture.contract, "HYP-CHILD", "strategies/child.py", parent="HYP-ALIAS")
    payload.update(hypothesis_id="HYP-CHILD", contract_id=fixture.contract.contract_id,
                   strategy_module="strategies/child.py")
    path.write_text(yaml.safe_dump(payload))
    child = bound_identity(fixture, path)
    assert child.code_content_sha256 != identity.code_content_sha256
    with pytest.raises(HypothesisBudgetExhaustedError, match="lineage.*exhausted"):
        reserve(reg, child)
    assert reg.candidate_budget("HYP-TINY")["lineage_used"] == 6
    assert reg.candidate_budget("HYP-CHILD")["lineage_used"] == 6


def test_interrupted_screen_cannot_advance_or_replay(pre_rg05_policy, tiny_repo):
    fixture = tiny_repo()
    reg = fixture.registry
    spec = variant(fixture, 1)
    identity = bound_identity(fixture, spec)
    reserve(reg, identity)
    runner = ExperimentRunner(registry=reg, repo_root=fixture.repo)
    with pytest.raises(ValueError, match="screen is unresolved"):
        runner.run_spec(spec)
    with pytest.raises(ScreenAlreadyAttemptedError):
        runner.run_stage1_screen(spec)
    assert reg.candidate_budget("HYP-TINY")["used"] == 1
    assert count(reg, "attempt_receipts") == 0


def test_v3_migration_keeps_old_rows_artifact_and_conservative_debt(pre_rg05_policy, tiny_repo):
    fixture = tiny_repo()
    reg = fixture.registry
    runner = ExperimentRunner(registry=reg, repo_root=fixture.repo)
    result = runner.run_spec(variant(fixture, 0))
    reg.reserve_screen("HYP-TINY", fixture.contract.contract_id, "strategies/flat.py",
                       {"candidate_variant": 1}, fixture.sha)
    reg.reserve_screen("HYP-TINY", fixture.contract.contract_id, "strategies/flat.py",
                       {"candidate_variant": 2}, fixture.sha)
    tables = ("contracts", "hypotheses", "trials", "selection_attempts", "screen_receipts",
              "attempt_receipts", "contract_search_state", "contract_governance", "holdout_claims")
    with sqlite3.connect(reg.db_path) as conn:
        artifact = fixture.registry.db_path.parent / "returns" / fixture.contract.contract_id / f"{result.run_id}.parquet"
        artifact_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
        for name in ("approved_hypothesis_no_change", "approved_hypothesis_no_delete"):
            conn.execute(f"DROP TRIGGER {name}")
        for name in ("candidate_intent_bindings", "conformance_reviews", "research_intents"):
            conn.execute(f"DROP TABLE {name}")
        conn.execute("DROP TABLE robustness_outcomes")
        conn.execute("DROP TABLE robustness_receipts")
        conn.execute("DROP TABLE robustness_plans")
        conn.execute("DROP TABLE candidate_operations")
        conn.execute("DROP TABLE candidate_admissions")
        conn.execute("PRAGMA user_version=3")
        before = {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}
    migrated = ResearchRegistry(reg.db_path)
    with sqlite3.connect(reg.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables} == before
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == artifact_sha
    budget = migrated.candidate_budget("HYP-TINY")
    assert (budget["legacy_full"], budget["legacy_screens"], budget["used"]) == (1, 2, 3)
    with pytest.raises(HypothesisBudgetExhaustedError):
        ExperimentRunner(registry=migrated, repo_root=fixture.repo).run_stage1_screen(variant(fixture, 3))



def test_material_code_revision_is_distinct_but_does_not_reset_budget(pre_rg05_policy, tiny_repo):
    fixture = tiny_repo()
    reg = fixture.registry
    runner = ExperimentRunner(registry=reg, repo_root=fixture.repo)
    spec = variant(fixture, 1)
    runner.run_stage1_screen(spec)
    module = fixture.repo / "strategies/flat.py"
    module.write_text(module.read_text().replace("w = 0.1 if", "w = 0.2 if"))
    runner.run_stage1_screen(spec)
    assert reg.candidate_budget("HYP-TINY")["used"] == 2
    # The legacy raw-spec status cannot identify which code version is intended.
    assert reg.screen_status("HYP-TINY", fixture.contract.contract_id, "strategies/flat.py",
                             {"candidate_variant": 1})["state"] == "EVIDENCE_DRIFT"
    runner.run_spec(spec)
    assert reg.candidate_budget("HYP-TINY")["used"] == 2
    with pytest.raises(ScreenAlreadyAttemptedError):
        runner.run_spec(spec, override_run_id="RUN-DUPLICATE-REVISION")


def test_missing_legacy_binding_requires_review_not_replay(pre_rg05_policy, tiny_repo):
    fixture = tiny_repo()
    reg = fixture.registry
    reg.reserve_screen("HYP-TINY", fixture.contract.contract_id, "strategies/flat.py",
                       {"candidate_variant": 1}, fixture.sha)
    with pytest.raises(ScreenAlreadyAttemptedError, match="Legacy candidate"):
        ExperimentRunner(registry=reg, repo_root=fixture.repo).run_spec(variant(fixture, 1))
    assert reg.candidate_budget("HYP-TINY")["used"] == 1


def test_candidate_identity_and_operation_links_are_append_only(pre_rg05_policy, tiny_repo):
    fixture = tiny_repo()
    reg = fixture.registry
    reserve(reg, bound_identity(fixture, variant(fixture, 1)))
    for statement in ("DELETE FROM candidate_admissions", "DELETE FROM candidate_operations",
                      "UPDATE candidate_admissions SET identity_json='{}'",
                      "UPDATE candidate_operations SET operation_id='erased'"):
        with sqlite3.connect(reg.db_path) as conn, pytest.raises(sqlite3.IntegrityError, match="Append-only"):
            conn.execute(statement)
    assert reg.candidate_budget("HYP-TINY")["used"] == 1
