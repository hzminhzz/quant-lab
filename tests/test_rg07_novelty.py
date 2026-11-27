"""RG-07: the public scored runner must refuse absent prior-outcome review."""
import pytest

from tests.test_rg05_research_intent import approve


def test_conformance_without_novelty_is_not_admission_authority(tiny_repo):
    f = tiny_repo()
    runner, _, _ = approve(f)
    binding = runner.describe_candidate_intent(f.spec)
    with pytest.raises(ValueError, match="novelty"):
        runner.record_conformance_review(f.spec, {
            "binding_sha256": binding["binding_sha256"], "verdict": "PASS",
            "reviewer": "independent", "evidence": "Implementation only",
        }, "principal")
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 0


def test_changed_lessons_after_review_fail_closed_at_reservation(tiny_repo):
    from tests.test_rg05_research_intent import review
    from warden.novelty import LESSONS

    f = tiny_repo()
    runner, _, _ = approve(f)
    review(runner, f)
    path = f.repo / LESSONS
    path.write_text(path.read_text() + "\n## New negative research lesson\n")
    with pytest.raises(ValueError, match="novelty memory changed"):
        runner.run_stage1_screen(f.spec)
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 0
    review(runner, f)
    runner.run_stage1_screen(f.spec)
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1


def test_missing_or_malformed_sources_are_not_empty_memory(tiny_repo):
    from warden.novelty import LESSONS
    from warden_ops.research_outcome_memory import INDEX

    f = tiny_repo()
    runner, _, _ = approve(f)
    (f.repo / LESSONS).unlink()
    with pytest.raises(ValueError, match="memory unavailable"):
        runner.describe_candidate_intent(f.spec)
    (f.repo / LESSONS).write_text("# Fixture lesson")
    (f.repo / INDEX).write_text("{}")
    with pytest.raises(ValueError, match="memory unavailable"):
        runner.describe_candidate_intent(f.spec)
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 0


def _related_fixture(f, *, hid, parent=None, code_change=False):
    from pathlib import Path

    import yaml

    from warden.contracts import HypothesisSpec

    base = yaml.safe_load(f.spec.read_text())
    module = f"strategies/{hid.lower()}.py"
    source = (f.repo / "strategies/flat.py").read_text()
    if code_change:
        source = source.replace("w = 0.1 if", "w = 0.2 if")
    (f.repo / module).write_text(source)
    f.registry.register_hypothesis(HypothesisSpec(
        hid, f.contract.contract_id, "Related fixture", "rules_ts",
        "Mechanism explanation of twenty chars", "Friction explanation of twenty chars",
        8, "Reject net Sharpe below one", parent_hypothesis_id=parent,
        strategy_module=module))
    f.spec = f.repo / Path(hid + ".yaml")
    base.update(hypothesis_id=hid, strategy_module=module)
    base.pop("research_intent_sha256", None)
    f.spec.write_text(yaml.safe_dump(base))
    return approve(f)


def test_renamed_byte_identical_candidate_refused_across_ids(tiny_repo):
    from tests.test_rg05_research_intent import review

    f = tiny_repo()
    runner, _, _ = approve(f)
    _, binding = review(runner, f)
    runner.run_stage1_screen(f.spec)
    runner, _, _ = _related_fixture(f, hid="HYP-ALIAS", parent="HYP-TINY")
    with pytest.raises(ValueError, match=binding["candidate_id"]):
        runner.describe_candidate_intent(f.spec)
    assert f.registry.candidate_budget("HYP-ALIAS")["lineage_used"] == 1


