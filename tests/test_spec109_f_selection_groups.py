"""Ticket #113 — unified contract schema and shared DSR search debt (spec #109 lane F).

What this file exists to guard, per the ticket's acceptance criteria:

* a contract cannot be registered without a policy-resolvable
  ``selection_group_id`` -- production and sandbox alike -- and the refusal names
  the ``(asset_class, cadence)`` domain it could not map;
* two distinct contract ids in one group (one of them a compiled sandbox id)
  accumulate **one** persisted trial/search-debt count, proven by writing trials
  through the real registry into a scratch database and recomputing the selection
  report from the recorded streams;
* a legacy v1 contract's existing debt joins its policy group through an additive
  ``contract_governance`` row while its own recorded payload, digest, trial rows
  and return artifacts stay byte-identical;
* #94's ``InitVar`` carrier is an explicit ``policy`` field, additively migrated
  into ``contracts.policy`` / ``contract_governance.group_source`` with no row
  rewritten and a data-only rollback.

Every fixture is synthetic: a scratch SQLite file under ``tmp_path``, a fixture
signed venue policy (the same HMAC-at-rest pattern as ``test_venue_policy``), no
live registry, no market data, no protected holdout, and no manifest generation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import pickle
import sqlite3
from copy import deepcopy
from dataclasses import asdict, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

import warden.registry as registry_module
from warden.contract_source import load_recorded_contract
from warden.contracts import (
    CURRENT_SPEC_VERSION,
    ContractPolicy,
    ContractValidationError,
    EvaluationContract,
    ExperimentResult,
    HypothesisSpec,
    attach_contract_policy,
)
from warden.registry import (
    SCHEMA_VERSION,
    ContractSelectionGroupError,
    ResearchRegistry,
    compute_selection_report,
)
from warden_ops.integrity import MANIFEST_FILE
from warden_ops.venue_policy import (
    SelectionGroupResolutionError,
    load_venue_policy,
    require_selection_group,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = "contracts/policy/venue_policy.yaml"
FIXTURE_KEY = b"fixture-only-venue-policy"
START = datetime(2025, 3, 3, tzinfo=UTC)

#: Real repository contract whose digest was pinned long before #113.
PINNED_LEGACY = ("crypto_perps_1h_v1.yaml", "CRYPTO_PERPS_1H_V1", "f24108d3be8852e1")

SYNTHETIC_POLICY = """\
version: 1
train_start: '2021-01-01T00:00:00Z'
val_start: '2022-01-01T00:00:00Z'
val_end: '2023-01-01T00:00:00Z'
holdout_start: '2023-01-01T01:00:00+01:00'
holdout_end: '2024-01-01T00:00:00Z'
asset_classes:
  fx:
    taker_fee_bps: 0.6
    slippage_bps: 0.7
    funding_bps_per_8h: 0
    allow_leverage: false
    max_gross_leverage: 0.9
    selection_groups:
      intraday: F113_FX_INTRADAY
      daily: F113_FX_DAILY
  crypto_perps:
    taker_fee_bps: 4.0
    slippage_bps: 2.0
    funding_bps_per_8h: 0.01
    allow_leverage: false
    max_gross_leverage: 1.0
    selection_groups:
      intraday: F113_CRYPTO_INTRADAY
      daily: F113_CRYPTO_DAILY
"""


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def venue_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Install a fixture-signed venue policy and return the loaded object."""
    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", FIXTURE_KEY.decode())
    policy = tmp_path / POLICY_REL
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(SYNTHETIC_POLICY, encoding="utf-8")
    manifest = f"{hashlib.sha256(SYNTHETIC_POLICY.encode()).hexdigest()}  {POLICY_REL}\n".encode()
    (tmp_path / MANIFEST_FILE).write_bytes(manifest)
    (tmp_path / f"{MANIFEST_FILE}.sig").write_text(
        hmac.digest(FIXTURE_KEY, manifest, "sha256").hex() + "\n", encoding="ascii")
    return load_venue_policy(tmp_path)


