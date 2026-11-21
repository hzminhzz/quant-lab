"""Seam A tests: analytical round-trip oracles at 1e-8 precision."""

from __future__ import annotations

import sys
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
from ml4t.backtest import (
    AssetClass,
    BacktestConfig,
    ContractSpec,
    DataFeed,
    Engine,
    ExitReason,
    StopLoss,
    Strategy,
    TrailingStop,
)
from ml4t.backtest.config import (
    CommissionType,
    ExecutionMode,
    ExecutionPrice,
    ShareType,
    SlippageType,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.shadow_engine_parity import (
    EXTRA_CONTROL_RUN_IDS,
    HISTORICAL_RUN_IDS,
    OracleResult,
    _build_weights_by_ts,
    generate_parity_report,
    probe_multi_exchange_calendar,
    profile_engine_scaling,
    replay_historical_run,
    run_analytical_oracles,
)

EXPECTED_ORACLES = {
    "next_bar_open",
    "quote_side",
    "funding_8h_perps",
    "stop_loss",
    "trailing_stop",
    "cash_rejection_no_leverage",
    "cost_stress_1p5x",
}


def _make_ohlcv_df(
    asset: str,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    bids: list[float] | None = None,
    asks: list[float] | None = None,
    start: datetime = datetime(2025, 1, 1, tzinfo=UTC),
) -> pl.DataFrame:
    n = len(opens)
    timestamps = [start + timedelta(hours=i) for i in range(n)]
    data: dict[str, object] = {
        "timestamp": timestamps,
        "asset": [asset] * n,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000.0] * n,
    }
    if bids is not None:
        data["bid"] = bids
    if asks is not None:
        data["ask"] = asks
    return pl.DataFrame(data).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))


class _ScheduledRoundTripStrategy(Strategy):
    def __init__(
        self,
        asset: str,
        orders_by_bar: dict[int, float],
        rules: object | None = None,
    ) -> None:
        self.asset = asset
        self.orders_by_bar = orders_by_bar
        self.rules = rules
        self.bar_idx = 0

    def on_start(self, broker) -> None:
        if self.rules is not None:
            broker.set_position_rules(self.rules)

    def on_data(self, timestamp, data, context, broker) -> None:
        qty = self.orders_by_bar.get(self.bar_idx)
        if qty is not None:
            broker.submit_order(self.asset, qty)
        self.bar_idx += 1


def test_oracle_result_dataclass_and_run_analytical_oracles() -> None:
    field_names = {f.name for f in fields(OracleResult)}
    assert field_names == {"name", "passed", "max_abs_error", "checks", "notes"}

    results = run_analytical_oracles(tol=1e-8)
    assert set(results.keys()) == EXPECTED_ORACLES

    for name, res in results.items():
        assert isinstance(res, OracleResult)
        assert res.name == name
        assert res.passed is True, f"{name} failed: {res.notes} checks={res.checks}"
        assert res.max_abs_error <= 1e-8, f"{name} error {res.max_abs_error} > 1e-8"
        assert len(res.checks) > 0
        assert isinstance(res.notes, str) and len(res.notes) > 0
        for check_name, entry in res.checks.items():
            assert {"expected", "actual", "abs_error"} <= set(entry.keys()), check_name
            assert abs(entry["expected"] - entry["actual"]) <= 1e-8
            assert entry["abs_error"] <= 1e-8

    strict = run_analytical_oracles(tol=-1e-12)
    assert all(not res.passed for res in strict.values())


