"""Ticket #90: frozen ExperimentSpec, locked live epoch and execution policy."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from warden.contracts import ENGINE_SIMULATION_EPOCH
from warden.engine_sim import extract_execution_policy
from warden.runner import ExperimentRunner
from warden.schemas import (
    ALLOWED_EXPERIMENT_SPEC_FIELDS,
    ExperimentSpec,
    load_experiment_spec,
)

ACTIVE_SPECS = REPO_ROOT / "experiments" / "active"

FORBIDDEN_KEYS = (
    "simulation_epoch",
    "allow_leverage",
    "allow_short_selling",
    "max_volume_participation",
    "market_impact_coefficient",
    "model_tier",
    "unexpected_key",
)

FULL_SPEC = {
    "contract_id": "TINY_V1",
    "hypothesis_id": "HYP-TINY",
    "strategy_name": "Tiny",
    "strategy_family": "rules_ts",
    "strategy_module": "strategies/flat.py",
    "model_type": "rules",
    "model_class": "RulesModel",
    "parameters": {"lookback_bars": 6},
    "param_grid": [{"lookback_bars": 3}, {"lookback_bars": 9}],
    "run_id": "RUN-SCHEMA-001",
    "research_intent_sha256": "a" * 64,
}


def _spec_with_forbidden(**extra):
    return {**FULL_SPEC, **extra}


def test_experiment_spec_accepts_exactly_the_allowed_fields() -> None:
    assert set(FULL_SPEC) == ALLOWED_EXPERIMENT_SPEC_FIELDS
    spec = ExperimentSpec.model_validate(FULL_SPEC)
    assert spec.contract_id == "TINY_V1"
    assert spec.model_class == "RulesModel"
    assert spec.param_grid == [{"lookback_bars": 3}, {"lookback_bars": 9}]
    # Frozen: an agent cannot mutate policy-relevant state after validation.
    with pytest.raises(ValidationError):
        spec.model_class = "DeepModel"  # type: ignore[misc]


@pytest.mark.parametrize("key", FORBIDDEN_KEYS)
def test_experiment_spec_rejects_unknown_keys_with_validation_error(key: str) -> None:
    with pytest.raises(ValidationError, match=key):
        ExperimentSpec.model_validate(_spec_with_forbidden(**{key: True}))


@pytest.mark.parametrize("payload", [None, [], "spec", 42])
def test_experiment_spec_rejects_non_mapping_documents(payload) -> None:
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(payload)


def test_experiment_spec_requires_contract() -> None:
    with pytest.raises(ValidationError, match="contract_id"):
        ExperimentSpec.model_validate({k: v for k, v in FULL_SPEC.items() if k != "contract_id"})


def test_experiment_spec_allows_verify_only_specs_without_hypothesis() -> None:
    # cmd_verify proves causality from contract + module; screening/backtesting
    # (not the schema) additionally require a registered hypothesis.
    spec = ExperimentSpec.model_validate(
        {"contract_id": "TINY_V1", "strategy_module": "strategies/flat.py"}
    )
    assert spec.hypothesis_id is None


def test_null_param_grid_means_no_grid() -> None:
    spec = ExperimentSpec.model_validate({**FULL_SPEC, "param_grid": None})
    assert spec.param_grid == []


def test_all_active_experiment_yamls_validate_under_frozen_schema() -> None:
    specs = sorted(ACTIVE_SPECS.rglob("*.yaml")) + sorted(ACTIVE_SPECS.rglob("*.yml"))
    assert specs, "no active experiment specs found"
    for path in specs:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(document, dict), path
        assert set(document) <= ALLOWED_EXPERIMENT_SPEC_FIELDS, (path, set(document) - ALLOWED_EXPERIMENT_SPEC_FIELDS)
        load_experiment_spec(path)


def test_empty_yaml_file_fails_closed(tmp_path: Path) -> None:
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_experiment_spec(empty)


def _registry_counts(registry) -> dict[str, int]:
    with registry._connect() as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("trials", "selection_attempts", "screen_receipts", "attempt_receipts")
        }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("simulation_epoch", "bypass"),
        ("simulation_epoch", "legacy_matrix"),
        ("allow_leverage", True),
        ("allow_short_selling", False),
        ("max_volume_participation", 0.9),
        ("market_impact_coefficient", 0.0),
    ],
)
def test_run_spec_rejects_policy_keys_before_any_write(tiny_repo, key: str, value) -> None:
    fixture = tiny_repo()
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    fixture.spec.write_text(fixture.spec.read_text(encoding="utf-8") + yaml.safe_dump({key: value}))
    before = _registry_counts(fixture.registry)
    with pytest.raises(ValidationError, match=key):
        runner.run_spec(fixture.spec)
    assert _registry_counts(fixture.registry) == before


@pytest.mark.parametrize(
    ("key", "value"),
    [("simulation_epoch", "bypass"), ("allow_leverage", True), ("unexpected_key", "x")],
)
def test_run_stage1_screen_rejects_policy_keys_before_any_write(tiny_repo, key: str, value) -> None:
    fixture = tiny_repo()
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    fixture.spec.write_text(fixture.spec.read_text(encoding="utf-8") + yaml.safe_dump({key: value}))
    before = _registry_counts(fixture.registry)
    with pytest.raises(ValidationError, match=key):
        runner.run_stage1_screen(fixture.spec)
    assert _registry_counts(fixture.registry) == before


def test_runner_rejects_spec_without_hypothesis_before_any_write(tiny_repo) -> None:
    fixture = tiny_repo()
    document = yaml.safe_load(fixture.spec.read_text(encoding="utf-8"))
    document.pop("hypothesis_id")
    fixture.spec.write_text(yaml.safe_dump(document), encoding="utf-8")
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    before = _registry_counts(fixture.registry)
    with pytest.raises(ValueError, match="hypothesis_id"):
        runner.run_spec(fixture.spec)
    assert _registry_counts(fixture.registry) == before


def test_live_runner_pins_engine_epoch_and_records_it(pre_rg05_policy, tiny_repo) -> None:
    fixture = tiny_repo(n_bars=24)  # fence-compatible: ends before the fixture holdout_start
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    runner.run_stage1_screen(fixture.spec)
    res = runner.run_spec(fixture.spec)
    assert res.simulation_epoch == ENGINE_SIMULATION_EPOCH
    with fixture.registry._connect() as conn:
        row = conn.execute(
            "SELECT simulation_epoch FROM trials WHERE run_id=?", (res.run_id,)
        ).fetchone()
        assert row is not None and row[0] == ENGINE_SIMULATION_EPOCH
        screen_epochs = {
            r[0]
            for r in conn.execute("SELECT simulation_epoch FROM selection_attempts").fetchall()
        }
    assert screen_epochs == {ENGINE_SIMULATION_EPOCH}
    assert json.loads(res.diagnostics_json)["selection"]["simulation_epoch"] == ENGINE_SIMULATION_EPOCH


def test_execution_policy_is_not_derived_from_agent_spec_or_params() -> None:
    defaults = {
        "allow_leverage": False,
        "allow_short_selling": True,
        "max_volume_participation": None,
        "market_impact_coefficient": None,
    }
    injected_spec = {
        "allow_leverage": True,
        "allow_short_selling": False,
        "max_volume_participation": 0.9,
        "market_impact_coefficient": 0.01,
    }
    injected_params = dict(injected_spec)
    assert extract_execution_policy(injected_spec, injected_params) == defaults

    # Historical replay reads the policy Warden itself recorded on the trial.
    recorded = {
        "execution_policy": {
            "allow_leverage": True,
            "allow_short_selling": False,
            "max_volume_participation": 0.25,
            "market_impact_coefficient": 0.1,
        }
    }
    assert extract_execution_policy({}, {}, json.dumps(recorded)) == recorded["execution_policy"]