def _contract(
    contract_id: str,
    *,
    asset_class: str = "fx",
    frequency: str = "1h",
    group: str = "",
    inherit: str = "",
    spec_version: int = 1,
) -> EvaluationContract:
    policy = None
    if spec_version >= CURRENT_SPEC_VERSION:
        policy = ContractPolicy(spec_version=CURRENT_SPEC_VERSION, selection_group_id=group,
                                inherits_selection_from=inherit)
    return EvaluationContract(
        contract_id=contract_id,
        asset_class=asset_class,
        frequency=frequency,
        bars_per_year=8760,
        exploratory_data_path=f"data/exploratory/{contract_id}.parquet",
        holdout_data_path=f"data/holdout/{contract_id}.parquet",
        train_start=datetime(2024, 1, 1, tzinfo=UTC),
        val_start=datetime(2025, 1, 1, tzinfo=UTC),
        val_end=datetime(2025, 2, 1, tzinfo=UTC),
        holdout_start=datetime(2025, 2, 1, tzinfo=UTC),
        holdout_end=datetime(2025, 3, 1, tzinfo=UTC),
        n_folds=2,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
        data_snapshot_sha256="0" * 64,
        data_provenance="verified_market",
        policy=policy,
    )


def _hypothesis(hypothesis_id: str, contract_id: str, module: str) -> HypothesisSpec:
    return HypothesisSpec(
        hypothesis_id, contract_id, "F113 selection debt fixture", "rules_ts",
        "Mechanism explanation of twenty chars", "Friction explanation of twenty chars",
        8, "Reject net Sharpe below one", strategy_module=module)


def _result(run_id: str, hypothesis_id: str, contract_id: str, module: str) -> ExperimentResult:
    return ExperimentResult(
        run_id=run_id, hypothesis_id=hypothesis_id, contract_id=contract_id,
        strategy_name=Path(module).stem, strategy_family="rules_ts", config_hash="cfg",
        stage_reached="STAGE_3_STRESS", verdict="CANDIDATE_FOR_QUALIFICATION",
        gross_sharpe=1.0, net_sharpe=1.0, deflated_sharpe_prob=0.99,
        expected_max_sharpe_noise=0.1, total_trials_n=1, effective_trials_keff=1.0,
        mean_ic=None, ic_hac_tstat=None, hit_rate=0.55, profit_factor=1.3,
        max_drawdown_pct=4.0, annualized_turnover=25.0, breakeven_cost_bps=12.0,
        stress_2x_cost_sharpe=0.9, param_neighbor_stability=None, notes="fixture",
        data_snapshot_sha256="0" * 64, strategy_module=module, model_type="rules",
        data_provenance="verified_market")


def _grid(count: int, *, start: datetime = START) -> list[datetime]:
    return [start + timedelta(hours=index) for index in range(count)]


def _returns(count: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0, 0.01, count)


def _registry(tmp_path: Path) -> ResearchRegistry:
    return ResearchRegistry(tmp_path / "registry" / "registry.db")


def _seed_trial(registry: ResearchRegistry, contract: EvaluationContract, *, tag: str,
                seed: int = 5) -> str:
    """Register a hypothesis and write one real trial for ``contract``."""
    module = f"strategies/candidates/f113_{tag}.py"
    registry.register_hypothesis(_hypothesis(f"HYP-{tag.upper()}", contract.contract_id, module))
    run_id = f"RUN-{tag.upper()}"
    stamps = _grid(120)
    registry.record_trial(_result(run_id, f"HYP-{tag.upper()}", contract.contract_id, module),
                          {}, _returns(120, seed), timestamps=stamps)
    return run_id


# --------------------------------------------------- 1. mandatory policy-resolvable group


def test_v2_governance_still_cannot_exist_without_a_selection_group(tmp_path: Path) -> None:
    """What already enforces the mandate (proven, not assumed)."""
    with pytest.raises(ContractValidationError, match="selection_group_id"):
        ContractPolicy(spec_version=CURRENT_SPEC_VERSION)
    with pytest.raises(ContractValidationError, match="selection_group_id"):
        EvaluationContract.from_dict(
            {**asdict(_contract("F113_NO_GROUP")),
             "spec_version": CURRENT_SPEC_VERSION, "evidence_tier": "native_venue"})


