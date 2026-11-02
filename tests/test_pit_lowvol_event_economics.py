"""PIT low-vol event-funded economic preview: exact clock and source receipts."""

import hashlib
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from scripts.preflight_pit_lowvol_event_economics import (
    bridge_month_exits,
    exact_hour_matrix,
    observed_portfolio_returns,
)


def sample():
    first = datetime(2024, 8, 1, 0, tzinfo=UTC)
    hours = [first + timedelta(hours=i) for i in range(4)]
    assets = ["BTCUSDT", "EOSUSDT"]
    panel = pl.DataFrame({
        "timestamp": hours + [hours[0], hours[2], hours[3]],
        "asset": ["BTCUSDT"] * 4 + ["EOSUSDT"] * 3,
        "open": [100., 101., 102., 103., 10., 12., 13.],
    }).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    return panel, hours, assets


def source(tmp_path, hours):
    rel = "symbol=EOSUSDT/year=2024/month=8/data.parquet"
    raw = tmp_path / rel
    raw.parent.mkdir(parents=True)
    pl.DataFrame({
        "timestamp": hours,
        "symbol": ["EOSUSDT"] * 4,
        "open": [10., 11., 12., 13.],
        "volume": [1000., 1000., 1000., 1000.],
    }).write_parquet(raw)
    sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    receipt = {
        "status": "READ_ONLY_EXECUTION_SOURCE_BRIDGE_NOT_STAGE3",
        "no_holdout_access": True,
        "exit_coverage": {
            "monthly_position_boundary_pairs": 1,
            "transition_evidence": [{
                "status": "SOURCE_OBSERVED_NEXT_OPEN",
                "actual_fill_verified": False,
                "asset": "EOSUSDT",
                "decision": (hours[0] - timedelta(hours=1)).isoformat(),
                "required_exit_hour": hours[0].isoformat(),
                "relative_source": rel,
                "source_digest": sha,
                "observed_next_open": 10.0,
            }],
        },
    }
    return raw, receipt


def test_original_matrix_has_null_for_missing_selected_asset_hour() -> None:
    panel, hours, assets = sample()
    matrix = exact_hour_matrix(panel, hours, assets)
    assert matrix.shape == (4, 2)
    assert np.isnan(matrix[1, 1])
    assert matrix[3, 1] == 13.0


def test_bridge_only_adds_raw_sha_verified_missing_hour(tmp_path) -> None:
    panel, hours, assets = sample()
    raw, receipt = source(tmp_path, hours)
    base = exact_hour_matrix(panel, hours, assets)
    linked, audit = bridge_month_exits(base, hours, assets, receipt, tmp_path)
    assert linked[1, 1] == 11.
    assert audit["bridged_missing_asset_hour_opens"] == 1
    assert audit["source_verified_transition_count"] == 1
    assert audit["fills_observed"] is False
    assert np.isnan(base[1, 1])
    # Versioned raw source hash is mandatory even when its schema looks fine.
    raw.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="source missing or changed"):
        bridge_month_exits(base, hours, assets, receipt, tmp_path)


def test_exact_hourly_portfolio_costs_and_return_accounting(tmp_path) -> None:
    panel, hours, assets = sample()
    _, receipt = source(tmp_path, hours)
    original = exact_hour_matrix(panel, hours, assets)
    linked, _ = bridge_month_exits(original, hours, assets, receipt, tmp_path)
    held = np.array([
        [0., .5],
        [0., .5],
        [0., 0.],
        [0., 0.],
    ])
    result = observed_portfolio_returns(
        held, linked, validation_start=0, validation_stop=4,
        per_side_cost=0.0006,
    )
    # EOS 10->11 and 11->12, no returns after position is closed.
    expected = np.array([.05, .5 * (12. / 11. - 1), 0., 0.])
    assert np.allclose(result["gross"], expected)
    assert result["turnover"].tolist() == [.5, 0., .5, 0.]
    assert np.allclose(result["execution_fees"], [.0003, 0., .0003, 0.])
    assert result["coverage"]["terminal_position_gross"] == 0


def test_no_future_source_bridge_no_pnl(tmp_path) -> None:
    panel, _, assets = sample()
    hours = sorted(panel["timestamp"].unique().to_list())
    base = exact_hour_matrix(panel, hours, assets)
    held = np.array([[0., .5], [0., .5], [0., 0.], [0., 0.]])
    with pytest.raises(ValueError, match="Active position lacks exact"):
        observed_portfolio_returns(
            held, base, validation_start=0, validation_stop=4,
            per_side_cost=0.0006,
        )


def test_end_position_must_be_flat_for_protected_cutoff(tmp_path) -> None:
    panel, hours, assets = sample()
    _, receipt = source(tmp_path, hours)
    linked, _ = bridge_month_exits(
        exact_hour_matrix(panel, hours, assets), hours, assets, receipt, tmp_path
    )
    held = np.array([[0., .5], [0., .5], [0., .5], [0., .5]])
    with pytest.raises(ValueError, match="exact current/next open|Terminal position"):
        observed_portfolio_returns(
            held, linked, validation_start=0, validation_stop=4,
            per_side_cost=0.0006,
        )


def test_future_month_receipt_is_not_accepted(tmp_path) -> None:
    panel, hours, assets = sample()
    _, receipt = source(tmp_path, hours)
    receipt["exit_coverage"]["transition_evidence"][0]["required_exit_hour"] = (
        "2025-07-01T00:00:00+00:00"
    )
    with pytest.raises(ValueError, match="Wrong monthly bridge"):
        bridge_month_exits(
            exact_hour_matrix(panel, hours, assets), hours, assets, receipt, tmp_path
        )


def test_invalid_frozen_receipt_fails_closed(tmp_path) -> None:
    panel, hours, assets = sample()
    _, receipt = source(tmp_path, hours)
    receipt["exit_coverage"]["transition_evidence"][0]["actual_fill_verified"] = True
    with pytest.raises(ValueError, match="invalid fill claim"):
        bridge_month_exits(
            exact_hour_matrix(panel, hours, assets), hours, assets, receipt, tmp_path
        )