def test_next_bar_open_independent_ledger() -> None:
    opens = [100.0, 102.0, 105.0, 110.0, 108.0]
    closes = [101.0, 104.0, 107.0, 109.0, 108.5]
    highs = [max(o, c) + 1.0 for o, c in zip(opens, closes, strict=True)]
    lows = [min(o, c) - 1.0 for o, c in zip(opens, closes, strict=True)]
    df = _make_ohlcv_df("BTC", opens, highs, lows, closes)

    qty = 12.5
    comm_rate = 0.001
    slip_rate = 0.0005
    initial_cash = 10_000.0
    cfg = BacktestConfig(
        initial_cash=initial_cash,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.OPEN,
        share_type=ShareType.FRACTIONAL,
        commission_type=CommissionType.PERCENTAGE,
        commission_rate=comm_rate,
        slippage_type=SlippageType.PERCENTAGE,
        slippage_rate=slip_rate,
    )
    res = Engine(DataFeed(prices_df=df), _ScheduledRoundTripStrategy("BTC", {0: qty, 2: -qty}), cfg).run()

    entry_slip = 102.0 * slip_rate
    entry_price = 102.0 + entry_slip
    entry_comm = qty * entry_price * comm_rate
    cash_t1 = initial_cash - qty * entry_price - entry_comm
    eq_t1 = cash_t1 + qty * 104.0
    eq_t2 = cash_t1 + qty * 107.0

    exit_slip = 110.0 * slip_rate
    exit_price = 110.0 - exit_slip
    exit_comm = qty * exit_price * comm_rate
    final_cash = cash_t1 + qty * exit_price - exit_comm
    net_pnl = (exit_price - entry_price) * qty - (entry_comm + exit_comm)

    assert len(res.fills) == 2
    assert abs(res.fills[0].price - entry_price) <= 1e-8
    assert abs(res.fills[0].slippage - entry_slip) <= 1e-8
    assert abs(res.fills[0].commission - entry_comm) <= 1e-8
    assert abs(res.fills[1].price - exit_price) <= 1e-8
    assert abs(res.fills[1].slippage - exit_slip) <= 1e-8
    assert abs(res.fills[1].commission - exit_comm) <= 1e-8
    assert abs(res.equity_curve[1][1] - eq_t1) <= 1e-8
    assert abs(res.equity_curve[2][1] - eq_t2) <= 1e-8
    assert abs(res.portfolio_state[-1][2] - final_cash) <= 1e-8
    assert abs(res.metrics["final_value"] - final_cash) <= 1e-8
    assert abs(res.trades[0].pnl - net_pnl) <= 1e-8


def test_quote_side_independent_ledger() -> None:
    bids = [99.5, 100.0, 104.0, 103.0, 98.0, 99.0]
    asks = [100.5, 101.0, 105.0, 104.0, 99.0, 100.0]
    mids = [(b + a) / 2.0 for b, a in zip(bids, asks, strict=True)]
    highs = [a + 0.5 for a in asks]
    lows = [b - 0.5 for b in bids]
    df = _make_ohlcv_df("ETH", mids, highs, lows, mids, bids=bids, asks=asks)

    initial_cash = 50_000.0
    cfg = BacktestConfig(
        initial_cash=initial_cash,
        allow_short_selling=True,
        allow_leverage=True,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.QUOTE_SIDE,
        mark_price=ExecutionPrice.QUOTE_SIDE,
        share_type=ShareType.FRACTIONAL,
        commission_type=CommissionType.NONE,
        slippage_type=SlippageType.NONE,
    )
    strat = _ScheduledRoundTripStrategy("ETH", {0: 10.0, 1: -10.0, 2: -20.0, 3: 20.0})
    res = Engine(DataFeed(prices_df=df, bid_col="bid", ask_col="ask"), strat, cfg).run()

    cash_1 = initial_cash - 10.0 * 101.0
    eq_1 = cash_1 + 10.0 * 100.0  # long marked at bid[1]=100.0
    cash_2 = cash_1 + 10.0 * 104.0
    cash_3 = cash_2 + 20.0 * 103.0
    eq_3 = cash_3 - 20.0 * 104.0  # short marked at ask[3]=104.0
    final_cash = cash_3 - 20.0 * 99.0

    assert [f.price for f in res.fills] == [101.0, 104.0, 103.0, 99.0]
    assert abs(res.equity_curve[1][1] - eq_1) <= 1e-8
    assert abs(res.equity_curve[2][1] - cash_2) <= 1e-8
    assert abs(res.equity_curve[3][1] - eq_3) <= 1e-8
    assert abs(res.portfolio_state[-1][2] - final_cash) <= 1e-8
    assert abs(res.metrics["final_value"] - final_cash) <= 1e-8
    assert abs(res.trades[0].pnl - 30.0) <= 1e-8
    assert abs(res.trades[1].pnl - 80.0) <= 1e-8