def test_economic_relationship_needs_explicit_disposition_and_shared_lineage(tiny_repo):
    from tests.conftest import novelty_fixture_review
    from tests.test_rg05_research_intent import review

    f = tiny_repo()
    runner, _, _ = approve(f)
    review(runner, f)
    runner.run_stage1_screen(f.spec)
    runner, _, _ = _related_fixture(f, hid="HYP-NO-PARENT", code_change=True)
    binding = runner.describe_candidate_intent(f.spec)
    assert "HYP-TINY" in binding["prior_memory"]["required_relationships"]
    novelty = novelty_fixture_review(binding)
    novelty["relationships"]["HYP-TINY"]["relation"] = "RELATED"
    with pytest.raises(ValueError, match="registered shared lineage"):
        review(runner, f, novelty=novelty)
    novelty["relationships"]["HYP-TINY"]["relation"] = "UNRESOLVED"
    with pytest.raises(ValueError, match="Unresolved novelty"):
        review(runner, f, novelty=novelty)
    runner, _, _ = _related_fixture(f, hid="HYP-CHILD", parent="HYP-TINY", code_change=True)
    binding = runner.describe_candidate_intent(f.spec)
    novelty = novelty_fixture_review(binding)
    novelty["relationships"] = {}
    with pytest.raises(ValueError, match="Unresolved novelty"):
        review(runner, f, novelty=novelty)
    review(runner, f)
    runner.run_stage1_screen(f.spec)
    assert f.registry.candidate_budget("HYP-CHILD")["lineage_used"] == 2


def test_off_branch_exact_outcome_remains_locked_in_direct_runner(tiny_repo):
    import json

    from tests.test_rg05_research_intent import review
    from warden_ops.research_outcome_memory import INDEX

    f = tiny_repo()
    runner, _, _ = approve(f)
    review(runner, f)
    index = f.repo / INDEX
    doc = json.loads(index.read_text())
    doc["outcomes"].append({
        "hypothesis_id": "HYP-TINY", "contract_id": f.contract.contract_id,
        "strategy_module": "strategies/candidates/historical.py", "disposition": "STAGE1_REJECT",
        "source_pr": 40, "evidence_id": "ATT-BRANCH", "data_sha256": f.sha,
        "auto_retry": "DENY_SAME_CONFIG",
    })
    index.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="Historical branch outcome locked"):
        runner.run_spec(f.spec)
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 0


