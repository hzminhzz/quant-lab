"""Tests for Ticket 07 Slice 1: Stage 1 statistical gates & Engine-free speed cutover."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml
from ml4t.diagnostic.metrics import compute_ic_hac_stats

from warden.contracts import EvaluationContract, HypothesisSpec
from warden.gates import (
    compute_cross_sectional_ic_for_screen,
    evaluate_stage1_cross_sectional_ml,
    evaluate_stage1_rules_ts,
)
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner


def _build_stage1_repo(
    tmp_path: Path,
    *,
    contract_id: str,
    panel_df: pl.DataFrame,
    strategy_family: str,
    strategy_name: str,
    strategy_source: str,
    expected_holding_bars: int,
    val_start: datetime,
    val_end: datetime,
    holdout_start: datetime,
    holdout_end: datetime,
    taker_fee_bps: float = 1.0,
    slippage_bps: float = 1.0,
    label_horizon_bars: int = 4,
) -> tuple[Path, ResearchRegistry, Path]:
    repo = tmp_path / contract_id.lower()
    data_rel = f"data/exploratory/{contract_id.lower()}.parquet"
    data_path = repo / data_rel
    data_path.parent.mkdir(parents=True, exist_ok=True)
    panel_df.write_parquet(data_path)
    sha = hashlib.sha256(data_path.read_bytes()).hexdigest()

    train_start = panel_df["timestamp"].min()
    assert isinstance(train_start, datetime)
    contract = EvaluationContract(
        contract_id=contract_id,
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path=data_rel,
        holdout_data_path=f"data/holdout/{contract_id.lower()}.parquet",
        train_start=train_start,
        val_start=val_start,
        val_end=val_end,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        n_folds=4,
        label_horizon_bars=label_horizon_bars,
        embargo_bars=2,
        execution_delay_bars=1,
        taker_fee_bps=taker_fee_bps,
        slippage_bps=slippage_bps,
        data_snapshot_sha256=sha,
        data_provenance="verified_market",
    )
    manifest = repo / "contracts" / "datasets" / f"{contract_id}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "contract_id": contract_id,
                "path": data_rel,
                "sha256": sha,
                "provenance": "verified_market",
                "source": "synthetic_t07",
            }
        ),
        encoding="utf-8",
    )
    (repo / "contracts" / f"{contract_id.lower()}.yaml").write_text(
        yaml.safe_dump(contract.__dict__), encoding="utf-8"
    )
    (repo / "strategies").mkdir(parents=True, exist_ok=True)
    strat_rel = f"strategies/{strategy_name}.py"
    (repo / strat_rel).write_text(strategy_source, encoding="utf-8")

    reg = ResearchRegistry(tmp_path / f"{contract_id.lower()}_registry.db")
    reg.register_contract(contract)
    hyp = HypothesisSpec(
        hypothesis_id=f"HYP-{contract_id}",
        contract_id=contract_id,
        title=f"Ticket 07 test {contract_id}",
        strategy_family=strategy_family,
        economic_mechanism="Synthetic mechanism explanation with sufficient characters",
        counterparty_or_friction="Synthetic friction explanation with sufficient characters",
        expected_holding_bars=expected_holding_bars,
        falsification_criteria="Reject if Stage 1 falsification gate fails",
    )
    reg.register_hypothesis(hyp)

    spec_path = repo / "spec.yaml"
    spec_path.write_text(
        yaml.safe_dump(
            {
                "contract_id": contract_id,
                "hypothesis_id": hyp.hypothesis_id,
                "strategy_name": strategy_name,
                "strategy_family": strategy_family,
                "model_type": "rules" if strategy_family == "rules_ts" else "ridge",
                "strategy_module": strat_rel,
                "parameters": {},
            }
        ),
        encoding="utf-8",
    )
    return repo, reg, spec_path


def _make_single_asset_panel_from_returns(
    bar_returns: np.ndarray, *, start: datetime = datetime(2025, 1, 1, tzinfo=UTC)
) -> pl.DataFrame:
    n = len(bar_returns) + 1
    prices = np.empty(n, dtype=float)
    prices[0] = 100.0
    for i, r in enumerate(bar_returns):
        prices[i + 1] = prices[i] * (1.0 + float(r))
    ts = pl.datetime_range(start, start + timedelta(hours=n - 1), "1h", eager=True)
    return pl.DataFrame(
        {
            "timestamp": ts,
            "asset": ["BTC"] * n,
            "open": prices,
            "high": prices * 1.002,
            "low": prices * 0.998,
            "close": prices,
            "volume": np.full(n, 1000.0),
            "funding_rate": np.zeros(n),
        }
    )


def test_falsifier_1a_hourly_trend_carry_rescue(tmp_path: Path) -> None:
    """1h trend/carry with Sharpe ~ 2.0 and bar PF ~ 1.06 < 1.15 passes via closed-trade/horizon PF >= 1.15."""
    # Pattern per 10-bar block: nine +12 bps bars and one -99 bps bar.
    # Gross bar sum = +9 bps/10 bars; bar PF ~ 1.09 -> after 2 bps round-trip per 50-bar trade, bar PF ~ 1.06 < 1.15.
    # Dropping top 5% bars leaves positive drift -> trimmed_sharpe > 0, annualized Sharpe ~ 2.0.
    n_train = 200
    n_val = 2000
    block = np.array([0.0012] * 9 + [-0.0099], dtype=float)
    val_rets = np.tile(block, n_val // len(block))
    train_rets = np.full(n_train, 0.0005, dtype=float)
    all_rets = np.concatenate([train_rets, val_rets])
    panel_df = _make_single_asset_panel_from_returns(all_rets)

    # Strategy enters 40-bar long trades (20 closed trades across validation) with 10-bar flat gaps.
    strategy_src = """
import numpy as np

def generate_positions(panel_df, contract, **kwargs):
    n_ts = panel_df["timestamp"].n_unique()
    pos = np.zeros((n_ts, 1), dtype=float)
    # 50-bar cycle: 40 bars long, 10 bars flat (starts inside val)
    for t in range(n_ts):
        if (t % 50) < 40:
            pos[t, 0] = 1.0
    return {"positions": pos}
