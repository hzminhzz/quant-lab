"""Ticket #94 — versioned ``spec_hash()``, evidence-tier promotion cap, contract policy.

Two invariants this file exists to guard:

* every one of the 36 legacy contract YAMLs keeps its exact pre-#94 digest, and
  registering them against a registry is idempotent;
* a ``research_proxy`` / ``unverified_roll`` holdout pass is recorded as
  ``PROXY_HOLDOUT_PASS`` with ``promoted=False`` and never reaches
  ``hypotheses.status = 'PROMOTED'``, while ``Engine`` takes leverage only from
  the contract.

All fixtures are synthetic and every database is a scratch SQLite file under
``tmp_path``; nothing here reads the live registry or a protected holdout.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml
from ml4t.backtest import Strategy
from pydantic import ValidationError

from warden.codeid import code_closure
from warden.contract_source import all_contract_paths, load_recorded_contract
from warden.contracts import (
    CURRENT_SPEC_VERSION,
    EVIDENCE_TIERS,
    GOVERNANCE_KEYS,
    PROXY_HOLDOUT_PASS,
    ContractPolicy,
    ContractValidationError,
    EvaluationContract,
    attach_contract_policy,
    load_contract,
)
from warden.engine_sim import (
    LeveragePolicyError,
    extract_execution_policy,
    resolve_execution_limits,
    run_engine_simulation,
    run_engine_strategy,
)
from warden.provenance import resolve_contract_governance
from warden.qualify import qualify_candidate
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner
from warden.schemas import ExperimentSpec

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Pre-#94 digests, captured at 442308f before any code in this change existed.
LEGACY_SPEC_HASHES = {
    "AUTO_CRYPTO_PERPS_TOP100_1H_V1_H24_E24_F45_S20_V1": "cfb99eb95b5a2d6e",
    "AUTO_INDICES_1H_V2_H24_E24_F5_S10_V1": "68cacf458b2e11ec",
    "AUTO_INDICES_30M_V1_H24_E24_F5_S10_V1": "6be1681aee09a791",
    "CME_ES_15M_SYNCSTER_V1": "845409b5becf3bf6",
    "CME_NQ_15M_SYNCSTER_V1": "26fd7340ca335c39",
    "COMMODITIES_1M_V1": "e4a96dc2906a1509",
    "CRYPTO_MAJORS_1H_V1": "b298b53e3654d455",
    "CRYPTO_PERPS_1D_V1": "111f1fbe260f5bb0",
    "CRYPTO_PERPS_1H_V1": "f24108d3be8852e1",
    "CRYPTO_PERPS_1H_V2": "fa8e93900947a8a9",
    "CRYPTO_PERPS_1M_V1": "5d418715648cdca4",
    "CRYPTO_PERPS_30M_V1": "8b70e0fab02bd948",
    "CRYPTO_PERPS_4H_V1": "10bac3c302133094",
    "CRYPTO_PERPS_5M_V1": "b7743b88812be79c",
    "CRYPTO_PERPS_TOP100_1H_V1": "9f138c29cd8c4778",
    "FX_MAJORS_15M_V1": "5778652f86feb4a6",
    "FX_MAJORS_1D_V1": "bb01d60a9ea90527",
    "FX_MAJORS_1D_V2": "86eb39766d4b3764",
    "FX_MAJORS_1H_V1": "629bae1d62dbfd17",
    "FX_MAJORS_1M_V1": "e159945eb3ddef88",
    "FX_MAJORS_30M_V1": "bcd88fa42c5313cc",
    "FX_MAJORS_4H_V1": "170c2dadb4e1cd13",
    "FX_MAJORS_5M_V1": "43a04a1c9aa153b1",
    "INDICES_1D_V1": "2a59c552abaf3e86",
    "INDICES_1H_V1": "5f676e527a4f69ab",
    "INDICES_1H_V2": "1957474e76ca6a34",
    "INDICES_1M_V1": "cdd4cfde5573ad2d",
    "INDICES_30M_V1": "4f4a84134951fcff",
    "INDICES_4H_V1": "1f954af6d863e4a8",
    "INDICES_5M_V1": "f6e0303edc8d4ee0",
    "METALS_1M_V1": "6710b49de3886263",
    "MULTI_ASSET_1D_V1": "fdcac1c715a06e58",
    "MULTI_ASSET_1H_V1": "900a8e86d0a37f30",
    "MULTI_ASSET_30M_V1": "ca52ed49e884155a",
    "MULTI_ASSET_4H_V1": "d2764422ff91cdee",
    "MULTI_ASSET_5M_V1": "ff7fc8b3d6c5e33a",
}

#: The registered ``contracts`` column set, which governance must not perturb.
LEGACY_CONTRACT_FIELDS = 22
#: #113: the governance block travels in one explicit carrier field (persisted as
#: ``contracts.policy``), excluded from ``spec_hash_payload()`` and ``to_dict()``.
POLICY_CARRIER_FIELDS = frozenset({"policy"})


def _legacy_documents() -> dict[str, dict]:
    return {
        Path(p).stem.upper(): yaml.safe_load(Path(p).read_text())
        for p in all_contract_paths(REPO_ROOT)
    }


def _v2_yaml(base: dict, **governance) -> dict:
    document = dict(base)
    document.update({"spec_version": CURRENT_SPEC_VERSION, "selection_group_id": "GRP-T08", **governance})
    return document


def _write_yaml(path: Path, document: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Legacy digest stability
# ---------------------------------------------------------------------------


def test_all_36_legacy_contracts_keep_their_pinned_spec_hash() -> None:
    computed = {
        c.contract_id: c.spec_hash() for c in (load_contract(p) for p in all_contract_paths(REPO_ROOT))
    }
    assert len(computed) == 36
    assert computed == LEGACY_SPEC_HASHES
    for c in (load_contract(p) for p in all_contract_paths(REPO_ROOT)):
        assert c.governance.spec_version == 1
        assert c.governance.evidence_tier == "native_venue"
        assert "governance" not in c.spec_hash_payload()


def test_governance_carrier_never_enters_asdict_or_the_registry_schema() -> None:
    contract = load_recorded_contract(REPO_ROOT, "CRYPTO_PERPS_1H_V1")
    pinned = contract.spec_hash()
    attach_contract_policy(contract, ContractPolicy(evidence_tier="research_proxy", selection_group_id="G1"))
    # v1 keeps its digest even though the resolved tier changed...
    assert contract.spec_hash() == pinned
    assert contract.governance.evidence_tier == "research_proxy"
    # ...because no governance *key* is a field and the carrier is excluded from the
    # digest, so the historical field projection and the digest are untouched.  #113
    # promotes the carrier itself to an explicit field (``policy``), which is why the
    # field count is the historical 22 plus that one carrier.
    assert len(fields(EvaluationContract)) - len(POLICY_CARRIER_FIELDS) == LEGACY_CONTRACT_FIELDS
    assert {f.name for f in fields(EvaluationContract)} & POLICY_CARRIER_FIELDS == POLICY_CARRIER_FIELDS
    assert not {f.name for f in fields(EvaluationContract)} & set(GOVERNANCE_KEYS)
    assert "governance" not in contract.spec_hash_payload()


def test_frozen_contract_still_rejects_assignment_of_governance() -> None:
    from dataclasses import FrozenInstanceError

    contract = load_recorded_contract(REPO_ROOT, "CRYPTO_PERPS_1H_V1")
    with pytest.raises(FrozenInstanceError):
        contract.governance = ContractPolicy()  # type: ignore[misc]


def test_legacy_contract_reads_tier_from_manifest_without_moving_its_hash(tmp_path: Path) -> None:
    document = _legacy_documents()["FX_MAJORS_1H_V1"]
    repo = tmp_path / "repo"
    contract_path = _write_yaml(repo / "contracts" / "fx_majors_1h_v1.yaml", document)
    manifest_path = repo / "contracts" / "datasets" / "FX_MAJORS_1H_V1.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "contract_id": "FX_MAJORS_1H_V1",
                "path": document["exploratory_data_path"],
                "sha256": document["data_snapshot_sha256"],
                "provenance": "verified_market",
                "source": "fixture",
                "evidence_tier": "research_proxy",
                "selection_group_id": "SELECTION-GRP-FX",
                "holdout_refit_mode": "frozen_pre_holdout",
            }
        ),
        encoding="utf-8",
    )
    contract = load_contract(contract_path)
    assert contract.spec_hash() == LEGACY_SPEC_HASHES["FX_MAJORS_1H_V1"]
    assert contract.governance.evidence_tier == "native_venue"  # the YAML owns nothing

    resolved = resolve_contract_governance(contract, repo)
    assert resolved.spec_version == 1
    assert resolved.evidence_tier == "research_proxy"
    assert resolved.selection_group_id == "SELECTION-GRP-FX"
    assert resolved.holdout_refit_mode == "frozen_pre_holdout"
    assert resolved.is_promotion_capped is True
    # Reading lifecycle metadata is not a contract mutation.
    assert contract.spec_hash() == LEGACY_SPEC_HASHES["FX_MAJORS_1H_V1"]


def test_v2_contract_cannot_be_downgraded_by_its_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    document = _v2_yaml(_legacy_documents()["CRYPTO_PERPS_1H_V1"], evidence_tier="native_venue")
    contract_path = _write_yaml(repo / "contracts" / "crypto_perps_1h_v1.yaml", document)
    manifest_path = repo / "contracts" / "datasets" / "CRYPTO_PERPS_1H_V1.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"contract_id": "CRYPTO_PERPS_1H_V1", "evidence_tier": "research_proxy"}), encoding="utf-8"
    )
    contract = load_contract(contract_path)
    resolved = resolve_contract_governance(contract, repo)
    assert resolved.declares_governance is True
    assert resolved.evidence_tier == "native_venue"
    assert resolved.is_promotion_capped is False


def test_manifest_declared_tier_pin_must_match_manifest_bytes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    document = _legacy_documents()["INDICES_1H_V1"]
    contract = load_contract(_write_yaml(repo / "contracts" / "indices_1h_v1.yaml", document))
    manifest_path = repo / "contracts" / "datasets" / "INDICES_1H_V1.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    stale = hashlib.sha256(b"an older manifest").hexdigest()
    manifest_path.write_text(
        json.dumps({"contract_id": "INDICES_1H_V1", "evidence_tier": "research_proxy",
                    "dataset_manifest_sha256": stale}),
        encoding="utf-8",
    )
    with pytest.raises(ContractValidationError, match="dataset_manifest_sha256"):
        resolve_contract_governance(contract, repo)


def test_governance_keys_in_a_v1_yaml_are_refused_not_silently_dropped(tmp_path: Path) -> None:
    document = dict(_legacy_documents()["CRYPTO_PERPS_1H_V1"])
    document["evidence_tier"] = "research_proxy"
    with pytest.raises(ContractValidationError, match="require spec_version"):
        load_contract(_write_yaml(tmp_path / "contracts" / "crypto_perps_1h_v1.yaml", document))


# ---------------------------------------------------------------------------
# 2. v2 validation and digest coverage
# ---------------------------------------------------------------------------


def test_v2_governance_is_covered_by_the_hash_and_stable_across_reloads(tmp_path: Path) -> None:
    base = _legacy_documents()["CRYPTO_PERPS_1H_V1"]
    path = _write_yaml(tmp_path / "contracts" / "t08_v2.yaml", _v2_yaml(base, evidence_tier="native_venue"))
    first = load_contract(path)
    again = load_contract(path)
    assert first.governance.spec_version == 2
    assert first.spec_hash() == again.spec_hash()
    assert "governance" in first.spec_hash_payload()
    assert first.spec_hash() != LEGACY_SPEC_HASHES["CRYPTO_PERPS_1H_V1"]

    tiers = {}
    for tier in EVIDENCE_TIERS:
        tiers[tier] = load_contract(
            _write_yaml(tmp_path / f"contracts/{tier}.yaml", _v2_yaml(base, evidence_tier=tier))
        ).spec_hash()
    assert len(set(tiers.values())) == len(EVIDENCE_TIERS)

    lean = load_contract(
        _write_yaml(tmp_path / "contracts/lev.yaml",
                    _v2_yaml(base, evidence_tier="native_venue", allow_leverage=True, max_gross_leverage=1.5))
    )
    assert lean.spec_hash() != first.spec_hash()
    assert lean.governance.allow_leverage is True
    assert lean.governance.max_gross_leverage == 1.5


@pytest.mark.parametrize(
    "overrides",
    [
        {"evidence_tier": "proxyish"},
        {"evidence_tier": ""},
        {"holdout_refit_mode": "expanding_on_all"},
        {"selection_group_id": ""},
        {"selection_group_id": "bad id"},
        {"spec_version": 3},
        {"spec_version": 2.0},
        {"allow_leverage": 1},
        {"allow_leverage": False, "max_gross_leverage": 2.0},
        {"allow_leverage": True, "max_gross_leverage": 0.0},
        {"allow_leverage": True, "max_gross_leverage": float("nan")},
        {"dataset_manifest_sha256": "deadbeef"},
        {"inherits_selection_from": "../escape"},
        {"unexpected_governance": True},
    ],
)
def test_v2_contract_rejects_invalid_governance(tmp_path: Path, overrides: dict) -> None:
    document = _v2_yaml(_legacy_documents()["CRYPTO_PERPS_1H_V1"], evidence_tier="native_venue")
    document.update(overrides)
    if document.get("spec_version") is None:
        document["spec_version"] = CURRENT_SPEC_VERSION
    with pytest.raises(ContractValidationError):
        load_contract(_write_yaml(tmp_path / "contracts" / "bad.yaml", document))


def test_contract_policy_rejects_implicit_leverage_directly() -> None:
    with pytest.raises(ContractValidationError, match="requires allow_leverage=True"):
        ContractPolicy(spec_version=2, selection_group_id="G", max_gross_leverage=3.0)
    with pytest.raises(ContractValidationError, match="selection_group_id"):
        ContractPolicy(spec_version=2)


# ---------------------------------------------------------------------------
# 3. Registration idempotency
# ---------------------------------------------------------------------------


def test_register_contract_is_idempotent_for_all_36_legacy_and_one_v2(tmp_path: Path) -> None:
    reg = ResearchRegistry(tmp_path / "scratch" / "registry.db")
    contracts = [load_contract(p) for p in all_contract_paths(REPO_ROOT)]
    for contract in contracts:
        reg.register_contract(contract)
    for contract in contracts:  # second pass must be a no-op, never a refusal
        reg.register_contract(contract)

    base = _legacy_documents()["METALS_1M_V1"]
    v2_document = _v2_yaml(dict(base, contract_id="T08_V2_METALS_V1"), evidence_tier="research_proxy")
    v2 = load_contract(_write_yaml(tmp_path / "contracts" / "v2.yaml", v2_document))
    reg.register_contract(v2)
    reg.register_contract(v2)

    with reg._connect() as conn:
        rows = dict(conn.execute("SELECT contract_id, spec_hash FROM contracts").fetchall())
        columns = {r[1] for r in conn.execute("PRAGMA table_info(contracts)").fetchall()}
    assert len(rows) == 37
    assert {cid: rows[cid] for cid in LEGACY_SPEC_HASHES} == LEGACY_SPEC_HASHES
    assert rows[v2.contract_id] == v2.spec_hash()
    assert not columns & set(GOVERNANCE_KEYS)

    # A digest change is still refused as an immutable-contract violation.
    changed = load_contract(
        _write_yaml(tmp_path / "contracts" / "v2b.yaml",
                    _v2_yaml(dict(base, contract_id="T08_V2_METALS_V1"), evidence_tier="unverified_roll"))
    )
    with pytest.raises(ValueError, match="immutable"):
        reg.register_contract(changed)


# ---------------------------------------------------------------------------
# 4. Engine reads leverage from the contract only
# ---------------------------------------------------------------------------


def _engine_panel(n_bars: int = 48, assets: tuple[str, ...] = ("AAA", "BBB")) -> tuple[pl.DataFrame, list, list]:
    rows = []
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for idx, asset in enumerate(assets):
        price = 100.0 + 10.0 * idx
        for t in range(n_bars):
            nxt = price * 1.001
            rows.append({"timestamp": start + timedelta(hours=t), "asset": asset, "open": price,
                         "high": max(price, nxt), "low": min(price, nxt), "close": nxt,
                         "volume": 1_000.0, "funding_rate": 0.0})
            price = nxt
    panel = pl.DataFrame(rows).sort("timestamp", "asset").with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    ts = sorted(panel["timestamp"].unique().to_list())
    return panel, ts, sorted(assets)


def _engine_contract(*, leverage: bool = False, max_gross: float = 1.0, tier: str = "native_venue") -> EvaluationContract:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    contract = EvaluationContract(
        contract_id="T08_ENGINE_V1", asset_class="crypto_perps", frequency="1h", bars_per_year=8760,
        exploratory_data_path="data/exploratory/t08.parquet", holdout_data_path="data/holdout/t08.parquet",
        train_start=start, val_start=start + timedelta(hours=50), val_end=start + timedelta(hours=100),
        holdout_start=start + timedelta(hours=100), holdout_end=start + timedelta(hours=200),
        n_folds=2, label_horizon_bars=2, embargo_bars=1, execution_delay_bars=1,
        taker_fee_bps=1.0, slippage_bps=1.0, data_provenance="unknown",
    )
    return attach_contract_policy(
        contract,
        ContractPolicy(spec_version=CURRENT_SPEC_VERSION, evidence_tier=tier, selection_group_id="GRP-T08",
                       allow_leverage=leverage, max_gross_leverage=max_gross),
    )


def test_v2_contract_forbids_leverage_even_when_the_caller_asks_for_it() -> None:
    panel, ts, assets = _engine_panel()
    contract = _engine_contract(leverage=False)
    assert resolve_execution_limits(contract, True) == (False, 1.0)
    weights = np.full((len(ts), len(assets)), 0.75, dtype=float)
    with pytest.raises(LeveragePolicyError, match="max_gross_leverage"):
        run_engine_simulation(panel, weights, ts, assets, contract, slice(0, len(ts)), allow_leverage=True)


def test_v2_contract_max_gross_leverage_is_enforced_in_both_directions() -> None:
    panel, ts, assets = _engine_panel()
    contract = _engine_contract(leverage=True, max_gross=1.5)
    assert resolve_execution_limits(contract, False, 1.0) == (True, 1.5)

    within = np.full((len(ts), len(assets)), 0.7, dtype=float)  # gross 1.4 <= 1.5
    sim = run_engine_simulation(panel, within, ts, assets, contract, slice(0, len(ts)),
                                allow_leverage=False, max_gross_leverage=1.0)
    assert sim.net_returns.shape[0] == len(ts)

    beyond = np.full((len(ts), len(assets)), 0.8, dtype=float)  # gross 1.6 > 1.5
    with pytest.raises(LeveragePolicyError, match="max_gross_leverage"):
        run_engine_simulation(panel, beyond, ts, assets, contract, slice(0, len(ts)))


def test_legacy_contract_keeps_its_historical_non_leveraged_limits() -> None:
    legacy = load_recorded_contract(REPO_ROOT, "CRYPTO_PERPS_1H_V1")
    assert resolve_execution_limits(legacy) == (False, 1.0)
    assert resolve_execution_limits(legacy, False, None) == (False, 1.0)

    defaults = {"allow_leverage": False, "allow_short_selling": True,
                "max_volume_participation": None, "market_impact_coefficient": None}
    assert extract_execution_policy({"allow_leverage": True}, {"allow_leverage": True}) == defaults

    recorded = {"execution_policy": dict(defaults, allow_leverage=True)}
    replay = extract_execution_policy({}, {}, json.dumps(recorded), contract=legacy)
    assert replay["allow_leverage"] is True and replay["max_gross_leverage"] is None

    v2 = _engine_contract(leverage=False)
    widening = extract_execution_policy({}, {}, json.dumps(recorded), contract=v2)
    assert widening["allow_leverage"] is False and widening["max_gross_leverage"] == 1.0


def test_agent_spec_cannot_inject_leverage_or_headroom() -> None:
    valid = {"contract_id": "C1", "hypothesis_id": "H1"}
    assert ExperimentSpec.model_validate(valid).parameters == {}
    for key, value in (("allow_leverage", True), ("max_gross_leverage", 4.0), ("max_volume_participation", 0.9)):
        with pytest.raises(ValidationError):
            ExperimentSpec.model_validate({**valid, key: value})


# ---------------------------------------------------------------------------
# 4b. copies keep contract governance (the live `warden verify` copy path)
# ---------------------------------------------------------------------------


def _copied(contract: EvaluationContract, target: Path) -> EvaluationContract:
    """Exactly what ``leakage.py`` does for its prefix-invariance replays."""
    return replace(contract, exploratory_data_path=str(target))


def test_dataclasses_replace_keeps_the_validated_governance(tmp_path: Path) -> None:
    base = _legacy_documents()["CRYPTO_PERPS_1H_V1"]
    cases = {
        "proxy": {"evidence_tier": "research_proxy"},
        "roll": {"evidence_tier": "unverified_roll"},
        "leveraged": {"evidence_tier": "native_venue", "allow_leverage": True, "max_gross_leverage": 1.5},
    }
    for label, governance in cases.items():
        path = _write_yaml(tmp_path / f"contracts/{label}.yaml", _v2_yaml(base, **governance))
        original = load_contract(path)
        copy = _copied(original, tmp_path / f"{label}_trunc.parquet")

        assert copy.governance == original.governance
        assert copy.governance.spec_version == CURRENT_SPEC_VERSION
        assert copy.governance.allow_leverage is original.governance.allow_leverage
        assert copy.governance.max_gross_leverage == original.governance.max_gross_leverage
        assert "governance" in copy.spec_hash_payload()
        # A dropped carrier would collapse to the pinned v1 digest and be
        # accepted against the already-registered legacy row.
        assert copy.spec_hash() != LEGACY_SPEC_HASHES["CRYPTO_PERPS_1H_V1"]
        # The copied digest is the v2 digest of the copied *fields*: the only
        # difference from the original is the replaced path.
        assert copy.spec_hash() != original.spec_hash()
        assert replace(original).spec_hash() == original.spec_hash()


def test_copied_non_leveraged_contract_still_denies_leverage_on_both_engine_entry_points(tmp_path: Path) -> None:
    panel, ts, assets = _engine_panel()
    contract = _engine_contract(leverage=False)
    copy = _copied(contract, tmp_path / "trunc.parquet")
    assert resolve_execution_limits(copy, True, 5.0) == (False, 1.0)

    gross_1_5 = np.full((len(ts), len(assets)), 0.75, dtype=float)
    with pytest.raises(LeveragePolicyError, match="max_gross_leverage"):
        run_engine_simulation(panel, gross_1_5, ts, assets, copy, slice(0, len(ts)),
                              allow_leverage=True, max_gross_leverage=5.0)

    # The strategy entry point enforces permission through the margin config:
    # a >1x order book is rejected even though the caller asked for leverage.
    with pytest.raises(LeveragePolicyError, match="allow_leverage=False"):
        run_engine_strategy(panel, _UnitOrderStrategy(7_000.0), ts, assets, copy, slice(0, len(ts)),
                            allow_leverage=True)


def test_copied_leveraged_contract_keeps_its_contract_owned_cap(tmp_path: Path) -> None:
    panel, ts, assets = _engine_panel()
    contract = _engine_contract(leverage=True, max_gross=1.5)
    copy = _copied(contract, tmp_path / "trunc.parquet")
    assert resolve_execution_limits(copy, False, 1.0) == (True, 1.5)

    within = np.full((len(ts), len(assets)), 0.7, dtype=float)  # gross 1.4 <= 1.5
    sim = run_engine_simulation(panel, within, ts, assets, copy, slice(0, len(ts)), allow_leverage=False)
    assert sim.net_returns.shape[0] == len(ts)

    beyond = np.full((len(ts), len(assets)), 0.8, dtype=float)  # gross 1.6 > 1.5
    with pytest.raises(LeveragePolicyError, match="max_gross_leverage"):
        run_engine_simulation(panel, beyond, ts, assets, copy, slice(0, len(ts)))

    # A caller that explicitly forbids leverage cannot narrow or widen it either.
    strategy_sim = run_engine_strategy(panel, _UnitOrderStrategy(7_000.0), ts, assets, copy,
                                       slice(0, len(ts)), allow_leverage=False)
    assert strategy_sim.net_returns.shape[0] == len(ts)


def test_legacy_copy_keeps_authorised_replay_semantics(tmp_path: Path) -> None:
    """Live policy is contract-owned; a legacy v1 copy stays a recorded replay."""
    panel, ts, assets = _engine_panel()
    legacy = load_recorded_contract(REPO_ROOT, "CRYPTO_PERPS_1H_V1")
    copy = _copied(legacy, tmp_path / "trunc.parquet")
    assert copy.governance.spec_version == 1
    assert "governance" not in copy.spec_hash_payload()
    # Still the v1 rule: plain field projection, no governance block.  Only the
    # replaced path moves the digest (a no-op copy is byte-identical to the
    # registered contract).
    assert copy.spec_hash() == EvaluationContract(**asdict(copy)).spec_hash()
    assert replace(legacy).spec_hash() == legacy.spec_hash() == LEGACY_SPEC_HASHES["CRYPTO_PERPS_1H_V1"]
    assert resolve_execution_limits(copy, True, None) == (True, None)
    replayed = run_engine_simulation(panel, np.full((len(ts), len(assets)), 0.7, dtype=float), ts, assets,
                                     copy, slice(0, len(ts)), allow_leverage=True)
    assert replayed.net_returns.shape[0] == len(ts)


def test_official_serialisation_preserves_governance(tmp_path: Path) -> None:
    base = _legacy_documents()["CRYPTO_PERPS_1H_V1"]
    path = _write_yaml(tmp_path / "contracts/lev.yaml",
                       _v2_yaml(base, evidence_tier="research_proxy", allow_leverage=True, max_gross_leverage=1.5))
    contract = load_contract(path)

    for label, document in (
        ("to_dict", contract.to_dict()),
        ("__dict__", dict(contract.__dict__)),
        ("yaml-dumped __dict__", yaml.safe_load(yaml.safe_dump(contract.__dict__))),
    ):
        rebuilt = EvaluationContract.from_dict(document)
        assert rebuilt.governance == contract.governance, label
        assert rebuilt.spec_hash() == contract.spec_hash(), label

    # ``asdict()`` is the field projection *including* the explicit policy carrier
    # (#113): a v2 contract survives it instead of collapsing to a v1 projection, so
    # the projection rebuilds the same digest rather than the legacy one.
    projected = EvaluationContract(**asdict(contract))
    assert len(asdict(contract)) == LEGACY_CONTRACT_FIELDS + len(POLICY_CARRIER_FIELDS)
    assert projected.governance == contract.governance
    assert projected.spec_hash() == contract.spec_hash()


def test_v2_copy_registers_on_the_unchanged_schema(tmp_path: Path) -> None:
    reg = ResearchRegistry(tmp_path / "scratch" / "registry.db")
    base = _legacy_documents()["METALS_1M_V1"]
    contract = load_contract(
        _write_yaml(tmp_path / "contracts/v2copy.yaml",
                    _v2_yaml(dict(base, contract_id="T08_COPY_METALS_V1"), evidence_tier="research_proxy"))
    )
    reg.register_contract(contract)
    reg.register_contract(replace(contract))  # a no-op copy is still the same contract
    with reg._connect() as conn:
        assert dict(conn.execute("SELECT contract_id, spec_hash FROM contracts").fetchall()) == {
            contract.contract_id: contract.spec_hash()
        }
        assert not {r[1] for r in conn.execute("PRAGMA table_info(contracts)").fetchall()} & set(GOVERNANCE_KEYS)

    copied = _copied(contract, tmp_path / "other.parquet")
    with pytest.raises(ValueError, match="immutable"):
        reg.register_contract(copied)


def test_leakage_prefix_invariance_copies_keep_the_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real ``warden verify`` copy path, not a replica of its expression.

    ``leakage.py`` is out of this ticket's write scope, so the Engine is faked:
    the assertion is on the contracts ``leakage`` hands to it.  Every copied
    contract (truncated and perturbed panels) must still carry the declared
    policy, otherwise ``warden verify`` executes under legacy v1 limits.
    """
    from types import SimpleNamespace

    from warden import engine_sim
    from warden.leakage import verify_rules_strategy_prefix_invariance

    seen: list[EvaluationContract] = []

    def fake_run(panel_df, strat, ts, assets, contract, val_slice, **kwargs):
        seen.append(contract)
        length = (val_slice.stop or len(ts)) - (val_slice.start or 0)
        return SimpleNamespace(backtest_result=SimpleNamespace(fills=[]),
                               positions=np.zeros((length, len(assets)), dtype=float))

    monkeypatch.setattr(engine_sim, "run_engine_strategy", fake_run)
    module = SimpleNamespace(required_symbols=lambda params: None,
                             configure_strategy=lambda contract, params: object())

    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    for asset in ("AAA", "BBB"):
        for step in range(40):
            price = 100.0 + step * 0.01
            rows.append({"timestamp": start + timedelta(hours=step), "asset": asset, "open": price,
                         "high": price + 0.1, "low": price - 0.1, "close": price, "volume": 1_000.0,
                         "funding_rate": 0.0})
    panel = pl.DataFrame(rows).sort("timestamp", "asset").with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")))

    contract = _engine_contract(leverage=True, max_gross=1.5)
    verify_rules_strategy_prefix_invariance(module, panel, contract, {})

    assert len(seen) >= 3  # full window plus at least one truncated/perturbed pair
    for observed in seen:
        assert observed.governance == contract.governance
        assert observed.governance.spec_version == CURRENT_SPEC_VERSION
        assert observed.governance.allow_leverage is True
        assert observed.governance.max_gross_leverage == 1.5
        assert "governance" in observed.spec_hash_payload()


