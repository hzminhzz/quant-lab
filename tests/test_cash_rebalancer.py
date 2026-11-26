"""Cash rebalance behavior at Warden's real Engine and verify seams."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl
import pytest
import yaml
from ml4t.backtest import Strategy

from warden.cli import main
from warden.contracts import EvaluationContract, HypothesisSpec
from warden.engine_sim import run_engine_strategy
from warden.registry import ResearchRegistry

START = datetime(2025, 1, 1, tzinfo=UTC)


def _panel(opens=None, *, missing=(), n_bars=24):
    opens = opens or {}
    return pl.DataFrame(
        [
            {
                "timestamp": START + timedelta(hours=t),
                "asset": asset,
                "open": (opening := opens.get((t, asset), 100.0)),
                "high": max(101.0, opening),
                "low": min(99.0, opening),
                "close": 100.0,
                "volume": 100_000.0,
            }
            for t in range(n_bars)
            for asset in ("AAA", "BBB")
            if (t, asset) not in missing
        ]
    )


def _contract(**overrides):
    fields: dict[str, Any] = {
        "contract_id": "CASH_TEST",
        "asset_class": "indices",
        "frequency": "1h",
        "bars_per_year": 252 * 7,
        "exploratory_data_path": "data/exploratory/cash.parquet",
        "holdout_data_path": "data/holdout/never-read.parquet",
        "train_start": START,
        "val_start": START + timedelta(hours=10),
        "val_end": START + timedelta(days=1),
        "holdout_start": START + timedelta(days=1),
        "holdout_end": START + timedelta(days=2),
        "n_folds": 2,
        "label_horizon_bars": 2,
        "embargo_bars": 1,
        "execution_delay_bars": 1,
        "taker_fee_bps": 10.0,
        "slippage_bps": 10.0,
        "data_provenance": "unknown",
        "spec_version": 2,
        "selection_group_id": "cash_test",
        "allow_leverage": False,
        "max_gross_leverage": 0.8,
    }
    return EvaluationContract.from_dict(fields | overrides)


def _run(panel, strategy, contract):
    ts = panel["timestamp"].unique().sort().to_list()
    return run_engine_strategy(
        panel,
        strategy,
        ts,
        ["AAA", "BBB"],
        contract,
        slice(0, len(ts)),
        initial_cash=10_000,
    ).backtest_result


class _Targets(Strategy):
    def __init__(self, contract, targets, **settings):
        self.contract = contract
        self.targets = targets
        self.settings = settings

    def on_data(self, timestamp, data, context, broker):
        from warden.rebalancer import rebalance_cash_weights

        t = int((timestamp - START) / timedelta(hours=1))
        if t in self.targets:
            rebalance_cash_weights(broker, self.contract, self.targets[t], **self.settings)

    def on_end(self, broker):
        self.orders = broker.get_orders()


def test_target_gross_uses_contract_cap_with_headroom_before_next_open():
    contract = _contract()
    result = _run(
        _panel({(2, "AAA"): 108.0, (2, "BBB"): 106.0}),
        _Targets(contract, {1: {"BBB": 0.5, "AAA": 1.5}}, headroom=0.10),
        contract,
    )

    assert not result.rejected_orders
    assert [(fill.timestamp, fill.asset) for fill in result.fills] == [
        (START + timedelta(hours=2), "AAA"),
        (START + timedelta(hours=2), "BBB"),
    ]
    # 0.8 cap less 10% headroom, split 3:1 at the decision close of $100.
    assert [fill.quantity for fill in result.fills] == pytest.approx([54.0, 18.0])
    assert [fill.price for fill in result.fills] == pytest.approx([108.108, 106.106])
    assert all(gross / equity <= 0.8 for _, equity, _, gross, _, _ in result.portfolio_state)


@pytest.mark.parametrize("remaining", [{}, {"BBB": 0.1}], ids=["dropped", "reduced"])
def test_rotation_submits_sells_before_buys(remaining):
    contract = _contract()
    strategy = _Targets(contract, {1: {"BBB": 1.0}, 3: {"AAA": 0.60, **remaining}}, headroom=0.10)
    result = _run(_panel({(4, "BBB"): 94.0, (4, "AAA"): 106.0}), strategy, contract)

    assert not result.rejected_orders
    assert [(order.asset, order.side.value) for order in strategy.orders] == [
        ("BBB", "buy"),
        ("BBB", "sell"),
        ("AAA", "buy"),
    ]
    assert [(fill.asset, fill.side.value) for fill in result.fills] == [
        ("BBB", "buy"),
        ("BBB", "sell"),
        ("AAA", "buy"),
    ]
    assert result.fills[1].quantity == pytest.approx(62.0144072 if remaining else 72.0)


def test_dust_does_not_trade_but_a_zero_target_still_flattens():
    contract = _contract()
    strategy = _Targets(
        contract,
        {1: {"AAA": 0.02}, 3: {"AAA": 0.021, "BBB": 0.005}, 5: {"AAA": 0.0}},
        min_trade_value=100.0,
        min_weight_change=0.002,
    )
    result = _run(_panel(), strategy, contract)

    assert not result.rejected_orders
    assert [(fill.asset, fill.side.value, fill.quantity) for fill in result.fills] == [
        ("AAA", "buy", 2.0),
        ("AAA", "sell", 2.0),
    ]


def test_missing_held_asset_does_not_fund_replacement_before_its_exit():
    contract = _contract()
    strategy = _Targets(contract, {1: {"BBB": 1.0}, 3: {"AAA": 1.0}, 6: {"AAA": 1.0}})
    result = _run(_panel(missing={(3, "BBB"), (4, "BBB")}), strategy, contract)

    assert not result.rejected_orders
    assert [(fill.asset, fill.side.value) for fill in result.fills] == [
        ("BBB", "buy"),
        ("BBB", "sell"),
        ("AAA", "buy"),
    ]
    assert result.fills[-1].timestamp == START + timedelta(hours=7)


def test_untraded_dust_reduction_does_not_release_gross_budget():
    contract = _contract()
    strategy = _Targets(
        contract,
        {1: {"BBB": 0.60}, 3: {"AAA": 0.70, "BBB": 0.02}},
        headroom=0.10,
        min_trade_value=5_900.0,
    )
    result = _run(_panel(), strategy, contract)

    # The $5.8k reduction was suppressed; the account cannot also put $7k into AAA.
    assert [(fill.asset, fill.side.value) for fill in result.fills] == [("BBB", "buy")]
    assert not result.rejected_orders


_VERIFY_STRATEGY = """\
from ml4t.backtest import Strategy
from warden.rebalancer import rebalance_cash_weights