"""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    val_start = start + timedelta(hours=n_train)
    val_end = start + timedelta(hours=n_train + n_val)
    holdout_end = val_end + timedelta(hours=24)

    repo, reg, spec_path = _build_stage1_repo(
        tmp_path,
        contract_id="T07_FALSIFIER_1A",
        panel_df=panel_df,
        strategy_family="rules_ts",
        strategy_name="trend_carry_1h",
        strategy_source=strategy_src,
        expected_holding_bars=40,
        val_start=val_start,
        val_end=val_end,
        holdout_start=val_end,
        holdout_end=holdout_end,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )
    runner = ExperimentRunner(registry=reg, repo_root=repo, allow_legacy_replay=True)
    report = runner.run_stage1_screen(spec_path)

    assert report.strategy_family == "rules_ts"
    assert report.metrics["bar_profit_factor"] < 1.15
    assert report.metrics["profit_factor"] >= 1.15
    assert report.metrics["pf_basis"] == "closed_trade"
    assert report.metrics["trimmed_sharpe"] is not None
    assert report.metrics["trimmed_sharpe"] > 0.0
    assert report.passed is True

    # Always-in-market carry (< 2 closed episodes) uses holding_horizon PF and passes when horizon PF >= 1.15
    always_in_pos = np.ones((n_val, 1), dtype=float)
    always_in_rep = evaluate_stage1_rules_ts(
        net_returns=val_rets,
        gross_returns=val_rets,
        one_way_cost=0.0002,
        positions=always_in_pos,
        bars_per_year=8760,
        holding_horizon_bars=40,
    )
    assert always_in_rep.metrics["pf_basis"] == "holding_horizon"
    assert always_in_rep.metrics["bar_profit_factor"] < 1.15
    assert always_in_rep.metrics["profit_factor"] >= 1.15
    assert always_in_rep.passed is True


def test_falsifier_1b_outlier_trap_rejected_at_stage1(tmp_path: Path) -> None:
    """Strategy whose positive Sharpe comes solely from top-5% outlier bars MUST reject in Stage 1."""
    n_train = 100
    n_val = 400
    rng = np.random.default_rng(123)
    val_rets = np.full(n_val, -0.0004, dtype=float) + rng.normal(0.0, 0.00005, n_val)
    # Top 5% bars (20 bars) carry huge positive spikes so raw Sharpe & PF look great, trimmed_sharpe <= 0
    val_rets[-20:] = 0.025
    all_rets = np.concatenate([np.zeros(n_train), val_rets])
    panel_df = _make_single_asset_panel_from_returns(all_rets)

    strategy_src = """
import numpy as np

def generate_positions(panel_df, contract, **kwargs):
    n_ts = panel_df["timestamp"].n_unique()
    return {"positions": np.ones((n_ts, 1), dtype=float)}
"""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    val_start = start + timedelta(hours=n_train)
    val_end = start + timedelta(hours=n_train + n_val)
    holdout_end = val_end + timedelta(hours=24)

    repo, reg, spec_path = _build_stage1_repo(
        tmp_path,
        contract_id="T07_FALSIFIER_1B",
        panel_df=panel_df,
        strategy_family="rules_ts",
        strategy_name="outlier_trap_1h",
        strategy_source=strategy_src,
        expected_holding_bars=24,
        val_start=val_start,
        val_end=val_end,
        holdout_start=val_end,
        holdout_end=holdout_end,
        taker_fee_bps=0.5,
        slippage_bps=0.5,
    )
    runner = ExperimentRunner(registry=reg, repo_root=repo, allow_legacy_replay=True)
    report = runner.run_stage1_screen(spec_path)

    assert report.passed is False
    assert report.metrics["trimmed_sharpe"] is not None
    assert report.metrics["trimmed_sharpe"] <= 0.0

    with sqlite3.connect(tmp_path / "t07_falsifier_1b_registry.db") as conn:
        row = conn.execute(
            "SELECT outcome, diagnostics_json FROM selection_attempts ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row[0] == "REJECT"
    diag = json.loads(row[1])
    assert diag["trimmed_sharpe"] <= 0.0


def test_falsifier_2_stage1_100_assets_20k_bars_sub5s_and_engine_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage 1 screen on 100 assets x 20,000 bars completes in < 5.0s and never instantiates Engine."""
    import ml4t.backtest

    def _forbid_engine(*args, **kwargs):
        raise AssertionError("ml4t.backtest.Engine must NOT be instantiated during Stage 1 screen")

    monkeypatch.setattr(ml4t.backtest.Engine, "__init__", _forbid_engine)

    n_ts, n_assets = 20_000, 100
    start = datetime(2024, 1, 1, tzinfo=UTC)
    ts_s = pl.datetime_range(start, start + timedelta(hours=n_ts - 1), "1h", eager=True)
    assets_s = pl.Series("asset", [f"A{j:03d}" for j in range(n_assets)])
    grid = pl.DataFrame({"timestamp": ts_s}).join(pl.DataFrame({"asset": assets_s}), how="cross")

    # Vectorized realistic drifting prices with non-zero volatility to clear Stage 0
    rng = np.random.default_rng(7)
    log_rets = 0.0001 + rng.normal(0.0, 0.002, size=(n_ts, n_assets))
    px = (100.0 * np.exp(np.cumsum(log_rets, axis=0))).ravel()
    panel_df = grid.with_columns(
        pl.Series("open", px),
        pl.Series("high", px * 1.005),
        pl.Series("low", px * 0.995),
        pl.Series("close", px * 1.001),
        pl.Series("volume", np.full(n_ts * n_assets, 1000.0)),
        pl.Series("funding_rate", np.zeros(n_ts * n_assets)),
    )

    strategy_src = """
import numpy as np

def generate_positions(panel_df, contract, **kwargs):
    n_ts = panel_df["timestamp"].n_unique()
    n_assets = panel_df["asset"].n_unique()
    pos = np.full((n_ts, n_assets), 1.0 / n_assets, dtype=float)
    return {"positions": pos}
"""
    val_start = start + timedelta(hours=5_000)
    val_end = start + timedelta(hours=19_900)
    holdout_end = start + timedelta(hours=20_000)

    repo, reg, spec_path = _build_stage1_repo(
        tmp_path,
        contract_id="T07_FALSIFIER_2",
        panel_df=panel_df,
        strategy_family="rules_ts",
        strategy_name="fast_panel_1h",
        strategy_source=strategy_src,
        expected_holding_bars=24,
        val_start=val_start,
        val_end=val_end,
        holdout_start=val_end,
        holdout_end=holdout_end,
    )
    runner = ExperimentRunner(registry=reg, repo_root=repo, allow_legacy_replay=True)

    t0 = time.perf_counter()
    report = runner.run_stage1_screen(spec_path)
    elapsed = time.perf_counter() - t0

    assert elapsed < 5.0, f"Stage 1 screen took {elapsed:.2f}s (>= 5.0s)"
    assert report.strategy_family == "rules_ts"
    assert "profit_factor" in report.metrics