def test_policy_mapping_names_the_unmapped_domain(venue_policy) -> None:
    assert require_selection_group(venue_policy, "fx", "1h") == "F113_FX_INTRADAY"
    assert require_selection_group(venue_policy, "fx", "1d") == "F113_FX_DAILY"
    with pytest.raises(SelectionGroupResolutionError) as metals:
        require_selection_group(venue_policy, "metals", "1h")
    assert "'metals'" in str(metals.value) and "'intraday'" in str(metals.value)
    with pytest.raises(SelectionGroupResolutionError) as cadence:
        require_selection_group(venue_policy, "fx", "1w")
    assert "'fx'" in str(cadence.value)


def test_new_contract_registration_fails_closed_without_a_policy(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    sandbox = _contract("SANDBOX_F113_NOPOLICY", group="MY_OWN_GROUP",
                        spec_version=CURRENT_SPEC_VERSION)
    with pytest.raises(ContractSelectionGroupError, match="signed venue policy"):
        registry.register_new_contract(sandbox)
    # A new contract may never be a legacy v1 contract, policy or not.
    with pytest.raises(ContractSelectionGroupError, match="spec_version 2"):
        registry.register_new_contract(_contract("SANDBOX_F113_LEGACY"))
    with registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 0


def test_new_contract_registration_uses_the_policy_and_refuses_a_conflict(
        tmp_path: Path, venue_policy) -> None:
    registry = _registry(tmp_path)
    sandbox = _contract("SANDBOX_F113_FX_1H", group="F113_FX_INTRADAY",
                        spec_version=CURRENT_SPEC_VERSION)
    recorded = registry.register_new_contract(sandbox, venue_policy=venue_policy)
    assert recorded == {"contract_id": "SANDBOX_F113_FX_1H", "spec_hash": sandbox.spec_hash(),
                        "selection_group_id": "F113_FX_INTRADAY",
                        "inherits_selection_from": "", "group_source": "policy", "created": True}

    # A declaration that contradicts the policy is refused, never re-homed.
    conflicting = _contract("SANDBOX_F113_TYPO", group="F113_TYPO_GROUP",
                            spec_version=CURRENT_SPEC_VERSION)
    with pytest.raises(ContractSelectionGroupError, match="authoritative") as conflict:
        registry.register_new_contract(conflicting, venue_policy=venue_policy)
    assert "F113_TYPO_GROUP" in str(conflict.value) and "F113_FX_INTRADAY" in str(conflict.value)

    # An unmapped domain is refused with the domain named, sandbox or production alike.
    unmapped = _contract("SANDBOX_F113_METALS", asset_class="metals",
                         spec_version=CURRENT_SPEC_VERSION, group="F113_METALS")
    with pytest.raises(SelectionGroupResolutionError) as failure:
        registry.register_new_contract(unmapped, venue_policy=venue_policy)
    assert "'metals'" in str(failure.value) and "'intraday'" in str(failure.value)

    with registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 1


def test_repo_root_resolves_the_same_signed_policy(tmp_path: Path, venue_policy) -> None:
    """``repo_root`` is the file-based spelling of the same authority."""
    registry = _registry(tmp_path)
    sandbox = _contract("SANDBOX_F113_ROOT", group="F113_FX_INTRADAY",
                        spec_version=CURRENT_SPEC_VERSION)
    recorded = registry.register_new_contract(sandbox, repo_root=tmp_path)
    assert recorded["selection_group_id"] == "F113_FX_INTRADAY"
    assert recorded["group_source"] == "policy"


# ------------------------------------- 2. shared persisted selection debt (production + sandbox)


def test_two_contract_ids_in_one_group_share_one_persisted_debt_count(
        tmp_path: Path, venue_policy) -> None:
    registry = _registry(tmp_path)
    production = _contract("F113_FX_PROD_1H", group="F113_FX_INTRADAY",
                           spec_version=CURRENT_SPEC_VERSION)
    sandbox = _contract("SANDBOX_F113_FX_SUBSET_1H", group="F113_FX_INTRADAY",
                        spec_version=CURRENT_SPEC_VERSION)
    # A variant id in another group proves the aggregation is scoped, not global.
    other = _contract("F113_FX_DAILY_PROD", frequency="1d", group="F113_FX_DAILY",
                      spec_version=CURRENT_SPEC_VERSION)
    for contract in (production, sandbox, other):
        registry.register_new_contract(contract, venue_policy=venue_policy)

    _seed_trial(registry, production, tag="prod", seed=3)
    _seed_trial(registry, sandbox, tag="sandbox", seed=11)
    _seed_trial(registry, other, tag="daily", seed=17)

    debt = registry.selection_group_debt(production)
    assert debt["selection_group_id"] == "F113_FX_INTRADAY"
    assert debt["scope"] == [production.contract_id, sandbox.contract_id]
    assert debt["per_contract"] == {production.contract_id: 1, sandbox.contract_id: 1}
    assert debt["shared_attempt_count"] == 2  # one persisted count for the group

    # The recomputed DSR report really consumes both persisted streams.
    stamps = _grid(120)
    candidate = _returns(120, seed=23)
    history = registry.selection_history(production.contract_id, len(stamps),
                                        timestamps=stamps, candidate_returns=candidate,
                                        contract=production)
    assert history["alignment"]["selection_group_id"] == "F113_FX_INTRADAY"
    assert set(history["alignment"]["scope"]) == {production.contract_id, sandbox.contract_id}
    assert len(history["streams"]) == 2
    assert history["raw_attempt_count"] == 2
    assert history["unvectorized_attempts"] == 0

    report = compute_selection_report(candidate, history["streams"], production.bars_per_year,
                                      raw_attempt_count=history["raw_attempt_count"])
    # The recomputed hurdle consumes the two *persisted* streams plus the candidate:
    # both ids' debt is in the panel, not just the candidate's own trial.
    assert report["raw_attempt_count"] == 2
    assert report["streams_used"] == 3
    assert report["unvectorized_attempts"] == 0
    assert report["k_eff_effective"] >= 2.0
    assert report["probability"] > 0.0

    # Same group => same hurdle from the other id's perspective; another group is untouched.
    sandbox_report = registry.selection_group_debt(sandbox)
    assert sandbox_report["shared_attempt_count"] == 2
    assert registry.selection_group_debt(other)["shared_attempt_count"] == 1


def test_shared_debt_survives_a_reopened_database(tmp_path: Path, venue_policy) -> None:
    db = tmp_path / "registry" / "registry.db"
    registry = ResearchRegistry(db)
    production = _contract("F113_FX_PROD_1H", group="F113_FX_INTRADAY",
                           spec_version=CURRENT_SPEC_VERSION)
    sandbox = _contract("SANDBOX_F113_FX_SUBSET_1H", group="F113_FX_INTRADAY",
                        spec_version=CURRENT_SPEC_VERSION)
    for contract in (production, sandbox):
        registry.register_new_contract(contract, venue_policy=venue_policy)
    _seed_trial(registry, production, tag="prod", seed=3)
    _seed_trial(registry, sandbox, tag="sandbox", seed=11)

    reopened = ResearchRegistry(db)
    assert reopened.selection_group_debt(sandbox)["shared_attempt_count"] == 2


# --------------------------------- 4. the live CLI registration path obeys the policy (A6)


def _cli_registration_repo(
    tmp_path: Path, name: str, contract: EvaluationContract, *, with_policy: bool
) -> Path:
    """A minimal signed repo the ``register-hypothesis`` CLI path can run in.

    Only the protected inventory plus the contract and hypothesis are present: no market
    data, no lifecycle document, no production registry.
    """
    import yaml

    from warden_ops.integrity import generate_manifest

    repo = tmp_path / name
    for rel in (
        "contracts/datasets",
        "registry",
        ".workspace/hypotheses",
        "experiments/active",
        "strategies/candidates",
        "cache/features",
        "src/warden",
    ):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / "AGENTS.md").write_text("# f113 fixture\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='f113'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version=1\n", encoding="utf-8")
    (repo / "contracts" / f"{contract.contract_id.lower()}.yaml").write_text(
        yaml.safe_dump(contract.to_dict()), encoding="utf-8"
    )
    if with_policy:
        (repo / POLICY_REL).parent.mkdir(parents=True, exist_ok=True)
        (repo / POLICY_REL).write_text(SYNTHETIC_POLICY, encoding="utf-8")
    hypothesis = _hypothesis(f"HYP-{contract.contract_id}", contract.contract_id,
                             "strategies/candidates/f113_cli.py")
    (repo / ".workspace" / "hypotheses" / f"{hypothesis.hypothesis_id}.yaml").write_text(
        yaml.safe_dump({name: getattr(hypothesis, name)
                        for name in hypothesis.__dataclass_fields__}),
        encoding="utf-8",
    )
    # The manifest is fixture-owned: one HMAC over the fixture key, never a production key.
    content = generate_manifest(repo)
    (repo / f"{MANIFEST_FILE}.sig").write_text(
        hmac.digest(FIXTURE_KEY, content.encode(), "sha256").hex() + "\n", encoding="ascii"
    )
    return repo