class _UnitOrderStrategy(Strategy):
    """Submit one fixed-size order per asset on the first bar (no leverage logic)."""

    def __init__(self, units: float) -> None:
        self._units = float(units)
        self._submitted = False

    def on_data(self, timestamp, data, context, broker) -> None:
        if self._submitted:
            return
        self._submitted = True
        for asset in data:
            broker.submit_order(asset, self._units)


class _HedgeStrategy(Strategy):
    """Long one asset, short the other: multi-asset gross ~2x while net stays ~0."""

    def __init__(self, units: float) -> None:
        self._units = float(units)
        self._submitted = False

    def on_data(self, timestamp, data, context, broker) -> None:
        if self._submitted:
            return
        self._submitted = True
        first, second = sorted(data)[:2]
        broker.submit_order(first, self._units)
        broker.submit_order(second, -self._units)


class _PeakThenFlatStrategy(Strategy):
    """Take `units` per asset on bar 1, then send the offsetting order on `flatten_bar`.

    ``submit_order`` is a delta order, so the run really does end flat: a
    final-snapshot-only cap check would accept it.
    """

    def __init__(self, units: float, flatten_bar: int = 6) -> None:
        self._units = float(units)
        self._flatten_bar = int(flatten_bar)
        self._bar = 0

    def on_data(self, timestamp, data, context, broker) -> None:
        self._bar += 1
        if self._bar == 1:
            for asset in data:
                broker.submit_order(asset, self._units)
        elif self._bar == self._flatten_bar:
            for asset in data:
                broker.submit_order(asset, -self._units)