def test_cross_sectional_ml_stage1_uses_ml4t_ic_hac_and_predictions_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both evaluate_stage1_cross_sectional_ml and run_stage1_screen use ml4t.diagnostic.metrics IC/HAC."""
    # (a) Unit check against ml4t.diagnostic.metrics.compute_ic_hac_stats
    rng = np.random.default_rng(7)
    ic_arr = 0.04 + rng.normal(0.0, 0.015, 60)
    expected = compute_ic_hac_stats(
        ic_arr, label_horizon=4, kernel="bartlett", allow_naive_fallback=True
    )
    rep = evaluate_stage1_cross_sectional_ml(ic_arr, label_horizon=4)
    assert rep.passed is True
    assert rep.metrics["mean_ic"] == pytest.approx(expected["mean_ic"])
    assert rep.metrics["ic_hac_tstat"] == pytest.approx(expected["t_stat"])
    assert rep.metrics["ic_hac_p_value"] == pytest.approx(expected["p_value"])
    assert rep.metrics["effective_lags"] == expected["effective_lags"]

    # Constant IC series should fail cleanly without NaN
    const_rep = evaluate_stage1_cross_sectional_ml(np.full(30, 0.05), label_horizon=4)
    assert const_rep.passed is False
    assert const_rep.metrics["ic_hac_tstat"] == 0.0

    # (b) End-to-end run_stage1_screen with predictions + forward_returns DataFrames
    n_ts, n_assets = 80, 12
    start = datetime(2025, 1, 1, tzinfo=UTC)
    ts_s = pl.datetime_range(start, start + timedelta(hours=n_ts - 1), "1h", eager=True)
    assets_s = pl.Series("asset", [f"S{j:02d}" for j in range(n_assets)])
    grid = pl.DataFrame({"timestamp": ts_s}).join(pl.DataFrame({"asset": assets_s}), how="cross")
    rng_px = np.random.default_rng(11)
    px_ml = (100.0 * np.exp(np.cumsum(rng_px.normal(0.0, 0.003, size=(n_ts, n_assets)), axis=0))).ravel()
    panel_df = grid.with_columns(
        pl.Series("open", px_ml),
        pl.Series("high", px_ml * 1.005),
        pl.Series("low", px_ml * 0.995),
        pl.Series("close", px_ml),
        pl.Series("volume", np.full(n_ts * n_assets, 100.0)),
        pl.Series("funding_rate", np.zeros(n_ts * n_assets)),
    )

    strategy_src = """
import numpy as np
import polars as pl

def generate_positions(panel_df, contract, **kwargs):
    rng = np.random.default_rng(42)
    base = panel_df.select("timestamp", "asset").sort("timestamp", "asset")
    n = base.height
    signal = rng.normal(0.0, 1.0, n)
    fwd = 0.3 * signal + rng.normal(0.0, 0.5, n)
    pred_df = base.with_columns(pl.Series("prediction", signal))
    ret_df = base.with_columns(pl.Series("forward_return", fwd))
    n_ts = panel_df["timestamp"].n_unique()
    n_assets = panel_df["asset"].n_unique()
    return {
        "positions": np.zeros((n_ts, n_assets), dtype=float),
        "predictions": pred_df,
        "forward_returns": ret_df,
    }
