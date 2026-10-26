"""Family-specific Stage 1 falsification gates and Stage 0 feasibility screen."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy import stats

from warden.contracts import EvaluationContract, HypothesisSpec, Stage1Report
from warden.registry import HypothesisBudgetExhaustedError


class GateFalsificationError(ValueError): pass


def compute_hac_tstat(ic_series: np.ndarray, max_lags: int = 5) -> float:
    x = np.asarray(ic_series, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 10 or float(np.std(x)) < 1e-6:
        return 0.0
    mean_x = float(np.mean(x))
    demeaned = x - mean_x
    hac_var = float(np.sum(demeaned ** 2) / n)
    for lag in range(1, min(max_lags + 1, n // 2)):
        hac_var += 2.0 * (1.0 - lag / (max_lags + 1.0)) * float(np.sum(demeaned[lag:] * demeaned[:-lag]) / n)
    return mean_x / math.sqrt(max(hac_var, 1e-12) / n)


def evaluate_stage0_feasibility(
    contract: EvaluationContract, hypothesis: HypothesisSpec, holding_volatility: float, trials_used: int
) -> bool:
    if trials_used >= 3:
        raise HypothesisBudgetExhaustedError(f"Hypothesis {hypothesis.hypothesis_id} budget exhausted ({trials_used}/3)")
    hurdle = contract.total_one_way_cost_decimal() * 2.0 * contract.min_expected_move_to_cost_ratio
    if holding_volatility < hurdle:
        raise GateFalsificationError(f"Holding volatility ({holding_volatility:.6f}) < hurdle ({hurdle:.6f})")
    return True


def evaluate_stage1_cross_sectional_ml(
    ic_series: Sequence[float] | np.ndarray, min_mean_ic: float = 0.015, min_hac_tstat: float = 2.0, max_lags: int = 5
) -> Stage1Report:
    arr = np.asarray(ic_series, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 10:
        return Stage1Report("cross_sectional_ml", False, {"mean_ic": 0.0, "ic_hac_tstat": 0.0}, "Insufficient IC series")
    mean_ic = float(np.mean(arr))
    t_stat = compute_hac_tstat(arr, max_lags=max_lags)
    passed = (mean_ic >= min_mean_ic) and (t_stat >= min_hac_tstat)
    return Stage1Report("cross_sectional_ml", passed, {"mean_ic": mean_ic, "ic_hac_tstat": t_stat}, f"Mean IC={mean_ic:.4f}, HAC t={t_stat:.2f}")


def evaluate_stage1_rules_ts(
    net_returns: Sequence[float] | np.ndarray,
    gross_returns: Sequence[float] | np.ndarray,
    one_way_cost: float,
    positions: Sequence[float] | np.ndarray | None = None,
    min_profit_factor: float = 1.15,
    min_time_in_market: float = 0.05,
    edge_cost_multiplier: float = 1.5,
) -> Stage1Report:
    net_arr, gross_arr = np.asarray(net_returns, dtype=float), np.asarray(gross_returns, dtype=float)
    active = np.any(np.abs(np.asarray(positions)) > 1e-6, axis=1) if positions is not None else np.abs(gross_arr) > 1e-10
    n_total, n_active = len(net_arr), int(np.sum(active))
    if n_total == 0 or n_active == 0:
        return Stage1Report("rules_ts", False, {}, "Zero active bars")

    time_in_market = n_active / n_total
    active_gross = gross_arr[active]
    cond_edge = float(np.mean(active_gross))
    gains = float(np.sum(net_arr[net_arr > 0]))
    losses = float(np.abs(np.sum(net_arr[net_arr < 0]))) + 1e-12
    profit_factor = gains / losses
    if positions is not None:
        pos_arr = np.asarray(positions, dtype=float)
        turn = float(np.sum(np.abs(np.diff(pos_arr, axis=0, prepend=0.0))))
        avg_holding = (2.0 * n_active / turn) if turn > 1e-6 else 1.0
    else:
        avg_holding = 1.0

    trade_edge = cond_edge * avg_holding

    rng = np.random.default_rng(42)
    block_size = max(1, min(24, int(math.sqrt(n_active))))
    n_blocks = math.ceil(n_active / block_size)
    boot_means = []
    for _ in range(200):
        starts = rng.integers(0, n_active, size=n_blocks)
        idxs = np.concatenate([(starts + i) % n_active for i in range(block_size)])[:n_active]
        boot_means.append(np.mean(active_gross[idxs]))
    p_value = float(np.mean(np.asarray(boot_means) <= 0.0))

    passed = (trade_edge > edge_cost_multiplier * one_way_cost) and (profit_factor >= min_profit_factor) and (time_in_market >= min_time_in_market) and (p_value < 0.05)
    return Stage1Report("rules_ts", passed, {"conditional_edge": cond_edge, "profit_factor": profit_factor, "time_in_market": time_in_market, "bootstrap_p_value": p_value, "avg_holding_bars": avg_holding, "trade_edge": trade_edge}, f"PF={profit_factor:.2f}, Edge={cond_edge*1e4:.1f}bps, TiM={time_in_market*100:.1f}%, Holding={avg_holding:.1f}h")


def evaluate_stage1_carry_rv(net_carry_series: Sequence[float], spread_series: Sequence[float]) -> Stage1Report:
    carry_arr, spread_arr = np.asarray(net_carry_series, dtype=float), np.asarray(spread_series, dtype=float)
    y = spread_arr[np.isfinite(spread_arr)]
    if len(y) < 20:
        return Stage1Report("carry_rv", False, {}, "Spread series too short")
    reg = stats.linregress(y[:-1], np.diff(y))
    adf_p = float(getattr(reg, "pvalue", reg[3])) if getattr(reg, "slope", reg[0]) < 0 else 1.0
    mean_carry = float(np.mean(carry_arr))
    return Stage1Report("carry_rv", (mean_carry > 0.0) and (adf_p < 0.05), {"net_carry_mean": mean_carry, "adf_p_value": adf_p}, f"Net carry={mean_carry*1e4:.1f}bps, ADF p={adf_p:.4f}")


def evaluate_stage1_meta_overlay(base_net_returns: Sequence[float], overlay_net_returns: Sequence[float], base_turnover: float, overlay_turnover: float) -> Stage1Report:
    r_base, r_over = np.asarray(base_net_returns, dtype=float), np.asarray(overlay_net_returns, dtype=float)
    cvar_b, cvar_o = float(np.abs(np.percentile(r_base, 1.0))), float(np.abs(np.percentile(r_over, 1.0)))
    cvar_red = (cvar_b - cvar_o) / (cvar_b + 1e-12)
    turn_red = (base_turnover - overlay_turnover) / (base_turnover + 1e-12)
    sr_b = float(np.mean(r_base) / (np.std(r_base, ddof=1) + 1e-12))
    sr_o = float(np.mean(r_over) / (np.std(r_over, ddof=1) + 1e-12))
    passed = (cvar_red >= 0.15) or (turn_red >= 0.20 and (sr_o - sr_b) >= -0.05)
    return Stage1Report("meta_overlay", passed, {"cvar_reduction": cvar_red, "turnover_reduction": turn_red, "sharpe_delta": sr_o - sr_b}, f"CVaR red={cvar_red*100:.1f}%, Turnover red={turn_red*100:.1f}%")
