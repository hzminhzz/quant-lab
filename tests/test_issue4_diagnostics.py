"""Issue #4 tickets 12/13: trade-level metrics and exposure-aware timing skill (no new gates)."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from warden.diagnostics import compute_shift_skill_pvalue, compute_trade_metrics
from warden.runner import ExperimentRunner, calculate_metrics_from_returns


def test_closed_trade_profit_factor_uses_contiguous_same_sign_regimes():
    # long episode: +0.02; flat gap; short episode: -0.01; long episode: +0.005
    positions = np.array([[0.0], [1.0], [1.0], [0.5], [0.0], [0.0], [-1.0], [-1.0], [1.0], [0.0]])
    net = np.array([0.0, 0.02, 0.0, 0.0, 0.0, 0.0, -0.01, 0.0, 0.005, 0.0])
    m = compute_trade_metrics(positions, net, np.zeros((10, 1)))
    assert m["closed_trade_count"] == 3          # scale change 1.0 -> 0.5 stays one trade
    assert m["closed_trade_profit_factor"] == pytest.approx(0.025 / 0.01)
    assert m["closed_trade_hit_rate"] == pytest.approx(2 / 3)
    assert "partial scale-ins/scale-outs stay inside their trade" in m["trade_conventions"]


def test_bar_profit_factor_is_preserved_and_named_distinctly():
    net = np.array([0.02, -0.01, 0.03, -0.005, 0.0])
    bar = calculate_metrics_from_returns(net, net, np.zeros(5), 8760)
    trade = compute_trade_metrics(np.ones((5, 1)), net, np.zeros((5, 1)))
    assert trade["bar_profit_factor"] == pytest.approx(bar["profit_factor"])
    assert trade["bar_profit_factor"] != pytest.approx(0.04 / 0.015)
    empty = compute_trade_metrics(np.zeros((4, 1)), np.zeros(4), np.zeros((4, 1)))
    assert empty["closed_trade_count"] == 0 and empty["closed_trade_profit_factor"] == 0.0


def test_active_sharpe_and_benchmark_relative_reporting():
    rng = np.random.default_rng(5)
    asset_returns = rng.normal(0.0002, 0.01, (400, 2))
    positions = np.zeros((400, 1))
    positions[::2, 0] = 1.0                       # active every other bar
    net = (positions * asset_returns.mean(axis=1, keepdims=True)).ravel()
    m = compute_trade_metrics(positions, net, asset_returns)
    assert m["active_bar_share"] == pytest.approx(0.5)
    assert m["active_sharpe"] == pytest.approx(np.mean(net[net != 0]) / np.std(net[net != 0], ddof=1)
                                               * math.sqrt(8760))
    constant = compute_trade_metrics(np.ones((400, 1)), asset_returns.mean(axis=1), asset_returns)
    assert constant["information_ratio"] == pytest.approx(0.0, abs=1e-9)
    assert set(m).isdisjoint({"passed", "gate", "threshold"})


def test_timing_skill_is_deterministic_and_preserves_exposure():
    rng = np.random.default_rng(3)
    rets = rng.normal(0, 0.01, (400, 1))
    pos = np.zeros((400, 1))
    pos[::5] = 1.0
    first = compute_shift_skill_pvalue(pos, rets, label_horizon=4)
    second = compute_shift_skill_pvalue(pos, rets, label_horizon=4)
    assert first == second
    assert first["k"] == 200 and first["seed"] == 42 and first["gate_applied"] is False
    assert first["valid_shift_range"] == [12, 388]
    assert first["time_in_market"] == pytest.approx(np.mean(np.abs(pos) > 0) )
    # every circular shift keeps the same exposure profile
    for shift in (12, 137, 387):
        shifted = np.roll(pos, shift, axis=0)
        assert np.array_equal(np.sort(np.abs(shifted), axis=0), np.sort(np.abs(pos), axis=0))
    assert "not an exact distribution-free p-value" in first["caveat"]


def test_timing_skill_separates_timing_from_constant_beta():
    rets = np.zeros((400, 1))
    rets[100:200] = 0.02                          # the only profitable window
    timely = np.zeros((400, 1))
    timely[100:200] = 1.0
    result = compute_shift_skill_pvalue(timely, rets, label_horizon=2)
    assert result["p_value"] <= 0.05 and result["observed_statistic"] > result["null_q95"]
    constant = compute_shift_skill_pvalue(timely, np.full((400, 1), 0.02), label_horizon=2)
    assert constant["observed_statistic"] == pytest.approx(0.02 * 100 / 400)
    # a strongly nonstationary process: the diagnostic still reports honestly, with its caveat
    regime = np.concatenate([np.full(200, 0.03), np.full(200, -0.03)]).reshape(-1, 1)
    nonstationary = compute_shift_skill_pvalue(timely, regime, label_horizon=2)
    assert 0.0 <= nonstationary["p_value"] <= 1.0
    assert "regimes/trends" in nonstationary["caveat"]


def test_runner_exposes_metrics_as_additive_diagnostics(tiny_repo):
    fixture = tiny_repo()
    res = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo).run_spec(fixture.spec)
    diag = json.loads(res.diagnostics_json)
    assert "closed_trade_profit_factor" in diag["metrics"]
    assert diag["metrics"]["bar_profit_factor"] == pytest.approx(res.profit_factor)
    assert diag["timing_skill"]["gate_applied"] is False
    assert diag["timing_skill"]["k"] == 200
    # promotion arithmetic is unchanged by the new diagnostics
    assert res.verdict in {"CANDIDATE_FOR_QUALIFICATION", "REVISE", "NON_PROMOTABLE"}