def _registered_group(repo: Path, contract_id: str) -> tuple[str, str] | None:
    with ResearchRegistry(repo / "registry" / "registry.db")._connect() as conn:
        row = conn.execute(
            "SELECT selection_group_id, group_source FROM contract_governance "
            "WHERE contract_id=?", (contract_id,)
        ).fetchone()
    return (str(row[0]), str(row[1])) if row else None


def test_the_live_registration_path_refuses_a_group_that_contradicts_the_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A trusted policy is authoritative on ``register-hypothesis``, not just in the API."""
    from warden.cli import main as cli_main

    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", FIXTURE_KEY.decode())
    contract = _contract("F113_CLI_TYPO", group="F113_TYPO_GROUP",
                         spec_version=CURRENT_SPEC_VERSION)
    repo = _cli_registration_repo(tmp_path, "cli_typo", contract, with_policy=True)
    monkeypatch.chdir(repo)

    hyp = f".workspace/hypotheses/HYP-{contract.contract_id}.yaml"
    assert cli_main(["register-hypothesis", hyp, "--json"]) == 2
    refused = capsys.readouterr().err
    assert "REFUSED" in refused
    assert "F113_TYPO_GROUP" in refused and "F113_FX_INTRADAY" in refused
    # Nothing was recorded: the invention never became a row and never got its own counter.
    assert _registered_group(repo, contract.contract_id) is None


def test_the_live_registration_path_refuses_an_unmapped_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from warden.cli import main as cli_main

    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", FIXTURE_KEY.decode())
    contract = _contract("F113_CLI_METALS", asset_class="metals", group="F113_METALS",
                         spec_version=CURRENT_SPEC_VERSION)
    repo = _cli_registration_repo(tmp_path, "cli_metals", contract, with_policy=True)
    monkeypatch.chdir(repo)

    hyp = f".workspace/hypotheses/HYP-{contract.contract_id}.yaml"
    assert cli_main(["register-hypothesis", hyp, "--json"]) == 2
    refused = capsys.readouterr().err
    assert "REFUSED" in refused and "'metals'" in refused
    assert _registered_group(repo, contract.contract_id) is None


def test_the_live_registration_path_registers_as_before_without_a_trusted_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No policy installed: today's behaviour, and the row says the group is the contract's."""
    from warden.cli import main as cli_main

    monkeypatch.setenv("WARDEN_HUMAN_SIGNING_KEY", FIXTURE_KEY.decode())
    contract = _contract("F113_CLI_DECLARED", group="F113_DECLARED_GROUP",
                         spec_version=CURRENT_SPEC_VERSION)
    repo = _cli_registration_repo(tmp_path, "cli_no_policy", contract, with_policy=False)
    monkeypatch.chdir(repo)

    hyp = f".workspace/hypotheses/HYP-{contract.contract_id}.yaml"
    assert cli_main(["register-hypothesis", hyp, "--json"]) == 0
    capsys.readouterr()
    assert _registered_group(repo, contract.contract_id) == ("F113_DECLARED_GROUP", "contract")




