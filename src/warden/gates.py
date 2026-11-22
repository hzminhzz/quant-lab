"""Family-specific Stage 1 falsification gates and Stage 0 feasibility screen."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import polars as pl
from ml4t.diagnostic.metrics import compute_ic_hac_stats, cross_sectional_ic_series

from warden.contracts import EvaluationContract, HypothesisSpec, Stage1Report
from warden.registry import PER_HYPOTHESIS_CAP, HypothesisBudgetExhaustedError


class GateFalsificationError(ValueError): pass


# Single gate vocabulary: runner, cli audit and qualify compare against these, never literals.
# PER_HYPOTHESIS_CAP is a governance fact owned by the registry (imported above), not redefined here.
MIN_SR = {"cross_sectional_ml": 1.2, "rules_ts": 1.0}
SR2_FLOOR, SRD_FLOOR, DSR_FLOOR, IC_T_FLOOR = 0.50, 0.0, 0.90, 2.0
AUDIT_DSR_FLOOR, TRIMMED_SHARPE_FLOOR = 0.80, 0.0
NEIGHBOR_STABILITY_RATIO, NEIGHBOR_MAX_SR_FLOOR = 0.70, 0.0
HOLDOUT_SR_FLOOR, HOLDOUT_RATIO_FLOOR, REVISE_SR_FLOOR = 0.80, 0.50, 1.0


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
    if trials_used >= PER_HYPOTHESIS_CAP:
        raise HypothesisBudgetExhaustedError(f"Hypothesis {hypothesis.hypothesis_id} budget exhausted ({trials_used}/{PER_HYPOTHESIS_CAP})")
    hurdle = contract.total_one_way_cost_decimal() * 2.0 * contract.min_expected_move_to_cost_ratio
    if holding_volatility < hurdle:
        raise GateFalsificationError(f"Holding volatility ({holding_volatility:.6f}) < hurdle ({hurdle:.6f})")
    return True


def evaluate_stage1_cross_sectional_ml(
    ic_series: Sequence[float] | np.ndarray,
    min_mean_ic: float = 0.015,
    min_hac_tstat: float = IC_T_FLOOR,
    max_lags: int = 5,
    label_horizon: int | None = None,
) -> Stage1Report:
    arr = np.asarray(ic_series, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 10:
        return Stage1Report("cross_sectional_ml", False, {"mean_ic": 0.0, "ic_hac_tstat": 0.0}, "Insufficient IC series")
    if float(np.std(arr)) < 1e-6:
        return Stage1Report(
            "cross_sectional_ml",
            False,
            {"mean_ic": float(np.mean(arr)), "ic_hac_tstat": 0.0},
            "Near-zero IC variance",
        )
    if label_horizon is not None:
        eff_horizon = min(max(1, int(label_horizon)), max(1, len(arr) - 1))
        eff_maxlags = None
    else:
        eff_horizon = None
        eff_maxlags = min(max_lags, max(1, len(arr) // 2 - 1))
    hac = compute_ic_hac_stats(
        arr,
        maxlags=eff_maxlags,
        label_horizon=eff_horizon,
        kernel="bartlett",
        allow_naive_fallback=True,
    )
    raw_mean = float(hac["mean_ic"])
    mean_ic = raw_mean if math.isfinite(raw_mean) else float(np.mean(arr))
    raw_t = float(hac["t_stat"])
    t_stat = raw_t if math.isfinite(raw_t) else 0.0
    raw_p = float(hac["p_value"])
    p_value = raw_p if math.isfinite(raw_p) else 1.0
    effective_lags = int(hac["effective_lags"])
    passed = (mean_ic >= min_mean_ic) and (t_stat >= min_hac_tstat)
    return Stage1Report(
        "cross_sectional_ml",
        passed,
        {
            "mean_ic": mean_ic,
            "ic_hac_tstat": t_stat,
            "ic_hac_p_value": p_value,
            "effective_lags": effective_lags,
        },
        f"Mean IC={mean_ic:.4f}, HAC t={t_stat:.2f}",
    )


def compute_cross_sectional_ic_for_screen(
    predictions: pl.DataFrame,
    forward_returns: pl.DataFrame,
    *,
    pred_col: str = "prediction",
    ret_col: str = "forward_return",
    date_col: str = "timestamp",
    entity_col: str = "asset",
    min_obs: int = 5,
) -> np.ndarray:
    """Compute per-timestamp cross-sectional Spearman IC array for Stage 1 screening."""
    ic_df = cross_sectional_ic_series(
        predictions,
        forward_returns,
        pred_col=pred_col,
        ret_col=ret_col,
        date_col=date_col,
        entity_col=entity_col,
        min_obs=min_obs,
    )
    arr = ic_df.get_column("ic").cast(pl.Float64).drop_nulls().to_numpy()
    return arr[np.isfinite(arr)]


def evaluate_stage1_rules_ts(
    net_returns: Sequence[float] | np.ndarray,
    gross_returns: Sequence[float] | np.ndarray,
    one_way_cost: float,
    positions: Sequence[float] | np.ndarray | None = None,
    min_profit_factor: float = 1.15,
    min_time_in_market: float = 0.05,
    edge_cost_multiplier: float = 1.5,
    bars_per_year: int = 8760,
    holding_horizon_bars: int | None = None,
) -> Stage1Report:
    net_arr = np.asarray(net_returns, dtype=float)
    gross_arr = np.asarray(gross_returns, dtype=float)
    pos_arr = np.asarray(positions, dtype=float) if positions is not None else None
    if pos_arr is not None:
        if pos_arr.ndim == 2:
            exposure = pos_arr.sum(axis=1)
            active = np.any(np.abs(pos_arr) > 1e-6, axis=1)
        else:
            exposure = pos_arr
            active = np.abs(pos_arr) > 1e-6
    else:
        exposure = None
        active = np.abs(gross_arr) > 1e-10
    n_total, n_active = len(net_arr), int(np.sum(active))
    if n_total == 0 or n_active == 0:
        return Stage1Report("rules_ts", False, {}, "Zero active bars")

    time_in_market = n_active / n_total
    active_gross = gross_arr[active]
    cond_edge = float(np.mean(active_gross))
    bar_gains = float(np.sum(net_arr[net_arr > 0]))
    bar_losses = float(np.abs(np.sum(net_arr[net_arr < 0]))) + 1e-12
    bar_profit_factor = bar_gains / bar_losses
    if pos_arr is not None:
        turn = float(np.sum(np.abs(np.diff(pos_arr, axis=0, prepend=0.0))))
        avg_holding = (2.0 * n_active / turn) if turn > 1e-6 else 1.0
    else:
        avg_holding = 1.0
    trade_edge = cond_edge * avg_holding

    episodes = np.empty(0, dtype=float)
    if exposure is not None:
        exp_active = np.abs(exposure) > 1e-6
        if np.any(exp_active):
            sign = np.sign(exposure)
            prev_active = np.concatenate(([False], exp_active[:-1]))
            prev_sign = np.concatenate(([0.0], sign[:-1]))
            starts = exp_active & (~prev_active | (sign != prev_sign))
            n_episodes = int(starts.sum())
            if n_episodes >= 2:
                episode_ids = np.cumsum(starts)[exp_active] - 1
                episodes = np.bincount(episode_ids, weights=net_arr[exp_active], minlength=n_episodes)

    if len(episodes) >= 2:
        gains = float(episodes[episodes > 0].sum())
        losses = float(np.abs(episodes[episodes < 0].sum())) + 1e-12
        profit_factor = gains / losses
        pf_basis = "closed_trade"
    else:
        if holding_horizon_bars is not None:
            h = max(1, int(holding_horizon_bars))
        elif pos_arr is not None:
            h = max(1, round(avg_holding))
        else:
            h = 1
        active_net = net_arr[active]
        n_full = len(active_net) // h
        if n_full >= 2:
            horizon_rets = active_net[: n_full * h].reshape(n_full, h).sum(axis=1)
            rem = len(active_net) - n_full * h
            if rem > 0:
                horizon_rets = np.append(horizon_rets, float(active_net[n_full * h :].sum()))
        else:
            horizon_rets = active_net
        gains = float(horizon_rets[horizon_rets > 0].sum())
        losses = float(np.abs(horizon_rets[horizon_rets < 0].sum())) + 1e-12
        profit_factor = gains / losses
        pf_basis = "holding_horizon"

    trimmed_sharpe = compute_trimmed_sharpe(net_arr, bars_per_year, percentile=95.0)

    rng = np.random.default_rng(42)
    block_size = max(1, min(24, int(math.sqrt(n_active))))
    n_blocks = math.ceil(n_active / block_size)
    wrapped = np.concatenate([active_gross, active_gross[:block_size]])
    cs = np.concatenate(([0.0], np.cumsum(wrapped)))
    starts = rng.integers(0, n_active, size=(200, n_blocks))
    total_sums = np.zeros(200, dtype=float)
    if n_blocks > 1:
        s_full = starts[:, :-1]
        total_sums += np.sum(cs[s_full + block_size] - cs[s_full], axis=1)
    rem_len = n_active - (n_blocks - 1) * block_size
    s_last = starts[:, -1]
    total_sums += cs[s_last + rem_len] - cs[s_last]
    boot_means = total_sums / n_active
    p_value = float(np.mean(boot_means <= 0.0))

    passed = (
        (trade_edge > edge_cost_multiplier * one_way_cost)
        and (profit_factor >= min_profit_factor)
        and (time_in_market >= min_time_in_market)
        and (p_value < 0.05)
        and (trimmed_sharpe is not None and trimmed_sharpe > TRIMMED_SHARPE_FLOOR)
    )
    return Stage1Report(
        "rules_ts",
        passed,
        {
            "conditional_edge": cond_edge,
            "profit_factor": profit_factor,
            "bar_profit_factor": bar_profit_factor,
            "pf_basis": pf_basis,
            "trimmed_sharpe": trimmed_sharpe,
            "time_in_market": time_in_market,
            "bootstrap_p_value": p_value,
            "avg_holding_bars": avg_holding,
            "trade_edge": trade_edge,
        },
        f"PF={profit_factor:.2f} ({pf_basis}, barPF={bar_profit_factor:.2f}), Edge={cond_edge*1e4:.1f}bps, TiM={time_in_market*100:.1f}%, Holding={avg_holding:.1f}h",
    )


def per_bar_sharpe(returns) -> float:
    r = np.asarray(returns, dtype=float)
    return float(np.mean(r) / (np.std(r, ddof=1) + 1e-12))

def annualised_sharpe(returns, bars_per_year: int) -> float:
    return per_bar_sharpe(returns) * math.sqrt(bars_per_year)


def compute_trimmed_sharpe(
    returns: Sequence[float] | np.ndarray,
    bars_per_year: int,
    percentile: float = 95.0,
) -> float | None:
    """Annualised Sharpe after dropping returns above the given percentile."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2 or bars_per_year < 1:
        return None
    trimmed = r[r <= np.percentile(r, percentile)]
    if len(trimmed) < 2:
        return None
    sr = annualised_sharpe(trimmed, bars_per_year)
    return float(sr) if math.isfinite(sr) else None


