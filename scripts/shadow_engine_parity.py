"""Seam A: Analytical round-trip oracles for ml4t.backtest parity at 1e-8 precision."""

from __future__ import annotations

import argparse
import json
import math
import resource
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import (
    AssetClass,
    BacktestConfig,
    BacktestResult,
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
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor

from warden.contracts import EvaluationContract, load_contract
from warden.provenance import resolve_dataset
from warden.runner import (
    calculate_metrics_from_returns,
    prepare_panel,
    simulate_positions,
    simulation_inputs,
)
from warden.strategy_loader import load_module_from_path
from warden_evidence.market_validation import assign_multi_exchange_sessions
from warden_ops.readonly import ReadOnlyResearchRegistry


@dataclass(frozen=True)
class OracleResult:
    """Structured parity verification outcome for one analytical oracle."""

    name: str
    passed: bool
    max_abs_error: float
    checks: dict[str, dict[str, float]]
    notes: str


class _ScheduledOrderStrategy(Strategy):
    def __init__(
        self,
        asset: str,
        orders_by_bar: dict[int, float],
        rules: Any | None = None,
    ) -> None:
        self.asset = asset
        self.orders_by_bar = orders_by_bar
        self.rules = rules
        self.bar_idx = 0

    def on_start(self, broker: Any) -> None:
        if self.rules is not None:
            broker.set_position_rules(self.rules)

    def on_data(
        self,
        timestamp: datetime,
        data: dict[str, dict[str, Any]],
        context: dict[str, Any],
        broker: Any,
    ) -> None:
        qty = self.orders_by_bar.get(self.bar_idx)
        if qty is not None:
            broker.submit_order(self.asset, qty)
        self.bar_idx += 1


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
    data: dict[str, object] = {
        "timestamp": [start + timedelta(hours=i) for i in range(n)],
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


def _check(expected: float, actual: float) -> dict[str, float]:
    exp_f = float(expected)
    act_f = float(actual)
    return {"expected": exp_f, "actual": act_f, "abs_error": abs(exp_f - act_f)}


def _build_result(
    name: str,
    checks: dict[str, dict[str, float]],
    tol: float,
    notes: str,
) -> OracleResult:
    max_err = max(entry["abs_error"] for entry in checks.values())
    passed = all(entry["abs_error"] <= tol for entry in checks.values())
    return OracleResult(
        name=name,
        passed=passed,
        max_abs_error=max_err,
        checks=checks,
        notes=notes,
    )


def oracle_next_bar_open(tol: float = 1e-8) -> OracleResult:
    """Reconcile NEXT_BAR + OPEN + FRACTIONAL + percentage commission & slippage."""
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
    res = Engine(
        DataFeed(prices_df=df),
        _ScheduledOrderStrategy("BTC", {0: qty, 2: -qty}),
        cfg,
    ).run()

    entry_slip = opens[1] * slip_rate
    entry_price = opens[1] + entry_slip
    entry_comm = qty * entry_price * comm_rate
    cash_t1 = initial_cash - qty * entry_price - entry_comm
    eq_t1 = cash_t1 + qty * closes[1]
    eq_t2 = cash_t1 + qty * closes[2]

    exit_slip = opens[3] * slip_rate
    exit_price = opens[3] - exit_slip
    exit_comm = qty * exit_price * comm_rate
    final_cash = cash_t1 + qty * exit_price - exit_comm
    net_pnl = (exit_price - entry_price) * qty - (entry_comm + exit_comm)

    checks = {
        "fill_count": _check(2.0, len(res.fills)),
        "entry_fill_price": _check(entry_price, res.fills[0].price),
        "entry_slippage_per_unit": _check(entry_slip, res.fills[0].slippage),
        "entry_commission": _check(entry_comm, res.fills[0].commission),
        "exit_fill_price": _check(exit_price, res.fills[1].price),
        "exit_slippage_per_unit": _check(exit_slip, res.fills[1].slippage),
        "exit_commission": _check(exit_comm, res.fills[1].commission),
        "bar1_marked_equity": _check(eq_t1, res.equity_curve[1][1]),
        "bar2_marked_equity": _check(eq_t2, res.equity_curve[2][1]),
        "trade_net_pnl": _check(net_pnl, res.trades[0].pnl),
        "final_cash": _check(final_cash, res.portfolio_state[-1][2]),
        "terminal_equity": _check(final_cash, res.metrics["final_value"]),
    }
    return _build_result(
        "next_bar_open",
        checks,
        tol,
        "NEXT_BAR OPEN fractional round-trip with percentage commission and slippage.",
    )


def oracle_quote_side(tol: float = 1e-8) -> OracleResult:
    """Reconcile QUOTE_SIDE long/short spread crossing and bid/ask marking."""
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
    res = Engine(
        DataFeed(prices_df=df, bid_col="bid", ask_col="ask"),
        _ScheduledOrderStrategy("ETH", {0: 10.0, 1: -10.0, 2: -20.0, 3: 20.0}),
        cfg,
    ).run()

    cash_1 = initial_cash - 10.0 * asks[1]
    eq_1 = cash_1 + 10.0 * bids[1]
    cash_2 = cash_1 + 10.0 * bids[2]
    cash_3 = cash_2 + 20.0 * bids[3]
    eq_3 = cash_3 - 20.0 * asks[3]
    final_cash = cash_3 - 20.0 * asks[4]

    checks = {
        "fill_count": _check(4.0, len(res.fills)),
        "long_entry_ask": _check(asks[1], res.fills[0].price),
        "long_mark_bid_equity": _check(eq_1, res.equity_curve[1][1]),
        "long_exit_bid": _check(bids[2], res.fills[1].price),
        "long_trade_pnl": _check(10.0 * (bids[2] - asks[1]), res.trades[0].pnl),
        "short_entry_bid": _check(bids[3], res.fills[2].price),
        "short_mark_ask_equity": _check(eq_3, res.equity_curve[3][1]),
        "short_exit_ask": _check(asks[4], res.fills[3].price),
        "short_trade_pnl": _check(20.0 * (bids[3] - asks[4]), res.trades[1].pnl),
        "final_cash": _check(final_cash, res.portfolio_state[-1][2]),
        "terminal_equity": _check(final_cash, res.metrics["final_value"]),
    }
    return _build_result(
        "quote_side",
        checks,
        tol,
        "QUOTE_SIDE long (buy ask, mark bid, sell bid) and short (sell bid, mark ask, buy ask).",
    )


def oracle_funding_8h_perps(tol: float = 1e-8) -> OracleResult:
    """Reconcile 25h perpetual funding payments at 8h boundaries with ContractSpec."""
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
    comm_rate = 0.001
    initial_cash = 50_000.0
    cfg = BacktestConfig(
        initial_cash=initial_cash,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.OPEN,
        share_type=ShareType.FRACTIONAL,
        commission_type=CommissionType.PERCENTAGE,
        commission_rate=comm_rate,
        slippage_type=SlippageType.NONE,
    )
    specs = {
        "ETH-PERP": ContractSpec(symbol="ETH-PERP", asset_class=AssetClass.FUTURE, multiplier=mult)
    }
    res = Engine(
        DataFeed(prices_df=df),
        _ScheduledOrderStrategy("ETH-PERP", {0: qty, 20: -qty}),
        cfg,
        contract_specs=specs,
        funding_df=funding_df,
    ).run()

    entry_notional = qty * opens[1] * mult
    exit_notional = qty * opens[21] * mult
    # PercentageCommission.calculate(asset, qty, price) does not scale by ContractSpec.multiplier.
    comm_total = qty * (opens[1] + opens[21]) * comm_rate
    # Funding runs after _update_time (mark = bar close) and before fills at that bar.
    f0 = 0.0
    f8 = -qty * closes[8] * mult * funding_rates[1]
    f16 = -qty * closes[16] * mult * funding_rates[2]
    f24 = 0.0
    total_funding = f0 + f8 + f16 + f24
    final_cash = initial_cash + (exit_notional - entry_notional) - comm_total + total_funding

    checks = {
        "funding_event_count": _check(4.0, len(res.funding_payments)),
        "funding_h00_flat": _check(f0, res.funding_payments[0].cash_delta),
        "funding_h08_held": _check(f8, res.funding_payments[1].cash_delta),
        "funding_h16_held": _check(f16, res.funding_payments[2].cash_delta),
        "funding_h24_flat": _check(f24, res.funding_payments[3].cash_delta),
        "total_funding": _check(total_funding, res.metrics["total_funding"]),
        "final_cash": _check(final_cash, res.portfolio_state[-1][2]),
        "terminal_equity": _check(final_cash, res.metrics["final_value"]),
    }
    return _build_result(
        "funding_8h_perps",
        checks,
        tol,
        "25 hourly bars with 8h funding_df (h=0,8,16,24) and multiplier=2.0 ContractSpec.",
    )


def oracle_stop_loss(tol: float = 1e-8) -> OracleResult:
    """Reconcile native StopLoss(pct=0.05) trigger bar, fill price, exit_reason, and ledger."""
    opens = [100.0, 100.0, 101.0, 97.0, 95.0]
    highs = [101.0, 102.0, 103.0, 98.0, 96.0]
    lows = [99.0, 99.0, 96.0, 94.0, 94.0]
    closes = [100.0, 101.0, 97.0, 94.5, 95.5]
    start = datetime(2025, 1, 1, tzinfo=UTC)
    df = _make_ohlcv_df("SOL", opens, highs, lows, closes, start=start)

    qty = 10.0
    stop_pct = 0.05
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
        _ScheduledOrderStrategy("SOL", {0: qty}, rules=StopLoss(pct=stop_pct)),
        cfg,
    ).run()

    entry_price = opens[1] * (1.0 + slip_rate)
    entry_comm = qty * entry_price * comm_rate
    stop_level = entry_price * (1.0 - stop_pct)
    exit_price = stop_level * (1.0 - slip_rate)
    exit_comm = qty * exit_price * comm_rate
    total_fees = entry_comm + exit_comm
    final_cash = initial_cash + (exit_price - entry_price) * qty - total_fees

    trade = res.trades[0]
    trigger_bar = int((trade.exit_time - start).total_seconds() // 3600)
    exit_reason_match = 1.0 if trade.exit_reason == ExitReason.STOP_LOSS.value else 0.0

    checks = {
        "trade_count": _check(1.0, len(res.trades)),
        "trigger_bar_index": _check(3.0, trigger_bar),
        "exit_reason_is_stop_loss": _check(1.0, exit_reason_match),
        "exit_fill_price": _check(exit_price, trade.exit_price),
        "total_fees": _check(total_fees, trade.fees),
        "final_cash": _check(final_cash, res.portfolio_state[-1][2]),
        "terminal_equity": _check(final_cash, res.metrics["final_value"]),
    }
    return _build_result(
        "stop_loss",
        checks,
        tol,
        "Native StopLoss(0.05) triggers on bar 3 at exact stop level minus percentage slippage.",
    )


def oracle_trailing_stop(tol: float = 1e-8) -> OracleResult:
    """Reconcile TrailingStop(pct=0.05) with defaults (LAGGED, CLOSE, STOP_PRICE)."""
    opens = [100.0, 100.0, 104.0, 109.0, 106.0, 104.0]
    highs = [101.0, 105.0, 112.0, 111.0, 107.0, 105.0]
    lows = [99.0, 99.0, 102.0, 105.0, 104.0, 103.0]
    closes = [100.0, 104.0, 110.0, 106.0, 104.2, 104.5]
    start = datetime(2025, 1, 1, tzinfo=UTC)
    df = _make_ohlcv_df("AVAX", opens, highs, lows, closes, start=start)

    qty = 10.0
    trail_pct = 0.05
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
        _ScheduledOrderStrategy("AVAX", {0: qty}, rules=TrailingStop(pct=trail_pct)),
        cfg,
    ).run()

    # CLOSE-based HWM progression: 100 (entry) -> 104 (bar 1) -> 110 (bar 2).
    # Bar 3 evaluates lagged HWM=110 (stop=104.5 < low=105 -> holds); bar 4 low=104 triggers 104.5.
    hwm_bar2 = max(opens[1], closes[1], closes[2])
    expected_exit = hwm_bar2 * (1.0 - trail_pct)
    entry_comm = qty * opens[1] * comm_rate
    exit_comm = qty * expected_exit * comm_rate
    total_fees = entry_comm + exit_comm
    final_cash = initial_cash + (expected_exit - opens[1]) * qty - total_fees

    trade = res.trades[0]
    trigger_bar = int((trade.exit_time - start).total_seconds() // 3600)
    exit_reason_match = 1.0 if trade.exit_reason == ExitReason.TRAILING_STOP.value else 0.0

    checks = {
        "trade_count": _check(1.0, len(res.trades)),
        "hwm_from_close": _check(110.0, hwm_bar2),
        "trigger_bar_index": _check(4.0, trigger_bar),
        "exit_reason_is_trailing_stop": _check(1.0, exit_reason_match),
        "exit_fill_price": _check(expected_exit, trade.exit_price),
        "total_fees": _check(total_fees, trade.fees),
        "final_cash": _check(final_cash, res.portfolio_state[-1][2]),
        "terminal_equity": _check(final_cash, res.metrics["final_value"]),
    }
    return _build_result(
        "trailing_stop",
        checks,
        tol,
        "Native TrailingStop(0.05) with LAGGED/CLOSE/STOP_PRICE defaults; HWM=110 -> exit=104.5.",
    )


def oracle_cash_rejection_no_leverage(tol: float = 1e-8) -> OracleResult:
    """Reconcile cash-account order rejection when order notional exceeds cash."""
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
    res = Engine(
        DataFeed(prices_df=df),
        _ScheduledOrderStrategy("DOGE", {0: 200.0}),
        cfg,
    ).run()

    checks = {
        "rejected_order_count": _check(1.0, len(res.rejected_orders)),
        "metric_rejected_count": _check(1.0, res.metrics["num_rejected_orders"]),
        "fill_count": _check(0.0, len(res.fills)),
        "final_cash": _check(initial_cash, res.portfolio_state[-1][2]),
        "terminal_equity": _check(initial_cash, res.metrics["final_value"]),
    }
    return _build_result(
        "cash_rejection_no_leverage",
        checks,
        tol,
        "Order notional $20,000 > $10,000 with allow_leverage=False rejects cleanly with 0 fills.",
    )


def oracle_cost_stress_1p5x(tol: float = 1e-8) -> OracleResult:
    """Reconcile base 1.0x -> fill-derived 1.5x -> full 1.5x rerun (commission + cross-term)."""
    opens = [100.0, 100.0, 115.0, 120.0, 118.0]
    closes = [100.0, 102.0, 118.0, 119.0, 118.0]
    highs = [max(o, c) + 1.0 for o, c in zip(opens, closes, strict=True)]
    lows = [min(o, c) - 1.0 for o, c in zip(opens, closes, strict=True)]
    df = _make_ohlcv_df("BTC", opens, highs, lows, closes)

    qty = 20.0
    comm_rate = 0.002
    slip_rate = 0.001
    initial_cash = 50_000.0

    def _run(c_rate: float, s_rate: float) -> Any:
        cfg = BacktestConfig(
            initial_cash=initial_cash,
            execution_mode=ExecutionMode.NEXT_BAR,
            execution_price=ExecutionPrice.OPEN,
            share_type=ShareType.FRACTIONAL,
            commission_type=CommissionType.PERCENTAGE,
            commission_rate=c_rate,
            slippage_type=SlippageType.PERCENTAGE if s_rate > 0 else SlippageType.NONE,
            slippage_rate=s_rate,
        )
        return Engine(
            DataFeed(prices_df=df),
            _ScheduledOrderStrategy("BTC", {0: qty, 2: -qty}),
            cfg,
        ).run()

    base_comm = _run(comm_rate, 0.0)
    stress_comm = _run(1.5 * comm_rate, 0.0)
    pred_comm_only = base_comm.metrics["final_value"] - 0.5 * sum(
        f.commission for f in base_comm.fills
    )

    base_both = _run(comm_rate, slip_rate)
    stress_both = _run(1.5 * comm_rate, 1.5 * slip_rate)

    linear_extra = 0.5 * sum(
        f.commission + abs(f.quantity) * f.slippage * (f.multiplier or 1.0)
        for f in base_both.fills
    )
    linear_pred = base_both.metrics["final_value"] - linear_extra

    # Cross-term: k*(k-1)*c * sum_f(sigma_f * |qty_f| * slip_f * mult_f), sigma=+1 buy / -1 sell.
    cross_term = sum(
        (1.0 if f.side.value == "buy" else -1.0)
        * 1.5
        * 0.5
        * comm_rate
        * abs(f.quantity)
        * f.slippage
        * (f.multiplier or 1.0)
        for f in base_both.fills
    )
    exact_pred = linear_pred - cross_term
    linear_only_delta = abs(linear_pred - stress_both.metrics["final_value"])

    checks = {
        "commission_only_1p5x_equity": _check(pred_comm_only, stress_comm.metrics["final_value"]),
        "combined_exact_1p5x_equity": _check(exact_pred, stress_both.metrics["final_value"]),
        "cross_term_delta": _check(abs(cross_term), linear_only_delta),
    }
    return _build_result(
        "cost_stress_1p5x",
        checks,
        tol,
        (
            "Commission-only 1.5x matches exactly; combined commission+slippage includes "
            f"commission-on-slippage cross-term={cross_term:.6f} "
            f"(linear_only_delta={linear_only_delta:.6f})."
        ),
    )


def run_analytical_oracles(tol: float = 1e-8) -> dict[str, OracleResult]:
    """Run all 7 Seam A analytical round-trip oracles against ml4t.backtest.Engine."""
    oracles = [
        oracle_next_bar_open,
        oracle_quote_side,
        oracle_funding_8h_perps,
        oracle_stop_loss,
        oracle_trailing_stop,
        oracle_cash_rejection_no_leverage,
        oracle_cost_stress_1p5x,
    ]
    results = [fn(tol=tol) for fn in oracles]
    return {res.name: res for res in results}


# ---------------------------------------------------------------------------
# Seam B: Historical Replay, Calendar Probe, 100-Asset Scaling, Parity Report
# ---------------------------------------------------------------------------

HISTORICAL_RUN_IDS: tuple[str, ...] = (
    "RUN-HYP065-003",
    "RUN-HYP066-003",
    "RUN-HYP069-001",
    "RUN-HYP091-001",
    "RUN-HYP120-001",
    "RUN-XSRR-001",
)
EXTRA_CONTROL_RUN_IDS: tuple[str, ...] = ("RUN-HYP114-003",)


class _TargetWeightReplayStrategy(Strategy):
    """Submit precomputed target weights at each timestamp via TargetWeightExecutor."""

    def __init__(
        self,
        weights_by_ts: dict[datetime, dict[str, float]],
        rebalance_config: RebalanceConfig,
    ) -> None:
        self._weights_by_ts = weights_by_ts
        self._executor = TargetWeightExecutor(rebalance_config)

    def on_data(
        self,
        timestamp: datetime,
        data: dict[str, dict[str, Any]],
        context: dict[str, Any],
        broker: Any,
    ) -> None:
        targets = self._weights_by_ts.get(timestamp)
        if targets is None:
            return
        held = {
            asset: pos.quantity
            for asset, pos in broker.get_positions().items()
            if pos.quantity != 0
        }
        # Keep non-zero targets and any currently held assets so closed holidays are not liquidated.
        active_targets = {
            asset: wt
            for asset, wt in targets.items()
            if wt != 0.0 or asset in held
        }
        for asset in held:
            if asset not in active_targets:
                active_targets[asset] = 0.0
        if not active_targets and not held:
            return
        self._executor.execute(active_targets, data, broker)


def _peak_rss_mb() -> float:
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0


def _measure_peak_memory_and_time[T](fn: Callable[[], T]) -> tuple[T, dict[str, float]]:
    """Execute ``fn`` and return ``(result, timing_and_memory_metrics)``."""
    tracemalloc.start()
    rss_before = _peak_rss_mb()
    t0 = time.perf_counter()
    try:
        result = fn()
    finally:
        wall_s = time.perf_counter() - t0
        _, peak_traced_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after = _peak_rss_mb()
    return result, {
        "engine_wall_seconds": wall_s,
        "peak_rss_mb": rss_after,
        "rss_delta_mb": max(0.0, rss_after - rss_before),
        "tracemalloc_peak_mb": float(peak_traced_bytes) / (1024.0 * 1024.0),
    }


def _assert_exploratory_path(repo_root: Path, rel_path: str) -> None:
    resolved = (repo_root / rel_path).resolve()
    exploratory_root = (repo_root / "data" / "exploratory").resolve()
    if not resolved.is_relative_to(exploratory_root):
        raise ValueError(
            f"Refusing non-exploratory dataset path {rel_path!r}; must resolve under data/exploratory/"
        )


def _load_trial_metadata(repo_root: Path, run_id: str) -> dict[str, Any]:
    db_path = (repo_root / "registry" / "registry.db").resolve()
    row = ReadOnlyResearchRegistry(db_path).read_trial_evidence(run_id)
    if row is None:
        raise KeyError(f"Run ID {run_id!r} not found in {db_path}")
    return row


def _resolve_contract_path(repo_root: Path, contract_id: str) -> Path:
    cid = contract_id.lower()
    for suffix in (".yaml", ".yml"):
        cand = repo_root / "contracts" / f"{cid}{suffix}"
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"Contract {contract_id!r} not found under {repo_root / 'contracts'}")


def _load_strategy_with_fallback(repo_root: Path, mod_rel: str) -> tuple[Any, str]:
    rel = mod_rel.lstrip("/")
    disk_path = repo_root / rel
    if disk_path.is_file():
        return load_module_from_path(disk_path), "worktree"

    log_proc = subprocess.run(
        ["git", "log", "--all", "-n", "1", "--format=%H", "--", rel],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    sha = log_proc.stdout.strip() if log_proc.returncode == 0 else ""
    if not sha:
        raise FileNotFoundError(f"Strategy module {rel!r} not found in worktree or git history")

    show_proc = subprocess.run(
        ["git", "show", f"{sha}:{rel}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if show_proc.returncode != 0 or not show_proc.stdout:
        raise FileNotFoundError(f"Could not read {rel!r} from git commit {sha}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = Path(tmpdir) / Path(rel).name
        tmp_file.write_text(show_proc.stdout, encoding="utf-8")
        mod = load_module_from_path(tmp_file)
    return mod, f"git:{sha[:8]}"


def _sharpe_from_equity_values(equity_values: list[float] | np.ndarray, bars_per_year: int) -> float:
    eq = np.asarray(equity_values, dtype=float)
    if len(eq) < 2:
        return 0.0
    rets = np.diff(eq) / np.where(eq[:-1] != 0.0, eq[:-1], 1.0)
    if len(rets) < 2:
        return 0.0
    return float(np.mean(rets) / (np.std(rets, ddof=1) + 1e-12) * math.sqrt(bars_per_year))


def _build_engine_config(
    contract: EvaluationContract,
    *,
    initial_cash: float,
    cost_multiplier: float,
    allow_leverage: bool,
    reject_on_insufficient_cash: bool,
) -> BacktestConfig:
    comm_rate = contract.taker_fee_bps * 1e-4 * cost_multiplier
    slip_rate = contract.slippage_bps * 1e-4 * cost_multiplier
    is_quote = contract.execution_mode == "QUOTE_SIDE"
    return BacktestConfig(
        initial_cash=initial_cash,
        allow_short_selling=True,
        allow_leverage=allow_leverage,
        initial_margin=0.05 if allow_leverage else 1.0,
        long_maintenance_margin=0.025 if allow_leverage else 0.25,
        short_maintenance_margin=0.025 if allow_leverage else 0.30,
        reject_on_insufficient_cash=reject_on_insufficient_cash,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.QUOTE_SIDE if is_quote else ExecutionPrice.OPEN,
        mark_price=ExecutionPrice.QUOTE_SIDE if is_quote else ExecutionPrice.CLOSE,
        share_type=ShareType.FRACTIONAL,
        commission_type=CommissionType.PERCENTAGE if comm_rate > 0 else CommissionType.NONE,
        commission_rate=comm_rate,
        slippage_type=SlippageType.PERCENTAGE if slip_rate > 0 else SlippageType.NONE,
        slippage_rate=slip_rate,
    )


def _build_funding_df(feed_df: pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame | None:
    if contract.asset_class != "crypto_perps" or "funding_rate" not in feed_df.columns:
        return None
    return (
        feed_df.filter(
            (pl.col("timestamp").dt.hour() % 8 == 0) & pl.col("funding_rate").is_finite()
        )
        .select(
            "timestamp",
            "asset",
            pl.col("funding_rate").cast(pl.Float64).alias("rate"),
        )
        .unique(subset=["timestamp", "asset"], keep="last")
        .sort("timestamp", "asset")
    )


def _build_weights_by_ts(
    raw_pos: np.ndarray,
    ts: list[datetime],
    assets: list[str],
    start_idx: int,
    stop_idx: int,
) -> dict[datetime, dict[str, float]]:
    weights_by_ts: dict[datetime, dict[str, float]] = {}
    for idx in range(start_idx, stop_idx):
        row = raw_pos[idx]
        if idx == start_idx:
            nonzero = np.flatnonzero(row)
            if len(nonzero) == 0:
                continue
        elif np.array_equal(row, raw_pos[idx - 1]):
            continue
        else:
            nonzero = np.flatnonzero(row)
        weights_by_ts[ts[idx]] = {assets[j]: float(row[j]) for j in nonzero}
    return weights_by_ts


def _run_engine_replay(
    feed_df: pl.DataFrame,
    funding_df: pl.DataFrame | None,
    weights_by_ts: dict[datetime, dict[str, float]],
    contract: EvaluationContract,
    *,
    initial_cash: float,
    cost_multiplier: float,
    allow_leverage: bool,
    reject_on_insufficient_cash: bool,
) -> BacktestResult:
    cfg = _build_engine_config(
        contract,
        initial_cash=initial_cash,
        cost_multiplier=cost_multiplier,
        allow_leverage=allow_leverage,
        reject_on_insufficient_cash=reject_on_insufficient_cash,
    )
    rebalance_cfg = RebalanceConfig(
        allow_short=True,
        allow_fractional=True,
        max_single_weight=10.0,
        max_gross_leverage=None,
    )
    feed = DataFeed(
        prices_df=feed_df,
        bid_col="bid" if contract.execution_mode == "QUOTE_SIDE" else None,
        ask_col="ask" if contract.execution_mode == "QUOTE_SIDE" else None,
    )
    strat = _TargetWeightReplayStrategy(weights_by_ts, rebalance_cfg)
    return Engine(feed, strat, cfg, funding_df=funding_df).run()


def _analytical_1p5x_equity(base_res: BacktestResult) -> list[float]:
    extra_by_ts: dict[datetime, float] = {}
    for fill in base_res.fills:
        mult = fill.multiplier if fill.multiplier is not None else 1.0
        extra = 0.5 * (fill.commission + abs(fill.quantity) * fill.slippage * mult)
        extra_by_ts[fill.timestamp] = extra_by_ts.get(fill.timestamp, 0.0) + extra

    cum_extra = 0.0
    stressed: list[float] = []
    for ts_bar, eq in base_res.equity_curve:
        cum_extra += extra_by_ts.get(ts_bar, 0.0)
        stressed.append(float(eq) - cum_extra)
    return stressed


def replay_historical_run(
    run_id: str,
    repo_root: Path,
    initial_cash: float = 1_000_000.0,
) -> dict[str, Any]:
    """Replay a historical trial through ml4t.backtest.Engine and compare with legacy vector sim."""
    repo = Path(repo_root)
    row = _load_trial_metadata(repo, run_id)
    contract = load_contract(_resolve_contract_path(repo, row["contract_id"]))
    _assert_exploratory_path(repo, contract.exploratory_data_path)

    dataset = resolve_dataset(contract, repo)
    _assert_exploratory_path(repo, dataset.path)
    params = json.loads(row["config_json"] or "{}")
    panel_df, ts, assets, v_slice = prepare_panel(repo, dataset, params, contract)

    mod, module_source = _load_strategy_with_fallback(repo, str(row["strategy_module"]))
    out = mod.generate_positions(panel_df, contract, **params)
    raw_pos = np.asarray(out["positions"], dtype=float)

    ret_p, fund_p, fund_mask = simulation_inputs(panel_df, contract, ts, assets)
    sim1 = simulate_positions(
        raw_pos,
        ret_p["long"],
        fund_p,
        contract,
        v_slice,
        cost_multiplier=1.0,
        funding_mask=fund_mask,
        quote_returns=ret_p,
    )
    sim2 = simulate_positions(
        raw_pos,
        ret_p["long"],
        fund_p,
        contract,
        v_slice,
        cost_multiplier=2.0,
        funding_mask=fund_mask,
        quote_returns=ret_p,
    )
    m1 = calculate_metrics_from_returns(
        sim1["gross_returns"], sim1["net_returns"], sim1["turnover"], contract.bars_per_year
    )
    sr_vec = float(m1["net_sharpe"])
    sr_vec_gross = float(m1["gross_sharpe"])
    m2 = calculate_metrics_from_returns(
        sim2["gross_returns"], sim2["net_returns"], sim2["turnover"], contract.bars_per_year
    )
    sr_vec_2x = float(m2["net_sharpe"])

    if fund_p is not None:
        fund_slice = fund_p[v_slice]
        f_mask = np.asarray(fund_mask, dtype=bool)[v_slice, None]
        funding_cost_vec_bps = float(np.sum(sim1["positions"] * fund_slice * f_mask) * 1e4)
    else:
        funding_cost_vec_bps = 0.0

    gross_lev = np.sum(np.abs(sim1["positions"]), axis=1)
    max_gross_leverage = float(np.max(gross_lev)) if len(gross_lev) else 0.0
    cash_deficit_bars = int(np.sum(gross_lev > 1.0 + 1e-9))

    feed_start = max(0, v_slice.start - 1)
    feed_ts = ts[feed_start : v_slice.stop]
    feed_df = panel_df.filter(
        (pl.col("timestamp") >= feed_ts[0]) & (pl.col("timestamp") <= feed_ts[-1])
    )
    funding_df = _build_funding_df(feed_df, contract)
    weights_by_ts = _build_weights_by_ts(raw_pos, ts, assets, feed_start, v_slice.stop)

    # 1. Strict cash pass (allow_leverage=False)
    res_cash = _run_engine_replay(
        feed_df,
        funding_df,
        weights_by_ts,
        contract,
        initial_cash=initial_cash,
        cost_multiplier=1.0,
        allow_leverage=False,
        reject_on_insufficient_cash=True,
    )
    rejected_orders_no_leverage = len(res_cash.rejected_orders)

    # 2. Parity pass (allow_leverage=True, profiled)
    res_parity, parity_mem = _measure_peak_memory_and_time(
        lambda: _run_engine_replay(
            feed_df,
            funding_df,
            weights_by_ts,
            contract,
            initial_cash=initial_cash,
            cost_multiplier=1.0,
            allow_leverage=True,
            reject_on_insufficient_cash=False,
        )
    )

    parity_eq = [float(eq) for _, eq in res_parity.equity_curve]
    sr_engine = _sharpe_from_equity_values(parity_eq, contract.bars_per_year)
    abs_sr_delta = abs(sr_vec - sr_engine)

    funding_by_ts: dict[datetime, float] = {}
    for payment in res_parity.funding_payments:
        funding_by_ts[payment.timestamp] = (
            funding_by_ts.get(payment.timestamp, 0.0) + float(payment.cash_delta)
        )
    prev_equity_by_ts: dict[datetime, float] = {}
    prev_eq = float(initial_cash)
    for ts_bar, eq in res_parity.equity_curve:
        prev_equity_by_ts[ts_bar] = prev_eq if prev_eq != 0.0 else float(initial_cash)
        prev_eq = float(eq)

    total_funding_usd = float(res_parity.metrics.get("total_funding", 0.0))
    funding_cost_engine_bps = (
        sum(
            -delta / prev_equity_by_ts.get(ts_bar, float(initial_cash))
            for ts_bar, delta in funding_by_ts.items()
        )
        * 1e4
    )
    funding_delta_bps = abs(funding_cost_vec_bps - funding_cost_engine_bps)

    # 3. 1.5x cost stress parity (fill-derived analytical vs full 1.5x rerun)
    stressed_eq = _analytical_1p5x_equity(res_parity)
    sr_engine_1p5x_analytical = _sharpe_from_equity_values(stressed_eq, contract.bars_per_year)

    res_1p5x = _run_engine_replay(
        feed_df,
        funding_df,
        weights_by_ts,
        contract,
        initial_cash=initial_cash,
        cost_multiplier=1.5,
        allow_leverage=True,
        reject_on_insufficient_cash=False,
    )
    rerun_eq = [float(eq) for _, eq in res_1p5x.equity_curve]
    sr_engine_1p5x_rerun = _sharpe_from_equity_values(rerun_eq, contract.bars_per_year)
    analytical_vs_engine_delta = abs(sr_engine_1p5x_analytical - sr_engine_1p5x_rerun)
    terminal_return_delta_1p5x = abs(
        (stressed_eq[-1] / initial_cash - 1.0) - (rerun_eq[-1] / initial_cash - 1.0)
    )

    return {
        "run_id": run_id,
        "hypothesis_id": row["hypothesis_id"],
        "contract_id": contract.contract_id,
        "strategy_family": row["strategy_family"],
        "strategy_module": row["strategy_module"],
        "module_source": module_source,
        "verdict": row["verdict"],
        "bars_val": v_slice.stop - v_slice.start,
        "assets": len(assets),
        "sr_db_net": float(row["net_sharpe"]),
        "sr_vec": sr_vec,
        "sr_vec_gross": sr_vec_gross,
        "sr_vec_2x": sr_vec_2x,
        "sr_engine": sr_engine,
        "abs_sr_delta": abs_sr_delta,
        "max_gross_leverage": max_gross_leverage,
        "cash_deficit_bars": cash_deficit_bars,
        "rejected_orders_no_leverage": rejected_orders_no_leverage,
        "rejected_orders_parity": len(res_parity.rejected_orders),
        "fill_count": len(res_parity.fills),
        "total_funding_usd": total_funding_usd,
        "funding_cost_vec_bps": funding_cost_vec_bps,
        "funding_cost_engine_bps": funding_cost_engine_bps,
        "funding_delta_bps": funding_delta_bps,
        "sr_engine_1p5x_analytical": sr_engine_1p5x_analytical,
        "sr_engine_1p5x_rerun": sr_engine_1p5x_rerun,
        "1.5x_analytical_vs_engine_delta": analytical_vs_engine_delta,
        "1.5x_terminal_return_delta": terminal_return_delta_1p5x,
        "engine_wall_seconds": parity_mem["engine_wall_seconds"],
        "peak_rss_mb": parity_mem["peak_rss_mb"],
        "rss_delta_mb": parity_mem["rss_delta_mb"],
        "tracemalloc_peak_mb": parity_mem["tracemalloc_peak_mb"],
    }


def _capture_datafeed_session_error(df: pl.DataFrame) -> str | None:
    try:
        DataFeed(prices_df=df, session_col="session_date")
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _probe_session_assigned_panel(panel_df: pl.DataFrame) -> dict[str, Any]:
    assigned = assign_multi_exchange_sessions(panel_df)
    total_rows = assigned.height
    assets = sorted(assigned["asset"].unique().to_list())
    n_assets = len(assets)
    per_asset_counts = {
        str(row["asset"]): int(row["len"])
        for row in assigned.group_by("asset").len().sort("asset").iter_rows(named=True)
    }
    null_session_rows = int(assigned["session_date"].null_count())
    raw_err = _capture_datafeed_session_error(assigned)

    non_null = assigned.filter(pl.col("session_date").is_not_null())
    non_null_err = _capture_datafeed_session_error(non_null)

    # Collapse to 1 bar per (asset, session_date), preserving one timestamp per session_date
    # so DataFeed checks per-session asset completeness cleanly.
    collapsed = (
        non_null.sort("timestamp", "asset")
        .group_by(["session_date", "asset"])
        .agg(
            pl.col("open").last(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("volume").sum(),
        )
        .with_columns(
            pl.col("session_date")
            .cast(pl.Datetime("us", "UTC"))
            .alias("timestamp")
        )
        .sort("timestamp", "asset")
    )
    collapsed_err = _capture_datafeed_session_error(collapsed)

    ts_counts = assigned.group_by("timestamp").agg(pl.col("asset").n_unique().alias("n_assets"))
    incomplete_timestamp_count = int(ts_counts.filter(pl.col("n_assets") < n_assets).height)

    sess_counts = non_null.group_by("session_date").agg(
        pl.col("asset").unique().sort().alias("present_assets")
    )
    incomplete_sessions = sess_counts.filter(pl.col("present_assets").list.len() < n_assets)
    incomplete_session_count = int(incomplete_sessions.height)

    sample_partial_holiday: dict[str, Any] | None = None
    if incomplete_session_count > 0:
        first_row = incomplete_sessions.sort("session_date").row(0, named=True)
        present = list(first_row["present_assets"])
        missing = sorted(set(assets) - set(present))
        sample_partial_holiday = {
            "session_date": str(first_row["session_date"]),
            "present_assets": present,
            "missing_assets": missing,
        }

    return {
        "total_rows": total_rows,
        "asset_count": n_assets,
        "per_asset_row_counts": per_asset_counts,
        "null_session_date_rows": null_session_rows,
        "raw_datafeed_error": raw_err,
        "non_null_hourly_error": non_null_err,
        "session_collapsed_partial_holiday_error": collapsed_err,
        "incomplete_timestamp_count": incomplete_timestamp_count,
        "incomplete_session_count": incomplete_session_count,
        "partial_holiday_sessions": incomplete_session_count,
        "sample_partial_holiday": sample_partial_holiday,
    }


def _build_synthetic_mixed_venue_panel() -> pl.DataFrame:
    # 2025-01-26 (Sunday) is outside CME/HKEX regular sessions so assign_multi_exchange_sessions
    # assigns null session_date first; 2025-01-29 is Lunar New Year (HKEX closed, CME_Equity open).
    rows: list[dict[str, Any]] = []
    bars = [
        ("ES", datetime(2025, 1, 26, 12, 0, tzinfo=UTC)),
        ("HK33", datetime(2025, 1, 26, 12, 0, tzinfo=UTC)),
        ("ES", datetime(2025, 1, 28, 15, 0, tzinfo=UTC)),
        ("ES", datetime(2025, 1, 28, 16, 0, tzinfo=UTC)),
        ("ES", datetime(2025, 1, 29, 15, 0, tzinfo=UTC)),
        ("ES", datetime(2025, 1, 29, 16, 0, tzinfo=UTC)),
        ("HK33", datetime(2025, 1, 28, 2, 0, tzinfo=UTC)),
        ("HK33", datetime(2025, 1, 28, 3, 0, tzinfo=UTC)),
    ]
    for idx, (asset, ts_bar) in enumerate(bars):
        px = 5000.0 + idx if asset == "ES" else 20000.0 + idx
        rows.append(
            {
                "timestamp": ts_bar,
                "asset": asset,
                "open": px,
                "high": px + 2.0,
                "low": px - 2.0,
                "close": px + 1.0,
                "volume": 100.0,
            }
        )
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))


def probe_multi_exchange_calendar(repo_root: Path) -> dict[str, Any]:
    """Probe assign_multi_exchange_sessions + DataFeed(session_col) on synthetic and real panels."""
    repo = Path(repo_root)
    synthetic_probe = _probe_session_assigned_panel(_build_synthetic_mixed_venue_panel())

    datasets: dict[str, Any] = {}
    for cid in ("INDICES_1H_V2", "MULTI_ASSET_1H_V1"):
        try:
            contract_path = _resolve_contract_path(repo, cid)
        except FileNotFoundError:
            continue
        if not contract_path.is_file():
            continue
        contract = load_contract(contract_path)
        _assert_exploratory_path(repo, contract.exploratory_data_path)
        dataset = resolve_dataset(contract, repo)
        _assert_exploratory_path(repo, dataset.path)
        full_path = repo / dataset.path
        if full_path.is_file():
            datasets[cid] = _probe_session_assigned_panel(pl.read_parquet(full_path))

    return {
        "synthetic_mixed_venue": synthetic_probe,
        "datasets": datasets,
    }


def _profile_single_horizon(repo: Path, n_bars: int) -> dict[str, Any] | None:
    """Profile one 100-asset Engine horizon inside the current process."""
    contract = load_contract(_resolve_contract_path(repo, "CRYPTO_PERPS_TOP100_1H_V1"))
    _assert_exploratory_path(repo, contract.exploratory_data_path)
    dataset = resolve_dataset(contract, repo)
    _assert_exploratory_path(repo, dataset.path)
    panel_df = pl.read_parquet(repo / dataset.path)
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})
    panel_df = panel_df.sort("timestamp", "asset")

    ts = sorted(panel_df["timestamp"].unique().to_list())
    if n_bars > len(ts):
        return None
    assets = sorted(panel_df["asset"].unique().to_list())
    n_assets = len(assets)
    top_k = max(1, n_assets // 5)
    wt = 0.5 / float(top_k)

    slice_ts = ts[-n_bars:]
    sub_df = panel_df.filter(pl.col("timestamp") >= slice_ts[0])
    funding_df = _build_funding_df(sub_df, contract)

    weights_by_ts: dict[datetime, dict[str, float]] = {}
    for i, bar_ts in enumerate(slice_ts):
        if i % 24 == 0:
            offset = (i // 24) % n_assets
            longs = [assets[(offset + k) % n_assets] for k in range(top_k)]
            shorts = [assets[(offset + top_k + k) % n_assets] for k in range(top_k)]
            targets = {a: wt for a in longs}
            targets.update({a: -wt for a in shorts})
            weights_by_ts[bar_ts] = targets

    res, mem = _measure_peak_memory_and_time(
        lambda: _run_engine_replay(
            sub_df,
            funding_df,
            weights_by_ts,
            contract,
            initial_cash=1_000_000.0,
            cost_multiplier=1.0,
            allow_leverage=True,
            reject_on_insufficient_cash=False,
        )
    )
    wall_s = mem["engine_wall_seconds"]
    return {
        "bars": n_bars,
        "assets": n_assets,
        "rows": sub_df.height,
        "fill_count": len(res.fills),
        "engine_wall_seconds": wall_s,
        "bars_per_second": float(n_bars) / wall_s if wall_s > 0 else 0.0,
        "peak_rss_mb": mem["peak_rss_mb"],
        "rss_delta_mb": mem["rss_delta_mb"],
        "tracemalloc_peak_mb": mem["tracemalloc_peak_mb"],
        "under_300s_budget": wall_s < 300.0,
    }


def profile_engine_scaling(
    repo_root: Path,
    bar_counts: tuple[int, ...] = (500, 2000, 4000, 8760),
) -> list[dict[str, Any]]:
    """Profile 100-asset Engine wall time and isolated-subprocess peak RSS across bar horizons."""
    repo = Path(repo_root).resolve()
    script_repo = str(Path(__file__).resolve().parents[1])
    inline_code = (
        "import json, sys; from pathlib import Path; "
        f"sys.path.insert(0, {script_repo!r}); "
        "from scripts.shadow_engine_parity import _profile_single_horizon; "
        "print(json.dumps(_profile_single_horizon(Path(sys.argv[1]), int(sys.argv[2]))))"
    )
    results: list[dict[str, Any]] = []
    for n_bars in bar_counts:
        proc = subprocess.run(
            [sys.executable, "-c", inline_code, str(repo), str(n_bars)],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        rec = json.loads(proc.stdout.strip().splitlines()[-1])
        if rec is not None:
            results.append(rec)
    return results


def _render_parity_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = ["# Shadow Engine Parity Report", ""]

    lines.append("## 1. Analytical Oracles (`ml4t.backtest.Engine`)")
    lines.append("| Oracle | Status | Max Abs Error | Notes |")
    lines.append("| :--- | :--- | ---: | :--- |")
    for name, rec in report["analytical_oracles"].items():
        status = "PASS" if rec["passed"] else "FAIL"
        lines.append(f"| {name} | {status} | {rec['max_abs_error']:.3e} | {rec['notes']} |")
    lines.append("")

    lines.append("## 2. Historical Replays")
    lines.append(
        "| Run ID | Contract | Source | sr_vec | sr_engine | abs_sr_delta | "
        "max_gross_leverage | cash_deficit_bars | funding_delta_bps | "
        "1.5x_analytical_vs_engine_delta | engine_wall_seconds | peak_rss_mb |"
    )
    lines.append(
        "| :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    for r in report["historical_replays"]:
        lines.append(
            f"| {r['run_id']} | {r['contract_id']} | {r['module_source']} | "
            f"{r['sr_vec']:.4f} | {r['sr_engine']:.4f} | {r['abs_sr_delta']:.4f} | "
            f"{r['max_gross_leverage']:.3f} | {r['cash_deficit_bars']} | "
            f"{r['funding_delta_bps']:.4f} | {r['1.5x_analytical_vs_engine_delta']:.6f} | "
            f"{r['engine_wall_seconds']:.3f} | {r['peak_rss_mb']:.1f} |"
        )
    lines.append("")

    lines.append("## 3. Holiday & Session Behavior (`holiday_session_behavior`)")
    cal = report["holiday_session_behavior"]
    syn = cal.get("synthetic_mixed_venue", {})
    lines.append(
        f"- **Synthetic Mixed Venue (`ES` + `HK33`)**: "
        f"null_session_date_rows={syn.get('null_session_date_rows')}, "
        f"partial_holiday_sessions={syn.get('partial_holiday_sessions')}, "
        f"raw_error=`{syn.get('raw_datafeed_error')}`, "
        f"hourly_error=`{syn.get('non_null_hourly_error')}`, "
        f"collapsed_error=`{syn.get('session_collapsed_partial_holiday_error')}`"
    )
    for ds_name, ds in cal.get("datasets", {}).items():
        lines.append(
            f"- **{ds_name}**: rows={ds['total_rows']}, "
            f"null_session_date_rows={ds['null_session_date_rows']}, "
            f"incomplete_timestamps={ds['incomplete_timestamp_count']}, "
            f"incomplete_sessions={ds['incomplete_session_count']}, "
            f"raw_error=`{ds['raw_datafeed_error']}`, "
            f"collapsed_error=`{ds['session_collapsed_partial_holiday_error']}`"
        )
    lines.append("")

    lines.append("## 4. 100-Asset Engine Scaling (`engine_scaling_100_asset`)")
    scaling = report["engine_scaling_100_asset"]
    if scaling:
        lines.append(
            "| Bars | Assets | Rows | Fills | Wall (s) | Bars/s | Peak RSS (MB) | Tracemalloc Peak (MB) | <300s |"
        )
        lines.append("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |")
        for s in scaling:
            lines.append(
                f"| {s['bars']} | {s['assets']} | {s['rows']} | {s['fill_count']} | "
                f"{s['engine_wall_seconds']:.3f} | {s['bars_per_second']:.1f} | "
                f"{s['peak_rss_mb']:.1f} | {s['tracemalloc_peak_mb']:.2f} | {s['under_300s_budget']} |"
            )
    else:
        lines.append("_Scaling profiler skipped (`include_scaling=False`)._")
    lines.append("")
    return "\n".join(lines)


def generate_parity_report(
    repo_root: Path,
    json_out: Path,
    md_out: Path,
    *,
    include_scaling: bool = True,
) -> dict[str, Any]:
    """Run Seam A oracles, Seam B historical replays, calendar probe, and scaling profile."""
    repo = Path(repo_root)
    oracle_results = run_analytical_oracles()
    failed_oracles = [
        f"{name}(passed={res.passed}, max_abs_error={res.max_abs_error:.3e})"
        for name, res in oracle_results.items()
        if not (res.passed and res.max_abs_error <= 1e-8)
    ]
    if failed_oracles:
        raise AssertionError(
            f"Synthetic analytical oracle(s) failed: {', '.join(failed_oracles)}"
        )
    oracles = {name: asdict(res) for name, res in oracle_results.items()}
    replays = [
        replay_historical_run(run_id, repo)
        for run_id in (*HISTORICAL_RUN_IDS, *EXTRA_CONTROL_RUN_IDS)
    ]
    holiday_behavior = probe_multi_exchange_calendar(repo)
    scaling = profile_engine_scaling(repo) if include_scaling else []

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "analytical_oracles": oracles,
        "historical_replays": replays,
        "holiday_session_behavior": holiday_behavior,
        "engine_scaling_100_asset": scaling,
    }

    json_path = Path(json_out)
    md_path = Path(md_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_render_parity_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shadow ml4t.backtest Engine parity harness.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("artifacts/shadow_engine_parity_report.json"),
        help="Output JSON report path.",
    )
    parser.add_argument(
        "--md-out",
        type=Path,
        default=Path("artifacts/shadow_engine_parity_report.md"),
        help="Output Markdown report path.",
    )
    parser.add_argument(
        "--skip-scaling",
        action="store_true",
        help="Skip 100-asset scaling profiler.",
    )
    args = parser.parse_args(argv)
    report = generate_parity_report(
        args.repo_root,
        args.json_out,
        args.md_out,
        include_scaling=not args.skip_scaling,
    )
    print(
        f"Wrote parity report ({len(report['historical_replays'])} replays) to "
        f"{args.json_out} and {args.md_out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