"""
    val_start = start + timedelta(hours=20)
    val_end = start + timedelta(hours=70)
    holdout_end = start + timedelta(hours=80)

    repo, reg, spec_path = _build_stage1_repo(
        tmp_path,
        contract_id="T07_CS_ML",
        panel_df=panel_df,
        strategy_family="cross_sectional_ml",
        strategy_name="cs_ml_ic_screen",
        strategy_source=strategy_src,
        expected_holding_bars=4,
        val_start=val_start,
        val_end=val_end,
        holdout_start=val_end,
        holdout_end=holdout_end,
        label_horizon_bars=4,
    )
    seen_horizons: list[int | None] = []
    orig_eval_cs = evaluate_stage1_cross_sectional_ml

    def _spy_eval_cs(ic_series, **kwargs):
        seen_horizons.append(kwargs.get("label_horizon"))
        return orig_eval_cs(ic_series, **kwargs)

    monkeypatch.setattr("warden.runner.evaluate_stage1_cross_sectional_ml", _spy_eval_cs)
    runner = ExperimentRunner(registry=reg, repo_root=repo, allow_legacy_replay=True)
    report = runner.run_stage1_screen(spec_path)

    assert seen_horizons == [4 + 1]
    assert report.strategy_family == "cross_sectional_ml"
    assert report.passed is True
    assert report.metrics["mean_ic"] > 0.1
    assert report.metrics["ic_hac_tstat"] > 2.0
    assert "ic_hac_p_value" in report.metrics
    assert "effective_lags" in report.metrics
    assert callable(compute_cross_sectional_ic_for_screen)
    assert callable(evaluate_stage1_rules_ts)


# ---------------------------------------------------------------------------
# Ticket 07 Slice 2: Canonical Engine Simulation & Diagnostics
# ---------------------------------------------------------------------------


def _make_multi_freq_perp_panel(
    freq: str,
    *,
    n_warmup: int = 2,
    hours_val: int = 24,
    funding_rate: float = 0.001,
) -> tuple[pl.DataFrame, list[datetime], list[str], EvaluationContract, slice]:
    delta_map = {
        "5m": timedelta(minutes=5),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
    }
    bars_per_year_map = {"5m": 105120, "1h": 8760, "4h": 2190, "1d": 365}
    step = delta_map[freq]
    if freq == "1d":
        n_val = max(3, hours_val // 24)
    else:
        n_val = int(timedelta(hours=hours_val) // step)
    val_start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    train_start = val_start - n_warmup * step
    val_end = val_start + n_val * step
    holdout_end = val_end + 2 * step
    total_bars = n_warmup + n_val + 1

    ts_list = [train_start + i * step for i in range(total_bars)]
    panel_df = pl.DataFrame(
        {
            "timestamp": ts_list,
            "asset": ["BTC"] * total_bars,
            "open": [100.0] * total_bars,
            "high": [101.0] * total_bars,
            "low": [99.0] * total_bars,
            "close": [100.0] * total_bars,
            "volume": [10_000.0] * total_bars,
            "funding_rate": [funding_rate] * total_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))

    contract = EvaluationContract(
        contract_id=f"T07_FUND_{freq.upper()}",
        asset_class="crypto_perps",
        frequency=freq,
        bars_per_year=bars_per_year_map[freq],
        exploratory_data_path=f"data/exploratory/t07_fund_{freq}.parquet",
        holdout_data_path=f"data/holdout/t07_fund_{freq}.parquet",
        train_start=train_start,
        val_start=val_start,
        val_end=val_end,
        holdout_start=val_end,
        holdout_end=holdout_end,
        n_folds=4,
        label_horizon_bars=1,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )
    v_slice = slice(n_warmup, n_warmup + n_val)
    return panel_df, ts_list, ["BTC"], contract, v_slice


def test_slice2_contracts_epoch_constants() -> None:
    from warden.contracts import DEFAULT_SIMULATION_EPOCH, ENGINE_SIMULATION_EPOCH

    assert DEFAULT_SIMULATION_EPOCH == "legacy_matrix"
    assert ENGINE_SIMULATION_EPOCH == "ml4t_backtest_engine_v1"


@pytest.mark.parametrize(
    ("freq", "hours_val", "expected_val_settlements"),
    [
        ("5m", 24, 3),
        ("1h", 24, 3),
        ("4h", 24, 3),
        ("1d", 72, 3),
    ],
)
def test_slice2_funding_settlement_multi_frequency(
    freq: str, hours_val: int, expected_val_settlements: int
) -> None:
    from ml4t.backtest import BacktestConfig, DataFeed, Engine, Strategy
    from ml4t.backtest.config import (
        CommissionType,
        ExecutionMode,
        ExecutionPrice,
        ShareType,
        SlippageType,
    )

    from warden.engine_sim import build_funding_df, run_engine_simulation

    panel_df, ts_list, assets, contract, v_slice = _make_multi_freq_perp_panel(
        freq, n_warmup=2, hours_val=hours_val, funding_rate=0.001
    )
    val_df = panel_df.filter(
        (pl.col("timestamp") >= ts_list[v_slice.start])
        & (pl.col("timestamp") < ts_list[v_slice.stop])
    )
    fdf = build_funding_df(val_df, contract)
    assert fdf is not None
    assert fdf.height == expected_val_settlements
    if freq != "1d":
        for row_ts in fdf["timestamp"].to_list():
            assert row_ts.hour % 8 == 0
            assert row_ts.minute == 0
            assert row_ts.second == 0

    raw_pos = np.full((len(ts_list), 1), 0.5, dtype=float)
    sim = run_engine_simulation(
        panel_df,
        raw_pos,
        ts_list,
        assets,
        contract,
        v_slice,
        cost_multiplier=1.0,
        initial_cash=1_000_000.0,
        allow_leverage=False,
    )
    val_payments = [p for p in sim.backtest_result.funding_payments if p.timestamp >= ts_list[v_slice.start]]
    assert len(val_payments) == expected_val_settlements
    assert sum(p.cash_delta for p in val_payments) < 0.0
    assert all(p.cash_delta < 0.0 for p in val_payments[1:])

    # Exact parity against direct ml4t.backtest.Engine with the same feed/funding
    feed_df = panel_df.filter(
        (pl.col("timestamp") >= ts_list[v_slice.start - 1])
        & (pl.col("timestamp") <= ts_list[v_slice.stop - 1])
    ).sort("timestamp", "asset")
    feed_fdf = build_funding_df(feed_df, contract)

    class _OnceHalfStrat(Strategy):
        def on_data(self, timestamp, data, context, broker):
            if timestamp == ts_list[v_slice.start - 1]:
                # Match TargetWeightExecutor sizing (gross weight 0.5 at initial_cash=1_000_000)
                broker.submit_order("BTC", 5000.0)

    direct_cfg = BacktestConfig(
        initial_cash=1_000_000.0,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.OPEN,
        mark_price=ExecutionPrice.CLOSE,
        share_type=ShareType.FRACTIONAL,
        commission_type=CommissionType.PERCENTAGE,
        commission_rate=1e-4,
        slippage_type=SlippageType.PERCENTAGE,
        slippage_rate=1e-4,
    )
    direct_res = Engine(DataFeed(prices_df=feed_df), _OnceHalfStrat(), direct_cfg, funding_df=feed_fdf).run()
    direct_val_payments = [p for p in direct_res.funding_payments if p.timestamp >= ts_list[v_slice.start]]
    assert len(val_payments) == len(direct_val_payments)
    for sim_p, dir_p in zip(val_payments, direct_val_payments, strict=True):
        assert sim_p.timestamp == dir_p.timestamp
        assert sim_p.cash_delta == pytest.approx(dir_p.cash_delta, rel=1e-3)


def test_slice2_leverage_and_short_selling_fail_closed() -> None:
    from warden.engine_sim import LeveragePolicyError, run_engine_simulation

    panel_df, ts_list, assets, contract, v_slice = _make_multi_freq_perp_panel("1h", n_warmup=2, hours_val=24)

    over_lev_pos = np.full((len(ts_list), 1), 1.5, dtype=float)
    with pytest.raises(LeveragePolicyError) as exc_lev:
        run_engine_simulation(
            panel_df,
            over_lev_pos,
            ts_list,
            assets,
            contract,
            v_slice,
            allow_leverage=False,
        )
    assert isinstance(exc_lev.value, ValueError)

    short_pos = np.full((len(ts_list), 1), -0.4, dtype=float)
    with pytest.raises(LeveragePolicyError) as exc_short:
        run_engine_simulation(
            panel_df,
            short_pos,
            ts_list,
            assets,
            contract,
            v_slice,
            allow_leverage=False,
            allow_short_selling=False,
        )
    assert isinstance(exc_short.value, ValueError)


def test_slice2_positions_alignment_and_prewindow_policy() -> None:
    from warden.engine_sim import LeveragePolicyError, run_engine_simulation

    start_ts = datetime(2025, 2, 1, 0, 0, tzinfo=UTC)
    n_bars = 10
    start = 2
    ts_list = [start_ts + timedelta(hours=i) for i in range(n_bars)]
    opens = np.full(n_bars, 100.0, dtype=float)
    closes = np.full(n_bars, 100.0, dtype=float)
    # Price appreciates on bars start+2 and start+3 (where NEXT_BAR holds the 2-bar signal)
    closes[start + 2] = 102.0
    opens[start + 3] = 102.0
    closes[start + 3] = 105.0
    opens[start + 4:] = 105.0
    closes[start + 4:] = 105.0

    panel_df = pl.DataFrame(
        {
            "timestamp": ts_list,
            "asset": ["BTC"] * n_bars,
            "open": opens,
            "high": np.maximum(opens, closes) + 0.5,
            "low": np.minimum(opens, closes) - 0.5,
            "close": closes,
            "volume": [10_000.0] * n_bars,
            "funding_rate": [0.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))

    contract = EvaluationContract(
        contract_id="T07_ALIGN_CHECK",
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="data/exploratory/align.parquet",
        holdout_data_path="data/holdout/align.parquet",
        train_start=ts_list[0],
        val_start=ts_list[start],
        val_end=ts_list[-1] + timedelta(hours=1),
        holdout_start=ts_list[-1] + timedelta(hours=1),
        holdout_end=ts_list[-1] + timedelta(hours=2),
        n_folds=4,
        label_horizon_bars=1,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )
    v_slice = slice(start, n_bars)

    # (a) 2-bar signal submitted at start+1 and start+2 -> held & PnL realized on start+2 and start+3
    pos_align = np.zeros((n_bars, 1), dtype=float)
    pos_align[start + 1, 0] = 0.5
    pos_align[start + 2, 0] = 0.5
    sim = run_engine_simulation(panel_df, pos_align, ts_list, ["BTC"], contract, v_slice)

    assert sim.positions[1, 0] == 0.0
    assert sim.net_returns[1] == pytest.approx(0.0, abs=1e-12)
    assert sim.positions[2, 0] == pytest.approx(0.5)
    assert sim.net_returns[2] > 0.0
    assert sim.positions[3, 0] == pytest.approx(0.5)
    assert sim.net_returns[3] > 0.0
    assert sim.positions[4, 0] == 0.0
    assert sim.gross_returns[4] == pytest.approx(0.0, abs=1e-12)
    assert np.nonzero(sim.positions[:, 0])[0].tolist() == [2, 3]
    assert np.flatnonzero(np.abs(sim.gross_returns) > 1e-12).tolist() == [2, 3]

    # (b) Pre-window submission at start-1 must fail closed against policy checks
    pre_short = np.zeros((n_bars, 1), dtype=float)
    pre_short[start - 1, 0] = -0.5
    with pytest.raises(LeveragePolicyError):
        run_engine_simulation(
            panel_df, pre_short, ts_list, ["BTC"], contract, v_slice, allow_short_selling=False
        )

    pre_lev = np.zeros((n_bars, 1), dtype=float)
    pre_lev[start - 1, 0] = 1.5
    with pytest.raises(LeveragePolicyError):
        run_engine_simulation(
            panel_df, pre_lev, ts_list, ["BTC"], contract, v_slice, allow_leverage=False
        )


def test_engine_delay_replays_matching_bar_and_fills_one_bar_later() -> None:
    """A delayed callback sees its own bar, and its order fills one bar later."""
    from ml4t.backtest import Strategy

    from warden.engine_sim import run_engine_strategy

    class _EntryOnce(Strategy):
        def __init__(self) -> None:
            self.seen: list[tuple[datetime, float]] = []
            self._submitted = False

        def on_data(self, timestamp, data, context, broker) -> None:
            self.seen.append((timestamp, float(data["BTC"]["close"])))
            if not self._submitted:
                self._submitted = True
                broker.submit_order("BTC", 1.0)

    start_ts = datetime(2025, 2, 1, 0, 0, tzinfo=UTC)
    n_bars = 12
    ts_list = [start_ts + timedelta(hours=i) for i in range(n_bars)]
    opens = [100.0 + float(i) for i in range(n_bars)]
    closes = [100.5 + float(i) for i in range(n_bars)]
    panel_df = pl.DataFrame(
        {
            "timestamp": ts_list,
            "asset": ["BTC"] * n_bars,
            "open": opens,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [10_000.0] * n_bars,
            "funding_rate": [0.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))

    contract = EvaluationContract(
        contract_id="T07_DELAY_ALIGN",
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="data/exploratory/delay.parquet",
        holdout_data_path="data/holdout/delay.parquet",
        train_start=ts_list[0] - timedelta(hours=24),
        val_start=ts_list[0],
        val_end=ts_list[-1] + timedelta(hours=1),
        holdout_start=ts_list[-1] + timedelta(hours=1),
        holdout_end=ts_list[-1] + timedelta(hours=2),
        n_folds=4,
        label_horizon_bars=1,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )
    v_slice = slice(0, n_bars)

    base_strat = _EntryOnce()
    base = run_engine_strategy(panel_df, base_strat, ts_list, ["BTC"], contract, v_slice)
    delayed_strat = _EntryOnce()
    delayed = run_engine_strategy(
        panel_df, delayed_strat, ts_list, ["BTC"], contract, v_slice, delay_offset=1
    )

    # The baseline callback and the first replayed callback both describe bar 0.
    assert base_strat.seen[0] == (ts_list[0], closes[0])
    assert delayed_strat.seen[0] == (ts_list[0], closes[0])
    # Replay keeps each timestamp paired with its own bar, in order, one bar behind.
    assert delayed_strat.seen == [(ts_list[i], closes[i]) for i in range(n_bars - 1)]

    # Orders are placed at the callback bar, so the delayed signal fills one bar later.
    base_fills = base.backtest_result.fills
    delayed_fills = delayed.backtest_result.fills
    assert len(base_fills) == 1
    assert len(delayed_fills) == 1
    assert base_fills[0].timestamp == ts_list[1]
    assert delayed_fills[0].timestamp == ts_list[2]


def test_slice2_session_date_holiday_and_clean_single_venue() -> None:
    from warden.engine_sim import run_engine_simulation

    # 1. Mixed-venue / incomplete holiday session_date + null session_date -> session_col_used is None
    bars = [
        ("ES", datetime(2025, 1, 26, 12, 0, tzinfo=UTC), None),
        ("HK33", datetime(2025, 1, 26, 12, 0, tzinfo=UTC), None),
        ("ES", datetime(2025, 1, 28, 15, 0, tzinfo=UTC), datetime(2025, 1, 28, tzinfo=UTC).date()),
        ("HK33", datetime(2025, 1, 28, 15, 0, tzinfo=UTC), datetime(2025, 1, 28, tzinfo=UTC).date()),
        # Holiday for HK33 on 2025-01-29 (ES present, HK33 absent)
        ("ES", datetime(2025, 1, 29, 15, 0, tzinfo=UTC), datetime(2025, 1, 29, tzinfo=UTC).date()),
        ("ES", datetime(2025, 1, 30, 15, 0, tzinfo=UTC), datetime(2025, 1, 30, tzinfo=UTC).date()),
        ("HK33", datetime(2025, 1, 30, 15, 0, tzinfo=UTC), datetime(2025, 1, 30, tzinfo=UTC).date()),
    ]
    mixed_df = pl.DataFrame(
        {
            "timestamp": [b[1] for b in bars],
            "asset": [b[0] for b in bars],
            "open": [100.0] * len(bars),
            "high": [101.0] * len(bars),
            "low": [99.0] * len(bars),
            "close": [100.5] * len(bars),
            "volume": [1000.0] * len(bars),
            "session_date": [b[2] for b in bars],
        }
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
        pl.col("session_date").cast(pl.Date),
    ).sort("timestamp", "asset")
    mixed_ts = mixed_df["timestamp"].unique().sort().to_list()
    mixed_assets = sorted(mixed_df["asset"].unique().to_list())
    mixed_contract = EvaluationContract(
        contract_id="T07_MIXED_SESS",
        asset_class="indices",
        frequency="1d",
        bars_per_year=252,
        exploratory_data_path="data/exploratory/mixed.parquet",
        holdout_data_path="data/holdout/mixed.parquet",
        train_start=mixed_ts[0],
        val_start=mixed_ts[1],
        val_end=mixed_ts[-1] + timedelta(days=1),
        holdout_start=mixed_ts[-1] + timedelta(days=1),
        holdout_end=mixed_ts[-1] + timedelta(days=3),
        n_folds=4,
        label_horizon_bars=1,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )
    mixed_pos = np.full((len(mixed_ts), len(mixed_assets)), 0.4, dtype=float)
    sim_mixed = run_engine_simulation(
        mixed_df,
        mixed_pos,
        mixed_ts,
        mixed_assets,
        mixed_contract,
        slice(1, len(mixed_ts)),
    )
    assert sim_mixed.session_col_used is None
    assert len(sim_mixed.net_returns) == len(mixed_ts) - 1

    # 2. Clean single-venue panel with complete session_date -> session_col_used == "session_date"
    start = datetime(2025, 2, 1, 0, 0, tzinfo=UTC)
    n_days = 8
    clean_ts = [start + timedelta(days=i) for i in range(n_days)]
    clean_df = pl.DataFrame(
        {
            "timestamp": clean_ts,
            "asset": ["SPY"] * n_days,
            "open": [500.0 + i for i in range(n_days)],
            "high": [502.0 + i for i in range(n_days)],
            "low": [499.0 + i for i in range(n_days)],
            "close": [501.0 + i for i in range(n_days)],
            "volume": [10_000.0] * n_days,
            "session_date": [t.date() for t in clean_ts],
        }
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
        pl.col("session_date").cast(pl.Date),
    )
    clean_contract = EvaluationContract(
        contract_id="T07_CLEAN_SESS",
        asset_class="indices",
        frequency="1d",
        bars_per_year=252,
        exploratory_data_path="data/exploratory/clean.parquet",
        holdout_data_path="data/holdout/clean.parquet",
        train_start=clean_ts[0],
        val_start=clean_ts[2],
        val_end=clean_ts[-1] + timedelta(days=1),
        holdout_start=clean_ts[-1] + timedelta(days=1),
        holdout_end=clean_ts[-1] + timedelta(days=3),
        n_folds=4,
        label_horizon_bars=1,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )
    clean_pos = np.full((n_days, 1), 0.5, dtype=float)
    sim_clean = run_engine_simulation(
        clean_df,
        clean_pos,
        clean_ts,
        ["SPY"],
        clean_contract,
        slice(2, n_days),
    )
    assert sim_clean.session_col_used == "session_date"


def test_slice2_microstructure_trade_analysis_pf_and_diagnostics(tmp_path: Path) -> None:
    from ml4t.diagnostic.evaluation.trade_analysis import TradeAnalysis, TradeRecord

    from warden.contracts import canonical_json
    from warden.engine_sim import (
        analytical_1p5x_sharpe,
        compute_engine_diagnostics,
        export_backtest_artifacts,
        run_engine_simulation,
    )

    n_bars = 48
    start = datetime(2025, 3, 1, 0, 0, tzinfo=UTC)
    ts_list = [start + timedelta(hours=i) for i in range(n_bars)]
    # Deterministic sawtooth: 2 winning trades and 1 losing trade when entered/exited at specific bars
    opens = [100.0] * n_bars
    # Trade 1 (entry bar 5 open=100, exit bar 9 open=106 -> win)
    for i in range(6, 10):
        opens[i] = 100.0 + (i - 5) * 1.5
    # Trade 2 (entry bar 15 open=100, exit bar 19 open=95 -> loss)
    for i in range(16, 20):
        opens[i] = 100.0 - (i - 15) * 1.25
    # Trade 3 (entry bar 25 open=100, exit bar 29 open=108 -> win)
    for i in range(26, 30):
        opens[i] = 100.0 + (i - 25) * 2.0

    closes = list(opens)
    highs = [p + 1.0 for p in opens]
    lows = [p - 1.0 for p in opens]
    panel_df = pl.DataFrame(
        {
            "timestamp": ts_list,
            "asset": ["BTC"] * n_bars,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [10_000.0] * n_bars,
            "funding_rate": [0.0] * n_bars,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))

    contract = EvaluationContract(
        contract_id="T07_DIAG",
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="data/exploratory/diag.parquet",
        holdout_data_path="data/holdout/diag.parquet",
        train_start=ts_list[0],
        val_start=ts_list[2],
        val_end=ts_list[-1] + timedelta(hours=1),
        holdout_start=ts_list[-1] + timedelta(hours=1),
        holdout_end=ts_list[-1] + timedelta(hours=5),
        n_folds=4,
        label_horizon_bars=1,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )

    raw_pos = np.zeros((n_bars, 1), dtype=float)
    raw_pos[4:8, 0] = 0.5
    raw_pos[14:18, 0] = 0.5
    raw_pos[24:28, 0] = 0.5

    v_slice = slice(2, n_bars)
    sim = run_engine_simulation(
        panel_df,
        raw_pos,
        ts_list,
        ["BTC"],
        contract,
        v_slice,
        max_volume_participation=0.25,
        market_impact_coefficient=0.1,
        market_impact_volatility=0.02,
    )

    closed_trades = [t for t in sim.backtest_result.trades if t.status == "closed"]
    assert len(closed_trades) == 3
    gross_profit = sum(t.pnl for t in closed_trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in closed_trades if t.pnl < 0))
    assert gross_profit > 0 and gross_loss > 0
    expected_pf = gross_profit / gross_loss

    recs = [
        TradeRecord(**r)
        for r in sim.backtest_result.to_trade_records()
        if r.get("status", "closed") == "closed"
    ]
    ta_stats = TradeAnalysis(recs).compute_statistics()
    assert ta_stats.profit_factor == pytest.approx(expected_pf, rel=1e-9)

    rng = np.random.default_rng(7)
    grid_mat = np.column_stack(
        [
            sim.net_returns,
            sim.net_returns + rng.normal(0.0, 0.0005, size=len(sim.net_returns)),
            sim.net_returns * 0.8 + rng.normal(0.0, 0.0005, size=len(sim.net_returns)),
        ]
    )
    diag = compute_engine_diagnostics(
        sim.backtest_result,
        sim.net_returns,
        ts_list[v_slice],
        contract,
        grid_return_matrix=grid_mat,
    )
    assert diag["trade_analysis"]["profit_factor"] == pytest.approx(expected_pf, rel=1e-9)
    assert diag["portfolio_analysis"]["sharpe_ratio"] is not None
    assert np.isfinite(diag["portfolio_analysis"]["sharpe_ratio"])
    assert diag["pbo"] is not None
    assert 0.0 <= diag["pbo"]["pbo"] <= 1.0
    assert diag["ras"] is not None
    assert np.isfinite(diag["ras"]["rademacher_complexity"])
    # Must serialize cleanly under allow_nan=False
    canonical_json(diag)

    # Analytical 1.5x Sharpe matches full 1.5x simulation closely
    sr_1p5_analyt = analytical_1p5x_sharpe(
        sim.backtest_result, contract.bars_per_year, val_ts=ts_list[v_slice]
    )
    assert np.isfinite(sr_1p5_analyt)
    # Legacy positional callers: the replay feed starts one bar before the window,
    # so the positional form resolves the same validation bars here.
    legacy = analytical_1p5x_sharpe(
        sim.backtest_result, contract.bars_per_year, start_idx=v_slice.start
    )
    assert legacy == pytest.approx(sr_1p5_analyt, abs=1e-9)

    # Artifact export writes parquet files with valid SHA-256 digests
    artifacts = export_backtest_artifacts(
        sim.backtest_result, tmp_path / "artifacts" / "run1", repo_root=tmp_path
    )
    for key in ("trades", "fills", "portfolio_state", "funding", "equity"):
        assert key in artifacts
        assert len(artifacts[key]["sha256"]) == 64
        assert (tmp_path / artifacts[key]["path"]).is_file()


# ---------------------------------------------------------------------------
# Ticket 07 Slice 3: Full backtest -> audit-check -> qualify Engine Cutover
# ---------------------------------------------------------------------------


def _make_trending_panel(
    n_bars: int,
    *,
    start: datetime = datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
) -> pl.DataFrame:
    rng = np.random.default_rng(123)
    # Positive drift with realistic noise so rules_ts clears Stage 0, Stage 1, and Stage 3 cleanly
    rets = 0.0020 + rng.normal(0.0, 0.0040, size=n_bars)
    prices = np.empty(n_bars, dtype=float)
    prices[0] = 100.0
    for i in range(1, n_bars):
        prices[i] = prices[i - 1] * (1.0 + float(rets[i]))
    ts_list = [start + timedelta(hours=i) for i in range(n_bars)]
    return pl.DataFrame(
        {
            "timestamp": ts_list,
            "asset": ["BTC"] * n_bars,
            "open": prices,
            "high": prices * 1.003,
            "low": prices * 0.997,
            "close": prices * 1.0005,
            "volume": np.full(n_bars, 10_000.0),
            "funding_rate": np.zeros(n_bars),
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))


def test_falsifier_3_full_backtest_audit_qualify_engine_parity_and_fail_closed_leverage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse
    import math

    monkeypatch.setattr("warden.cli.check_worktree", lambda: True)

    from warden.cli import cmd_audit_check
    from warden.contracts import ENGINE_SIMULATION_EPOCH
    from warden.engine_sim import (
        LeveragePolicyError,
        analytical_1p5x_sharpe,
        run_engine_simulation,
    )
    from warden.qualify import qualify_candidate
    from warden.runner import calculate_metrics_from_returns, prepare_panel

    start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    panel_df = _make_trending_panel(96, start=start)
    val_start = start + timedelta(hours=12)
    val_end = start + timedelta(hours=72)
    holdout_start = val_end
    holdout_end = start + timedelta(hours=96)

    # Toggle position every 10 bars so closed trades exist and 2-point param_grid differs
    strat_src = """
