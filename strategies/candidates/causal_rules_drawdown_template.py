"""CausalRulesStrategy reference template with native ml4t-backtest risk overlays.

Demonstrates faithful portfolio drawdown risk gating (analogous to ``s183`` ``gdd``)
inside ``ml4t.backtest.Engine`` using ``RiskManager`` + ``MaxDrawdownLimit`` and
``StopLoss`` position rules without any hand-rolled P&L loop or ``generate_positions``.
"""

from __future__ import annotations

import math
from typing import Any

from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor
from ml4t.backtest.risk.portfolio import MaxDrawdownLimit, RiskManager
from ml4t.backtest.risk.position import StopLoss

from warden.contracts import EvaluationContract

# Portfolio drawdown gating couples asset allocations via live account equity.
portfolio_coupled = True


def required_symbols(params: dict[str, Any]) -> list[str] | None:
    """Declare target symbols explicitly when provided in spec parameters."""
    syms = params.get("target_symbols")
    return [str(s) for s in syms] if syms else None


class CausalDrawdownRulesStrategy(Strategy):
    """Causal EWMAC trend-following strategy gated by MaxDrawdownLimit & StopLoss."""

    def __init__(
        self,
        *,
        fast_span: int = 8,
        slow_span: int = 24,
        max_drawdown: float = 0.10,
        drawdown_floor: float = 0.50,
        stop_loss_pct: float = 0.05,
        rebalance_buffer: float = 0.10,
        max_single_weight: float = 0.40,
        max_gross_leverage: float = 0.90,
        warmup_bars: int = 4,
    ) -> None:
        self._alpha_fast = 2.0 / (max(1, int(fast_span)) + 1.0)
        self._alpha_slow = 2.0 / (max(2, int(slow_span)) + 1.0)
        self._max_drawdown = float(max_drawdown)
        self._drawdown_floor = float(drawdown_floor)
        self._stop_loss_pct = float(stop_loss_pct)
        self._rebalance_buffer = float(rebalance_buffer)
        self._max_single_weight = float(max_single_weight)
        self._max_gross_leverage = float(max_gross_leverage)
        self._warmup_bars = int(warmup_bars)

        self._executor = TargetWeightExecutor(
            RebalanceConfig(
                allow_short=True,
                allow_fractional=True,
                max_single_weight=self._max_single_weight,
                max_gross_leverage=self._max_gross_leverage,
            )
        )
        reduction_pct = min(1.0, max(0.05, 1.0 - self._drawdown_floor))
        self._risk = RiskManager(
            limits=[
                MaxDrawdownLimit(
                    max_drawdown=self._max_drawdown,
                    action="reduce",
                    reduction_pct=reduction_pct,
                )
            ]
        )
        self._ewm_fast: dict[str, float] = {}
        self._ewm_slow: dict[str, float] = {}
        self._ewm_var: dict[str, float] = {}
        self._prev_close: dict[str, float] = {}
        self._bars_seen: int = 0

    def on_start(self, broker: Any) -> None:
        broker.set_position_rules(StopLoss(pct=self._stop_loss_pct))
        self._risk.initialize(broker.get_account_value())

    def _drawdown_gate(self) -> float:
        dd = max(0.0, float(self._risk.current_drawdown))
        if dd >= self._max_drawdown:
            return self._drawdown_floor
        if self._max_drawdown <= 0.0:
            return 1.0
        # Smoothly interpolate toward drawdown_floor as drawdown approaches threshold.
        ratio = dd / self._max_drawdown
        return max(self._drawdown_floor, 1.0 - (1.0 - self._drawdown_floor) * (ratio**2))

    def on_data(
        self,
        timestamp: Any,
        data: dict[str, dict[str, Any]],
        context: dict[str, Any],
        broker: Any,
    ) -> None:
        _ = context
        self._bars_seen += 1
        equity = float(broker.get_account_value())
        held = {
            asset: pos.quantity
            for asset, pos in broker.get_positions().items()
            if pos.quantity != 0
        }
        results = self._risk.update(
            equity=equity,
            positions=held,
            timestamp=timestamp,
            broker=broker,
        )

        raw_signals: dict[str, float] = {}
        for asset, bar in data.items():
            px = float(bar["close"])
            if not math.isfinite(px) or px <= 0.0:
                continue
            prev = self._prev_close.get(asset)
            if prev is None:
                self._ewm_fast[asset] = px
                self._ewm_slow[asset] = px
                self._ewm_var[asset] = 1e-4
                self._prev_close[asset] = px
                continue

            ret = (px - prev) / prev
            self._prev_close[asset] = px
            self._ewm_fast[asset] = self._alpha_fast * px + (1.0 - self._alpha_fast) * self._ewm_fast[asset]
            self._ewm_slow[asset] = self._alpha_slow * px + (1.0 - self._alpha_slow) * self._ewm_slow[asset]
            self._ewm_var[asset] = 0.06 * (ret * ret) + 0.94 * self._ewm_var[asset]

            vol = math.sqrt(max(self._ewm_var[asset], 1e-8))
            spread = (self._ewm_fast[asset] - self._ewm_slow[asset]) / prev
            raw_signals[asset] = max(-1.0, min(1.0, spread / (2.0 * vol)))

        if any(r.breached for r in results):
            return
        if self._bars_seen < self._warmup_bars or not raw_signals:
            return

        gate = self._drawdown_gate()
        n_assets = len(raw_signals)
        per_asset_cap = min(self._max_single_weight, self._max_gross_leverage / max(1, n_assets))

        targets: dict[str, float] = {}
        for asset, sig in raw_signals.items():
            want = sig * per_asset_cap * gate
            pos = broker.get_position(asset)
            cur_px = float(data[asset]["close"])
            cur_wt = ((pos.quantity * cur_px) / equity) if (pos is not None and equity > 0.0) else 0.0
            if abs(want - cur_wt) > self._rebalance_buffer * max(abs(cur_wt), 1e-4):
                targets[asset] = want
            elif abs(cur_wt) > 1e-6:
                targets[asset] = cur_wt

        gross = sum(abs(w) for w in targets.values())
        if gross > self._max_gross_leverage and gross > 0.0:
            scale = self._max_gross_leverage / gross
            targets = {a: w * scale for a, w in targets.items()}

        if targets or held:
            self._executor.execute(targets, data, broker, timestamp=timestamp)


def configure_strategy(contract: EvaluationContract, params: dict[str, Any]) -> Strategy:
    """Instantiate the causal drawdown-gated rules strategy for Engine execution."""
    _ = contract
    return CausalDrawdownRulesStrategy(
        fast_span=int(params.get("fast_span", 8)),
        slow_span=int(params.get("slow_span", 24)),
        max_drawdown=float(params.get("max_drawdown", 0.10)),
        drawdown_floor=float(params.get("drawdown_floor", 0.50)),
        stop_loss_pct=float(params.get("stop_loss_pct", 0.05)),
        rebalance_buffer=float(params.get("rebalance_buffer", 0.10)),
        max_single_weight=float(params.get("max_single_weight", 0.40)),
        max_gross_leverage=float(params.get("max_gross_leverage", 0.90)),
        warmup_bars=int(params.get("warmup_bars", 4)),
    )