def _max_realized_gross(sim) -> float:
    """Peak per-bar ``gross_exposure / equity`` recorded by the Engine itself."""
    return max(
        (gross / equity for _ts, equity, _cash, gross, _net, _n in sim.backtest_result.portfolio_state
         if equity > 0.0),
        default=0.0,
    )


# ---------------------------------------------------------------------------
# 4c. the numeric contract cap on the strategy entry point
# ---------------------------------------------------------------------------


def test_strategy_entry_point_enforces_the_contract_numeric_cap() -> None:
    panel, ts, assets = _engine_panel()
    contract = _engine_contract(leverage=True, max_gross=1.5)
    assert resolve_execution_limits(contract, True, 5.0) == (True, 1.5)

    # Realized gross 1.47: runs.  The caller asks for the *narrower* 1.0, and the
    # contract still governs, so the run proves the contract value is the one used.
    within = run_engine_strategy(
        panel, _UnitOrderStrategy(7_000.0), ts, assets, contract, slice(0, len(ts)),
        allow_leverage=True, max_gross_leverage=1.0,
    )
    assert within.net_returns.shape[0] == len(ts)
    assert 1.4 <= _max_realized_gross(within) <= 1.5

    # Realized gross 1.89: the same permissive caller state cannot widen the cap.
    with pytest.raises(LeveragePolicyError, match="max_gross_leverage=1.5"):
        run_engine_strategy(
            panel, _UnitOrderStrategy(9_000.0), ts, assets, contract, slice(0, len(ts)),
            allow_leverage=True, max_gross_leverage=5.0,
        )
    with pytest.raises(LeveragePolicyError, match="max_gross_leverage=1.5"):
        run_engine_strategy(
            panel, _UnitOrderStrategy(9_000.0), ts, assets, contract, slice(0, len(ts)),
            allow_leverage=False, max_gross_leverage=1.0,
        )


