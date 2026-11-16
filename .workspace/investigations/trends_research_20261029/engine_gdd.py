"""Drawdown gate (gdd) implemented in the execution engine, where the equity path exists.

The reference applies `gdd` inside `portfolio.simulate`, driven by each instrument's cumulative
P&L and the live NAV. Warden's runner simulates positions itself and returns no equity, so a
gated book cannot be screened there without hand-rolling a P&L loop. Here the gate is computed
inside a `ml4t.backtest.Strategy` from the broker's own state, which is the correct home for it.

Runs the same base weights twice - gate off, gate on - and reports both, so the gate's effect
is isolated rather than assumed.

Run: uv run python .workspace/investigations/trends_research_20261029/engine_gdd.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import polars as pl
from ml4t.backtest import BacktestConfig, DataFeed, Engine, Strategy
from ml4t.backtest.config import CommissionType, ExecutionMode, ExecutionPrice, SlippageType

sys.path.insert(0, str(Path("strategies/candidates")))
sys.path.insert(0, str(Path("strategies/candidates/carver_daily")))

from s183_reference import TAU, _idm, build_signal_frame

from warden.contracts import load_contract

BARS = 312
GDD_LOOKBACK, GDD_THRESHOLD, GDD_STEEPNESS, GDD_FLOOR = 64, -0.10, 10.0, 0.50
BUFFER = 0.10
VAL_START, VAL_END = "2023-01-01T00:00:00+00:00", "2025-07-01T00:00:00+00:00"


def base_weights(panel: pl.DataFrame, contract) -> tuple[pl.DataFrame, np.ndarray, int]:
    """Reference sizing without the drawdown gate: SIGNAL -> tau*w*idm*gvol/rel_vol."""
    feat = build_signal_frame(panel, contract)
    assets = sorted(feat["asset"].unique().to_list())
    wide = {c: feat.pivot(on="asset", index="timestamp", values=c).sort("timestamp")
            for c in ("SIGNAL", "gvol", "vol_abs", "ret", "close")}
    mat = {c: np.column_stack([wide[c][a].to_numpy() for a in assets]).astype(float) for c in wide}
    active = np.isfinite(mat["SIGNAL"]) & np.isfinite(mat["close"]) & (mat["close"] > 0)
    idm, n_act = _idm(mat["ret"], active)
    with np.errstate(invalid="ignore", divide="ignore"):
        rel_vol = mat["vol_abs"] * math.sqrt(256) / mat["close"]
        base = np.where(
            active & (rel_vol > 0),
            (np.nan_to_num(mat["SIGNAL"]) / 10.0) * TAU
            * np.where(n_act > 0, 1.0 / n_act, 0.0)[:, None] * idm[:, None]
            * np.where(np.isfinite(mat["gvol"]), mat["gvol"], 1.0) / rel_vol,
            0.0,
        )
    out = pl.DataFrame({"timestamp": wide["SIGNAL"]["timestamp"].to_list()}
                       | {a: base[:, i] for i, a in enumerate(assets)})
    out = out.unpivot(index="timestamp", variable_name="asset", value_name="weight")
    return out, mat["ret"], len(assets)


class GatedWeights(Strategy):
    """Reference drawdown gate fed by the engine's own per-instrument P&L.

    `cum_i` is `AssetTradingStats.total_realized_pnl + Position.unrealized_pnl`, i.e. what the
    broker reports, not a reconstructed price path. `span` is its 64-bar change and the
    denominator is the reference's `equity * (1/n_active) * idm`.
    """

    def __init__(self, stats_by_ts: dict, use_gdd: bool, buffer: float = BUFFER) -> None:
        self.stats = stats_by_ts
        self.use_gdd = use_gdd
        self.buffer = buffer
        self.prev_w: dict[str, float] = {}
        self.cum: dict[str, list[float]] = {}

    def on_data(self, timestamp, data, context, broker) -> None:
        equity = broker.get_account_value()
        n_act, idm = self.stats.get(timestamp, (0.0, 1.0))
        w = 1.0 / n_act if n_act > 0 else 0.0
        den = equity * w * idm
        for symbol, bar in data.items():
            price = bar["close"]
            if not price or price <= 0:
                continue
            position = broker.get_position(symbol)
            realized = broker.get_asset_stats(symbol).total_realized_pnl
            unrealized = position.unrealized_pnl(price) if position is not None else 0.0
            hist = self.cum.setdefault(symbol, [])
            hist.append(realized + unrealized)

            held_w = self.prev_w.get(symbol, 0.0)
            target = bar["signals"].get("weight", 0.0) or 0.0
            if self.use_gdd and len(hist) >= GDD_LOOKBACK + 2 and den > 0:
                span = hist[-2] - hist[-2 - GDD_LOOKBACK]
                dd = span / den
                target *= 1.0 - (1.0 - GDD_FLOOR) / (
                    1.0 + math.exp(-GDD_STEEPNESS * (GDD_THRESHOLD - dd))
                )
            target = target if abs(target - held_w) > self.buffer * abs(held_w) else held_w

            qty = position.quantity if position is not None else 0.0
            delta = target * equity / price - qty
            if abs(delta) * price > 1e-6 * equity:
                broker.submit_order(symbol, delta)
            self.prev_w[symbol] = target


def run(panel: pl.DataFrame, weights: pl.DataFrame, stats_by_ts: dict, use_gdd: bool,
        buffer: float = BUFFER):
    prices = panel.select("timestamp", "asset", "open", "high", "low", "close", "volume").rename(
        {"asset": "symbol"})
    signals = weights.rename({"asset": "symbol"})
    config = BacktestConfig(
        initial_cash=1_000_000.0,
        allow_short_selling=True,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.OPEN,
        commission_type=CommissionType.PERCENTAGE, commission_rate=0.5 / 10_000,
        slippage_type=SlippageType.PERCENTAGE, slippage_rate=0.5 / 10_000,
    )
    res = Engine(DataFeed(prices_df=prices, signals_df=signals),
                 GatedWeights(stats_by_ts, use_gdd, buffer=buffer), config).run()
    r = res.to_daily_returns(calendar=None).drop_nulls().to_numpy()
    cum = np.cumprod(1 + r)
    sd = float(r.std(ddof=1))
    return {
        "n": len(r),
        "sharpe": float(r.mean() / sd * math.sqrt(BARS)),
        "ann_ret": float(cum[-1] ** (BARS / len(r)) - 1),
        "ann_vol": sd * math.sqrt(BARS),
        "max_dd": float((cum / np.maximum.accumulate(cum) - 1).min()),
    }


def module_weights(panel: pl.DataFrame, contract) -> tuple[pl.DataFrame, np.ndarray, int]:
    """The exact matrix warden screened: `generate_positions` output, buffer already applied."""
    from s183_reference import generate_positions

    out = generate_positions(panel, contract)
    pos, assets = out["positions"], out["assets"]
    ts = panel["timestamp"].unique().sort().to_list()
    rows = []
    for t, stamp in enumerate(ts):
        for i, a in enumerate(assets):
            rows.append((stamp, a, float(pos[t, i])))
    weights = pl.DataFrame(rows, schema={"timestamp": panel["timestamp"].dtype,
                                         "asset": pl.Utf8, "weight": pl.Float64},
                           orient="row")
    ret = panel.pivot(on="asset", index="timestamp", values="close").sort("timestamp")
    ret = np.column_stack([ret[a].pct_change().to_numpy() for a in assets])
    return weights, ret, len(assets)


def main() -> None:
    contract = load_contract(Path("contracts/multi_asset_1d_v1.yaml"))
    panel = pl.read_parquet(contract.exploratory_data_path).sort("timestamp", "asset")
    weights, ret, n_assets = module_weights(panel, contract)
    active = np.isfinite(ret)
    idm, n_act = _idm(ret, active)
    ts = panel["timestamp"].unique().sort().to_list()
    stats_by_ts = {t: (float(n), float(i)) for t, n, i in zip(ts, n_act, idm)}

    import datetime as dt
    lo = dt.datetime.fromisoformat(VAL_START)
    hi = dt.datetime.fromisoformat(VAL_END)
    val = panel.filter((pl.col("timestamp") >= lo) & (pl.col("timestamp") < hi))
    val_ts = set(val["timestamp"].unique().to_list())
    w_val = weights.filter(pl.col("timestamp").is_in(val_ts))

    # buffer=0: these weights are already buffered by generate_positions
    for label, use in (("module matrix, gate off", False), ("module matrix, gate on", True)):
        s = run(val, w_val, stats_by_ts, use, buffer=0.0)
        print(f"{label:9} n={s['n']:4d} sharpe={s['sharpe']:6.3f} ann_ret={s['ann_ret']:7.2%} "
              f"vol={s['ann_vol']:6.2%} maxDD={s['max_dd']:7.2%}")
    print(f"assets={n_assets} val rows={val.height}")


if __name__ == "__main__":
    main()