def test_funding_8h_perps_independent_ledger() -> None:
    n = 25
    start = datetime(2025, 1, 1, tzinfo=UTC)
    opens = [200.0 + 0.5 * i for i in range(n)]
    closes = [200.25 + 0.5 * i for i in range(n)]
    highs = [c + 1.0 for c in closes]
    lows = [o - 1.0 for o, c in zip(opens, closes, strict=True)]
    df = _make_ohlcv_df("ETH-PERP", opens, highs, lows, closes, start=start)

    funding_hours = [0, 8, 16, 24]
    funding_rates = [0.0001, 0.0002, -0.00015, 0.0003]
    funding_df = pl.DataFrame(
        {
            "timestamp": [start + timedelta(hours=h) for h in funding_hours],
            "asset": ["ETH-PERP"] * len(funding_hours),
            "rate": funding_rates,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))

    qty = 5.0
    mult = 2.0
    initial_cash = 50_000.0
    cfg = BacktestConfig(
        initial_cash=initial_cash,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.OPEN,
        share_type=ShareType.FRACTIONAL,
        commission_type=CommissionType.PERCENTAGE,
        commission_rate=0.001,
        slippage_type=SlippageType.NONE,
    )
    specs = {"ETH-PERP": ContractSpec(symbol="ETH-PERP", asset_class=AssetClass.FUTURE, multiplier=mult)}
    res = Engine(
        DataFeed(prices_df=df),
        _ScheduledRoundTripStrategy("ETH-PERP", {0: qty, 20: -qty}),
        cfg,
        contract_specs=specs,
        funding_df=funding_df,
    ).run()

    entry_notional = qty * opens[1] * mult
    exit_notional = qty * opens[21] * mult
    comm_total = qty * (opens[1] + opens[21]) * 0.001
    f8 = -qty * closes[8] * mult * 0.0002
    f16 = -qty * closes[16] * mult * (-0.00015)
    total_funding = f8 + f16
    final_cash = initial_cash + (exit_notional - entry_notional) - comm_total + total_funding

    assert len(res.funding_payments) == 4
    expected_deltas = [0.0, f8, f16, 0.0]
    for payment, exp_delta in zip(res.funding_payments, expected_deltas, strict=True):
        assert abs(payment.cash_delta - exp_delta) <= 1e-8
    assert abs(res.metrics["total_funding"] - total_funding) <= 1e-8
    assert abs(res.portfolio_state[-1][2] - final_cash) <= 1e-8
    assert abs(res.metrics["final_value"] - final_cash) <= 1e-8


def test_stop_loss_independent_ledger() -> None:
    opens = [100.0, 100.0, 101.0, 97.0, 95.0]
    highs = [101.0, 102.0, 103.0, 98.0, 96.0]
    lows = [99.0, 99.0, 96.0, 94.0, 94.0]
    closes = [100.0, 101.0, 97.0, 94.5, 95.5]
    start = datetime(2025, 1, 1, tzinfo=UTC)
    df = _make_ohlcv_df("SOL", opens, highs, lows, closes, start=start)

    qty = 10.0
    comm_rate = 0.001
    slip_rate = 0.0005
    initial_cash = 10_000.0
    cfg = BacktestConfig(
        initial_cash=initial_cash,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.OPEN,
        share_type=ShareType.FRACTIONAL,
        commission_type=CommissionType.PERCENTAGE,
        commission_rate=comm_rate,
        slippage_type=SlippageType.PERCENTAGE,
        slippage_rate=slip_rate,
    )
    res = Engine(
        DataFeed(prices_df=df),
        _ScheduledRoundTripStrategy("SOL", {0: qty}, rules=StopLoss(pct=0.05)),
        cfg,
    ).run()

    entry_price = 100.0 * (1.0 + slip_rate)
    entry_comm = qty * entry_price * comm_rate
    stop_level = entry_price * (1.0 - 0.05)
    exit_price = stop_level * (1.0 - slip_rate)
    exit_comm = qty * exit_price * comm_rate
    final_cash = initial_cash + (exit_price - entry_price) * qty - (entry_comm + exit_comm)

    assert len(res.trades) == 1
    trade = res.trades[0]
    assert trade.exit_time == start + timedelta(hours=3)
    assert trade.exit_reason == ExitReason.STOP_LOSS.value
    assert abs(trade.exit_price - exit_price) <= 1e-8
    assert abs(trade.fees - (entry_comm + exit_comm)) <= 1e-8
    assert abs(res.portfolio_state[-1][2] - final_cash) <= 1e-8
    assert abs(res.metrics["final_value"] - final_cash) <= 1e-8