def test_strategy_cap_measures_multi_asset_gross_not_net() -> None:
    panel, ts, assets = _engine_panel()
    strict = _engine_contract(leverage=True, max_gross=1.5)
    with pytest.raises(LeveragePolicyError, match="max_gross_leverage=1.5"):
        run_engine_strategy(
            panel, _HedgeStrategy(9_000.0), ts, assets, strict, slice(0, len(ts)),
            allow_leverage=True, max_gross_leverage=5.0,
        )

    # Control: the same hedge under a contract that really allows 3x runs, so the
    # refusal above is about gross 1.99 vs 1.5 and not about short selling.
    wide = _engine_contract(leverage=True, max_gross=3.0)
    sim = run_engine_strategy(panel, _HedgeStrategy(9_000.0), ts, assets, wide, slice(0, len(ts)))
    _ts, equity, _cash, gross, net, _n = sim.backtest_result.portfolio_state[-1]
    assert gross / equity > 1.5
    assert abs(net / equity) < 0.15


def test_strategy_cap_rejects_a_peak_that_later_flattens() -> None:
    panel, ts, assets = _engine_panel()
    strict = _engine_contract(leverage=True, max_gross=1.5)
    with pytest.raises(LeveragePolicyError, match="max_gross_leverage=1.5"):
        run_engine_strategy(
            panel, _PeakThenFlatStrategy(9_000.0), ts, assets, strict, slice(0, len(ts))
        )

    wide = _engine_contract(leverage=True, max_gross=3.0)
    sim = run_engine_strategy(
        panel, _PeakThenFlatStrategy(9_000.0), ts, assets, wide, slice(0, len(ts))
    )
    _ts, equity, _cash, gross, _net, _n = sim.backtest_result.portfolio_state[-1]
    assert gross / equity < 0.05  # the run really ends flat...
    assert _max_realized_gross(sim) > 1.5  # ...but only after breaching the cap


