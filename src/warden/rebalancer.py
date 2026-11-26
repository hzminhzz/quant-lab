"""Target-weight rebalancing for Warden rules strategies."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ml4t.backtest.config import ExecutionPrice

from warden.engine_sim import resolve_execution_limits

if TYPE_CHECKING:
    from ml4t.backtest.broker import Broker
    from ml4t.backtest.types import Order

    from warden.contracts import EvaluationContract


def rebalance_cash_weights(
    broker: Broker,
    contract: EvaluationContract,
    target_weights: Mapping[str, float],
    *,
    headroom: float = 0.15,
    min_trade_value: float = 1.0,
    min_weight_change: float = 0.0,
) -> list[Order]:
    """Submit market orders toward a complete target book under a cash contract.

    Nonzero adjustments below either dust threshold are skipped; zero or omitted
    targets close held positions. Reductions precede additions. Headroom is a
    fraction of the contract cap reserved against costs and gaps, not a fill guarantee.
    """
    allow_leverage, cap = resolve_execution_limits(contract)
    if allow_leverage or cap is None:
        raise ValueError("cash rebalancing requires a non-leveraged contract")
    if not (0 < headroom < 1):
        raise ValueError("headroom must be between zero and one (exclusive)")
    if not all(math.isfinite(v) and v >= 0 for v in (min_trade_value, min_weight_change)):
        raise ValueError("dust thresholds must be finite and nonnegative")
    if not all(math.isfinite(weight) for weight in target_weights.values()):
        raise ValueError("target weights must be finite")
    gross = sum(abs(weight) for weight in target_weights.values())
    if not math.isfinite(gross):
        raise ValueError("gross target weight must be finite")
    budget = cap * (1 - headroom)
    scale = budget / gross if gross > budget else 1.0
    equity = broker.get_account_value()
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError("cash rebalancing requires finite positive account equity")
    # The pinned target-value primitive skips deltas below one cent itself.
    dust = max(0.01, min_trade_value, min_weight_change * equity)
    positions = broker.get_positions()
    pending = {order.asset for order in broker.get_pending_orders()}
    defer_increases = bool(pending)
    reductions: list[tuple[str, float]] = []
    increases: list[tuple[str, float, float]] = []
    committed_gross = 0.0
    for asset in sorted(target_weights.keys() | positions.keys()):
        if asset in pending:
            continue
        target = target_weights.get(asset, 0.0) * scale * equity
        position = positions.get(asset)
        price = broker.get_price_for_source(ExecutionPrice.PRICE, asset)
        priced = price is not None and math.isfinite(price) and price > 0
        if not priced and position and position.quantity != 0:
            # A liquidation may have to wait for the symbol's next bar.
            defer_increases = True
        if target == 0:
            if position and position.quantity != 0:
                reductions.append((asset, target))
            continue
        if price is None or not math.isfinite(price) or price <= 0:
            continue
        current = position.quantity * price * broker.get_multiplier(asset) if position else 0.0
        if abs(target - current) < dust:
            committed_gross += abs(current)
            continue
        if target * current > 0 and abs(target) < abs(current):
            reductions.append((asset, target))
            committed_gross += abs(target)
        elif target * current < 0:
            # Close before reopening on a subsequent decision. Treating the reversal as
            # one entry can make the next-open broker require buying power for both legs.
            reductions.append((asset, 0.0))
        else:
            increases.append((asset, current, target))
            committed_gross += abs(current)

    additions = []
    if not defer_increases:
        requested = sum(abs(target - current) for _, current, target in increases)
        room = max(0.0, budget * equity - committed_gross)
        allocation = room / requested if requested > room else 1.0
        for asset, current, target in increases:
            delta = (target - current) * allocation
            if abs(delta) >= dust:
                additions.append((asset, current + delta))

    orders = []
    for asset, target in reductions + additions:
        order = (
            broker.close_position(asset)
            if target == 0
            else broker.order_target_value(asset, target)
        )
        if order is not None:
            orders.append(order)
    return orders
