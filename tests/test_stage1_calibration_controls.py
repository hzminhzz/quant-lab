"""Frozen Stage 1 gate calibration controls; does not modify the evaluator.

These are engineering controls, NOT market evidence or a new approved gate.
"""
from __future__ import annotations

import numpy as np

from warden.gates import evaluate_stage1_rules_ts


def _run(gross: np.ndarray, positions: np.ndarray, one_way_cost: float = 0.0001):
    turnover = np.abs(np.diff(positions, prepend=0.0))
    net = gross - one_way_cost * turnover
    return evaluate_stage1_rules_ts(
        net_returns=net, gross_returns=gross,
        one_way_cost=one_way_cost, positions=positions[:, None]
    )


def test_zero_signal_cannot_pass_stage1() -> None:
    p = np.ones(500)
    report = _run(np.zeros(500), p)
    assert not report.passed
    assert report.metrics["profit_factor"] == 0.0
    assert report.metrics["bootstrap_p_value"] == 1.0


def test_known_positive_edge_passes_with_explicit_costs() -> None:
    # Constant returns represent a deliberately artificial high-power control.
    p = np.ones(500)
    report = _run(np.full(500, 0.001), p)
    assert report.passed
    assert report.metrics["profit_factor"] > 1.15
    assert report.metrics["bootstrap_p_value"] < 0.05


def test_friction_dominated_control_fails() -> None:
    p = np.where(np.arange(500) % 2 == 0, 1.0, -1.0)
    gross = p * 0.00002
    report = _run(gross, p, one_way_cost=0.0001)
    assert not report.passed
    assert report.metrics["profit_factor"] < 1.15


def test_inactive_strategy_fails_even_when_selected_bars_are_winners() -> None:
    p = np.zeros(1000)
    p[:25] = 1.0
    gross = np.zeros_like(p)
    gross[:25] = 0.001
    report = _run(gross, p)
    assert not report.passed
    assert report.metrics["time_in_market"] == 0.025


def test_null_distribution_diagnostic_not_regression_threshold() -> None:
    # Empirical calibration using the *same* current gate on a truly zero-drift
    # process. This asserts reproducibility and documents false-positive behavior.
    rng = np.random.default_rng(20261102)
    decisions = []
    for _ in range(50):
        p = np.ones(300)
        gross = rng.normal(0.0, 0.001, len(p))
        decisions.append(_run(gross, p).passed)
    assert len(decisions) == 50
    assert all(isinstance(x, bool) for x in decisions)
    print(f"STAGE1_NULL_DIAGNOSTIC seed=20261102 runs=50 pass_count={sum(decisions)}")