def test_legacy_v1_strategy_replay_is_bit_identical_after_cap_enforcement(tmp_path: Path) -> None:
    """Pin legacy replay parity: a v1 contract owns no numeric cap to enforce.

    The digest below was captured from this same synthetic panel before the
    strategy-path audit existed; a v1 replay must keep producing those bytes.
    """
    panel, ts, assets = _engine_panel()
    legacy = load_recorded_contract(REPO_ROOT, "CRYPTO_PERPS_1H_V1")
    copy = _copied(legacy, tmp_path / "trunc.parquet")
    assert copy.governance.declares_governance is False
    assert resolve_execution_limits(copy, True, None) == (True, None)

    sim = run_engine_strategy(
        panel, _UnitOrderStrategy(9_000.0), ts, assets, copy, slice(0, len(ts)), allow_leverage=True
    )
    assert _max_realized_gross(sim) > 1.8  # legal before this repair, still legal
    digest = hashlib.sha256(np.ascontiguousarray(sim.net_returns).astype("<f8").tobytes()).hexdigest()
    assert digest == "b2fb3b5c5599731f63ea3b6508ff28bb98f3b8d63ba46b0de597a2296b9d8d3f"
    assert sim.terminal_equity == pytest.approx(1089859.695104578, rel=0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 4d. the supported v2 contract writer (scratch repo only)
# ---------------------------------------------------------------------------


def test_write_contract_and_manifest_atomic_round_trips_a_v2_declaration(tmp_path: Path) -> None:
    from warden_evidence.market_validation import (
        build_dataset_manifest,
        write_contract_and_manifest_atomic,
    )

    repo = tmp_path / "repo"
    exp_path = repo / "data" / "exploratory" / "t08.parquet"
    exp_path.parent.mkdir(parents=True, exist_ok=True)
    _engine_panel()[0].write_parquet(exp_path)
    sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()

    start = datetime(2025, 1, 1, tzinfo=UTC)
    base = EvaluationContract(
        contract_id="T08_WRITER_V1", asset_class="crypto_perps", frequency="1h", bars_per_year=8760,
        exploratory_data_path="data/exploratory/t08.parquet",
        holdout_data_path="data/holdout/t08.parquet",
        train_start=start, val_start=start + timedelta(hours=50), val_end=start + timedelta(hours=100),
        holdout_start=start + timedelta(hours=100), holdout_end=start + timedelta(hours=200),
        n_folds=2, label_horizon_bars=2, embargo_bars=1, execution_delay_bars=1,
        taker_fee_bps=1.0, slippage_bps=1.0, data_snapshot_sha256=sha, data_provenance="synthetic",
    )
    declared = attach_contract_policy(
        base,
        ContractPolicy(spec_version=CURRENT_SPEC_VERSION, evidence_tier="research_proxy",
                       selection_group_id="GRP-T08", allow_leverage=True, max_gross_leverage=1.5),
    )
    document = declared.to_dict()  # the supported, policy-preserving serialisation
    manifest = build_dataset_manifest(
        contract_id="T08_WRITER_V1", path=declared.exploratory_data_path, sha256=sha,
        provenance="synthetic", source="synthetic", pipeline="tests/test_p2_t08.py",
    )
    contract_path = repo / "contracts" / "t08_writer_v1.yaml"
    manifest_path = repo / "contracts" / "datasets" / "T08_WRITER_V1.json"

    write_contract_and_manifest_atomic(contract_path, document, manifest_path, manifest, repo_root=repo)
    reloaded = load_contract(contract_path)
    assert reloaded.governance == declared.governance
    assert (
        reloaded.governance.spec_version,
        reloaded.governance.allow_leverage,
        reloaded.governance.max_gross_leverage,
    ) == (CURRENT_SPEC_VERSION, True, 1.5)
    assert reloaded.spec_hash() == declared.spec_hash()
    # #113: ``asdict()`` carries the explicit policy carrier, so the historical
    # projection is now policy-preserving rather than a silent v1 downgrade.
    assert reloaded.spec_hash() == EvaluationContract(**asdict(declared)).spec_hash()

    # Identical bytes are an idempotent no-op, so the policy-bearing document is
    # stable across a write/reload/write cycle.
    before = contract_path.read_bytes()
    write_contract_and_manifest_atomic(contract_path, document, manifest_path, manifest, repo_root=repo)
    assert contract_path.read_bytes() == before

    # A v1 control document still writes with its historical digest.  Note
    # ``attach_contract_policy`` mutates in place, so the historical projection is
    # rebuilt from the fields with the #113 policy carrier dropped.
    v1_control = EvaluationContract(
        **{key: value for key, value in asdict(declared).items() if key not in POLICY_CARRIER_FIELDS})
    assert v1_control.governance.declares_governance is False
    v1_path = repo / "contracts" / "v1_control.yaml"
    v1_manifest = repo / "contracts" / "datasets" / "T08_WRITER_V1_v1.json"
    write_contract_and_manifest_atomic(
        v1_path, {key: value for key, value in asdict(v1_control).items() if key not in POLICY_CARRIER_FIELDS},
        v1_manifest, manifest, repo_root=repo)
    assert load_contract(v1_path).spec_hash() == v1_control.spec_hash()

    # Governance without `spec_version: 2` is refused (never silently downgraded)
    # and nothing is written.
    downgraded = {key: value for key, value in document.items() if key != "spec_version"}
    bad_path = repo / "contracts" / "downgraded.yaml"
    with pytest.raises(ContractValidationError, match="require spec_version"):
        write_contract_and_manifest_atomic(
            bad_path, downgraded, repo / "contracts" / "datasets" / "DOWNGRADED.json",
            manifest, repo_root=repo,
        )
    assert not bad_path.exists()



# ---------------------------------------------------------------------------
# 5. qualification: the promotion cap
# ---------------------------------------------------------------------------

STRATEGY_SOURCE = """
import numpy as np


def required_symbols(params):
    return None


def generate_positions(panel_df, contract, **kwargs):
    weight = float(kwargs.get("weight", 0.35))
    n_ts = panel_df["timestamp"].n_unique()
    n_assets = panel_df["asset"].n_unique()
    pos = np.zeros((n_ts, n_assets), dtype=float)
    for t in range(n_ts):
        pos[t, :] = weight
    return {"positions": pos}
"""


def _write_panel(path: Path, start: datetime, n_bars: int, drift: float, vol: float, seed: int) -> pl.DataFrame:
    rows = []
    rng = np.random.default_rng(seed)
    for idx, asset in enumerate(("AAA", "BBB")):
        price = 100.0 + 10.0 * idx
        for t in range(n_bars):
            nxt = price * (1.0 + drift + float(rng.normal(0.0, vol)))
            rows.append({"timestamp": start + timedelta(hours=t), "asset": asset, "open": price,
                         "high": max(price, nxt) * 1.0005, "low": min(price, nxt) * 0.9995,
                         "close": nxt, "volume": 500.0, "funding_rate": 0.0})
            price = nxt
    panel = pl.DataFrame(rows).sort("timestamp", "asset").with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(path)
    return panel


def _qualification_repo(tmp_path: Path, *, spec_version: int, tier: str) -> tuple[Path, Path, EvaluationContract]:
    """Synthetic repo + scratch registry + a CANDIDATE trial ready to qualify."""
    repo = tmp_path / "repo"
    start = datetime(2025, 1, 1, tzinfo=UTC)
    val_start, val_end = start + timedelta(hours=60), start + timedelta(hours=120)
    holdout_start, holdout_end = val_end, val_end + timedelta(hours=120)

    exp_path = repo / "data" / "exploratory" / "t08.parquet"
    _write_panel(exp_path, start, 120, drift=0.003, vol=0.0008, seed=5)
    sha = hashlib.sha256(exp_path.read_bytes()).hexdigest()

    contract = EvaluationContract(
        contract_id="T08_QUAL_V1", asset_class="crypto_perps", frequency="1h", bars_per_year=8760,
        exploratory_data_path="data/exploratory/t08.parquet",
        holdout_data_path="data/holdout/t08.parquet",
        train_start=start, val_start=val_start, val_end=val_end,
        holdout_start=holdout_start, holdout_end=holdout_end,
        n_folds=2, label_horizon_bars=2, embargo_bars=1, execution_delay_bars=1,
        taker_fee_bps=1.0, slippage_bps=1.0, data_snapshot_sha256=sha,
        data_provenance="verified_market",
    )
    document = yaml.safe_load(yaml.safe_dump(
        {k: v for k, v in contract.__dict__.items() if k not in {"policy", "_governance"}}))
    if spec_version >= CURRENT_SPEC_VERSION:
        document.update({"spec_version": CURRENT_SPEC_VERSION, "selection_group_id": "GRP-T08",
                         "evidence_tier": tier, "allow_leverage": False, "max_gross_leverage": 1.0,
                         "holdout_refit_mode": "frozen_pre_holdout"})

    contract_path = repo / "contracts" / "t08_qual_v1.yaml"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    manifest = repo / "contracts" / "datasets" / "T08_QUAL_V1.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "contract_id": "T08_QUAL_V1", "path": contract.exploratory_data_path, "sha256": sha,
        "provenance": "verified_market", "source": "synthetic_t08_fixture",
        **({} if spec_version >= CURRENT_SPEC_VERSION else {"evidence_tier": tier}),
    }), encoding="utf-8")

    module_rel = "strategies/candidates/t08_flat.py"
    (repo / module_rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / module_rel).write_text(STRATEGY_SOURCE, encoding="utf-8")

    holdout_path = repo / "data" / "holdout" / "t08.parquet"
    _write_panel(holdout_path, holdout_start, 120, drift=0.004, vol=0.0006, seed=11)
    return repo, holdout_path, load_contract(contract_path)