def test_legacy_contract_debt_joins_the_policy_group_without_moving_its_own_scope(
        tmp_path: Path, venue_policy) -> None:
    registry = _registry(tmp_path)
    legacy = load_recorded_contract(REPO_ROOT, PINNED_LEGACY[1])
    assert legacy.contract_id == PINNED_LEGACY[1]
    assert legacy.spec_hash() == PINNED_LEGACY[2]
    assert legacy.selection_group_id == ""

    recorded = registry.register_contract(legacy, venue_policy=venue_policy)
    assert recorded["selection_group_id"] == "F113_CRYPTO_INTRADAY"
    assert recorded["group_source"] == "policy"
    assert legacy.spec_hash() == PINNED_LEGACY[2]  # payload untouched by mapping
    assert registry.recorded_policy(legacy.contract_id) is None  # v1 carries no block

    legacy_run = _seed_trial(registry, legacy, tag="legacy", seed=29)

    # A legacy contract keeps its historical own-scope and family floor: a policy row
    # must never narrow the hurdle it is scored against.
    assert registry.selection_scope(legacy).members == (legacy.contract_id,)
    floor, _noise, family = registry.prior_search_floor(legacy)
    assert family == "crypto_perps:1h" and floor >= 1.0

    new_contract = _contract("F113_CRYPTO_PROD_1H", asset_class="crypto_perps",
                             group="F113_CRYPTO_INTRADAY", spec_version=CURRENT_SPEC_VERSION)
    registry.register_new_contract(new_contract, venue_policy=venue_policy)
    new_run = _seed_trial(registry, new_contract, tag="new", seed=31)

    debt = registry.selection_group_debt(new_contract)
    assert debt["scope"] == [legacy.contract_id, new_contract.contract_id]
    assert debt["shared_attempt_count"] == 2

    stamps = _grid(120)
    history = registry.selection_history(new_contract.contract_id, len(stamps),
                                        timestamps=stamps,
                                        candidate_returns=_returns(120, seed=37),
                                        contract=new_contract)
    assert history["raw_attempt_count"] == 2
    assert len(history["streams"]) == 2
    with registry._connect() as conn:
        recorded_runs = {row[0] for row in conn.execute(
            "SELECT run_id FROM trials WHERE contract_id IN (?,?)",
            (legacy.contract_id, new_contract.contract_id))}
        source = conn.execute("SELECT group_source FROM contract_governance WHERE contract_id=?",
                              (legacy.contract_id,)).fetchone()[0]
    assert recorded_runs == {legacy_run, new_run}
    assert source == "policy"


