"""Regression test for open-to-open forward label basis alignment against runner.simulate_positions."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract, utc
from warden.leakage import compute_open_forward_label
from warden.runner import _aligned_matrix, load_module_from_path, simulate_positions

amihud_positions = load_module_from_path(Path("strategies/candidates/cs_amihud_1h.py")).generate_positions
bab_positions = load_module_from_path(Path("strategies/candidates/cs_bab_1h.py")).generate_positions
lgbm_positions = load_module_from_path(Path("strategies/candidates/cs_lgbm_1h.py")).generate_positions


def _make_gapped_panel(n_bars: int = 50, n_assets: int = 3):
    assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    base_time = datetime(2025, 1, 1, tzinfo=UTC)

    rows = []
    rng = np.random.default_rng(42)

    for a_idx, asset in enumerate(assets):
        price = 100.0 * (a_idx + 1)
        for t in range(n_bars):
            ts = base_time + timedelta(hours=t)
            gap = float(rng.normal(0, 0.5))
            o = price + gap
            h = o + float(abs(rng.normal(0, 1.0))) + 0.1
            low_val = o - float(abs(rng.normal(0, 1.0))) - 0.1
            c = float(rng.uniform(low_val, h))
            v = float(rng.uniform(100, 1000))
            rows.append({
                "timestamp": ts,
                "asset": asset,
                "open": o,
                "high": h,
                "low": low_val,
                "close": c,
                "volume": v,
                "funding_rate": 0.0001,
            })
            price = c

    return pl.DataFrame(rows).sort("timestamp", "asset"), assets


def test_gapped_ohlc_forward_label_alignment():
    panel_df, assets = _make_gapped_panel(50, 3)
    n_bars, n_assets = 50, len(assets)

    for delay in [1, 2, 3]:
        for horizon in [2, 4, 8]:
            contract = EvaluationContract(
                contract_id="TEST_GAP_V1",
                asset_class="crypto_perps",
                frequency="1h",
                bars_per_year=8760,
                exploratory_data_path="data/exploratory/test.parquet",
                holdout_data_path="data/holdout/test.parquet",
                train_start=utc("2025-01-01T00:00:00Z"),
                val_start=utc("2025-01-01T10:00:00Z"),
                val_end=utc("2025-01-02T10:00:00Z"),
                holdout_start=utc("2025-01-02T10:00:00Z"),
                holdout_end=utc("2025-01-03T10:00:00Z"),
                n_folds=2,
                label_horizon_bars=horizon,
                embargo_bars=1,
                execution_delay_bars=delay,
                taker_fee_bps=1.0,
                slippage_bps=1.0,
                funding_bps_per_8h=0.0,
                execution_mode="NEXT_BAR",
                min_expected_move_to_cost_ratio=2.5,
                data_snapshot_sha256="",
            )

            ret_df = panel_df.with_columns(
                ret=(pl.col("open").shift(-1).over("asset") / pl.col("open") - 1.0).fill_null(0.0)
            )
            ret_p = _aligned_matrix(ret_df, "ret", assets)
            open_pivot = panel_df.pivot(index="timestamp", on="asset", values="open").select(assets).to_numpy()

            for decision_bar in [5, 10, 15]:
                entry_open = open_pivot[decision_bar + delay]
                exit_open = open_pivot[decision_bar + delay + horizon]
                direct_open_to_open = (exit_open - entry_open) / entry_open

                for a_i in range(n_assets):
                    positions = np.zeros((n_bars, n_assets), dtype=float)
                    positions[decision_bar : decision_bar + horizon, a_i] = 1.0

                    v_slice = slice(0, n_bars)
                    sim = simulate_positions(positions, ret_p, None, contract, v_slice, cost_multiplier=0.0)

                    start_bar = decision_bar + delay
                    active_pnl = sim["gross_returns"][start_bar : start_bar + horizon]
                    compound_runner_ret = np.prod(1.0 + active_pnl) - 1.0

                    np.testing.assert_allclose(
                        compound_runner_ret,
                        direct_open_to_open[a_i],
                        rtol=1e-10,
                        atol=1e-10,
                        err_msg=f"Mismatch for asset {assets[a_i]} at delay={delay}, h={horizon}, bar={decision_bar}",
                    )


def test_candidate_strategy_labels_match_open_to_open():
    """Verify compute_open_forward_label helper and exercise actual candidate generate_positions."""
    panel_df, assets = _make_gapped_panel(220, 3)
    contract = EvaluationContract(
        contract_id="TEST_GAP_V2",
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="data/exploratory/test.parquet",
        holdout_data_path="data/holdout/test.parquet",
        train_start=utc("2025-01-01T00:00:00Z"),
        val_start=utc("2025-01-05T00:00:00Z"),
        val_end=utc("2025-01-09T00:00:00Z"),
        holdout_start=utc("2025-01-09T00:00:00Z"),
        holdout_end=utc("2025-01-10T00:00:00Z"),
        n_folds=2,
        label_horizon_bars=4,
        embargo_bars=1,
        execution_delay_bars=2,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
        funding_bps_per_8h=0.0,
        execution_mode="NEXT_BAR",
        min_expected_move_to_cost_ratio=2.5,
        data_snapshot_sha256="",
    )

    open_mat = panel_df.pivot(index="timestamp", on="asset", values="open").select(assets).to_numpy()
    close_mat = panel_df.pivot(index="timestamp", on="asset", values="close").select(assets).to_numpy()

    # 1. Assert gapped data is distinct between open and close
    assert not np.allclose(open_mat, close_mat), "Panel must have gapped open/close prices"

    # 2. Test compute_open_forward_label helper against direct formula across all valid bars
    delay = contract.execution_delay_bars
    h = contract.label_horizon_bars
    labels = compute_open_forward_label(open_mat, delay, h)

    for t in range(len(open_mat) - delay - h):
        e_open = open_mat[t + delay]
        x_open = open_mat[t + delay + h]
        expected_raw = (x_open - e_open) / e_open
        expected_demeaned = expected_raw - np.mean(expected_raw)
        np.testing.assert_allclose(labels[t], expected_demeaned, rtol=1e-10, atol=1e-10)

    # 3. Test end boundary behavior: no IndexError, and trailing invalid bars are 0.0
    for t in range(len(open_mat) - delay - h, len(open_mat)):
        np.testing.assert_allclose(labels[t], np.zeros(len(assets)), atol=1e-12)

    # 4. Exercise actual candidate generate_positions on the gapped panel
    out_amihud = amihud_positions(panel_df, contract, alpha=10.0)
    assert "positions" in out_amihud and "ic_series" in out_amihud
    assert len(out_amihud["positions"]) == len(panel_df.unique("timestamp"))

    out_bab = bab_positions(panel_df, contract, alpha=10.0)
    assert "positions" in out_bab and "ic_series" in out_bab
    assert len(out_bab["positions"]) == len(panel_df.unique("timestamp"))

    out_lgbm = lgbm_positions(panel_df, contract)
    assert "positions" in out_lgbm and "ic_series" in out_lgbm
    assert len(out_lgbm["positions"]) == len(panel_df.unique("timestamp"))


def test_delayed_label_purging_fold_separation():
    """Verify that when delay > embargo, the full information span (delay + h) prevents test fold leakage."""
    from warden.leakage import purged_walk_forward_splits

    n_timestamps = 500
    min_train = 200
    n_folds = 3
    delay = 3
    h = 4
    embargo = 1

    # 1. With corrected span (delay + h):
    full_span = delay + h
    corrected_splits = purged_walk_forward_splits(
        n_timestamps=n_timestamps,
        n_folds=n_folds,
        min_train_timestamps=min_train,
        label_horizon=full_span,
        embargo=embargo,
    )

    for fold, (train_idx, test_idx) in enumerate(corrected_splits):
        if len(train_idx) == 0:
            continue
        last_train_bar = train_idx[-1]
        # Training label at last_train_bar exits at last_train_bar + delay + h
        train_label_exit = last_train_bar + delay + h
        test_start = test_idx[0]
        # Assert strict separation: training label exits strictly before test fold start
        assert train_label_exit < test_start, (
            f"Fold {fold}: training label exit ({train_label_exit}) leaks into test fold start ({test_start})"
        )
        assert test_start - train_label_exit >= embargo + 1

    # 2. Counter-example proof: if uncorrected (purging by h only, ignoring delay):
    leaking_splits = purged_walk_forward_splits(
        n_timestamps=n_timestamps,
        n_folds=n_folds,
        min_train_timestamps=min_train,
        label_horizon=h,
        embargo=embargo,
    )
    leaks_found = 0
    for train_idx, test_idx in leaking_splits:
        if len(train_idx) == 0:
            continue
        # With delay=3, the uncorrected last train bar label exits at train_idx[-1] + delay + h
        uncorrected_exit = train_idx[-1] + delay + h
        if uncorrected_exit >= test_idx[0]:
            leaks_found += 1
    assert leaks_found > 0, "Counter-example must prove uncorrected horizon leaks when delay > embargo"

    # 3. Exercise actual candidate modules on contract where delay > embargo
    panel_df, _ = _make_gapped_panel(250, 3)
    contract_high_delay = EvaluationContract(
        contract_id="TEST_HIGH_DELAY_V1",
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="data/exploratory/test.parquet",
        holdout_data_path="data/holdout/test.parquet",
        train_start=utc("2025-01-01T00:00:00Z"),
        val_start=utc("2025-01-05T00:00:00Z"),
        val_end=utc("2025-01-09T00:00:00Z"),
        holdout_start=utc("2025-01-09T00:00:00Z"),
        holdout_end=utc("2025-01-10T00:00:00Z"),
        n_folds=2,
        label_horizon_bars=h,
        embargo_bars=embargo,
        execution_delay_bars=delay,  # delay (3) > embargo (1)
        taker_fee_bps=1.0,
        slippage_bps=1.0,
        funding_bps_per_8h=0.0,
        execution_mode="NEXT_BAR",
        min_expected_move_to_cost_ratio=2.5,
        data_snapshot_sha256="",
    )

    out_ami = amihud_positions(panel_df, contract_high_delay, alpha=10.0)
    out_bab = bab_positions(panel_df, contract_high_delay, alpha=10.0)
    out_lgb = lgbm_positions(panel_df, contract_high_delay)

    assert len(out_ami["positions"]) == len(panel_df.unique("timestamp"))
    assert len(out_bab["positions"]) == len(panel_df.unique("timestamp"))
    assert len(out_lgb["positions"]) == len(panel_df.unique("timestamp"))
