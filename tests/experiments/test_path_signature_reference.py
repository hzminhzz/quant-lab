"""SIG-01: deterministic identity, permutation and convention tests for path signatures."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from warden.strategy_loader import load_module_from_path

_sig = load_module_from_path(Path("strategies/candidates/path_signature_common.py"))
SIGNAL_NAMES = _sig.SIGNAL_NAMES
bruteforce_iterated = _sig.bruteforce_iterated
depth2_signature = _sig.depth2_signature
levy_area = _sig.levy_area
source_signals = _sig.source_signals
standardize_path = _sig.standardize_path
strict_pair_signature = _sig.strict_pair_signature


def _random_path(seed: int, n: int = 40) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 1.0, size=(n, 2))
    path = np.cumsum(steps, axis=0)
    return path[:, 0], path[:, 1]


def test_shuffle_identity_holds_for_random_paths():
    for seed in range(5):
        x, y = _random_path(seed)
        s = depth2_signature(x, y)
        assert s["S12"] + s["S21"] == pytest.approx((x[-1] - x[0]) * (y[-1] - y[0]), rel=1e-12, abs=1e-12)


def test_strict_pair_level_two_is_ordered_and_standard_diagonal_is_not():
    up_then_right = ([0.0, 1.0, 1.0], [0.0, 0.0, 1.0])
    right_then_up = ([0.0, 0.0, 1.0], [0.0, 1.0, 1.0])
    standard_a, standard_b = depth2_signature(*up_then_right), depth2_signature(*right_then_up)
    strict_a, strict_b = strict_pair_signature(*up_then_right), strict_pair_signature(*right_then_up)
    # Both conventions leave the diagonal to the endpoints; only the cross terms rotate.
    assert standard_a["S11"] == pytest.approx(standard_b["S11"]) == pytest.approx(0.5)
    assert strict_a["S11"] == pytest.approx(strict_b["S11"]) == pytest.approx(0.0)
    assert standard_a["S12"] != pytest.approx(standard_b["S12"])
    assert strict_a["S12"] != pytest.approx(strict_b["S12"])


def test_permuting_increments_changes_cross_terms_not_diagonals():
    rng = np.random.default_rng(21)
    steps = rng.normal(0.0, 1.0, size=(12, 2))
    base_x, base_y = np.concatenate(([0.0], np.cumsum(steps[:, 0]))), np.concatenate(([0.0], np.cumsum(steps[:, 1])))
    order = rng.permutation(len(steps))
    perm_x = np.concatenate(([0.0], np.cumsum(steps[order, 0])))
    perm_y = np.concatenate(([0.0], np.cumsum(steps[order, 1])))
    for convention in (depth2_signature, strict_pair_signature):
        a, b = convention(base_x, base_y), convention(perm_x, perm_y)
        assert a["S11"] == pytest.approx(b["S11"], rel=1e-12, abs=1e-12)
        assert a["S22"] == pytest.approx(b["S22"], rel=1e-12, abs=1e-12)
        assert a["S12"] != pytest.approx(b["S12"])


def test_strict_diagonal_equals_half_of_dispersion_identity():
    x, y = _random_path(9)
    comps = strict_pair_signature(x, y)
    dx, dy = np.diff(x), np.diff(y)
    assert comps["S11"] == pytest.approx(0.5 * (dx.sum() ** 2 - np.sum(dx**2)), rel=1e-12, abs=1e-12)
    assert comps["S22"] == pytest.approx(0.5 * (dy.sum() ** 2 - np.sum(dy**2)), rel=1e-12, abs=1e-12)


def test_refinement_invariance_inserting_collinear_points():
    x, y = _random_path(11, n=12)
    refined_x, refined_y = [], []
    for i in range(len(x) - 1):
        refined_x.extend([x[i], 0.5 * (x[i] + x[i + 1])])
        refined_y.extend([y[i], 0.5 * (y[i] + y[i + 1])])
    refined_x.append(x[-1])
    refined_y.append(y[-1])
    coarse, fine = depth2_signature(x, y), depth2_signature(refined_x, refined_y)
    for key in coarse:
        assert fine[key] == pytest.approx(coarse[key], rel=1e-12, abs=1e-12)


def test_matches_independent_riemann_integration():
    for seed in range(3):
        x, y = _random_path(100 + seed, n=8)
        closed = depth2_signature(x, y)
        numeric = bruteforce_iterated(x, y, substeps=400)
        for key in ("S11", "S12", "S21", "S22"):
            assert closed[key] == pytest.approx(numeric[key], rel=1e-9, abs=1e-9)


def test_straight_path_has_zero_levy_area():
    x, y = np.linspace(0.0, 2.0, 9), np.linspace(0.0, 3.0, 9)
    s = depth2_signature(x, y)
    assert levy_area(x, y) == pytest.approx(0.0, abs=1e-12)
    assert s["S12"] == pytest.approx(s["S21"])
    assert s["S12"] == pytest.approx(0.5 * 2.0 * 3.0)


def test_minus_s21_equals_half_area_minus_endpoint_product():
    for seed in range(5):
        x, y = _random_path(seed)
        s = depth2_signature(x, y)
        endpoint = (x[-1] - x[0]) * (y[-1] - y[0])
        assert -s["S21"] == pytest.approx(0.5 * (levy_area(x, y) - endpoint), rel=1e-12, abs=1e-12)


def test_strict_pair_convention_differs_from_standard():
    x, y = _random_path(3)
    standard, strict = depth2_signature(x, y), strict_pair_signature(x, y)
    assert strict["S12"] != pytest.approx(standard["S12"])
    assert (strict["S12"] + strict["S21"]) == pytest.approx(
        (x[-1] - x[0]) * (y[-1] - y[0]) - np.sum(np.diff(x) * np.diff(y)), rel=1e-12, abs=1e-12)


def test_flat_range_dimension_produces_no_nan():
    x = np.linspace(0.0, 1.0, 6)
    y = np.zeros(6)
    s = depth2_signature(x, y)
    assert all(np.isfinite(v) for v in s.values())
    assert s["S22"] == 0.0 and s["S12"] == 0.0 and s["S21"] == 0.0


def test_source_signals_carry_the_appendix_signs():
    x, y = _random_path(4)
    signals = source_signals(x, y)
    comps = strict_pair_signature(x, y)
    assert tuple(signals) == SIGNAL_NAMES
    assert signals["Sig-Reversion"] == pytest.approx(-(x[-1] - x[0]))
    assert signals["Sig-VolDrift"] == pytest.approx(y[-1] - y[0])
    assert signals["Sig-Accel"] == pytest.approx(comps["S11"])
    assert signals["Sig-LevyArea"] == pytest.approx(comps["S12"] - comps["S21"])
    assert signals["Sig-FadeRetVol"] == pytest.approx(-comps["S12"])
    assert signals["Sig-FadeVolRet"] == pytest.approx(-comps["S21"])
    assert signals["Sig-VolAccel"] == pytest.approx(comps["S22"])
    with pytest.raises(ValueError):
        source_signals(x, y, convention="reversed")


def test_path_standardization_keeps_displacement_and_scales_each_dimension():
    t = np.arange(48.0)
    path = np.column_stack((0.001 * t + 0.0002 * np.sin(t), 0.01 + 0.002 * np.cos(t)))
    scaled = standardize_path(path)
    assert scaled[:, 0].std() == pytest.approx(1.0, abs=1e-12)
    assert scaled[:, 1].std() == pytest.approx(1.0, abs=1e-12)
    # Displacement survives: standardising increments would have zeroed it.
    assert abs(scaled[-1, 0] - scaled[0, 0]) > 0.1


def test_translating_the_path_leaves_the_signature_unchanged():
    x, y = _random_path(17)
    for shift_x, shift_y in ((5.0, -3.0), (0.01, 0.0), (-1e3, 1e3)):
        moved = (x + shift_x, y + shift_y)
        for fn in (depth2_signature, strict_pair_signature):
            base, shifted = fn(x, y), fn(*moved)
            for key in base:
                assert shifted[key] == pytest.approx(base[key], rel=1e-9, abs=1e-9)
        for name, value in source_signals(x, y).items():
            assert source_signals(*moved)[name] == pytest.approx(value, rel=1e-9, abs=1e-9)
    # ...and so does the path standardisation itself, which is why no centering choice exists.
    path, moved_path = np.column_stack((x, y)), np.column_stack((x + 5.0, y - 3.0))
    assert np.allclose(standardize_path(path), standardize_path(moved_path))


def test_increment_standardization_would_erase_the_endpoint_term():
    steps = np.array([0.001, 0.002, -0.001, 0.004])
    raw = np.concatenate(([0.0], np.cumsum(steps)))
    centred_steps = steps - steps.mean()
    zeroed = np.concatenate(([0.0], np.cumsum(centred_steps)))
    assert zeroed[-1] == pytest.approx(0.0, abs=1e-15)
    assert abs(raw[-1]) > 0.0
    # The endpoint product is what -S(2,1) is half of, so this is a signal-destroying bug.
    assert abs(source_signals(raw, raw)["Sig-FadeVolRet"]) > 1e-6


def test_degenerate_window_is_finite():
    scaled = standardize_path(np.column_stack((np.full(5, 0.004), np.zeros(5))))
    assert np.isfinite(scaled).all()