def test_trailing_stop_independent_ledger() -> None:
    opens = [100.0, 100.0, 104.0, 109.0, 106.0, 104.0]
    highs = [101.0, 105.0, 112.0, 111.0, 107.0, 105.0]
    lows = [99.0, 99.0, 102.0, 105.0, 104.0, 103.0]
    closes = [100.0, 104.0, 110.0, 106.0, 104.2, 104.5]
    start = datetime(2025, 1, 1, tzinfo=UTC)
    df = _make_ohlcv_df("AVAX", opens, highs, lows, closes, start=start)

    qty = 10.0
    comm_rate = 0.001
    initial_cash = 10_000.0
    cfg = BacktestConfig(
        initial_cash=initial_cash,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.OPEN,
        share_type=ShareType.FRACTIONAL,
        commission_type=CommissionType.PERCENTAGE,
        commission_rate=comm_rate,
        slippage_type=SlippageType.NONE,
    )
    res = Engine(
        DataFeed(prices_df=df),
        _ScheduledRoundTripStrategy("AVAX", {0: qty}, rules=TrailingStop(pct=0.05)),
        cfg,
    ).run()

    # CLOSE HWM progression: 100 -> 104 (bar 1) -> 110 (bar 2); bar 3 low=105 > 110*0.95=104.5 holds;
    # bar 4 low=104 <= 104.5 triggers at exact stop 104.5.
    expected_exit = 110.0 * 0.95
    entry_comm = qty * 100.0 * comm_rate
    exit_comm = qty * expected_exit * comm_rate
    final_cash = initial_cash + (expected_exit - 100.0) * qty - (entry_comm + exit_comm)

    assert len(res.trades) == 1
    trade = res.trades[0]
    assert trade.exit_time == start + timedelta(hours=4)
    assert trade.exit_reason == ExitReason.TRAILING_STOP.value
    assert abs(trade.exit_price - expected_exit) <= 1e-8
    assert abs(res.portfolio_state[-1][2] - final_cash) <= 1e-8
    assert abs(res.metrics["final_value"] - final_cash) <= 1e-8


def test_cash_rejection_no_leverage_independent() -> None:
    opens = [100.0, 100.0, 101.0]
    highs = [101.0, 102.0, 103.0]
    lows = [99.0, 99.0, 100.0]
    closes = [100.0, 101.0, 102.0]
    df = _make_ohlcv_df("DOGE", opens, highs, lows, closes)

    initial_cash = 10_000.0
    cfg = BacktestConfig(
        initial_cash=initial_cash,
        allow_leverage=False,
        reject_on_insufficient_cash=True,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.OPEN,
        share_type=ShareType.FRACTIONAL,
    )
    res = Engine(DataFeed(prices_df=df), _ScheduledRoundTripStrategy("DOGE", {0: 200.0}), cfg).run()

    assert len(res.rejected_orders) >= 1
    assert res.metrics["num_rejected_orders"] >= 1
    assert len(res.fills) == 0
    assert abs(res.portfolio_state[-1][2] - initial_cash) <= 1e-8
    assert abs(res.metrics["final_value"] - initial_cash) <= 1e-8