def _seed_candidate(repo: Path, contract: EvaluationContract, *, net_sharpe: float = 1.0) -> ResearchRegistry:
    from warden.contracts import ExperimentResult, HypothesisSpec

    reg = ResearchRegistry(repo / "registry" / "registry.db")
    reg.register_contract(contract)
    hyp = HypothesisSpec("HYP-T08", contract.contract_id, "T08 cap", "rules_ts",
                         "Mechanism explanation of twenty chars", "Friction explanation of twenty chars",
                         8, "Reject net Sharpe below one")
    reg.register_hypothesis(hyp)
    code_sha, _ = code_closure(repo, "strategies/candidates/t08_flat.py")
    trial = ExperimentResult(
        run_id="RUN-T08-001", hypothesis_id="HYP-T08", contract_id=contract.contract_id,
        strategy_name="t08_flat", strategy_family="rules_ts", config_hash="cfg", stage_reached="STAGE_3_STRESS",
        verdict="CANDIDATE_FOR_QUALIFICATION", gross_sharpe=net_sharpe, net_sharpe=net_sharpe,
        deflated_sharpe_prob=0.99, expected_max_sharpe_noise=0.1, total_trials_n=1, effective_trials_keff=1.0,
        mean_ic=None, ic_hac_tstat=None, hit_rate=0.55, profit_factor=1.3, max_drawdown_pct=4.0,
        annualized_turnover=25.0, breakeven_cost_bps=12.0, stress_2x_cost_sharpe=net_sharpe * 0.9,
        param_neighbor_stability=None, notes="fixture", data_snapshot_sha256=contract.data_snapshot_sha256,
        strategy_module="strategies/candidates/t08_flat.py", model_type="rules",
        data_provenance="verified_market", strategy_code_sha256=code_sha,
    )
    reg.record_trial(trial, {}, np.full(120, 0.0005))
    return reg