def test_principal_early_stop_is_terminal_without_consuming_budget(tiny_repo, monkeypatch, capsys):
    import json
    import sqlite3

    from tests.conftest import write_fixture_checksums
    from tests.test_rg05_research_intent import review
    from warden.cli import main
    from warden.registry import ResearchRegistry

    f = tiny_repo()
    runner, _, _ = approve(f)
    review(runner, f)
    repo = f.repo
    write_fixture_checksums(repo, "contracts/tiny_v1.yaml", "contracts/datasets/TINY_V1.json")
    (repo / "registry").mkdir()
    db = repo / "registry/registry.db"
    with sqlite3.connect(f.registry.db_path) as src, sqlite3.connect(db) as dst:
        src.backup(dst)
    monkeypatch.chdir(repo)
    cmd = ["close-hypothesis", "HYP-TINY", "--status", "EARLY_STOPPED",
           "--reason", "Principal decides not to spend the remaining research allowance",
           "--principal", "principal", "--human-confirm", "YES_CLOSE_HYPOTHESIS", "--json"]
    monkeypatch.setenv("WARDEN_NON_INTERACTIVE", "1")
    assert main(cmd) == 2
    assert ResearchRegistry(db).terminal_status("HYP-TINY") is None
    capsys.readouterr()
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE")
    assert main(cmd) == 0
    closed = json.loads(capsys.readouterr().out)
    assert closed["status"] == "EARLY_STOPPED"
    assert closed["evidence"]["budget"]["used"] == 0
    assert main(["advance", str(f.spec), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["value"]["action"] == "SKIP_TERMINAL"
    assert main(["screen", str(f.spec), "--json"]) != 0
    assert main(["backtest", str(f.spec), "--json"]) != 0
    assert ResearchRegistry(db).candidate_budget("HYP-TINY")["used"] == 0
    with sqlite3.connect(db) as conn, pytest.raises(sqlite3.IntegrityError, match="terminal hypothesis"):
        conn.execute("UPDATE hypotheses SET status='ACTIVE' WHERE hypothesis_id='HYP-TINY'")
    with sqlite3.connect(db) as conn, pytest.raises(sqlite3.IntegrityError, match="Append-only"):
        conn.execute("DELETE FROM hypothesis_terminal")


def test_rejected_and_inconclusive_closures_keep_outcomes_and_unspent_budget(tiny_repo):
    from tests.test_rg05_research_intent import review

    for status in ("REJECTED", "INCONCLUSIVE"):
        f = tiny_repo()
        runner, _, _ = approve(f)
        review(runner, f)
        assert not runner.run_stage1_screen(f.spec).passed
        before = f.registry.candidate_budget("HYP-TINY")
        closure = f.registry.close_hypothesis(
            "HYP-TINY", status, "Principal disposition of recorded negative fixture evidence", "principal")
        assert closure["status"] == status
        assert closure["evidence"]["screens"][0]["outcome"] == "REJECT"
        assert f.registry.candidate_budget("HYP-TINY") == before
        assert f.registry.terminal_status("HYP-TINY") == closure
        from warden_ops.research_outcome_memory import INDEX, inventory

        assert inventory(f.registry.db_path, f.repo / INDEX)["terminal_hypotheses"] == [closure]
        assert f.registry.read_spec_status(
            "HYP-TINY", f.contract.contract_id, "strategies/flat.py", {})["state"] == "HYPOTHESIS_TERMINAL"
        with pytest.raises(ValueError):
            runner.run_spec(f.spec)


def test_exhausted_is_terminal_after_three_negative_frozen_candidates(tiny_repo):
    import yaml

    from tests.test_rg05_research_intent import review

    f = tiny_repo()
    runner, _, _ = approve(f)
    base = yaml.safe_load(f.spec.read_text())
    prior = None
    for i in range(3):
        f.spec.write_text(yaml.safe_dump({**base, "parameters": {"variant": i}}))
        if prior:
            _, binding = review(runner, f, revision="sizing", predecessor_candidate_id=prior)
        else:
            _, binding = review(runner, f)
        assert not runner.run_stage1_screen(f.spec).passed
        prior = binding["candidate_id"]
    closure = f.registry.terminal_status("HYP-TINY")
    assert closure["status"] == "EXHAUSTED"
    assert closure["attributed_to"] == "warden"
    assert len(closure["evidence"]["screens"]) == 3
    assert closure["evidence"]["budget"]["used"] == 3


def test_pending_operation_must_be_reconciled_before_terminal_disposition(tiny_repo):
    from tests.test_rg05_research_intent import review

    f = tiny_repo()
    runner, _, sha = approve(f)
    review(runner, f)
    spec, candidate, plan = runner.describe_review_candidate(f.spec)
    f.registry.reserve_screen(
        candidate.hypothesis_id, candidate.contract_id, candidate.strategy_module,
        spec.parameters, candidate.dataset_sha256, candidate=candidate, robustness_plan=plan,
        intent_sha=sha, repo_root=f.repo)
    with pytest.raises(ValueError, match="Resolve reserved"):
        f.registry.close_hypothesis("HYP-TINY", "INCONCLUSIVE", "Interrupted writer needs disposition", "principal")
    f.registry.reconcile_screen(
        candidate.hypothesis_id, candidate.contract_id, candidate.strategy_module,
        spec.parameters, confirm_interrupted=True)
    closed = f.registry.close_hypothesis(
        "HYP-TINY", "INCONCLUSIVE", "Interrupted writer never produced scientific evidence", "principal")
    assert closed["evidence"]["screens"][0]["state"] == "INTERRUPTED_REVIEW"
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1


def test_related_off_branch_evidence_needs_canonical_reconciliation(tiny_repo):
    import json

    from tests.test_rg05_research_intent import review
    from warden.contracts import HypothesisSpec
    from warden_ops.research_outcome_memory import INDEX

    f = tiny_repo()
    f.registry.register_hypothesis(HypothesisSpec(
        "HYP-PR", f.contract.contract_id, "Earlier branch idea", "rules_ts",
        "Mechanism explanation of twenty chars", "Friction explanation of twenty chars",
        8, "Reject net Sharpe below one", strategy_module="strategies/candidates/old.py"))
    runner, _, _ = _related_fixture(f, hid="HYP-CHILD", parent="HYP-PR", code_change=True)
    index = f.repo / INDEX
    doc = json.loads(index.read_text())
    doc["outcomes"].append({
        "hypothesis_id": "HYP-PR", "contract_id": f.contract.contract_id,
        "strategy_module": "strategies/candidates/old.py", "disposition": "STAGE1_REJECT",
        "source_pr": 40, "evidence_id": "ATT-UNRECONCILED", "data_sha256": f.sha,
        "auto_retry": "DENY_SAME_CONFIG",
    })
    index.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="requires canonical reconciliation"):
        review(runner, f)
    assert f.registry.candidate_budget("HYP-CHILD")["used"] == 0


def test_new_branch_outcome_cannot_be_ignored_on_screen_to_full(tiny_repo):
    import json

    from tests.test_rg05_research_intent import review
    from warden_ops.research_outcome_memory import INDEX

    f = tiny_repo()
    runner, _, _ = approve(f)
    review(runner, f)
    runner.run_stage1_screen(f.spec)
    index = f.repo / INDEX
    doc = json.loads(index.read_text())
    doc["outcomes"].append({
        "hypothesis_id": "HYP-TINY", "contract_id": f.contract.contract_id,
        "strategy_module": "strategies/candidates/historical.py", "disposition": "STAGE3_REVISE",
        "source_pr": 40, "evidence_id": "RUN-BRANCH-FULL", "data_sha256": f.sha,
        "auto_retry": "DENY_SAME_CONFIG",
    })
    index.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="New related branch outcome"):
        runner.run_spec(f.spec)
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1


