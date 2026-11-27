"""Hermetic RG-05 admission witnesses against the real runner and scratch ledger."""

import json
import sqlite3

import pytest
import yaml

from warden.runner import ExperimentRunner


def test_unapproved_intent_refused_before_scored_screen(tiny_repo):
    fixture = tiny_repo()
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    with pytest.raises(ValueError, match="research intent"):
        runner.run_stage1_screen(fixture.spec)
    with sqlite3.connect(fixture.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM candidate_admissions").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM selection_attempts").fetchone()[0] == 0


def approve(fixture, *, incremental=False, benchmark_source=None, **plan_overrides):
    from tests.conftest import write_novelty_fixture

    write_novelty_fixture(fixture.repo)
    manifest = fixture.repo / f"contracts/datasets/{fixture.contract.contract_id}.json"
    payload = json.loads(manifest.read_text())
    payload.update(price_basis="ohlcv_funding", dataset={
        "assets": ["AAA", "BBB"], "first_bar": fixture.panel["timestamp"].min().isoformat(),
        "last_bar": fixture.panel["timestamp"].max().isoformat(),
    })
    manifest.write_text(json.dumps(payload))
    spec = yaml.safe_load(fixture.spec.read_text())
    plan = {
        "author": "strategy-author", "thesis": "Inventory pressure predicts continuation", "strategy_module": spec["strategy_module"],
        "data_requirement": {"asset_class": "crypto_perps", "symbols": ["AAA", "BBB"],
                             "frequency": fixture.contract.frequency, "min_start": "2025-01-01T00:00:00Z",
                             "price_basis": "ohlcv_funding", "execution_mode": "NEXT_BAR"},
        "purpose": "incremental" if incremental else "absolute",
        "evaluation_criteria": "Reject below contract gates and positive after-cost edge",
        "allowed_revisions": {"sizing": "Change exposure without changing the thesis"},
    }
    if incremental:
        benchmark = fixture.repo / "experiments/benchmarks/benchmark.yaml"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        module = fixture.repo / "strategies/benchmark.py"
        if benchmark_source is None:
            module.write_bytes((fixture.repo / spec["strategy_module"]).read_bytes())
        else:
            module.write_text(benchmark_source)
        doc = yaml.safe_load(fixture.spec.read_text())
        doc["strategy_module"] = "strategies/benchmark.py"
        benchmark.write_text(yaml.safe_dump(doc))
        plan["benchmark_spec"] = "experiments/benchmarks/benchmark.yaml"
    plan.update(plan_overrides)
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    sha = runner.approve_research_intent(spec["hypothesis_id"], plan, "principal")
    spec = yaml.safe_load(fixture.spec.read_text())
    spec["research_intent_sha256"] = sha
    fixture.spec.write_text(yaml.safe_dump(spec))
    return runner, plan, sha


def review(runner, fixture, verdict="PASS", **kwargs):
    from tests.conftest import novelty_fixture_review

    binding = runner.describe_candidate_intent(fixture.spec)
    document = {"binding_sha256": binding["binding_sha256"], "verdict": verdict,
                "reviewer": "independent-reviewer", "evidence": "Synthetic review fixture",
                "novelty": novelty_fixture_review(binding),
                **kwargs}
    return runner.record_conformance_review(fixture.spec, document, "principal"), binding


def test_approved_intent_and_independent_review_admit_once_then_advance(tiny_repo):
    from tests.test_rg03_candidate_admission import passing_repo

    fixture = passing_repo(tiny_repo)
    runner, _, _ = approve(fixture)
    with pytest.raises(ValueError, match="Independent conformance review required"):
        runner.run_stage1_screen(fixture.spec)
    review_id, binding = review(runner, fixture)
    assert runner.run_stage1_screen(fixture.spec).passed
    result = runner.run_spec(fixture.spec)
    candidate, intent = fixture.registry.require_trial_conformance(result.run_id)
    assert candidate.candidate_id == binding["candidate_id"]
    assert intent["plan"]["purpose"] == "absolute"
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 1
    with sqlite3.connect(fixture.registry.db_path) as conn:
        assert conn.execute("SELECT candidate_id,review_id,binding_sha256 FROM candidate_intent_bindings").fetchall() == [
            (binding["candidate_id"], review_id, binding["binding_sha256"])]
        assert conn.execute("SELECT count(*) FROM candidate_operations").fetchone()[0] == 2


