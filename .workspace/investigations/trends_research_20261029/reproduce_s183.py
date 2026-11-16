"""DISCLOSED UNREGISTERED DIAGNOSTIC - not a research entry point.

CORRECTION: every number this script produced before 2026-10-29 was a LONG-ONLY book.
`BacktestConfig.allow_short_selling` defaults to False, so the engine silently clamped
short legs at zero. With shorts enabled the reference book scores about -0.09, not +0.7.

The authoritative reproduction of this hypothesis is `HYP-2026-126` run through
`uv run warden screen experiments/active/hyp_2026_126_screen.yaml`, which records the
attempt in the registry and applies the contract's gates. This script predates that
routing and must never be used to produce a strategy verdict: it has no pre-registration,
no selection accounting and no audit trail. It is kept only because `ml4t.backtest`'s
event-driven engine measures execution realism that the harness's vectorised simulator
does not, and because it is the worked example for REPRODUCTION.md.

Signal construction lives in `strategies/candidates/s183_composite.py` (single source of
truth, leakage-checked); this file only sizes, executes and scores it.

Usage: uv run python .workspace/investigations/trends_research_20261029/reproduce_s183.py
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import polars as pl
from ml4t.backtest import BacktestConfig, DataFeed, Engine, Strategy
from ml4t.backtest.config import CommissionType, ExecutionMode, ExecutionPrice, SlippageType
from ml4t.diagnostic.api import analyze_signal
from ml4t.diagnostic.evaluation.stats import (
    deflated_sharpe_ratio,
    deflated_sharpe_ratio_from_statistics,
)

sys.path.insert(0, str(Path("strategies/candidates")))
sys.path.insert(0, str(Path("strategies/candidates/carver_daily")))

from s183_composite import add_weights, buffered, build_signal_frame

from warden.contracts import load_contract

PANEL_SHA = "8f0ae0636c779754291830692de5f1eaa40ce7792d4e0a87693e129bbfafd1f4"
OUT = Path(".workspace/investigations/trends_research_20261029/reproduction")
BARS_PER_YEAR = 312
NOISE_FLOOR = 1.3942022742422475  # warden contract_search_state, annualised expected max under the null
FEE_BPS, SLIP_BPS = 0.5, 0.5


class TargetWeights(Strategy):
    """Submit the delta to the target weight each bar; the engine fills NEXT_BAR at OPEN."""

    def on_data(self, timestamp, data, context, broker) -> None:
        equity = broker.get_account_value()
        for symbol, bar in data.items():
            price = bar["close"]
            if not price or price <= 0:
                continue
            target = bar["signals"].get("weight", 0.0) or 0.0
            position = broker.get_position(symbol)
            held = position.quantity if position is not None else 0.0
            delta = target * equity / price - held
            if abs(delta) * price > 1e-6 * equity:
                broker.submit_order(symbol, delta)


def run_engine(panel: pl.DataFrame, weights: pl.DataFrame, cash: float) -> tuple[pl.Series, dict]:
    prices = panel.select("timestamp", "asset", "open", "high", "low", "close", "volume").rename(
        {"asset": "symbol"}
    )
    signals = weights.select("timestamp", "asset", "weight").rename({"asset": "symbol"})
    config = BacktestConfig(
        initial_cash=cash,
        allow_short_selling=True,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.OPEN,
        commission_type=CommissionType.PERCENTAGE,
        commission_rate=FEE_BPS / 10_000,
        slippage_type=SlippageType.PERCENTAGE,
        slippage_rate=SLIP_BPS / 10_000,
    )
    result = Engine(DataFeed(prices_df=prices, signals_df=signals), TargetWeights(), config).run()
    # calendar=None: the panel is already one observation per date; no re-aggregation.
    return result.to_daily_returns(calendar=None), result.metrics


def stats(returns: pl.Series) -> dict[str, float]:
    r = returns.drop_nulls().to_numpy()
    cum = np.cumprod(1 + r)
    dd = float((cum / np.maximum.accumulate(cum) - 1).min())
    sd = float(r.std(ddof=1))
    return {
        "n": len(r),
        "sharpe": float(r.mean() / sd * math.sqrt(BARS_PER_YEAR)) if sd else 0.0,
        "ann_ret": float(cum[-1] ** (BARS_PER_YEAR / len(r)) - 1),
        "ann_vol": sd * math.sqrt(BARS_PER_YEAR),
        "max_dd": dd,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    contract = load_contract(Path("contracts/multi_asset_1d_v1.yaml"))
    panel = pl.read_parquet(contract.exploratory_data_path)
    digest = hashlib.sha256(Path(contract.exploratory_data_path).read_bytes()).hexdigest()
    if digest != PANEL_SHA:
        raise SystemExit(f"panel digest {digest} != contract pin {PANEL_SHA}")
    print(f"[1/4] panel {panel.height:,} rows, {panel['asset'].n_unique()} instruments")

    print("[2/4] sleeves (ml4t-engineer features + leakage-checked signal module)")
    feat = build_signal_frame(panel, contract)

    rows = []
    for vol_target in (0.20, 0.13):
        print(f"[3/4] size + ml4t-backtest engine at vol_target={vol_target}")
        w = add_weights(feat, vol_target, cap=0.5)
        assets = sorted(w["asset"].unique().to_list())
        mat = w.select("timestamp", "asset", "weight").pivot(
            on="asset", index="timestamp", values="weight"
        ).sort("timestamp")
        target = np.nan_to_num(np.column_stack([mat[a].to_numpy() for a in assets]).astype(float))
        observed = np.column_stack([mat[a].is_not_null().to_numpy() for a in assets])
        held = buffered(target, observed)
        weight_df = pl.DataFrame(
            {"timestamp": mat["timestamp"].to_list()} | {a: held[:, i] for i, a in enumerate(assets)}
        ).unpivot(index="timestamp", variable_name="asset", value_name="weight")

        daily, metrics = run_engine(panel, weight_df, cash=1_000_000.0)
        r = daily.drop_nulls().to_numpy()
        psr = deflated_sharpe_ratio(r, frequency="daily", periods_per_year=BARS_PER_YEAR)
        from scipy import stats as sst

        dsr = deflated_sharpe_ratio_from_statistics(
            observed_sharpe=psr.sharpe_ratio, n_samples=len(r), n_trials=1,
            benchmark_sharpe=NOISE_FLOOR, skewness=float(sst.skew(r)),
            excess_kurtosis=float(sst.kurtosis(r)), frequency="daily",
            periods_per_year=BARS_PER_YEAR,
        )
        s = stats(daily)
        rows.append({"vol_target": vol_target, **s, "psr": float(psr.probability),
                     "dsr_at_floor": float(dsr.probability), "trades": metrics.get("num_trades")})
        daily.to_frame("return").write_parquet(OUT / f"daily_returns_vol{vol_target:.2f}.parquet")
        print(f"      Sharpe {s['sharpe']:.2f} vol {s['ann_vol']:.2%} maxDD {s['max_dd']:.2%} "
              f"PSR {psr.probability:.3f} DSR@floor {dsr.probability:.4f}")

    print("[4/4] ml4t-diagnostic signal analysis (trend sleeve, IC by horizon)")
    factor = feat.select(
        pl.col("timestamp").alias("date"), pl.col("asset"), pl.col("f_trend").alias("factor")
    ).drop_nulls()
    prices = panel.select(
        pl.col("timestamp").alias("date"), pl.col("asset"), pl.col("close").alias("price")
    )
    sig = analyze_signal(factor, prices, periods=(1, 5, 21), factor_col="factor",
                         date_col="date", asset_col="asset", price_col="price", min_assets=10)
    def by_period(value: object) -> dict[str, float]:
        if isinstance(value, dict):
            return {str(k): float(v) for k, v in value.items()}
        return {str(h): float(v) for h, v in zip(sig.periods, value)}  # type: ignore[arg-type]

    summary = {
        "panel": {"rows": panel.height, "instruments": panel["asset"].n_unique(),
                  "start": str(panel["timestamp"].min()), "end": str(panel["timestamp"].max())},
        "runs": rows,
        "signal": {
            "ic": by_period(sig.ic),
            "ic_t_stat": by_period(sig.ic_t_stat),
            "ic_ir": by_period(sig.ic_ir),
            "monotonicity": by_period(sig.monotonicity),
            "turnover": by_period(sig.turnover),
        },
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
