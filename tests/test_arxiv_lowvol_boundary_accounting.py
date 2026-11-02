"""Adversarial controls: boundary-only positions and missing-market diagnostics."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from scripts.audit_lowvol_execution_integrity import classify_execution_gaps
from scripts.evaluate_lowvol_terminal_close import terminal_flat_targets


def test_terminal_flat_never_modifies_prior_rebalance_decisions() -> None:
    p = np.tile([0.1, -0.1], (100, 1))
    for delay in (1, 2, 3):
        result = terminal_flat_targets(p, val_start=20, val_end=90, execution_delay=delay)
        np.testing.assert_array_equal(result[:90-delay-1], p[:90-delay-1])
        assert np.all(result[90-delay-1:90] == 0)
        np.testing.assert_array_equal(result[90:], p[90:])
        # Entry/exit at the final observable price is possible.
        pos_after_execution = result[90-delay-1]
        assert np.sum(np.abs(pos_after_execution)) == 0
    np.testing.assert_array_equal(p, np.tile([0.1, -0.1], (100, 1)))


def test_fail_closed_on_invalid_terminal_bounds() -> None:
    with pytest.raises(ValueError):
        terminal_flat_targets(np.ones((10,2)), val_start=0, val_end=10, execution_delay=0)
    with pytest.raises(ValueError):
        terminal_flat_targets(np.ones((10,2)), val_start=9, val_end=10, execution_delay=2)


def test_classifies_only_last_bar_as_boundary() -> None:
    ts = [datetime(2025,6,30,20,tzinfo=UTC) + timedelta(hours=i) for i in range(4)]
    p = np.array([[0.5,-0.5]]*4)
    opens = np.ones_like(p)
    result = classify_execution_gaps(opens, p, ts, ["BTCUSDT","ETHUSDT"], start=0, stop=4)
    assert result["total"] == 2
    assert result["counts"] == {"terminal_no_next_exploratory_open": 2}
    assert result["gross_exposure_by_reason"]["terminal_no_next_exploratory_open"] == 1.0


def test_detects_true_interior_missing_next_price() -> None:
    ts = [datetime(2025,6,30,20,tzinfo=UTC) + timedelta(hours=i) for i in range(4)]
    p = np.array([[0.5,0.5]]*4)
    opens = np.ones_like(p)
    opens[2,1] = np.nan
    result = classify_execution_gaps(opens, p, ts, ["BTCUSDT","ETHUSDT"], start=0, stop=4)
    assert result["counts"]["missing_next_open"] == 1
    assert result["counts"]["missing_current_open"] == 1
    assert result["counts"]["terminal_no_next_exploratory_open"] == 2


def test_detects_time_gap_and_never_uses_later_price_as_next_hour() -> None:
    ts = [datetime(2025,6,30,20,tzinfo=UTC) + timedelta(hours=i) for i in [0,1,3,4]]
    p = np.ones((4,1))*0.3
    result = classify_execution_gaps(np.ones_like(p),p,ts,["BTCUSDT"],start=0,stop=4)
    assert result["counts"] == {
        "nonconsecutive_global_hour": 1,
        "terminal_no_next_exploratory_open": 1,
    }