def test_cost_stress_1p5x_independent_cross_term() -> None:
    opens = [100.0, 100.0, 115.0, 120.0, 118.0]
    closes = [100.0, 102.0, 118.0, 119.0, 118.0]
    highs = [max(o, c) + 1.0 for o, c in zip(opens, closes, strict=True)]
    lows = [min(o, c) - 1.0 for o, c in zip(opens, closes, strict=True)]
    df = _make_ohlcv_df("BTC", opens, highs, lows, closes)

    qty = 20.0
    c = 0.002
    s = 0.001
    initial_cash = 50_000.0

    def _run(comm_rate: float, slip_rate: float):
        cfg = BacktestConfig(
            initial_cash=initial_cash,
            execution_mode=ExecutionMode.NEXT_BAR,
            execution_price=ExecutionPrice.OPEN,
            share_type=ShareType.FRACTIONAL,
            commission_type=CommissionType.PERCENTAGE,
            commission_rate=comm_rate,
            slippage_type=SlippageType.PERCENTAGE if slip_rate > 0 else SlippageType.NONE,
            slippage_rate=slip_rate,
        )
        return Engine(
            DataFeed(prices_df=df),
            _ScheduledRoundTripStrategy("BTC", {0: qty, 2: -qty}),
            cfg,
        ).run()

    base_comm = _run(c, 0.0)
    stress_comm = _run(1.5 * c, 0.0)
    pred_comm_only = base_comm.metrics["final_value"] - 0.5 * sum(
        f.commission for f in base_comm.fills
    )
    assert abs(pred_comm_only - stress_comm.metrics["final_value"]) <= 1e-8

    base_both = _run(c, s)
    stress_both = _run(1.5 * c, 1.5 * s)
    linear_extra = 0.5 * sum(
        f.commission + abs(f.quantity) * f.slippage * (f.multiplier or 1.0)
        for f in base_both.fills
    )
    linear_pred = base_both.metrics["final_value"] - linear_extra
    cross_term = sum(
        (1.0 if f.side.value == "buy" else -1.0)
        * 1.5
        * 0.5
        * c
        * abs(f.quantity)
        * f.slippage
        * (f.multiplier or 1.0)
        for f in base_both.fills
    )
    exact_pred = linear_pred - cross_term

    assert abs(cross_term) > 1e-6
    assert abs(abs(linear_pred - stress_both.metrics["final_value"]) - abs(cross_term)) <= 1e-8
    assert abs(exact_pred - stress_both.metrics["final_value"]) <= 1e-8


# ---------------------------------------------------------------------------
# Seam B tests: historical replay, calendar probe, scaling, parity report
# ---------------------------------------------------------------------------