def required_symbols(params):
    return ['AAA', 'BBB']

class Rotation(Strategy):
    def __init__(self, contract):
        self.contract = contract

    def on_data(self, timestamp, data, context, broker):
        targets = {9: {'BBB': 1.0}, 10: {'AAA': 1.0}, 11: {}}
        if timestamp.hour in targets:
            rebalance_cash_weights(broker, self.contract, targets[timestamp.hour])

def configure_strategy(contract, params):
    return Rotation(contract)
"""


@pytest.mark.parametrize("buy_open,sell_open", [(108.0, 94.0), (95.0, 105.0)])
def test_warden_verify_accepts_cash_rotation_across_open_gaps(
    buy_open,
    sell_open,
    tmp_path,
    monkeypatch,
    capsys,
):
    panel = _panel({(10, "BBB"): buy_open, (11, "AAA"): buy_open, (11, "BBB"): sell_open})
    monkeypatch.chdir(tmp_path)
    # This exercises the verify CLI and all its execution checks, not worktree signing.
    monkeypatch.setattr("warden.cli.check_worktree", lambda: True)
    data_path = tmp_path / "data/exploratory/cash.parquet"
    data_path.parent.mkdir(parents=True)
    panel.write_parquet(data_path)
    sha = hashlib.sha256(data_path.read_bytes()).hexdigest()
    contract = _contract(data_provenance="synthetic", data_snapshot_sha256=sha)
    contracts = tmp_path / "contracts"
    (contracts / "datasets").mkdir(parents=True)
    (contracts / "cash_test.yaml").write_text(yaml.safe_dump(contract.to_dict()))
    (contracts / "datasets/CASH_TEST.json").write_text(
        json.dumps(
            {
                "contract_id": contract.contract_id,
                "path": contract.exploratory_data_path,
                "sha256": sha,
                "provenance": "synthetic",
                "source": "pytest fixture",
            }
        )
    )
    registry = ResearchRegistry(tmp_path / "registry/registry.db")
    registry.register_contract(contract)
    registry.register_hypothesis(
        HypothesisSpec(
            "HYP-CASH-TEST",
            contract.contract_id,
            "Cash rotation",
            "rules_ts",
            "Synthetic rotation for verifying the order lifecycle",
            "Synthetic costs deducted from the executed fills",
            1,
            "Reject if the next-bar Engine rejects any cash order",
        )
    )
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    module = strategies / "rotation.py"
    module.write_text(_VERIFY_STRATEGY)
    spec = tmp_path / "rotation.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "contract_id": contract.contract_id,
                "hypothesis_id": "HYP-CASH-TEST",
                "strategy_name": "Cash rotation",
                "strategy_family": "rules_ts",
                "model_type": "rules",
                "strategy_module": "strategies/rotation.py",
                "parameters": {},
            }
        )
    )

    # A control that uses the same feed, policy and strategy but submits raw weights.
    start = "rebalance_cash_weights(broker, self.contract, targets[timestamp.hour])"
    raw = "for asset, weight in targets[timestamp.hour].items():\n                broker.order_target_percent(asset, weight)"
    module.write_text(_VERIFY_STRATEGY.replace(start, raw))
    assert main(["verify", str(spec), "--json"]) == 1
    assert "order(s) rejected for insufficient cash/margin" in capsys.readouterr().err

    module.write_text(_VERIFY_STRATEGY)
    rc = main(["verify", str(spec), "--json"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["status"] == "PASS"
    assert payload["data_provenance"] == "synthetic"
    assert payload["market_eligible"] is False
    assert payload["causal_evidence_key"]