@pytest.mark.parametrize("tier", ["research_proxy", "unverified_roll"])
def test_qualified_proxy_pass_is_never_promoted(tmp_path: Path, tier: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE", raising=False)
    repo, holdout, contract = _qualification_repo(tmp_path, spec_version=CURRENT_SPEC_VERSION, tier=tier)
    reg = _seed_candidate(repo, contract)
    runner = ExperimentRunner(registry=reg, repo_root=repo)

    result = qualify_candidate("RUN-T08-001", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)

    assert result["verdict"] == PROXY_HOLDOUT_PASS
    assert result["promoted"] is False
    assert result["evidence_tier"] == tier
    assert result["promotion_capped"] is True
    assert result["promotion_cap_reason"]
    # The numeric holdout conjunct really did clear, so this is a cap, not a failure.
    assert result["holdout_net_sharpe"] > 0.80
    assert result["val_to_holdout_ratio"] >= 0.50

    with reg._connect() as conn:
        status = conn.execute("SELECT status FROM hypotheses WHERE hypothesis_id='HYP-T08'").fetchone()[0]
        claim = conn.execute("SELECT state FROM holdout_claims WHERE run_id='RUN-T08-001'").fetchone()[0]
        qual = conn.execute("SELECT qual_id FROM qualifications WHERE run_id='RUN-T08-001'").fetchone()[0]
    assert status == PROXY_HOLDOUT_PASS
    assert status != "PROMOTED"
    assert claim == PROXY_HOLDOUT_PASS
    assert qual == "QUAL-RUN-T08-001"


def test_native_venue_v2_contract_still_promotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE", raising=False)
    repo, holdout, contract = _qualification_repo(tmp_path, spec_version=CURRENT_SPEC_VERSION, tier="native_venue")
    reg = _seed_candidate(repo, contract)
    runner = ExperimentRunner(registry=reg, repo_root=repo)

    result = qualify_candidate("RUN-T08-001", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)
    assert result["verdict"] == "PROMOTED" and result["promoted"] is True
    assert result["promotion_capped"] is False
    with reg._connect() as conn:
        assert conn.execute("SELECT status FROM hypotheses WHERE hypothesis_id='HYP-T08'").fetchone()[0] == "PROMOTED"


def test_legacy_v1_contract_without_declared_tier_is_unaffected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE", raising=False)
    repo, holdout, contract = _qualification_repo(tmp_path, spec_version=1, tier="native_venue")
    assert contract.governance.spec_version == 1
    reg = _seed_candidate(repo, contract)
    runner = ExperimentRunner(registry=reg, repo_root=repo)

    result = qualify_candidate("RUN-T08-001", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)
    assert result["verdict"] == "PROMOTED" and result["promoted"] is True
    assert result["contract_spec_version"] == 1


def test_legacy_v1_manifest_declared_proxy_tier_is_capped_at_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE", raising=False)
    repo, holdout, contract = _qualification_repo(tmp_path, spec_version=1, tier="research_proxy")
    reg = _seed_candidate(repo, contract)
    runner = ExperimentRunner(registry=reg, repo_root=repo)

    result = qualify_candidate("RUN-T08-001", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)
    assert result["verdict"] == PROXY_HOLDOUT_PASS and result["promoted"] is False
    assert result["evidence_tier"] == "research_proxy"
    assert result["contract_spec_version"] == 1
    with reg._connect() as conn:
        assert conn.execute("SELECT status FROM hypotheses WHERE hypothesis_id='HYP-T08'").fetchone()[0] != "PROMOTED"


def test_synthetic_tier_is_refused_before_any_holdout_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE", raising=False)
    repo, holdout, contract = _qualification_repo(tmp_path, spec_version=CURRENT_SPEC_VERSION, tier="synthetic")
    reg = _seed_candidate(repo, contract)
    runner = ExperimentRunner(registry=reg, repo_root=repo)

    with pytest.raises(ValueError, match="evidence_tier 'synthetic'"):
        qualify_candidate("RUN-T08-001", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout)
    with reg._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM holdout_claims").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM hypotheses WHERE hypothesis_id='HYP-T08'").fetchone()[0] == "ACTIVE"
