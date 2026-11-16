"""Reconcile warden's simulator against ml4t.backtest on identical inputs.

The two disagree by >1 Sharpe on the S183 matrix. This fixture removes the ambiguity: one
asset, five bars, a known weight path, zero costs. Each simulator is compared with a
hand-computed P&L under its own stated convention, so a mismatch is a bug and a match is a
verified convention difference - not an assertion.

  ml4t.backtest NEXT_BAR/OPEN (share-based)
      at close t-1: shares = w_t-1 * E_t-1 / close_t-1, filled at open_t
      E_t = E_t-1 + prev_shares*(open_t - close_t-1) + new_shares*(close_t - open_t)

  warden (weight-based, open-to-open, delay 1)
      gross_t = pos_t-1 * (open_t+1 / open_t - 1)

Run: uv run python .workspace/investigations/trends_research_20261029/reconcile_sims.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl
from ml4t.backtest import BacktestConfig, DataFeed, Engine, Strategy
from ml4t.backtest.config import CommissionType, SlippageType

sys.path.insert(0, str(Path("strategies/candidates")))
sys.path.insert(0, str(Path("strategies/candidates/carver_daily")))

from warden.contracts import load_contract
from warden.runner import simulate_positions

OPEN = np.array([100.0, 102.0, 101.0, 104.0, 103.0])
CLOSE = np.array([101.0, 103.0, 100.0, 105.0, 104.0])
WEIGHT = np.array([0.0, 1.0, 1.0, -1.0, 0.0])  # decided at each close
CASH = 1_000_000.0


class FixedWeights(Strategy):
    def on_data(self, timestamp, data, context, broker) -> None:
        equity = broker.get_account_value()
        for symbol, bar in data.items():
            price = bar["close"]
            target = bar["signals"].get("weight", 0.0) or 0.0
            pos = broker.get_position(symbol)
            held = pos.quantity if pos is not None else 0.0
            delta = target * equity / price - held
            if abs(delta) * price > 1e-9 * equity:
                broker.submit_order(symbol, delta)


def _panel() -> pl.DataFrame:
    stamps = [f"2024-01-0{i + 1}" for i in range(len(OPEN))]
    return pl.DataFrame(
        {
            "timestamp": stamps,
            "symbol": ["X"] * len(OPEN),
            "open": OPEN,
            "high": np.maximum(OPEN, CLOSE),
            "low": np.minimum(OPEN, CLOSE),
            "close": CLOSE,
            "volume": [1e12] * len(OPEN),  # no volume participation cap
        }
    ).with_columns(pl.col("timestamp").str.to_datetime(time_zone="UTC"))


def engine_returns() -> np.ndarray:
    prices = _panel()
    signals = prices.select("timestamp", "symbol").with_columns(weight=pl.Series(WEIGHT))
    config = BacktestConfig(
        initial_cash=CASH, allow_short_selling=True,
        commission_type=CommissionType.NONE, slippage_type=SlippageType.NONE
    )
    result = Engine(DataFeed(prices_df=prices, signals_df=signals), FixedWeights(), config).run()
    return result.to_daily_returns(calendar=None).to_numpy()


def hand_engine_returns() -> np.ndarray:
    """Share-based recursion: decide at close t-1 with close t-1 equity, fill at open t."""
    equity = CASH
    prev_shares = 0.0
    out = np.zeros(len(OPEN))
    for t in range(1, len(OPEN)):
        e0 = equity
        new_shares = WEIGHT[t - 1] * e0 / CLOSE[t - 1]
        equity = e0 + prev_shares * (OPEN[t] - CLOSE[t - 1]) + new_shares * (CLOSE[t] - OPEN[t])
        out[t] = equity / e0 - 1.0
        prev_shares = new_shares
    return out


def hand_warden_gross() -> np.ndarray:
    out = np.zeros(len(OPEN))
    for t in range(1, len(OPEN) - 1):
        out[t] = WEIGHT[t - 1] * (OPEN[t + 1] / OPEN[t] - 1.0)
    return out


def warden_gross() -> np.ndarray:
    contract = load_contract(Path("contracts/multi_asset_1d_v1.yaml"))
    oo = np.zeros((len(OPEN), 1))
    oo[:-1, 0] = OPEN[1:] / OPEN[:-1] - 1.0
    pos = WEIGHT.reshape(-1, 1)
    sim = simulate_positions(pos, oo, None, contract, slice(0, len(OPEN)), cost_multiplier=0.0)
    return sim["gross_returns"]


def main() -> None:
    e, eh = engine_returns(), hand_engine_returns()
    w, wh = warden_gross(), hand_warden_gross()
    print("engine  (impl):", np.round(e, 6))
    print("engine  (hand):", np.round(eh, 6))
    print("max |diff|     :", float(np.max(np.abs(e - eh))))
    print()
    print("warden  (impl):", np.round(w, 6))
    print("warden  (hand):", np.round(wh, 6))
    print("max |diff|     :", float(np.max(np.abs(w - wh))))
    print()
    print("same decisions, two conventions -> cumulative difference:")
    ce, cw = float(np.prod(1 + e) - 1), float(np.sum(w))
    print(f"  engine total {ce:+.6f}   warden total {cw:+.6f}   gap {ce - cw:+.6f}")
    print("\nboth implementations match their own convention:",
          float(np.max(np.abs(e - eh))) < 1e-9 and float(np.max(np.abs(w - wh))) < 1e-12)


if __name__ == "__main__":
    main()