def test_v6_review_migration_preserves_rows_without_fabricating_novelty(tiny_repo):
    import sqlite3

    from warden import research_intent
    from warden.registry import ResearchRegistry
    from warden.runner import ExperimentRunner

    f = tiny_repo()
    runner, _, sha = approve(f)
    _, candidate, plan = runner.describe_review_candidate(f.spec)
    binding = runner.describe_candidate_intent(f.spec)
    # Recreate only the pre-RG07 approval record in an isolated v6 database.
    with f.registry._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        research_intent.record_review(conn, candidate, plan, sha, research_intent.Review(
            binding_sha256=binding["binding_sha256"], verdict="PASS", reviewer="independent",
            evidence="Prior RG05-only synthetic approval"), "principal")
    with sqlite3.connect(f.registry.db_path) as conn:
        before = conn.execute("SELECT * FROM conformance_reviews").fetchall()
        from warden.exposure import TABLES

        for table in reversed(TABLES):
            conn.execute(f"DROP TABLE {table}")
        for name in ("terminal_hypothesis_no_reactivate", "terminal_hypothesis_no_delete"):
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute("DROP TABLE hypothesis_terminal")
        conn.execute("PRAGMA user_version=6")
    migrated = ResearchRegistry(f.registry.db_path)
    with sqlite3.connect(f.registry.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert conn.execute("SELECT * FROM conformance_reviews").fetchall() == before
        assert conn.execute("SELECT count(*) FROM hypothesis_terminal").fetchone()[0] == 0
    runner = ExperimentRunner(registry=migrated, repo_root=f.repo)
    with pytest.raises(ValueError, match="Missing pre-outcome novelty review"):
        runner.run_stage1_screen(f.spec)
    assert migrated.candidate_budget("HYP-TINY")["used"] == 0
