"""Behavior and cost of the canonical research lifecycle on isolated fixtures."""
from __future__ import annotations

import hashlib
import json
import statistics
import time
from contextlib import contextmanager
from pathlib import Path

import pytest


@pytest.mark.parametrize("method", ["read_spec_status", "spec_status"])
@pytest.mark.parametrize("screened", [False, True])
def test_state_projection_cost_and_result(tiny_repo, monkeypatch, method, screened):
    fixture = tiny_repo()
    registry = fixture.registry
    subject = ("HYP-TINY", fixture.contract.contract_id, "strategies/flat.py", {})
    if screened:
        key = registry.reserve_screen(*subject, fixture.sha)
        registry.record_selection_attempt(
            hypothesis_id=subject[0], contract_id=subject[1], strategy_module=subject[2],
            parameters={}, kind="standalone_screen", stage="STAGE_1_SIGNAL",
            outcome="REJECT", strategy_family="rules_ts", screen_key=key,
        )
    connect = registry._connect
    connections = 0

    @contextmanager
    def counted():
        nonlocal connections
        connections += 1
        with connect() as connection:
            yield connection

    monkeypatch.setattr(registry, "_connect", counted)
    operation = getattr(registry, method)
    expected = operation(*subject)
    assert expected["state"] == ("STAGE1_REJECT" if screened else "NEW")
    if method == "read_spec_status":
        assert expected["evidence_verified"] is False
    samples = []
    for _ in range(5):
        started = time.perf_counter()
        for _ in range(100):
            assert operation(*subject) == expected
        samples.append((time.perf_counter() - started) * 10)
    assert connections == 501
    print("LIFECYCLE_BENCHMARK " + json.dumps({
        "method": method, "state": expected["state"], "calls": 501,
        "connections_per_call": connections / 501,
        "median_ms": statistics.median(samples), "samples_ms": samples,
    }, sort_keys=True))

@pytest.mark.parametrize("operation", ["verify", "screen", "backtest"])
def test_native_operation_reference(pre_rg05_policy, tiny_repo, monkeypatch, capsys, operation):
    """Compare native computation and ledger effects with the pinned pre-refactor release."""
    from argparse import Namespace
    from warden import cli
    from warden.runner import ExperimentRunner
    from warden_ops.integrity import generate_manifest

    fixture = tiny_repo()
    generate_manifest(fixture.repo)
    monkeypatch.chdir(fixture.repo)
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    handler = {"verify": cli.cmd_verify, "screen": cli.cmd_screen,
               "backtest": cli.cmd_backtest}[operation]
    rc = handler(Namespace(spec_yaml=str(fixture.spec), json=True), runner)
    captured = capsys.readouterr()
    assert captured.out, captured.err
    payload, _ = json.JSONDecoder().raw_decode(captured.out)
    if operation == "verify":
        with fixture.registry._connect() as connection:
            proof = dict(connection.execute("SELECT * FROM spec_causality").fetchone())
        assert proof["proof_key"] == payload["causal_evidence_key"]
        assert proof["data_sha256"] == fixture.sha
        cache = fixture.repo / "cache/features"
        for suffix, digest in ((".parquet", "artifact_sha256"), (".proof.json", "proof_sha256")):
            artifact = cache / (proof["feature_identity"] + suffix)
            assert hashlib.sha256(artifact.read_bytes()).hexdigest() == proof[digest]
        # Feature identities include the fixture root; verify the binding before normalizing it.
        payload["causal_evidence_key"] = "<artifact-bound-proof>"
    audit = None
    if operation == "backtest":
        audit_rc = cli.cmd_audit_check(Namespace(run_id=payload["run_id"], json=True), runner)
        audit_payload = json.loads(capsys.readouterr().out)
        audit = {"returncode": audit_rc, "payload": {
            key: audit_payload[key] for key in (
                "run_id", "checks", "audit_passed", "historical_checks_passed",
                "verdict", "effective_eligibility",
            )
        }}
    with fixture.registry._connect() as connection:
        tables = ("trials", "attempt_receipts", "screen_receipts", "selection_attempts",
                  "spec_causality", "candidate_admissions", "candidate_operations")
        counts = {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                  for name in tables}
        screen = [dict(row) for row in connection.execute("SELECT * FROM screen_receipts")]
        selection = [dict(row) for row in connection.execute("SELECT * FROM selection_attempts")]
        trials = [dict(row) for row in connection.execute(
            "SELECT run_id,verdict,stage_reached,returns_json,returns_sha256,trial_number_in_hyp FROM trials"
        )]
    actual = _stable_evidence({
        "operation": operation, "returncode": rc, "payload": payload,
        "counts": counts, "screen": screen, "selection": selection, "trials": trials, "audit": audit,
    }, fixture.repo.parent)
    reference = json.loads((Path(__file__).parent / "fixtures/research_lifecycle_d1e9325.json").read_text())
    assert actual == reference["cases"][operation]