def test_mapping_is_additive_and_reports_unmapped_domains(tmp_path: Path, venue_policy) -> None:
    registry = _registry(tmp_path)
    legacy = _contract("F113_LEGACY_FX_1H")           # fx/intraday -> mapped
    metals = _contract("F113_LEGACY_METALS_1H", asset_class="metals", frequency="1h")
    for contract in (legacy, metals):
        registry.register_contract(contract)
    _seed_trial(registry, legacy, tag="legacy", seed=41)

    with registry._connect() as conn:
        before = {
            "contracts": conn.execute("SELECT * FROM contracts ORDER BY contract_id").fetchall(),
            "trials": conn.execute("SELECT * FROM trials ORDER BY run_id").fetchall(),
            "attempts": conn.execute("SELECT * FROM selection_attempts ORDER BY attempt_id").fetchall(),
            "artifacts": {row[0]: hashlib.sha256(Path(row[1]).read_bytes()).hexdigest()
                          for row in conn.execute(
                              "SELECT run_id, returns_parquet_path FROM trials "
                              "WHERE returns_parquet_path != ''")},
        }
    assert before["artifacts"]  # at least one real artifact hash is pinned

    report = registry.map_selection_groups(venue_policy=venue_policy)
    assert {item["contract_id"] for item in report["mapped"]} == {legacy.contract_id}
    assert report["mapped"][0]["group_source"] == "policy"
    assert [item["contract_id"] for item in report["unmapped"]] == [metals.contract_id]
    assert "'metals'" in report["unmapped"][0]["reason"]

    with registry._connect() as conn:
        assert conn.execute("SELECT * FROM contracts ORDER BY contract_id").fetchall() == before["contracts"]
        assert conn.execute("SELECT * FROM trials ORDER BY run_id").fetchall() == before["trials"]
        assert conn.execute("SELECT * FROM selection_attempts ORDER BY attempt_id").fetchall() == before["attempts"]
        assert conn.execute("SELECT COUNT(*) FROM contract_governance").fetchone()[0] == 1
        after_artifacts = {row[0]: hashlib.sha256(Path(row[1]).read_bytes()).hexdigest()
                           for row in conn.execute(
                               "SELECT run_id, returns_parquet_path FROM trials "
                               "WHERE returns_parquet_path != ''")}
    assert after_artifacts == before["artifacts"]

    # Idempotent: a second pass records nothing new and never rewrites the row.
    again = registry.map_selection_groups(venue_policy=venue_policy)
    assert again["mapped"] == [] and legacy.contract_id in again["already_recorded"]
    dry = registry.map_selection_groups(venue_policy=venue_policy, dry_run=True)
    assert dry["mapped"] == []


