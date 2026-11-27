"""Tests for idempotent `warden advance` progression over immutable specs."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from tests.conftest import FLAT_MODULE, write_fixture_checksums
from warden import cli
from warden.contracts import HypothesisSpec
from warden.lease import mutation_lease
from warden.runner import ExperimentRunner
from warden_ops.integrity import generate_manifest

PASS_RULES_MODULE = """
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor


def required_symbols(params):
    return None


class _LongOnlyStrategy(Strategy):
    def __init__(self):
        self._exec = TargetWeightExecutor(
            RebalanceConfig(allow_short=False, allow_fractional=True, max_single_weight=0.5, max_gross_leverage=1.0)
        )

    def on_data(self, timestamp, data, context, broker):
        w = 0.4 / max(1, len(data))
        self._exec.execute({a: w for a in data}, data, broker, timestamp=timestamp)


def configure_strategy(contract, params):
    return _LongOnlyStrategy()
"""


def _write_hypothesis_md(
    repo: Path,
    hyp_id: str,
    contract_id: str,
    *,
    economic_mechanism: str = "Persistent drift across crypto assets after short-term volatility compression",
) -> Path:
    hyp_path = repo / ".workspace" / "hypotheses" / f"{hyp_id}.md"
    hyp_path.parent.mkdir(parents=True, exist_ok=True)
    hyp_path.write_text(
        f"""---
hypothesis_id: {hyp_id}
contract_id: {contract_id}
title: Advance test hypothesis {hyp_id}
strategy_family: rules_ts
economic_mechanism: >
  {economic_mechanism}
counterparty_or_friction: >
  Paid by late trend followers after cost-adjusted breakout confirmation
expected_holding_bars: 4
falsification_criteria: >
  Reject if Stage 1 trade edge or Stage 3 stress fails