def empirical_holding_volatility(panel_df: pl.DataFrame, expected_holding_bars: int) -> float:
    h = int(expected_holding_bars)
    if h < 1:
        raise ValueError("expected_holding_bars must be >= 1")
    cols = set(panel_df.columns)
    if panel_df.height == 0:
        return 0.0
    if "close" in cols:
        px = pl.col("close")
    elif "mid" in cols:
        px = pl.col("mid")
    elif {"bid", "ask"} <= cols:
        px = (pl.col("bid") + pl.col("ask")) / 2.0
    elif "open" in cols:
        px = pl.col("open")
    else:
        return 0.0
    sym_col = "asset" if "asset" in cols else "symbol"
    df = panel_df.select(pl.col("timestamp"), pl.col(sym_col), px.cast(pl.Float64).alias("_px")).sort("timestamp", sym_col)
    stats_df = (
        df.with_columns(
            ret_h=pl.col("_px") / pl.col("_px").shift(h).over(sym_col) - 1.0,
            ret_1=pl.col("_px") / pl.col("_px").shift(1).over(sym_col) - 1.0,
        )
        .group_by(sym_col)
        .agg(n=pl.col("_px").count(), vol_h=pl.col("ret_h").std(), vol_1=pl.col("ret_1").std())
        .with_columns(
            vol=pl.when(pl.col("n") >= h + 2).then(pl.col("vol_h")).otherwise(pl.col("vol_1") * math.sqrt(h))
        )
    )
    med = stats_df.select(pl.col("vol").filter(pl.col("vol").is_finite()).median()).item()
    return float(med) if med is not None and math.isfinite(med) else 0.0


