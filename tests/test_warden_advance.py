"""Tests for idempotent `warden advance` progression over immutable specs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from tests.conftest import FLAT_MODULE
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
    return fx, runner, spec_path, module_rel


def test_advance_full_progression_and_idempotent_rerun(tiny_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    _fx, runner, spec_path, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-001", module_code=PASS_RULES_MODULE)
    monkeypatch.setattr("warden.cli.check_worktree", lambda *a, **k: True)

    out1 = cli._advance_one_spec(spec_path, runner, check_outcome_memory=False)
    assert out1["action"] in {"STAGE1_REJECTED", "BACKTEST_FINALIZED", "AUDITED"}
    assert spec_path.is_file()  # Immutable spec never moved or deleted

    # Re-running advance must be a pure SKIP_TERMINAL no-op
    out2 = cli._advance_one_spec(spec_path, runner, check_outcome_memory=False)
    assert out2["action"] == "SKIP_TERMINAL"
    assert out2["state"] in {"STAGE1_REJECT", "STAGE3_FINAL"}


def test_advance_never_overwrites_existing_audit(tiny_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, runner, spec_path, module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-002", module_code=FLAT_MODULE)
    monkeypatch.setattr("warden.cli.check_worktree", lambda *a, **k: True)

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

    out = cli._advance_one_spec(spec_path, runner, check_outcome_memory=False)
    assert out["action"] == "SKIP_TERMINAL"
    assert audit_md.read_text(encoding="utf-8") == "SENTINEL_DO_NOT_OVERWRITE\n"


def test_advance_requires_reconciliation_on_pending_receipt(tiny_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, runner, spec_path, _module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-003")
    monkeypatch.setattr("warden.cli.check_worktree", lambda *a, **k: True)

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

    out = cli._advance_one_spec(spec_path, runner, check_outcome_memory=False)
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
    tiny_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx, runner, spec_path, module_rel = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-004")
    monkeypatch.setattr("warden.cli.check_worktree", lambda *a, **k: True)

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
    out = cli._advance_one_spec(spec_path, runner, check_outcome_memory=True)
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
    tiny_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx, runner, spec_path, _ = _prepare_fixture(tiny_repo, hyp_id="HYP-ADV-005")
    monkeypatch.setattr("warden.cli.check_worktree", lambda *a, **k: True)
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
        Namespace(pending=True, dir="experiments/active", skip_outcome_memory=True, json=True),
        runner,
    )
    assert rc == 0
    assert spec_path.is_file()
    assert (fx.repo / "experiments" / "active" / "hyp_legacy.yaml").is_file()