# ------------------------------------- 4. explicit field, additive migration, byte identity


def test_explicit_policy_field_is_projection_and_copy_safe(tmp_path: Path) -> None:
    contract = _contract("F113_FIELD_V2", group="F113_FIELD_GROUP",
                         spec_version=CURRENT_SPEC_VERSION)
    assert {f.name for f in fields(EvaluationContract)} >= {"policy"}
    assert contract.policy is not None and contract.selection_group_id == "F113_FIELD_GROUP"
    # #94's carrier was dropped by asdict(); the explicit field is not.
    projection = EvaluationContract(**asdict(contract))
    assert projection.governance.spec_version == CURRENT_SPEC_VERSION
    assert projection.selection_group_id == contract.selection_group_id
    assert projection.spec_hash() == contract.spec_hash()
    # ... and it survives every copy channel, including the ``warden verify`` one.
    for copied in (replace(contract, exploratory_data_path="data/exploratory/trunc.parquet"),
                   deepcopy(contract), pickle.loads(pickle.dumps(contract))):
        assert copied.selection_group_id == contract.selection_group_id
        assert copied.governance.allow_leverage is contract.governance.allow_leverage
    # The carrier itself is still never hashed, so the digest is the v2 policy digest.
    assert "policy" not in contract.spec_hash_payload()
    assert contract.spec_hash_payload()["governance"]["selection_group_id"] == "F113_FIELD_GROUP"
    # Detaching the policy is explicit (no stale attribute survives).
    detached = attach_contract_policy(contract, ContractPolicy())
    assert detached.policy is None and detached.governance.spec_version == 1


def test_v1_contract_digest_is_byte_identical_to_the_pinned_value(tmp_path: Path) -> None:
    """A real repository contract, digest pinned before #113."""
    legacy = load_recorded_contract(REPO_ROOT, PINNED_LEGACY[1])
    assert legacy.spec_hash() == PINNED_LEGACY[2]
    assert legacy.policy is None
    assert "policy" not in legacy.spec_hash_payload()
    assert len(legacy.spec_hash_payload()) == len(fields(EvaluationContract)) - 1