def evaluate_neighbor_stability(
    neighbor_sharpes: Sequence[float], baseline_net_sharpe: float
) -> dict[str, Any]:
    """Sign-safe parameter neighbor stability without negative/negative ratio inversion."""
    base = float(baseline_net_sharpe)
    if not neighbor_sharpes:
        return {
            "neighbor_tested": False,
            "passed": None,
            "stability": None,
            "min_sharpe": None,
            "max_sharpe": None,
            "baseline_net_sharpe": base if math.isfinite(base) else None,
        }
    vals = [float(s) for s in neighbor_sharpes]
    if not math.isfinite(base) or not all(math.isfinite(v) for v in vals):
        return {
            "neighbor_tested": True,
            "passed": False,
            "stability": 0.0,
            "min_sharpe": None,
            "max_sharpe": None,
            "baseline_net_sharpe": base if math.isfinite(base) else None,
        }
    min_sr = min(vals)
    max_sr = max(vals)
    positive_anchor = max_sr > NEIGHBOR_MAX_SR_FLOOR and base > NEIGHBOR_MAX_SR_FLOOR
    passed = positive_anchor and (min_sr >= NEIGHBOR_STABILITY_RATIO * base)
    stability = float(min_sr / base) if positive_anchor else 0.0
    return {
        "neighbor_tested": True,
        "passed": bool(passed),
        "stability": stability,
        "min_sharpe": min_sr,
        "max_sharpe": max_sr,
        "baseline_net_sharpe": base,
    }
