"""Non-gating research diagnostics: trade-level metrics and timing skill.

These report economic conventions explicitly and never change a promotion
threshold. The bar-level ``profit_factor`` reported by the runner is unchanged.
"""

from __future__ import annotations

import math

import numpy as np

TRADE_CONVENTIONS = (
    "closed_trade_profit_factor: maximal contiguous runs of same-sign non-zero net exposure; "
    "sign flips and flat gaps start a new trade; partial scale-ins/scale-outs stay inside their "
    "trade; fees are charged on the bar they occur (turnover based) and net returns are summed "
    "per trade; ratio = sum(positive trade returns) / |sum(negative trade returns)|"
)
TIMING_CAVEAT = (
    "Exposure-aware circular-shift null: shifting preserves the declared position exposure "
    "(time-in-market and magnitudes) but assumes the return process is roughly stationary over "
    "the window. Under regimes/trends the empirical tail fraction is not an exact "
    "distribution-free p-value; read it as a labelled diagnostic, never as a gate."
)


def compute_trade_metrics(positions, net_returns, asset_returns=None, bars_per_year: int = 8760,
                          one_way_cost: float = 0.0) -> dict:
    """Bar-level PF (unchanged), closed-trade PF and benchmark-relative diagnostics."""
    pos = np.asarray(positions, dtype=float)
    net = np.asarray(net_returns, dtype=float)
    if pos.shape[0] != net.shape[0]:
        raise ValueError("positions and returns must cover the same bars")
    gross_exposure = np.abs(pos).sum(axis=1)
    active = gross_exposure > 1e-9
    # A dollar-neutral book has zero net exposure: use the dominant leg's sign, never net exposure.
    dominant = np.take_along_axis(pos, np.argmax(np.abs(pos), axis=1)[:, None], axis=1)[:, 0]
    sign = np.sign(dominant)
    prev_active, prev_sign = np.roll(active, 1), np.roll(sign, 1)
    prev_active[0], prev_sign[0] = False, 0.0
    starts = active & (~prev_active | (sign != prev_sign))
    episodes = (np.bincount(np.cumsum(starts)[active] - 1, weights=net[active], minlength=int(starts.sum()))
                if active.any() else np.zeros(0))
    gains = float(episodes[episodes > 0].sum())
    losses = abs(float(episodes[episodes < 0].sum())) + 1e-12
    bar_gains = float(net[net > 0].sum())
    bar_losses = abs(float(net[net < 0].sum())) + 1e-12
    active_net = net[active]
    sr = (float(np.mean(active_net) / (np.std(active_net, ddof=1) + 1e-12) * math.sqrt(bars_per_year))
          if len(active_net) > 1 else 0.0)
    metrics = {
        "bar_profit_factor": bar_gains / bar_losses,
        "closed_trade_profit_factor": gains / losses if len(episodes) else 0.0,
        "closed_trade_hit_rate": float(np.mean(episodes > 0)) if len(episodes) else 0.0,
        "closed_trade_count": len(episodes),
        "active_sharpe": sr,
        "active_bar_share": float(np.mean(active)),
        "one_way_cost_decimal": float(one_way_cost),
        "trade_conventions": TRADE_CONVENTIONS,
        "gating": False,
    }
    if asset_returns is not None:
        bench = np.asarray(asset_returns, dtype=float).mean(axis=1)  # constant 1/N, rebalanced every bar
        diff = net - bench
        metrics.update({
            "benchmark_net_sharpe": float(np.mean(bench) / (np.std(bench, ddof=1) + 1e-12) * math.sqrt(bars_per_year)),
            "equal_weight_rebalanced_net_sharpe": float(np.mean(bench) / (np.std(bench, ddof=1) + 1e-12) * math.sqrt(bars_per_year)),
            "information_ratio": float(np.mean(diff) / (np.std(diff, ddof=1) + 1e-12) * math.sqrt(bars_per_year)),
            "relative_return_annualized": float(np.mean(diff) * bars_per_year),
            "benchmark_conventions": "equal-weight rebalanced (constant 1/N every bar), no costs; "
                                     "NOT buy-and-hold; strategy net returns include fees",
            "gating": False,
        })
    return metrics


def compute_shift_skill_pvalue(positions, returns, label_horizon: int, k: int = 200, seed: int = 42) -> dict:
    """Deterministic exposure-preserving circular-shift timing diagnostic (no gate)."""
    pos, rets = np.asarray(positions, dtype=float), np.asarray(returns, dtype=float)
    if pos.shape != rets.shape:
        raise ValueError("positions and returns must have the same shape")
    n_bars = pos.shape[0]
    low, high = int(label_horizon) * 3, int(n_bars - label_horizon * 3)
    if low < 1 or high - low < 2:
        raise ValueError("insufficient bars for exposure-preserving shifts")
    observed = float(np.mean(np.sum(pos * rets, axis=1)))
    rng = np.random.default_rng(seed)
    shifts = rng.integers(low, high, size=k)
    null = np.array([float(np.mean(np.sum(np.roll(pos, int(s), axis=0) * rets, axis=1))) for s in shifts])
    return {
        "observed_statistic": observed,
        "p_value": float((1 + int(np.sum(null >= observed))) / (k + 1)),
        "null_mean": float(np.mean(null)), "null_std": float(np.std(null, ddof=1)),
        "null_q05": float(np.quantile(null, 0.05)), "null_q95": float(np.quantile(null, 0.95)),
        "k": int(k), "seed": int(seed), "valid_shift_range": [low, high],
        "time_in_market": float(np.mean(np.abs(pos).sum(axis=1) > 1e-12)),
        "exposure_preserved": True, "gate_applied": False,
        "position_basis": "long/short net return basis; QUOTE_SIDE PnL is not modelled here",
        "long_basis_only": bool(np.any(pos < 0)), "caveat": TIMING_CAVEAT,
    }
