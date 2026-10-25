from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path

import pytest
import yaml

from warden.contracts import (
    ContractValidationError,
    HypothesisSpec,
    Stage1Report,
    load_contract,
    load_hypothesis,
)


@pytest.fixture
def contract():
    return load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))


def test_frozen_hash_and_economic_cost(contract):
    assert len(contract.spec_hash()) == 16
    assert replace(contract).spec_hash() == contract.spec_hash()
    assert replace(contract, taker_fee_bps=5).spec_hash() != contract.spec_hash()
    assert contract.total_one_way_cost_decimal() == pytest.approx(0.0006)
    with pytest.raises(FrozenInstanceError):
        contract.taker_fee_bps = 0
    assert load_contract(Path("contracts/fx_majors_15m_v1.yaml")).execution_mode == "QUOTE_SIDE"


@pytest.mark.parametrize("change", [
    {"taker_fee_bps": 0}, {"slippage_bps": 0}, {"taker_fee_bps": -1},
    {"execution_delay_bars": 0}, {"embargo_bars": 0}, {"n_folds": 0},
    {"taker_fee_bps": float("nan")}, {"slippage_bps": float("inf")},
    {"execution_mode": "SAME_BAR"}, {"frequency": "2h"},
    {"train_start": "2025-01-01"}, {"contract_id": "../escape"},
    {"val_end": "2025-03-26T00:00:00Z"},
])
def test_invalid_contract_fails_closed(contract, change):
    with pytest.raises(ContractValidationError):
        replace(contract, **change)


def test_frontmatter_loaders_and_mechanism_validation(tmp_path):
    spec = HypothesisSpec("HYP-1", "CONTRACT-1", "Funding reversal", "cross_sectional_ml",
                          "Crowded leveraged longs predict reversal",
                          "Forced retail liquidations pay funding costs", 8,
                          "Reject net Sharpe below one")
    path = tmp_path / "hyp.yaml"
    path.write_text(yaml.safe_dump(asdict(spec)))
    assert HypothesisSpec.from_yaml(path) == spec
    md = tmp_path / "hyp.md"
    md.write_text("---\n" + path.read_text() + "---\nEconomic narrative\n")
    assert load_hypothesis(md) == spec == HypothesisSpec.from_markdown(md)
    with pytest.raises(ContractValidationError, match="economic_mechanism"):
        replace(spec, economic_mechanism="short")
    with pytest.raises(ContractValidationError, match="counterparty_or_friction"):
        replace(spec, counterparty_or_friction="short")
    md.write_text("no frontmatter")
    with pytest.raises(ContractValidationError):
        load_hypothesis(md)
    assert Stage1Report("rules_ts", False, {"edge": -1}).to_dict()["passed"] is False