@pytest.mark.parametrize("verdict", ["REFUSED", "JUDGE_UNAVAILABLE"])
def test_review_refusal_or_unavailable_is_not_pass(tiny_repo, verdict):
    fixture = tiny_repo()
    runner, _, _ = approve(fixture)
    review(runner, fixture, verdict)
    with pytest.raises(ValueError, match="refused, unavailable"):
        runner.run_stage1_screen(fixture.spec)
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 0
    with sqlite3.connect(fixture.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM conformance_reviews").fetchone()[0] == 1


def test_self_review_and_changed_plan_refused(tiny_repo):
    fixture = tiny_repo()
    runner, plan, sha = approve(fixture)
    with pytest.raises(ValueError, match="independent of strategy author"):
        review(runner, fixture, reviewer=plan["author"])
    assert runner.approve_research_intent("HYP-TINY", plan, "principal") == sha
    with pytest.raises(ValueError, match="immutable"):
        runner.approve_research_intent("HYP-TINY", {**plan, "evaluation_criteria": "weakened"}, "principal")
    doc = yaml.safe_load(fixture.spec.read_text())
    doc["research_intent_sha256"] = "0" * 64
    fixture.spec.write_text(yaml.safe_dump(doc))
    with pytest.raises(ValueError, match="research intent"):
        runner.run_spec(fixture.spec)


def test_code_and_config_drift_require_new_review_and_revision_budget(tiny_repo):
    fixture = tiny_repo()
    runner, _, _ = approve(fixture)
    _, initial = review(runner, fixture)
    runner.run_stage1_screen(fixture.spec)
    source = fixture.repo / "strategies/flat.py"
    source.write_text(source.read_text().replace("w = 0.1 if", "w = 0.2 if"))
    with pytest.raises(ValueError, match="Independent conformance review required"):
        runner.run_spec(fixture.spec)
    review(runner, fixture)
    with pytest.raises(ValueError, match="Material revision requires"):
        runner.run_spec(fixture.spec)
    review(runner, fixture, revision="sizing", predecessor_candidate_id=initial["candidate_id"])
    runner.run_spec(fixture.spec)
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 2
    doc = yaml.safe_load(fixture.spec.read_text())
    doc["parameters"] = {"new_parameter": 1}
    fixture.spec.write_text(yaml.safe_dump(doc))
    with pytest.raises(ValueError, match="Independent conformance review required"):
        runner.run_spec(fixture.spec)


def test_benchmark_fingerprints_are_frozen(tiny_repo):
    fixture = tiny_repo()
    runner, _, _ = approve(fixture, incremental=True)
    review(runner, fixture)
    benchmark = fixture.repo / "experiments/benchmarks/benchmark.yaml"
    doc = yaml.safe_load(benchmark.read_text())
    doc["parameters"] = {"foo": 1}
    benchmark.write_text(yaml.safe_dump(doc))
    with pytest.raises(ValueError, match="benchmark fingerprint changed"):
        runner.run_stage1_screen(fixture.spec)
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 0


def test_intent_review_and_binding_rows_are_append_only(tiny_repo):
    fixture = tiny_repo()
    runner, _, _ = approve(fixture)
    review(runner, fixture)
    runner.run_stage1_screen(fixture.spec)
    for table, col in (("research_intents", "intent_json"), ("conformance_reviews", "review_json"),
                       ("candidate_intent_bindings", "binding_sha256")):
        for statement in (f"DELETE FROM {table}", f"UPDATE {table} SET {col}='corrupt'"):
            with sqlite3.connect(fixture.registry.db_path) as conn, pytest.raises(sqlite3.IntegrityError, match="Append-only"):
                conn.execute(statement)


@pytest.mark.parametrize("target", ["dataset", "manifest", "contract", "benchmark_code"])
def test_pinned_data_contract_and_benchmark_code_drift_refused(tiny_repo, target):
    fixture = tiny_repo()
    runner, _, _ = approve(fixture, incremental=target == "benchmark_code")
    review(runner, fixture)
    if target == "dataset":
        path = fixture.repo / fixture.contract.exploratory_data_path
        path.write_bytes(path.read_bytes() + b"changed bytes")
    elif target == "manifest":
        path = fixture.repo / "contracts/datasets/TINY_V1.json"
        path.write_text(path.read_text() + "\n")
    elif target == "contract":
        path = fixture.repo / "contracts/tiny_v1.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["slippage_bps"] += 1.0
        path.write_text(yaml.safe_dump(doc))
    else:
        path = fixture.repo / "strategies/benchmark.py"
        path.write_text(path.read_text() + "\n# changed comparator\n")
    with pytest.raises(ValueError, match="dataset|contract|benchmark"):
        runner.run_stage1_screen(fixture.spec)
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 0


def test_reviewed_revisions_still_obey_atomic_candidate_budget(tiny_repo):
    from concurrent.futures import ThreadPoolExecutor

    from warden.registry import HypothesisBudgetExhaustedError

    fixture = tiny_repo()
    runner, _, sha = approve(fixture)
    _, initial = review(runner, fixture)
    runner.run_stage1_screen(fixture.spec)
    base = yaml.safe_load(fixture.spec.read_text())
    variants = []
    for index in range(1, 6):
        path = fixture.repo / f"candidate-{index}.yaml"
        path.write_text(yaml.safe_dump({**base, "parameters": {"candidate_variant": index}}))
        fixture.spec = path
        review(runner, fixture, revision="sizing", predecessor_candidate_id=initial["candidate_id"])
        variants.append(runner.describe_review_candidate(path))

    def reserve(record):
        spec, candidate, plan = record
        try:
            return fixture.registry.reserve_screen(
                candidate.hypothesis_id, candidate.contract_id, candidate.strategy_module,
                spec.parameters, candidate.dataset_sha256, candidate=candidate,
                robustness_plan=plan, intent_sha=sha, repo_root=fixture.repo)
        except HypothesisBudgetExhaustedError:
            return None

    with ThreadPoolExecutor(max_workers=5) as executor:
        accepted = [key for key in executor.map(reserve, variants) if key]
    assert len(accepted) == 2
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 3
    with sqlite3.connect(fixture.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM candidate_intent_bindings").fetchone()[0] == 3


def test_plan_approval_cannot_retroactively_bind_a_legacy_attempt(tiny_repo):
    fixture = tiny_repo()
    fixture.registry.reserve_screen("HYP-TINY", fixture.contract.contract_id,
                                   "strategies/flat.py", {}, fixture.sha)
    with pytest.raises(ValueError, match="after prior attempts"):
        approve(fixture)
    with sqlite3.connect(fixture.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM research_intents").fetchone()[0] == 0
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 1


def test_unbound_low_level_reservation_cannot_bypass_approved_intent(tiny_repo):
    fixture = tiny_repo()
    runner, _, sha = approve(fixture)
    with pytest.raises(ValueError, match="frozen reviewed candidate"):
        fixture.registry.reserve_screen("HYP-TINY", fixture.contract.contract_id,
                                       "strategies/flat.py", {}, fixture.sha)
    with pytest.raises(ValueError, match="frozen reviewed candidate"):
        fixture.registry.register_trial_start("HYP-TINY", "RUN-UNBOUND")
    _, candidate, plan = runner.describe_review_candidate(fixture.spec)
    with pytest.raises(ValueError, match="Independent conformance review required"):
        fixture.registry.reserve_screen(
            candidate.hypothesis_id, candidate.contract_id, candidate.strategy_module,
            {}, candidate.dataset_sha256, candidate=candidate, robustness_plan=plan, intent_sha=sha, repo_root=fixture.repo)
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 0


def test_governed_cli_human_approval_and_review(tiny_repo, monkeypatch, capsys):
    from tests.conftest import (
        novelty_fixture_review,
        write_fixture_checksums,
        write_novelty_fixture,
    )
    from warden.cli import main
    from warden.registry import ResearchRegistry

    fixture = tiny_repo()
    repo = fixture.repo
    manifest = repo / "contracts/datasets/TINY_V1.json"
    payload = json.loads(manifest.read_text())
    payload.update(price_basis="ohlcv_funding", dataset={
        "assets": ["AAA", "BBB"], "first_bar": "2025-01-01T00:00:00Z",
        "last_bar": "2025-01-01T23:00:00Z",
    })
    manifest.write_text(json.dumps(payload))
    plan = {
        "author": "author", "thesis": "Fixture-only causal inventory pressure thesis",
        "strategy_module": "strategies/flat.py",
        "data_requirement": {"asset_class": "crypto_perps", "symbols": ["AAA", "BBB"],
                             "frequency": "1h", "min_start": "2025-01-01T00:00:00Z",
                             "price_basis": "ohlcv_funding", "execution_mode": "NEXT_BAR"},
        "purpose": "absolute", "evaluation_criteria": "Contract-owned gates",
    }
    plan_path = repo / "intent.yaml"
    plan_path.write_text(yaml.safe_dump(plan))
    write_fixture_checksums(repo, "contracts/tiny_v1.yaml", "contracts/datasets/TINY_V1.json")
    (repo / "registry").mkdir()
    db = repo / "registry/registry.db"
    with sqlite3.connect(fixture.registry.db_path) as src, sqlite3.connect(db) as dst:
        src.backup(dst)
    monkeypatch.chdir(repo)
    write_novelty_fixture(repo)
    command = ["approve-research-intent", "HYP-TINY", str(plan_path), "--principal", "principal",
               "--human-confirm", "YES_APPROVE_INTENT", "--json"]
    monkeypatch.setenv("WARDEN_NON_INTERACTIVE", "1")
    assert main(command) == 2
    capsys.readouterr()
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE")
    assert main(command) == 0
    sha = json.loads(capsys.readouterr().out)["research_intent_sha256"]
    doc = yaml.safe_load(fixture.spec.read_text())
    doc["research_intent_sha256"] = sha
    fixture.spec.write_text(yaml.safe_dump(doc))
    assert main(["describe-candidate", str(fixture.spec), "--json"]) == 0
    binding = json.loads(capsys.readouterr().out)
    assert "binding_sha256" in binding
    assert not {"net_sharpe", "profit_factor", "verdict"} & binding.keys()
    assert main(["screen", str(fixture.spec), "--json"]) == 1
    assert "Independent conformance review required" in capsys.readouterr().err
    review_path = repo / "review.yaml"
    review_path.write_text(yaml.safe_dump({
        "binding_sha256": binding["binding_sha256"], "verdict": "PASS",
        "reviewer": "independent", "evidence": "Synthetic CLI test review",
        "novelty": novelty_fixture_review(binding),
    }))
    review_cmd = ["record-conformance", str(fixture.spec), str(review_path), "--principal", "principal",
                  "--human-confirm", "YES_RECORD_REVIEW", "--json"]
    monkeypatch.setenv("WARDEN_NON_INTERACTIVE", "1")
    assert main(review_cmd) == 2
    capsys.readouterr()
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE")
    assert main(review_cmd) == 0
    capsys.readouterr()
    assert main(["screen", str(fixture.spec), "--json"]) in (0, 1)
    assert json.loads(capsys.readouterr().out)["passed"] is False
    assert ResearchRegistry(db).candidate_budget("HYP-TINY")["used"] == 1
    assert main(["backtest", str(fixture.spec), "--json"]) == 1
    captured = capsys.readouterr()
    assert "screen outcome REJECT" in captured.err
    assert "WARDEN_RUN_COMPLETE" not in captured.out
    assert ResearchRegistry(db).candidate_budget("HYP-TINY")["used"] == 1


def test_latest_refusal_and_immutable_approved_hypothesis(tiny_repo):
    fixture = tiny_repo()
    runner, _, _ = approve(fixture)
    with sqlite3.connect(fixture.registry.db_path) as conn, pytest.raises(
        sqlite3.IntegrityError, match="approved hypothesis is immutable"
    ):
        conn.execute("UPDATE hypotheses SET title='changed' WHERE hypothesis_id='HYP-TINY'")
    review(runner, fixture)
    review(runner, fixture, "REFUSED")
    with pytest.raises(ValueError, match="refused, unavailable"):
        runner.run_stage1_screen(fixture.spec)
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 0
    review(runner, fixture, evidence="Independent follow-up addressed refusal")
    runner.run_stage1_screen(fixture.spec)
    with pytest.raises(ValueError, match="cannot be replaced"):
        review(runner, fixture, "REFUSED", evidence="Post-admission change")


def test_v5_migration_preserves_historical_trial_and_receipt(tiny_repo):
    import hashlib
    from dataclasses import replace
    from pathlib import Path

    import numpy as np

    from tests.test_attempt_lifecycle import _result
    from warden.registry import ResearchRegistry

    f = tiny_repo()
    run_id = "RUN-PRE-RG05"
    result = replace(_result(), run_id=run_id, hypothesis_id="HYP-TINY",
                     contract_id=f.contract.contract_id, strategy_module="strategies/flat.py")
    f.registry.record_trial(result, {}, np.array([0.01, -0.02, 0.03]))
    receipt = f.registry.reconcile_attempt(run_id)
    budget = f.registry.candidate_budget("HYP-TINY")
    with sqlite3.connect(f.registry.db_path) as conn:
        artifact = Path(conn.execute(
            "SELECT returns_parquet_path FROM trials WHERE run_id=?", (run_id,)
        ).fetchone()[0])
        before = hashlib.sha256(artifact.read_bytes()).hexdigest()
        tables = ("hypotheses", "trials", "attempt_receipts", "selection_attempts")
        snapshots = {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
        from warden.exposure import TABLES

        for table in reversed(TABLES):
            conn.execute(f"DROP TABLE {table}")
        for name in ("terminal_hypothesis_no_reactivate", "terminal_hypothesis_no_delete"):
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute("DROP TABLE hypothesis_terminal")
        for trigger in ("approved_hypothesis_no_change", "approved_hypothesis_no_delete"):
            conn.execute(f"DROP TRIGGER {trigger}")
        for table in ("candidate_intent_bindings", "conformance_reviews", "research_intents"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("PRAGMA user_version=5")
    migrated = ResearchRegistry(f.registry.db_path)
    with sqlite3.connect(migrated.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        for table, rows in snapshots.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() == rows
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == before
    assert migrated.reconcile_attempt(run_id) == receipt
    assert migrated.candidate_budget("HYP-TINY") == budget
    with pytest.raises(ValueError, match="pre-outcome research intent"):
        migrated.require_trial_conformance(run_id)


def test_legacy_unbound_qualification_refuses_before_holdout_claim(tiny_repo):
    import numpy as np

    from tests.test_holdout_execution_parity import _candidate
    from warden.qualify import qualify_candidate

    f = tiny_repo()
    f.registry.record_trial(_candidate(f), {"parameters": {}}, np.linspace(-0.002, 0.003, 40))
    runner = ExperimentRunner(registry=f.registry, repo_root=f.repo)
    with pytest.raises(ValueError, match="pre-outcome research intent"):
        qualify_candidate("RUN-FIX-QUAL", "YES_UNSEAL_HOLDOUT", runner,
                          holdout_override_path=f.repo / "never-read.parquet")
    with sqlite3.connect(f.registry.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM holdout_claims").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0] == 0
