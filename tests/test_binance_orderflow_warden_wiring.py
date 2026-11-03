"""Contract/strategy handshake: no silent re-use of original 4h contract."""
from dataclasses import replace
from pathlib import Path

import polars as pl
import pytest
import yaml

from strategies.candidates.recovery import binance_ats_h72, binance_ntr_h72
from strategies.candidates.recovery.warden_bridge import warden_output
from warden.contracts import load_contract


@pytest.fixture
def proposed():
    return load_contract("docs/contract-proposals/crypto_perps_top100_orderflow_1h_h72_v1.yaml")


@pytest.mark.parametrize("module", [binance_ats_h72, binance_ntr_h72])
def test_original_wrong_horizon_fails_closed_before_data(module, proposed) -> None:
    old_contract = replace(proposed, label_horizon_bars=4)
    with pytest.raises(ValueError, match="Wrong contract"):
        module.generate_positions(pl.DataFrame(), old_contract)
    with pytest.raises(ValueError, match="Unreviewed"):
        module.generate_positions(pl.DataFrame(), proposed, new_filter=True)


def test_modified_execution_costs_and_param_override_fail_closed(proposed) -> None:
    with pytest.raises(ValueError, match="Wrong contract"):
        warden_output(pl.DataFrame(), replace(proposed, slippage_bps=0.1), "ats")
    with pytest.raises(ValueError, match="Wrong contract"):
        warden_output(pl.DataFrame(), proposed, "ats", top_k=20)


@pytest.mark.parametrize(
    "kind,hypothesis,mod",
    [
        ("ats", "HYP-ORDERFLOW-ATS-H72-20261102", "binance_ats_h72.py"),
        ("ntr", "HYP-ORDERFLOW-NTR-H72-20261102", "binance_ntr_h72.py"),
    ],
)
def test_predeclared_modules_match_future_experiment_spec(kind, hypothesis, mod) -> None:
    repo = Path(__file__).resolve().parents[1]
    path = repo / f"docs/contract-proposals/binance_{kind}_h72_experiment.yaml"
    exp = yaml.safe_load(path.read_text())
    prereg = yaml.safe_load(
        (repo / f"docs/contract-proposals/{hypothesis}.yaml").read_text()
    )
    assert exp["contract_id"] == prereg["contract_id"]
    assert exp["hypothesis_id"] == prereg["hypothesis_id"]
    assert exp["strategy_module"] == prereg["strategy_module"]
    assert exp["strategy_module"].endswith(mod)
    assert exp["parameters"] == {
        "alpha": 10.0, "rebalance_every": 72, "top_k": 5,
    }
    assert not (repo / "contracts/crypto_perps_top100_orderflow_1h_h72_v1.yaml").exists()