def _stable_evidence(value, fixture_root):
    """Remove wall-clock/root-location variation; retain every scientific value and digest."""
    if isinstance(value, dict):
        return {key: _stable_evidence(
                    json.loads(item) if key.endswith("_json") and isinstance(item, str) and item else item,
                    fixture_root,
                )
                for key, item in value.items()
                if key not in {"created_at", "reserved_at", "finalized_at"}}
    if isinstance(value, list):
        return [_stable_evidence(item, fixture_root) for item in value]
    return value.replace(str(fixture_root), "<fixture>") if isinstance(value, str) else value


def test_advance_locks_the_selected_ledger(tiny_repo):
    from tests.test_warden_advance import _prepare_fixture
    from warden.lease import MutationBusy, mutation_lease

    fixture, runner, spec, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-LEASE")
    assert fixture.registry.db_path.parent != fixture.repo / "registry"
    with mutation_lease(fixture.registry.db_path), pytest.raises(MutationBusy):
        runner.advance_spec(spec)


def test_advance_registration_uses_its_repository_lifecycle(tiny_repo, monkeypatch, tmp_path):
    from tests.test_warden_advance import _prepare_fixture

    fixture, runner, spec, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-CLOSED")
    lifecycle = fixture.repo / "contracts/lifecycle/contract_lifecycle.json"
    lifecycle.parent.mkdir(parents=True)
    lifecycle.write_text(json.dumps({"schema_version": 1, "contracts": [{
        "contract_id": fixture.contract.contract_id,
        "status": "DEPRECATED_UNEXECUTABLE",
        "reason": "Closed synthetic fixture", "evidence": "fixture",
    }]}))
    monkeypatch.chdir(tmp_path)
    result = runner.advance_spec(spec)
    assert result["action"] == "REVIEW_REQUIRED"
    assert "Lifecycle gate:" in result["reason"]
    with fixture.registry._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM hypotheses WHERE hypothesis_id='HYP-CLOSED'"
        ).fetchone()[0] == 0


def test_metadata_state_cannot_attest_to_drifted_artifacts(pre_rg05_policy, tiny_repo, monkeypatch):
    from warden.runner import ExperimentRunner

    fixture = tiny_repo()
    result = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo).run_spec(fixture.spec)
    subject = ("HYP-TINY", fixture.contract.contract_id, "strategies/flat.py", {})
    before = fixture.registry.spec_status(*subject)
    assert before["state"] == "STAGE3_FINAL"
    with fixture.registry._connect() as connection:
        artifact = Path(connection.execute(
            "SELECT returns_parquet_path FROM trials WHERE run_id=?", (result.run_id,)
        ).fetchone()[0])

    def forbid_artifact_read(path):
        raise AssertionError(f"metadata projection opened {path}")

    with monkeypatch.context() as context:
        context.setattr(Path, "read_bytes", forbid_artifact_read)
        metadata = fixture.registry.read_spec_status(*subject)
    assert metadata["state"] == "STAGE3_FINAL"
    assert metadata["evidence_verified"] is False
    artifact.write_bytes(b"adversarial fixture corruption")
    assert fixture.registry.read_spec_status(*subject) == metadata
    assert fixture.registry.spec_status(*subject)["state"] == "REQUIRES_RECONCILIATION"
    assert fixture.registry.candidate_budget("HYP-TINY")["used"] == 1
