"""Illustrative Stage 1 bar-frequency distortion, without policy changes."""

import math

import numpy as np

from scripts.audit_stage1_pf_frequency import expected_bar_profit_factor, implied_sharpe
from warden.gates import evaluate_stage1_rules_ts


def test_same_annual_sharpe_has_different_bar_profit_factors() -> None:
    daily = expected_bar_profit_factor(2.0, 252)
    hourly = expected_bar_profit_factor(2.0, 8760)
    assert daily > 1.15
    assert hourly < 1.15
    assert hourly < daily
    assert expected_bar_profit_factor(0.0, 8760) == 1.0


def test_implied_hurdle_increases_with_observation_frequency() -> None:
    daily = implied_sharpe(1.15, 252)
    hourly = implied_sharpe(1.15, 8760)
    quarter_hour = implied_sharpe(1.15, 35040)
    assert 0.5 < daily < 2.0
    assert hourly > 4.5
    assert quarter_hour > hourly
    assert math.isclose(expected_bar_profit_factor(hourly, 8760), 1.15, rel_tol=1e-10)


def test_real_stage1_can_reject_known_positive_sharpe_only_on_bar_pf() -> None:
    # Two years of synthetic iid hourly returns; 2.0 is the population
    # annualized Sharpe, not a claim about observed market performance.
    rng = np.random.default_rng(20261102)
    bars = 2 * 8760
    sigma = 0.01
    mu = sigma * 2.0 / math.sqrt(8760)
    gross = rng.normal(mu, sigma, bars)
    positions = np.ones((bars, 1))
    net = gross.copy()
    net[0] -= 0.0006
    report = evaluate_stage1_rules_ts(net, gross, one_way_cost=0.0006, positions=positions)
    observed_sharpe = np.mean(net) / np.std(net, ddof=1) * math.sqrt(8760)
    assert observed_sharpe > 1.0
    assert report.metrics["profit_factor"] < 1.15
    assert not report.passed