def test_registration_persists_the_policy_block_and_keeps_the_digest(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    contract = _contract("F113_PERSIST_V2", group="F113_PERSIST_GROUP",
                         spec_version=CURRENT_SPEC_VERSION)
    registry.register_contract(contract)
    with registry._connect() as conn:
        row = conn.execute("SELECT policy, spec_hash FROM contracts WHERE contract_id=?",
                           (contract.contract_id,)).fetchone()
    assert row[1] == contract.spec_hash()
    persisted = json.loads(row[0])
    assert persisted["selection_group_id"] == "F113_PERSIST_GROUP"
    assert registry.recorded_policy(contract.contract_id) == persisted
    # Re-registering is a no-op, and the persisted block is not rewritten.
    registry.register_contract(contract)
    assert registry.recorded_policy(contract.contract_id) == persisted


def _legacy_shaped_db(path: Path, contract: EvaluationContract) -> dict:
    """A pre-#113 (v2) registry: 22 ``contracts`` columns, no ``group_source``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = []
    for f in fields(EvaluationContract):
        if f.name == "policy":
            continue
        kind = "TEXT" if ("str" in str(f.type) or "datetime" in str(f.type)) else "REAL"
        columns.append(f"{f.name} {kind}")
    row = {f.name: getattr(contract, f.name) for f in fields(EvaluationContract) if f.name != "policy"}
    row = json.loads(json.dumps(row, default=str))
    row["spec_hash"] = contract.spec_hash()
    with sqlite3.connect(path) as conn:
        conn.execute(f"CREATE TABLE contracts ({', '.join(columns)}, spec_hash TEXT NOT NULL, "
                     "PRIMARY KEY (contract_id))")
        conn.execute("CREATE TABLE contract_governance (contract_id TEXT PRIMARY KEY,"
                     "selection_group_id TEXT NOT NULL DEFAULT '',"
                     "inherits_selection_from TEXT NOT NULL DEFAULT '',"
                     "spec_version INTEGER NOT NULL DEFAULT 1, recorded_at REAL NOT NULL)")
        conn.execute("CREATE TABLE trials (run_id TEXT PRIMARY KEY, hypothesis_id TEXT,"
                     "contract_id TEXT, trial_number_in_hyp INTEGER, returns_parquet_path TEXT,"
                     "returns_sha256 TEXT, created_at REAL)")
        conn.execute("INSERT INTO trials (run_id, hypothesis_id, contract_id, trial_number_in_hyp,"
                     "returns_parquet_path, returns_sha256, created_at) "
                     "VALUES ('RUN-PRE-113','HYP-PRE-113','F113_PRE_113',1,'','',1.0)")
        conn.execute(
            f"INSERT INTO contracts ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
            tuple(row.values()))
        conn.execute("PRAGMA user_version=2")
        before = {
            "columns": [r[1] for r in conn.execute("PRAGMA table_info(contracts)")],
            "row": conn.execute("SELECT * FROM contracts").fetchone(),
            "trial_columns": [r[1] for r in conn.execute("PRAGMA table_info(trials)")],
            "trials": [tuple(item) for item in conn.execute("SELECT * FROM trials").fetchall()],
        }
    return before


def test_additive_migration_adds_columns_without_rewriting_rows(tmp_path: Path) -> None:
    contract = _contract("F113_PRE_113", group="F113_PRE_GROUP",
                         spec_version=CURRENT_SPEC_VERSION)
    db = tmp_path / "legacy" / "registry.db"
    before = _legacy_shaped_db(db, contract)
    assert "policy" not in before["columns"]

    registry = ResearchRegistry(db)
    assert registry.schema_version == SCHEMA_VERSION == 3
    with registry._connect() as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 3
        columns = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(contracts)")}
        governance = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(contract_governance)")}
        row = conn.execute("SELECT * FROM contracts").fetchone()
        trials = [tuple(item) for item in conn.execute("SELECT * FROM trials").fetchall()]
    assert columns["policy"] == "TEXT"
    assert governance["group_source"] == "TEXT"
    # The pre-existing row is byte-identical; only the new column is appended (NULL).
    assert tuple(row[:len(before["row"])]) == before["row"]
    assert row[len(before["row"])] is None
    # The trial row keeps every pre-existing value; the migration only appends the
    # columns a v0/v2-era trials table legitimately lacks (additive ``_ensure_columns``).
    assert len(before["trial_columns"]) == 7
    assert [item[:7] for item in trials] == before["trials"]
    # Its recorded digest still reads back exactly as recorded.
    with registry._connect() as conn:
        assert conn.execute("SELECT spec_hash FROM contracts").fetchone()[0] == contract.spec_hash()

    # A second open is a pure no-op (no DDL, no version bump).
    statements: list[str] = []
    real_connect = registry_module.sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    registry_module.sqlite3.connect = traced_connect
    try:
        ResearchRegistry(db)
    finally:
        registry_module.sqlite3.connect = real_connect
    assert not [s for s in statements
                if s.strip().upper().startswith(("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE"))]


def test_migration_backfills_nothing_and_keeps_legacy_scope(tmp_path: Path, venue_policy) -> None:
    """Opening the registry never invents a group; mapping is an explicit, auditable step."""
    registry = _registry(tmp_path)
    legacy = _contract("F113_QUIET_LEGACY")
    registry.register_contract(legacy)
    _seed_trial(registry, legacy, tag="quiet", seed=43)
    assert registry.selection_scope(legacy).members == (legacy.contract_id,)
    with registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM contract_governance").fetchone()[0] == 0
    # Unmapped legacy debt is simply absent from a group until the mapping runs.
    new_contract = _contract("F113_QUIET_NEW", group="F113_FX_INTRADAY",
                             spec_version=CURRENT_SPEC_VERSION)
    registry.register_new_contract(new_contract, venue_policy=venue_policy)
    assert registry.selection_group_debt(new_contract)["shared_attempt_count"] == 0
    registry.map_selection_groups(venue_policy=venue_policy)
    assert registry.selection_group_debt(new_contract)["shared_attempt_count"] == 1