def _build_synthetic_repo(
    tmp_path: Path,
    *,
    exploratory_rel: str = "data/exploratory/mini_perps_1h.parquet",
    use_git_fallback: bool = False,
    missing_strategy: bool = False,
    leverage: float = 1.5,
) -> tuple[Path, str]:
    import hashlib
    import json
    import sqlite3
    import subprocess

    repo = tmp_path / "repo"
    (repo / "contracts" / "datasets").mkdir(parents=True)
    (repo / "data" / "exploratory").mkdir(parents=True)
    (repo / "data" / "holdout").mkdir(parents=True)
    (repo / "registry").mkdir(parents=True)
    (repo / "strategies").mkdir(parents=True)

    # 30 hourly bars: 10 train + 20 val so 8h funding boundaries (0, 8, 16, 24) appear in val.
    start = datetime(2025, 1, 1, 0, tzinfo=UTC)
    n_bars = 30
    assets = ["BTC", "ETH"]
    rows: list[dict[str, object]] = []
    for i in range(n_bars):
        ts = start + timedelta(hours=i)
        for j, asset in enumerate(assets):
            base = (100.0 if j == 0 else 50.0) + 0.4 * i + (0.2 if (i % 3 == j) else -0.1)
            rows.append(
                {
                    "timestamp": ts,
                    "asset": asset,
                    "open": base,
                    "high": base + 1.0,
                    "low": base - 1.0,
                    "close": base + 0.25,
                    "volume": 1000.0,
                    "funding_rate": 0.0002 if (i % 8 == 0) else 0.0,
                }
            )
    panel_df = pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    data_path = repo / exploratory_rel
    data_path.parent.mkdir(parents=True, exist_ok=True)
    panel_df.write_parquet(data_path)
    data_sha = hashlib.sha256(data_path.read_bytes()).hexdigest()

    holdout_rel = "data/holdout/mini_perps_1h.parquet"
    panel_df.write_parquet(repo / holdout_rel)

    contract_id = "MINI_PERPS_1H_V1"
    contract_yaml = f"""contract_id: {contract_id}
asset_class: crypto_perps
frequency: 1h
bars_per_year: 8760
exploratory_data_path: {exploratory_rel}
holdout_data_path: {holdout_rel}
train_start: "2025-01-01T00:00:00+00:00"
val_start: "2025-01-01T10:00:00+00:00"
val_end: "2025-01-02T06:00:00+00:00"
holdout_start: "2025-01-02T06:00:00+00:00"
holdout_end: "2025-01-03T00:00:00+00:00"
n_folds: 2
label_horizon_bars: 1
embargo_bars: 1
execution_delay_bars: 1
taker_fee_bps: 4.0
slippage_bps: 2.0
funding_bps_per_8h: 1.0
execution_mode: NEXT_BAR
min_expected_move_to_cost_ratio: 2.0
data_snapshot_sha256: {data_sha}
data_provenance: synthetic
"""
    (repo / "contracts" / f"{contract_id.lower()}.yaml").write_text(contract_yaml, encoding="utf-8")
    manifest = {
        "contract_id": contract_id,
        "path": exploratory_rel,
        "sha256": data_sha,
        "provenance": "synthetic",
        "source": "unit_test",
    }
    (repo / "contracts" / "datasets" / f"{contract_id}.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    mod_rel = "strategies/mini_strat.py"
    strat_code = f"""import numpy as np

def generate_positions(panel_df, contract, **params):
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    pos = np.zeros((len(ts), len(assets)), dtype=float)
    half = float(params.get("leverage", {leverage})) / 2.0
    for i in range(len(ts)):
        if i % 4 < 2:
            pos[i, 0] = half
            pos[i, 1] = half
        else:
            pos[i, 0] = -half * 0.5
            pos[i, 1] = half * 0.5
    return {{"positions": pos}}
"""
    mod_path = repo / mod_rel
    if use_git_fallback:
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        mod_path.write_text(strat_code, encoding="utf-8")
        subprocess.run(["git", "add", mod_rel], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add strat"], cwd=repo, check=True, capture_output=True)
        mod_path.unlink()
    elif not missing_strategy:
        mod_path.write_text(strat_code, encoding="utf-8")

    run_id = "RUN-MINI-001"
    conn = sqlite3.connect(repo / "registry" / "registry.db")
    conn.execute(
        """
        CREATE TABLE trials (
            run_id TEXT PRIMARY KEY,
            hypothesis_id TEXT,
            contract_id TEXT,
            strategy_family TEXT,
            strategy_module TEXT,
            config_json TEXT,
            net_sharpe REAL,
            gross_sharpe REAL,
            stress_2x_cost_sharpe REAL,
            verdict TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            "HYP-MINI-001",
            contract_id,
            "rules_ts",
            mod_rel,
            json.dumps({"leverage": leverage}),
            0.0,
            0.0,
            0.0,
            "REVISE",
        ),
    )
    conn.commit()
    conn.close()
    return repo, run_id


def test_seam_b_constants_exported() -> None:
    assert HISTORICAL_RUN_IDS == (
        "RUN-HYP065-003",
        "RUN-HYP066-003",
        "RUN-HYP069-001",
        "RUN-HYP091-001",
        "RUN-HYP120-001",
        "RUN-XSRR-001",
    )
    assert EXTRA_CONTROL_RUN_IDS == ("RUN-HYP114-003",)
    assert callable(profile_engine_scaling)


def test_replay_historical_run_synthetic_and_git_fallback(tmp_path: Path) -> None:
    repo, run_id = _build_synthetic_repo(tmp_path / "worktree_case", use_git_fallback=False, leverage=1.5)
    rec = replay_historical_run(run_id, repo)

    required_keys = {
        "run_id",
        "hypothesis_id",
        "contract_id",
        "strategy_family",
        "strategy_module",
        "module_source",
        "sr_vec",
        "sr_vec_gross",
        "sr_vec_2x",
        "sr_engine",
        "abs_sr_delta",
        "max_gross_leverage",
        "cash_deficit_bars",
        "funding_cost_vec_bps",
        "funding_cost_engine_bps",
        "funding_delta_bps",
        "total_funding_usd",
        "sr_engine_1p5x_analytical",
        "sr_engine_1p5x_rerun",
        "1.5x_analytical_vs_engine_delta",
        "engine_wall_seconds",
        "peak_rss_mb",
        "tracemalloc_peak_mb",
        "rejected_orders_no_leverage",
    }
    assert required_keys <= set(rec.keys())
    assert rec["module_source"] == "worktree"
    assert rec["max_gross_leverage"] > 1.0
    assert rec["cash_deficit_bars"] > 0
    assert rec["rejected_orders_no_leverage"] > 0
    assert rec["1.5x_analytical_vs_engine_delta"] >= 0.0
    assert rec["engine_wall_seconds"] > 0.0

    # Git-blob fallback when module is deleted from disk
    repo_git, run_id_git = _build_synthetic_repo(
        tmp_path / "git_case", use_git_fallback=True, leverage=0.8
    )
    rec_git = replay_historical_run(run_id_git, repo_git)
    assert rec_git["module_source"].startswith("git:")
    assert len(rec_git["module_source"]) == len("git:") + 8

    # Missing everywhere fails closed
    repo_missing, run_id_missing = _build_synthetic_repo(
        tmp_path / "missing_case", missing_strategy=True
    )
    import pytest

    with pytest.raises(FileNotFoundError):
        replay_historical_run(run_id_missing, repo_missing)


def test_replay_historical_run_rejects_holdout_path(tmp_path: Path) -> None:
    import pytest

    repo, run_id = _build_synthetic_repo(
        tmp_path / "holdout_case", exploratory_rel="data/holdout/leak.parquet"
    )
    with pytest.raises(ValueError, match="exploratory"):
        replay_historical_run(run_id, repo)


def test_probe_multi_exchange_calendar_synthetic_and_repo(tmp_path: Path) -> None:
    res = probe_multi_exchange_calendar(_REPO_ROOT)
    assert "synthetic_mixed_venue" in res
    syn = res["synthetic_mixed_venue"]
    assert syn["null_session_date_rows"] > 0
    assert "null" in syn["raw_datafeed_error"].lower()
    assert "duplicate" in syn["non_null_hourly_error"].lower()
    assert "missing" in syn["session_collapsed_partial_holiday_error"].lower()
    assert syn["partial_holiday_sessions"] >= 1

    assert "INDICES_1H_V2" in res["datasets"]
    assert "MULTI_ASSET_1H_V1" in res["datasets"]
    for ds_key in ("INDICES_1H_V2", "MULTI_ASSET_1H_V1"):
        ds_probe = res["datasets"][ds_key]
        assert ds_probe["null_session_date_rows"] > 0
        assert "null" in ds_probe["raw_datafeed_error"].lower()
        assert ds_probe["incomplete_session_count"] > 0
        assert "missing" in ds_probe["session_collapsed_partial_holiday_error"].lower()


def test_generate_parity_report_outputs_json_and_md(tmp_path: Path, monkeypatch) -> None:
    import json

    import scripts.shadow_engine_parity as sep

    repo, run_id = _build_synthetic_repo(tmp_path / "report_case", leverage=1.2)
    monkeypatch.setattr(sep, "HISTORICAL_RUN_IDS", (run_id,))
    monkeypatch.setattr(sep, "EXTRA_CONTROL_RUN_IDS", ())
    monkeypatch.setattr(
        sep,
        "probe_multi_exchange_calendar",
        lambda _root: {
            "synthetic_mixed_venue": {
                "null_session_date_rows": 2,
                "raw_datafeed_error": "ValueError: session_col contains a null session date",
                "non_null_hourly_error": "ValueError: duplicate bar for 'ES' in session 2025-01-29",
                "session_collapsed_partial_holiday_error": "ValueError: session 2025-01-29 is missing bars for ['HK33']",
                "partial_holiday_sessions": 1,
            },
            "datasets": {},
        },
    )

    json_out = tmp_path / "out" / "parity_report.json"
    md_out = tmp_path / "out" / "parity_report.md"
    report = generate_parity_report(repo, json_out, md_out, include_scaling=False)

    assert json_out.is_file()
    assert md_out.is_file()
    loaded = json.loads(json_out.read_text(encoding="utf-8"))
    assert set(loaded.keys()) >= {
        "analytical_oracles",
        "historical_replays",
        "holiday_session_behavior",
        "engine_scaling_100_asset",
    }
    assert len(loaded["historical_replays"]) == 1
    row = loaded["historical_replays"][0]
    for col in (
        "abs_sr_delta",
        "max_gross_leverage",
        "cash_deficit_bars",
        "funding_delta_bps",
        "1.5x_analytical_vs_engine_delta",
        "engine_wall_seconds",
        "peak_rss_mb",
    ):
        assert col in row
    md_text = md_out.read_text(encoding="utf-8")
    assert "abs_sr_delta" in md_text
    assert "1.5x_analytical_vs_engine_delta" in md_text
    assert "holiday_session_behavior" in md_text or "Holiday" in md_text
    assert report["historical_replays"][0]["run_id"] == run_id


def test_build_weights_by_ts_skips_unchanged_and_emits_exit_to_zero() -> None:
    import numpy as np

    start = datetime(2025, 1, 1, tzinfo=UTC)
    ts = [start + timedelta(hours=i) for i in range(5)]
    assets = ["BTC", "ETH"]
    # Bar 0: all zeros (skipped at start_idx); bar 1: enter; bar 2: unchanged (skipped);
    # bar 3: exit to all zeros (emits {}); bar 4: unchanged zero (skipped).
    raw_pos = np.array(
        [
            [0.0, 0.0],
            [0.5, -0.5],
            [0.5, -0.5],
            [0.0, 0.0],
            [0.0, 0.0],
        ],
        dtype=float,
    )
    weights = _build_weights_by_ts(raw_pos, ts, assets, start_idx=0, stop_idx=len(ts))
    assert list(weights.keys()) == [ts[1], ts[3]]
    assert weights[ts[1]] == {"BTC": 0.5, "ETH": -0.5}
    assert weights[ts[3]] == {}

    # When start_idx starts on a non-zero row, emit start_idx and skip repeated bar after it.
    weights_mid = _build_weights_by_ts(raw_pos, ts, assets, start_idx=1, stop_idx=4)
    assert list(weights_mid.keys()) == [ts[1], ts[3]]


def test_generate_parity_report_fails_closed_on_oracle_failure(tmp_path: Path, monkeypatch) -> None:
    import pytest

    import scripts.shadow_engine_parity as sep

    bad = OracleResult(
        name="next_bar_open",
        passed=False,
        max_abs_error=1e-4,
        checks={},
        notes="forced failure",
    )
    monkeypatch.setattr(sep, "run_analytical_oracles", lambda: {"next_bar_open": bad})
    with pytest.raises(AssertionError, match="next_bar_open"):
        generate_parity_report(
            tmp_path,
            tmp_path / "out.json",
            tmp_path / "out.md",
            include_scaling=False,
        )


def test_profile_engine_scaling_isolated_subprocess() -> None:
    import pytest

    if not (_REPO_ROOT / "data" / "exploratory" / "crypto_perps_top100_1h.parquet").is_file():
        pytest.skip("CRYPTO_PERPS_TOP100_1H_V1 exploratory parquet not present")

    rows = profile_engine_scaling(_REPO_ROOT, bar_counts=(50,))
    assert len(rows) == 1
    row = rows[0]
    assert row["bars"] == 50
    assert row["assets"] >= 50
    assert row["rows"] > 0
    assert row["peak_rss_mb"] > 0.0
    assert row["engine_wall_seconds"] > 0.0
    assert isinstance(row["under_300s_budget"], bool)
    assert row["under_300s_budget"] is True