import numpy as np

def generate_positions(panel_df, contract, **kwargs):
    weight = float(kwargs.get("weight", 0.5))
    n_ts = panel_df["timestamp"].n_unique()
    n_assets = panel_df["asset"].n_unique()
    pos = np.zeros((n_ts, n_assets), dtype=float)
    for t in range(n_ts):
        if (t // 10) % 2 == 0 and t % 10 < 8:
            pos[t, :] = weight
    return {"positions": pos}
"""
    repo, reg, spec_path = _build_stage1_repo(
        tmp_path,
        contract_id="T07_FALSIFIER_3",
        panel_df=panel_df,
        strategy_family="rules_ts",
        strategy_name="trend_toggle",
        strategy_source=strat_src,
        expected_holding_bars=8,
        val_start=val_start,
        val_end=val_end,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        label_horizon_bars=1,
    )
    spec_cfg = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    spec_cfg["param_grid"] = [{"weight": 0.4}, {"weight": 0.6}]
    spec_path.write_text(yaml.safe_dump(spec_cfg), encoding="utf-8")

    runner = ExperimentRunner(registry=reg, repo_root=repo, allow_legacy_replay=True)
    res = runner.run_spec(spec_path)

    # 1. ENGINE_SIMULATION_EPOCH
    assert res.simulation_epoch == ENGINE_SIMULATION_EPOCH == "ml4t_backtest_engine_v1"

    # 2. stress_1p5_cost_sharpe & exact parity with run_engine_simulation
    assert res.stress_1p5_cost_sharpe is not None
    assert math.isfinite(res.stress_1p5_cost_sharpe)

    contract = runner.resolve_contract("T07_FALSIFIER_3")
    from warden.provenance import resolve_dataset
    from warden.strategy_loader import load_module_from_path

    dataset = resolve_dataset(contract, repo)
    p_df, ts, assets, v_slice = prepare_panel(repo, dataset, {}, contract)
    strat_mod = load_module_from_path(repo / "strategies" / "trend_toggle.py")
    raw_pos = strat_mod.generate_positions(p_df, contract)["positions"]

    sim1 = run_engine_simulation(p_df, raw_pos, ts, assets, contract, v_slice, cost_multiplier=1.0)
    m1 = calculate_metrics_from_returns(
        sim1.gross_returns, sim1.net_returns, sim1.turnover, contract.bars_per_year
    )
    exp_1p5 = analytical_1p5x_sharpe(
        sim1.backtest_result, contract.bars_per_year, val_ts=ts[v_slice]
    )
    sim15 = run_engine_simulation(
        p_df, raw_pos, ts, assets, contract, v_slice, cost_multiplier=1.5
    )
    sr15 = float(
        np.mean(sim15.net_returns)
        / (np.std(sim15.net_returns, ddof=1) + 1e-12)
        * math.sqrt(contract.bars_per_year)
    )
    sim2 = run_engine_simulation(p_df, raw_pos, ts, assets, contract, v_slice, cost_multiplier=2.0)
    sr2 = float(
        np.mean(sim2.net_returns)
        / (np.std(sim2.net_returns, ddof=1) + 1e-12)
        * math.sqrt(contract.bars_per_year)
    )
    sim_d = run_engine_simulation(p_df, raw_pos, ts, assets, contract, v_slice, delay_offset=1)
    srd = float(
        np.mean(sim_d.net_returns)
        / (np.std(sim_d.net_returns, ddof=1) + 1e-12)
        * math.sqrt(contract.bars_per_year)
    )
    assert res.net_sharpe == pytest.approx(m1["net_sharpe"], abs=1e-12)
    assert res.stress_1p5_cost_sharpe == pytest.approx(exp_1p5, abs=1e-12)
    # The analytical stress must track the full 1.5x Engine re-simulation, not itself.
    assert abs(res.stress_1p5_cost_sharpe - sr15) <= 1e-3
    assert res.stress_2x_cost_sharpe == pytest.approx(sr2, abs=1e-12)
    assert res.delay_plus1_sharpe == pytest.approx(srd, abs=1e-12)

    # 3. Parquet artifacts under <registry_dir>/backtest_results/<simulation_epoch>/<contract_id>/<run_id>/
    diag = json.loads(res.diagnostics_json)
    artifacts = diag.get("engine_artifacts", {})
    expected_dir = (
        reg.db_path.parent
        / "backtest_results"
        / ENGINE_SIMULATION_EPOCH
        / contract.contract_id
        / res.run_id
    )
    for key in ("trades", "fills", "portfolio_state", "funding", "equity"):
        assert key in artifacts
        assert artifacts[key]["simulation_epoch"] == ENGINE_SIMULATION_EPOCH
        p = Path(artifacts[key]["path"])
        if not p.is_absolute():
            p = repo / p
        assert p.resolve().parent == expected_dir.resolve()
        assert p.is_file()
        assert hashlib.sha256(p.read_bytes()).hexdigest() == artifacts[key]["sha256"]

    # 4. Diagnostics (portfolio_analysis, trade_analysis, pbo, ras) in diag & cmd_audit_check
    for key in ("portfolio_analysis", "trade_analysis", "pbo", "ras"):
        assert diag.get(key) is not None, f"missing diagnostic key {key}"

    capsys.readouterr()
    rc = cmd_audit_check(argparse.Namespace(run_id=res.run_id, json=True), runner)
    assert rc in (0, 1)
    audit_payload = json.loads(capsys.readouterr().out)
    assert audit_payload["simulation_epoch"] == ENGINE_SIMULATION_EPOCH
    for key in ("portfolio_analysis", "trade_analysis", "pbo", "ras", "engine_artifacts"):
        assert audit_payload.get(key) is not None
    audit_md = (repo / ".workspace" / "hypotheses" / f"AUDIT_{res.run_id}.md").read_text(
        encoding="utf-8"
    )
    assert ENGINE_SIMULATION_EPOCH in audit_md
    assert "Portfolio" in audit_md or "portfolio_analysis" in audit_md.lower()
    assert "Trade" in audit_md or "trade_analysis" in audit_md.lower()
    assert "PBO" in audit_md
    assert "RAS" in audit_md

    # Ensure verdict is CANDIDATE_FOR_QUALIFICATION so qualify_candidate proceeds
    assert res.verdict == "CANDIDATE_FOR_QUALIFICATION", (
        f"Expected CANDIDATE_FOR_QUALIFICATION, got {res.verdict}; failed_gates={diag.get('failed_gates')}"
    )

    # 5. Holdout qualify_candidate exact Engine parity
    holdout_panel = _make_trending_panel(72, start=holdout_start)
    holdout_path = tmp_path / "holdout_override.parquet"
    holdout_panel.write_parquet(holdout_path)

    qual_res = qualify_candidate(
        res.run_id, "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout_path
    )
    h_window = holdout_panel.filter(
        (pl.col("timestamp") >= contract.holdout_start)
        & (pl.col("timestamp") <= contract.holdout_end)
    )
    h_df = h_window if h_window.height >= 20 else holdout_panel
    h_ts = sorted(h_df["timestamp"].unique().to_list())
    h_assets = sorted(h_df["asset"].unique().to_list())
    h_pos = strat_mod.generate_positions(h_df, contract)["positions"]
    h_sim = run_engine_simulation(h_df, h_pos, h_ts, h_assets, contract, slice(0, len(h_ts)))
    h_m = calculate_metrics_from_returns(
        h_sim.gross_returns, h_sim.net_returns, h_sim.turnover, contract.bars_per_year
    )
    assert qual_res["holdout_net_sharpe"] == pytest.approx(h_m["net_sharpe"], abs=1e-12)
    assert qual_res["terminal_equity"] == pytest.approx(h_sim.terminal_equity, abs=1e-9)
    assert qual_res["engine_fills_count"] == len(h_sim.backtest_result.fills)
    assert qual_res["engine_trades_count"] == len(h_sim.backtest_result.trades)

    # 6. Fail-closed leverage (> 1.0) on run_spec and qualify_candidate
    lev_src = """
import numpy as np

def generate_positions(panel_df, contract, **kwargs):
    n_ts = panel_df["timestamp"].n_unique()
    n_assets = panel_df["asset"].n_unique()
    return {"positions": np.full((n_ts, n_assets), 1.5, dtype=float)}
"""
    repo_l, reg_l, spec_l = _build_stage1_repo(
        tmp_path / "lev_fail",
        contract_id="T07_LEV_FAIL",
        panel_df=panel_df,
        strategy_family="rules_ts",
        strategy_name="levered_bad",
        strategy_source=lev_src,
        expected_holding_bars=8,
        val_start=val_start,
        val_end=val_end,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        label_horizon_bars=1,
    )
    runner_l = ExperimentRunner(registry=reg_l, repo_root=repo_l, allow_legacy_replay=True)
    with pytest.raises(LeveragePolicyError):
        runner_l.run_spec(spec_l, override_run_id="RUN-LEV-FAIL")

    with reg_l._connect() as conn:
        lev_row = conn.execute(
            "SELECT verdict, stage_reached FROM trials WHERE run_id = ?",
            ("RUN-LEV-FAIL",),
        ).fetchone()
    assert lev_row is not None
    assert lev_row[0] == "FAILED"
    assert lev_row[1] == "FAILED"

    # Also fail-closed on qualify_candidate if a strategy emits > 1.0 on holdout when allow_leverage=False
    holdout_lev_src = """
import numpy as np

def generate_positions(panel_df, contract, **kwargs):
    n_ts = panel_df["timestamp"].n_unique()
    n_assets = panel_df["asset"].n_unique()
    # In-sample val has 72 bars; holdout override has 24 bars -> emit 1.5 only on holdout
    wt = 1.5 if panel_df["timestamp"].min() >= contract.holdout_start else 0.5
    pos = np.zeros((n_ts, n_assets), dtype=float)
    for t in range(n_ts):
        if (t // 10) % 2 == 0 and t % 10 < 8:
            pos[t, :] = wt
    return {"positions": pos}
"""
    repo_q, reg_q, spec_q = _build_stage1_repo(
        tmp_path / "qual_lev",
        contract_id="T07_QUAL_LEV",
        panel_df=panel_df,
        strategy_family="rules_ts",
        strategy_name="holdout_lev",
        strategy_source=holdout_lev_src,
        expected_holding_bars=8,
        val_start=val_start,
        val_end=val_end,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        label_horizon_bars=1,
    )
    runner_q = ExperimentRunner(registry=reg_q, repo_root=repo_q, allow_legacy_replay=True)
    res_q = runner_q.run_spec(spec_q)
    assert res_q.verdict == "CANDIDATE_FOR_QUALIFICATION"
    with pytest.raises(LeveragePolicyError):
        qualify_candidate(
            res_q.run_id, "YES_UNSEAL_HOLDOUT", runner_q, holdout_override_path=holdout_path
        )
    with reg_q._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0] == 0