source_provenance: synthetic_unit_test
llm_model_id: test-agent
---
# {hyp_id}
""",
        encoding="utf-8",
    )
    return hyp_path


def _write_spec_yaml(
    repo: Path,
    name: str,
    *,
    hyp_id: str,
    contract_id: str,
    module_rel: str,
) -> Path:
    spec_path = repo / "experiments" / "active" / name
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        yaml.safe_dump(
            {
                "contract_id": contract_id,
                "hypothesis_id": hyp_id,
                "strategy_name": f"Advance {hyp_id}",
                "strategy_family": "rules_ts",
                "strategy_module": module_rel,
                "model_type": "rules",
                "parameters": {},
                "param_grid": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return spec_path


def _prepare_fixture(tiny_repo, *, hyp_id: str = "HYP-ADV-001", module_code: str = FLAT_MODULE):
    fx = tiny_repo()
    runner = ExperimentRunner(repo_root=fx.repo, registry=fx.registry)
    strat_path = fx.repo / "strategies" / "candidates" / f"{hyp_id.lower()}.py"
    strat_path.parent.mkdir(parents=True, exist_ok=True)
    strat_path.write_text(module_code, encoding="utf-8")
    module_rel = strat_path.relative_to(fx.repo).as_posix()
    _write_hypothesis_md(fx.repo, hyp_id, fx.contract.contract_id)
    spec_path = _write_spec_yaml(
        fx.repo,
        f"{hyp_id.lower()}.yaml",
        hyp_id=hyp_id,
        contract_id=fx.contract.contract_id,
        module_rel=module_rel,
    )
    generate_manifest(fx.repo)
    _write_outcome_index(fx.repo)
    return fx, runner, spec_path, module_rel


def _write_outcome_index(repo: Path, body: dict | None = None) -> Path:
    index_path = repo / "docs" / "research-outcomes" / "branch-result-index-v1.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(body if body is not None else {
            "schema_version": 1,
            "source_refs": [{"pr": 40, "commit": "abc1234"}],
            "outcomes": [],
            "original_unattempted": [],
        }),
        encoding="utf-8",
    )
    return index_path


def test_installed_advance_without_pythonpath(tiny_repo) -> None:
    fx = tiny_repo()
    repo = fx.repo
    spec_path = _write_spec_yaml(
        repo, "tiny.yaml", hyp_id="HYP-TINY", contract_id=fx.contract.contract_id,
        module_rel="strategies/tiny.py",
    )
    (repo / "strategies" / "tiny.py").write_text(FLAT_MODULE, encoding="utf-8")
    # Installed subprocess uses the real RG-05 gate; no in-process policy double.
    from tests.test_rg05_research_intent import approve, review

    fx.spec = spec_path
    runner, _, _ = approve(fx)
    review(runner, fx)
    write_fixture_checksums(
        repo, "contracts/tiny_v1.yaml", f"contracts/datasets/{fx.contract.contract_id}.json",
    )
    (repo / "registry").mkdir()
    with sqlite3.connect(fx.registry.db_path) as src, sqlite3.connect(repo / "registry/registry.db") as dst:
        src.backup(dst)
    _write_outcome_index(repo)

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["WARDEN_NON_INTERACTIVE"] = "1"
    result = subprocess.run(
        [str(Path(sys.executable).with_name("warden")), "advance", str(spec_path), "--json"],
        cwd=repo, env=env, capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout)["value"]["action"] == "STAGE1_REJECTED"


def test_advance_full_progression_and_idempotent_rerun(pre_rg05_policy, tiny_repo) -> None:
    _fx, runner, spec_path, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-001", module_code=PASS_RULES_MODULE)

    out1 = runner.advance_spec(spec_path)
    assert out1["action"] in {"STAGE1_REJECTED", "BACKTEST_FINALIZED", "AUDITED"}
    assert spec_path.is_file()  # Immutable spec never moved or deleted

    # Re-running advance must be a pure SKIP_TERMINAL no-op
    out2 = runner.advance_spec(spec_path)
    assert out2["action"] == "SKIP_TERMINAL"
    assert out2["state"] in {"STAGE1_REJECT", "STAGE3_FINAL"}


def test_advance_never_overwrites_existing_audit(tiny_repo) -> None:
    fx, runner, spec_path, module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-002", module_code=FLAT_MODULE)

    import hashlib
    from dataclasses import replace

    import numpy as np

    from tests.test_attempt_lifecycle import _result
    from warden.contracts import canonical_json

    hyp = HypothesisSpec(
        "HYP-ADV-002",
        fx.contract.contract_id,
        "Candidate",
        "rules_ts",
        "Persistent drift across crypto assets after short-term volatility compression",
        "Paid by late trend followers after cost-adjusted breakout confirmation",
        4,
        "Reject if Stage 1 or Stage 3 fails",
    )
    fx.registry.register_hypothesis(hyp)
    res = replace(
        _result(),
        run_id="RUN-ADV-AUD",
        hypothesis_id="HYP-ADV-002",
        contract_id=fx.contract.contract_id,
        strategy_module=module_rel,
        config_hash=hashlib.sha256(canonical_json({}).encode()).hexdigest()[:12],
        verdict="CANDIDATE_FOR_QUALIFICATION",
        stage_reached="STAGE_3_STRESS",
    )
    fx.registry.record_trial(res, {}, np.array([0.01, -0.002, 0.008]))
    # Mark already audited in SQLite and write sentinel audit file
    fx.registry.record_audit("RUN-ADV-AUD", fx.contract.contract_id, "APPROVED", {"1_target_rebalance_alignment": True}, "ok")
    audit_md = fx.repo / ".workspace" / "hypotheses" / "AUDIT_RUN-ADV-AUD.md"
    audit_md.write_text("SENTINEL_DO_NOT_OVERWRITE\n", encoding="utf-8")

    st = fx.registry.spec_status("HYP-ADV-002", fx.contract.contract_id, module_rel, {})
    assert st["state"] == "STAGE3_FINAL"
    assert st["audit_verdict"] == "APPROVED"

    out = runner.advance_spec(spec_path)
    assert out["action"] == "SKIP_TERMINAL"
    assert audit_md.read_text(encoding="utf-8") == "SENTINEL_DO_NOT_OVERWRITE\n"


def test_advance_requires_reconciliation_on_pending_receipt(tiny_repo) -> None:
    fx, runner, spec_path, _module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-003")

    hyp = HypothesisSpec(
        "HYP-ADV-003",
        fx.contract.contract_id,
        "Interrupted",
        "rules_ts",
        "Persistent drift across crypto assets after short-term volatility compression",
        "Paid by late trend followers after cost-adjusted breakout confirmation",
        4,
        "Reject if Stage 1 or Stage 3 fails",
    )
    fx.registry.register_hypothesis(hyp)
    fx.registry.register_trial_start("HYP-ADV-003", "RUN-ADV-PEND")

    out = runner.advance_spec(spec_path)
    assert out["action"] == "REVIEW_REQUIRED"
    assert out["state"] == "REQUIRES_RECONCILIATION"
    assert out["run_id"] == "RUN-ADV-PEND"
    assert spec_path.is_file()


def test_register_hypothesis_is_idempotent_after_module_binding(tiny_repo) -> None:
    fx = tiny_repo()
    hyp = HypothesisSpec(
        "HYP-IDEMP",
        fx.contract.contract_id,
        "Idempotent binding",
        "rules_ts",
        "Persistent drift across crypto assets after short-term volatility compression",
        "Paid by late trend followers after cost-adjusted breakout confirmation",
        4,
        "Reject if Stage 1 or Stage 3 fails",
    )
    fx.registry.register_hypothesis(hyp)
    fx.registry.record_hypothesis_module("HYP-IDEMP", "strategies/candidates/foo.py")
    # Re-registering original hypothesis without strategy_module is idempotent
    fx.registry.register_hypothesis(hyp)
    # Real mechanism drift still fails closed (M01)
    drifted = HypothesisSpec(
        "HYP-IDEMP",
        fx.contract.contract_id,
        "Changed title",
        "rules_ts",
        "Persistent drift across crypto assets after short-term volatility compression",
        "Paid by late trend followers after cost-adjusted breakout confirmation",
        4,
        "Reject if Stage 1 or Stage 3 fails",
    )
    with pytest.raises(ValueError, match="immutable"):
        fx.registry.register_hypothesis(drifted)


def test_advance_outcome_memory_block_stops_before_verify_and_screen(
    tiny_repo
) -> None:
    fx, runner, spec_path, module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-004")

    index_path = fx.repo / "docs" / "research-outcomes" / "branch-result-index-v1.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_refs": [{"pr": 40, "commit": "abc1234"}],
                "outcomes": [
                    {
                        "hypothesis_id": "HYP-ADV-004",
                        "contract_id": fx.contract.contract_id,
                        "strategy_module": module_rel,
                        "disposition": "STAGE1_REJECT",
                        "source_pr": 40,
                        "evidence_id": "EV-1",
                        "data_sha256": "a" * 64,
                        "auto_retry": "DENY_SAME_CONFIG",
                    }
                ],
                "original_unattempted": [],
            }
        ),
        encoding="utf-8",
    )
    out = runner.advance_spec(spec_path)
    assert out["action"] == "BLOCKED_OUTCOME_MEMORY"
    assert fx.registry.screen_status("HYP-ADV-004", fx.contract.contract_id, module_rel, {})["state"] == "UNSEEN"


def test_advance_cli_busy_returns_75(tiny_repo) -> None:
    fx, _, spec_path, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-BUSY")
    with mutation_lease(fx.repo / "registry" / "registry.db"):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "warden.cli",
                "advance",
                spec_path.relative_to(fx.repo).as_posix(),
                "--json",
            ],
            cwd=fx.repo,
            env={**os.environ, "WARDEN_NON_INTERACTIVE": "1"},
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    assert proc.returncode == 75
    payload = json.loads(proc.stdout.strip())
    assert payload["source"] == "warden.mutation-lease"
    assert payload["value"]["status"] == "BUSY"


def test_advance_pending_skips_legacy_and_blocked_without_moving_files(
    pre_rg05_policy,
    tiny_repo
) -> None:
    fx, runner, spec_path, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-005")
    _write_outcome_index(fx.repo)
    legacy_mod = fx.repo / "strategies" / "candidates" / "legacy_mod.py"
    legacy_mod.write_text("def generate_positions(panel, contract, **params): return {}\n", encoding="utf-8")
    _write_hypothesis_md(fx.repo, "HYP-LEGACY", fx.contract.contract_id)
    _write_spec_yaml(
        fx.repo,
        "hyp_legacy.yaml",
        hyp_id="HYP-LEGACY",
        contract_id=fx.contract.contract_id,
        module_rel="strategies/candidates/legacy_mod.py",
    )

    rc = cli.cmd_advance(
        Namespace(pending=True, dir="experiments/active", json=True),
        runner,
    )
    assert rc == 0
    assert spec_path.is_file()
    assert (fx.repo / "experiments" / "active" / "hyp_legacy.yaml").is_file()


def test_advance_audits_third_trial_unaudited_candidate(tiny_repo) -> None:
    fx, runner, spec_path, module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-3RD")

    import hashlib
    from dataclasses import replace

    import numpy as np

    from tests.test_attempt_lifecycle import _result
    from warden.contracts import canonical_json

    hyp = HypothesisSpec(
        "HYP-ADV-3RD",
        fx.contract.contract_id,
        "Third trial candidate",
        "rules_ts",
        "Persistent drift across crypto assets after short-term volatility compression",
        "Paid by late trend followers after cost-adjusted breakout confirmation",
        4,
        "Reject if Stage 1 or Stage 3 fails",
    )
    fx.registry.register_hypothesis(hyp)
    res = replace(
        _result(),
        run_id="RUN-ADV-3RD",
        hypothesis_id="HYP-ADV-3RD",
        contract_id=fx.contract.contract_id,
        strategy_module=module_rel,
        config_hash=hashlib.sha256(canonical_json({}).encode()).hexdigest()[:12],
        verdict="CANDIDATE_FOR_QUALIFICATION",
        stage_reached="STAGE_3_STRESS",
    )
    fx.registry.record_trial(res, {}, np.array([0.01, 0.02, 0.015]))
    with fx.registry._connect() as conn:
        conn.execute("UPDATE hypotheses SET trials_used = 3 WHERE hypothesis_id = ?", ("HYP-ADV-3RD",))

    out = runner.advance_spec(spec_path)
    assert out["action"] == "AUDITED"
    assert out["audit_verdict"] is not None


def test_advance_audit_error_without_evidence_requires_review(
    tiny_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx, runner, spec_path, module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-AERR")

    import hashlib
    from dataclasses import replace

    import numpy as np

    from tests.test_attempt_lifecycle import _result
    from warden.contracts import canonical_json

    hyp = HypothesisSpec(
        "HYP-ADV-AERR",
        fx.contract.contract_id,
        "Audit failure case",
        "rules_ts",
        "Persistent drift across crypto assets after short-term volatility compression",
        "Paid by late trend followers after cost-adjusted breakout confirmation",
        4,
        "Reject if Stage 1 or Stage 3 fails",
    )
    fx.registry.register_hypothesis(hyp)
    res = replace(
        _result(),
        run_id="RUN-ADV-AERR",
        hypothesis_id="HYP-ADV-AERR",
        contract_id=fx.contract.contract_id,
        strategy_module=module_rel,
        config_hash=hashlib.sha256(canonical_json({}).encode()).hexdigest()[:12],
        verdict="CANDIDATE_FOR_QUALIFICATION",
        stage_reached="STAGE_3_STRESS",
    )
    fx.registry.record_trial(res, {}, np.array([0.01, 0.02, 0.015]))
    def unavailable(_run_id):
        raise ValueError("audit evidence unavailable")

    monkeypatch.setattr(runner, "audit_run", unavailable)

    out = runner.advance_spec(spec_path)
    assert out["action"] == "REVIEW_REQUIRED"
    assert out["audit_verdict"] is None


def test_advance_rejects_mismatched_hypothesis_file(tiny_repo) -> None:
    fx, runner, spec_path, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-SPEC")
    wrong_hyp = _write_hypothesis_md(fx.repo, "HYP-ADV-OTHER", fx.contract.contract_id)

    out = runner.advance_spec(spec_path,
        hypothesis_path=wrong_hyp.relative_to(fx.repo).as_posix(),
    )
    assert out["action"] == "REVIEW_REQUIRED"
    assert "does not match spec" in out["reason"]
    with fx.registry._connect() as conn:
        assert conn.execute("SELECT 1 FROM hypotheses WHERE hypothesis_id=?", ("HYP-ADV-OTHER",)).fetchone() is None


def test_advance_reconcile_screen_marks_review_and_never_screens(
    tiny_repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fx, runner, spec_path, module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-RECON")
    hyp = HypothesisSpec(
        "HYP-ADV-RECON",
        fx.contract.contract_id,
        "Interrupted screen",
        "rules_ts",
        "Persistent drift across crypto assets after short-term volatility compression",
        "Paid by late trend followers after cost-adjusted breakout confirmation",
        4,
        "Reject if Stage 1 or Stage 3 fails",
    )
    fx.registry.register_hypothesis(hyp)
    key = fx.registry.reserve_screen("HYP-ADV-RECON", fx.contract.contract_id, module_rel, {})

    rc = cli.cmd_reconcile_screen(
        Namespace(spec_yaml=str(spec_path), json=True), runner
    )
    payload = json.loads(capsys.readouterr().out.strip())

    assert rc == 0
    assert payload["ok"] is True and payload["source"] == "warden.reconcile-screen"
    assert payload["value"]["screen_key"] == key
    assert payload["value"]["state"] == "INTERRUPTED_REVIEW"
    assert fx.registry.screen_status("HYP-ADV-RECON", fx.contract.contract_id, module_rel, {})["state"] == "INTERRUPTED_REVIEW"

    # Review is CLI-only and idempotent; advance must report review, not replay.
    import sqlite3

    (fx.repo / "registry").mkdir(exist_ok=True)
    with sqlite3.connect(fx.registry.db_path) as src, sqlite3.connect(fx.repo / "registry" / "registry.db") as dst:
        src.backup(dst)
    monkeypatch.chdir(fx.repo)
    capsys.readouterr()
    cli_rc = cli.main(["reconcile-screen", spec_path.relative_to(fx.repo).as_posix(), "--json"])
    cli_payload = json.loads(capsys.readouterr().out.strip())
    assert cli_rc == 0 and cli_payload["source"] == "warden.reconcile-screen"
    assert cli_payload["value"]["state"] == "INTERRUPTED_REVIEW"

    out = runner.advance_spec(spec_path)
    assert out["action"] == "REVIEW_REQUIRED"
    assert out["state"] == "REQUIRES_RECONCILIATION"
    with fx.registry._connect() as conn:
        assert conn.execute("SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-ADV-RECON'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM selection_attempts").fetchone()[0] == 0


def test_advance_outcome_memory_missing_index_requires_review(
    tiny_repo
) -> None:
    fx, runner, spec_path, module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-NOIDX")
    (fx.repo / "docs/research-outcomes/branch-result-index-v1.json").unlink()

    out = runner.advance_spec(spec_path)
    assert out["action"] == "REVIEW_REQUIRED"
    assert "missing" in out["reason"].lower()
    assert fx.registry.screen_status("HYP-ADV-NOIDX", fx.contract.contract_id, module_rel, {})["state"] == "UNSEEN"


def test_advance_outcome_memory_malformed_index_requires_review(
    tiny_repo
) -> None:
    fx, runner, spec_path, module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-BADIDX")
    index_path = _write_outcome_index(fx.repo, {"schema_version": 1, "source_refs": [], "outcomes": [], "original_unattempted": []})

    out = runner.advance_spec(spec_path)
    assert out["action"] == "REVIEW_REQUIRED"
    assert "unreadable or malformed" in out["reason"]
    assert index_path.is_file()
    assert fx.registry.screen_status("HYP-ADV-BADIDX", fx.contract.contract_id, module_rel, {})["state"] == "UNSEEN"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
def test_advance_outcome_memory_unreadable_index_requires_review(
    tiny_repo
) -> None:
    fx, runner, spec_path, module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-PERM")
    index_path = _write_outcome_index(fx.repo)
    index_path.chmod(0o000)
    try:
        out = runner.advance_spec(spec_path)
    finally:
        index_path.chmod(0o600)
    assert out["action"] == "REVIEW_REQUIRED"
    assert "unreadable or malformed" in out["reason"]
    assert fx.registry.screen_status("HYP-ADV-PERM", fx.contract.contract_id, module_rel, {})["state"] == "UNSEEN"
