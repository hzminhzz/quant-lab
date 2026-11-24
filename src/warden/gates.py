"""Family-specific Stage 1 falsification gates and Stage 0 feasibility screen."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

from warden.contracts import EvaluationContract, HypothesisSpec, Stage1Report
from warden.registry import PER_HYPOTHESIS_CAP, HypothesisBudgetExhaustedError

if TYPE_CHECKING:
    import polars as pl


class GateFalsificationError(ValueError): pass


# Single gate vocabulary: runner, cli audit and qualify compare against these, never literals.
# PER_HYPOTHESIS_CAP is a governance fact owned by the registry (imported above), not redefined here.
MIN_SR = {"cross_sectional_ml": 1.2, "rules_ts": 1.0, "carry_rv": 1.0, "meta_overlay": 1.0}
SR2_FLOOR, SRD_FLOOR, DSR_FLOOR, IC_T_FLOOR = 0.50, 0.0, 0.90, 2.0
AUDIT_DSR_FLOOR, TRIMMED_SHARPE_FLOOR = 0.80, 0.0
NEIGHBOR_STABILITY_RATIO, NEIGHBOR_MAX_SR_FLOOR = 0.70, 0.0
HOLDOUT_SR_FLOOR, HOLDOUT_RATIO_FLOOR, REVISE_SR_FLOOR = 0.80, 0.50, 1.0
CARRY_ADF_ALPHA = 0.05
META_TURNOVER_REDUCTION_FLOOR = 0.20
META_RISK_REDUCTION_FLOOR = 0.10
META_MAX_SHARPE_LOSS = 0.05


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
    from ml4t.diagnostic.metrics import compute_ic_hac_stats

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
    import polars as pl
    from ml4t.diagnostic.metrics import cross_sectional_ic_series

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


def evaluate_stage1_carry_rv(
    net_carry_series: Sequence[float] | np.ndarray,
    spread_series: Sequence[float] | np.ndarray,
    *,
    max_adf_p_value: float = CARRY_ADF_ALPHA,
    min_obs: int = 20,
) -> Stage1Report:
    """Gate carry/RV mechanisms on positive net carry and spread stationarity (FeatureDiagnostics ADF)."""
    net_arr = np.asarray(net_carry_series, dtype=float)
    spread_arr = np.asarray(spread_series, dtype=float)
    if net_arr.ndim != 1 or spread_arr.ndim != 1:
        raise ValueError("net_carry_series and spread_series must be 1D vectors")
    if len(net_arr) != len(spread_arr):
        raise ValueError(
            f"Carry and spread lengths must match ({len(net_arr)} != {len(spread_arr)})"
        )
    valid_mask = np.isfinite(net_arr) & np.isfinite(spread_arr)
    net_arr = net_arr[valid_mask]
    spread_arr = spread_arr[valid_mask]
    n_obs = len(spread_arr)
    if n_obs < min_obs or float(np.std(spread_arr)) < 1e-12:
        return Stage1Report(
            "carry_rv",
            False,
            {"n_obs": float(n_obs), "mean_net_carry": float(np.mean(net_arr)) if n_obs else 0.0},
            f"Insufficient or degenerate carry/spread series (n_obs={n_obs})",
        )

    from ml4t.diagnostic.config import DiagnosticConfig
    from ml4t.diagnostic.evaluation import FeatureDiagnostics

    cfg = DiagnosticConfig()
    cfg.stationarity.enabled = True
    cfg.stationarity.significance_level = float(max_adf_p_value)
    cfg.stationarity.adf_enabled = True
    cfg.stationarity.kpss_enabled = True
    cfg.acf.enabled = False
    cfg.volatility.enabled = False
    cfg.distribution.enabled = False
    diag_res = FeatureDiagnostics(cfg).run_diagnostics(spread_arr, name="carry_spread")
    st = diag_res.stationarity
    adf_res = getattr(st, "adf_result", None) if st is not None else None
    kpss_res = getattr(st, "kpss_result", None) if st is not None else None

    adf_p = float(adf_res.p_value) if adf_res is not None and math.isfinite(adf_res.p_value) else 1.0
    adf_stat = (
        float(adf_res.test_statistic)
        if adf_res is not None and math.isfinite(adf_res.test_statistic)
        else 0.0
    )
    kpss_p = (
        float(kpss_res.p_value)
        if kpss_res is not None and math.isfinite(kpss_res.p_value)
        else None
    )
    consensus = str(getattr(st, "consensus", "unknown"))

    mean_net_carry = float(np.mean(net_arr))
    total_net_carry = float(np.sum(net_arr))
    spread_mean = float(np.mean(spread_arr))
    spread_std = float(np.std(spread_arr, ddof=1))
    abs_z = np.abs((spread_arr - spread_mean) / max(spread_std, 1e-12))
    max_abs_excursion_z = float(np.max(abs_z))
    p99_abs_excursion_z = float(np.percentile(abs_z, 99.0))
    running_max = np.maximum.accumulate(spread_arr)
    max_spread_drawdown = float(np.max(running_max - spread_arr))

    passed = (mean_net_carry > 0.0) and math.isfinite(adf_p) and (adf_p < max_adf_p_value)
    return Stage1Report(
        "carry_rv",
        passed,
        {
            "n_obs": float(n_obs),
            "mean_net_carry": mean_net_carry,
            "total_net_carry": total_net_carry,
            "adf_p_value": adf_p,
            "adf_statistic": adf_stat,
            "kpss_p_value": kpss_p,
            "stationarity_consensus": consensus,
            "max_abs_excursion_z": max_abs_excursion_z,
            "p99_abs_excursion_z": p99_abs_excursion_z,
            "max_spread_drawdown": max_spread_drawdown,
        },
        (
            f"MeanNetCarry={mean_net_carry*1e4:.2f}bps, ADF_p={adf_p:.4g} "
            f"({consensus}), MaxExcursionZ={max_abs_excursion_z:.2f}"
        ),
    )


def apply_causal_meta_overlay(
    primary_signal: Sequence[float] | np.ndarray,
    realized_returns: Sequence[float] | np.ndarray,
    meta_probability: Sequence[float] | np.ndarray,
    *,
    method: str = "sigmoid",
    scale: float = 5.0,
    threshold: float = 0.5,
) -> dict[str, np.ndarray]:
    """Compute ml4t-engineer meta-labels, bet sizes, and sized overlay signals."""
    import polars as pl
    from ml4t.engineer.labeling import apply_meta_model, compute_bet_size, meta_labels

    sig = np.asarray(primary_signal, dtype=float)
    ret = np.asarray(realized_returns, dtype=float)
    prob = np.asarray(meta_probability, dtype=float)
    if sig.ndim != 1 or ret.ndim != 1 or prob.ndim != 1 or not (len(sig) == len(ret) == len(prob)):
        raise ValueError("primary_signal, realized_returns, and meta_probability must be aligned 1D arrays")
    df = pl.DataFrame({"primary_signal": sig, "realized_return": ret, "meta_probability": prob})
    df = meta_labels(df, signal_col="primary_signal", return_col="realized_return", threshold=0.0)
    df = df.with_columns(
        compute_bet_size("meta_probability", method=method, scale=scale, threshold=threshold).alias("bet_size")
    )
    df = apply_meta_model(
        df,
        primary_signal_col="primary_signal",
        meta_probability_col="meta_probability",
        bet_size_method=method,
        scale=scale,
        threshold=threshold,
        output_col="sized_signal",
    )
    return {
        "meta_label": df["meta_label"].fill_null(0).to_numpy().astype(float),
        "bet_size": df["bet_size"].fill_null(0.0).to_numpy().astype(float),
        "sized_signal": df["sized_signal"].fill_null(0.0).to_numpy().astype(float),
    }


def _cvar_99_and_max_dd(returns: np.ndarray) -> tuple[float, float]:
    q01 = float(np.percentile(returns, 1.0))
    tail = returns[returns <= q01]
    cvar_99 = float(abs(np.mean(tail))) if len(tail) > 0 else float(abs(q01))
    eq = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(eq)
    max_dd = float(np.max((peak - eq) / np.maximum(peak, 1e-12))) * 100.0
    return cvar_99, max_dd


def _paired_risk_reduction_pvalue(parent: np.ndarray, child: np.ndarray, n_boot: int = 200) -> tuple[float, float]:
    """Paired circular-block bootstrap p-values for CVaR_99 and MaxDD reduction."""
    n = len(parent)
    block = max(2, min(16, int(math.sqrt(n))))
    n_blocks = math.ceil(n / block)
    rng = np.random.default_rng(42)
    p_wrap = np.concatenate([parent, parent[:block]])
    c_wrap = np.concatenate([child, child[:block]])
    cvar_worse = 0
    dd_worse = 0
    for _ in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:n]
        p_cvar, p_dd = _cvar_99_and_max_dd(p_wrap[idx])
        c_cvar, c_dd = _cvar_99_and_max_dd(c_wrap[idx])
        if c_cvar >= p_cvar:
            cvar_worse += 1
        if c_dd >= p_dd:
            dd_worse += 1
    return cvar_worse / n_boot, dd_worse / n_boot


def evaluate_stage1_meta_overlay(
    parent_net_returns: Sequence[float] | np.ndarray,
    child_net_returns: Sequence[float] | np.ndarray,
    parent_annualized_turnover: float,
    child_annualized_turnover: float,
    bars_per_year: int,
    *,
    min_turnover_reduction: float = META_TURNOVER_REDUCTION_FLOOR,
    min_risk_reduction: float = META_RISK_REDUCTION_FLOOR,
    max_sharpe_loss: float = META_MAX_SHARPE_LOSS,
    parent_run_id: str = "",
    parent_data_sha256: str = "",
    child_data_sha256: str = "",
) -> Stage1Report:
    """Gate meta_overlay children against their registered parent return stream."""
    p_arr = np.asarray(parent_net_returns, dtype=float)
    c_arr = np.asarray(child_net_returns, dtype=float)
    if p_arr.ndim != 1 or c_arr.ndim != 1 or not (np.isfinite(p_arr).all() and np.isfinite(c_arr).all()):
        raise ValueError("Parent and child return streams must be finite 1D vectors")
    if parent_data_sha256 and child_data_sha256 and parent_data_sha256 != child_data_sha256:
        raise ValueError(
            f"Parent and child datasets are not aligned ({parent_data_sha256[:12]} != {child_data_sha256[:12]})"
        )
    if len(p_arr) != len(c_arr):
        raise ValueError(
            f"Parent and child return streams are not temporally aligned ({len(p_arr)} != {len(c_arr)})"
        )
    if len(p_arr) < 10:
        return Stage1Report(
            "meta_overlay",
            False,
            {"n_obs": float(len(p_arr))},
            f"Insufficient aligned validation bars ({len(p_arr)}) for meta_overlay evaluation",
        )

    parent_sharpe = annualised_sharpe(p_arr, bars_per_year)
    child_sharpe = annualised_sharpe(c_arr, bars_per_year)
    sharpe_delta = child_sharpe - parent_sharpe

    p_turn = float(parent_annualized_turnover)
    c_turn = float(child_annualized_turnover)
    turnover_reduction = ((p_turn - c_turn) / p_turn) if p_turn > 1e-12 else 0.0

    parent_cvar, parent_max_dd = _cvar_99_and_max_dd(p_arr)
    child_cvar, child_max_dd = _cvar_99_and_max_dd(c_arr)
    cvar_99_reduction = ((parent_cvar - child_cvar) / parent_cvar) if parent_cvar > 1e-12 else 0.0
    max_dd_reduction = ((parent_max_dd - child_max_dd) / parent_max_dd) if parent_max_dd > 1e-12 else 0.0
    cvar_p_val, dd_p_val = _paired_risk_reduction_pvalue(p_arr, c_arr)

    sharpe_preserved = (parent_sharpe - child_sharpe) <= float(max_sharpe_loss)
    turnover_ok = turnover_reduction >= float(min_turnover_reduction)
    cvar_ok = (cvar_99_reduction >= float(min_risk_reduction)) and (cvar_p_val < 0.05)
    dd_ok = (max_dd_reduction >= float(min_risk_reduction)) and (dd_p_val < 0.05)
    risk_ok = cvar_ok or dd_ok
    passed = bool(sharpe_preserved and (turnover_ok or risk_ok))

    metrics: dict[str, Any] = {
        "n_obs": float(len(p_arr)),
        "parent_run_id": parent_run_id,
        "parent_net_sharpe": parent_sharpe,
        "child_net_sharpe": child_sharpe,
        "sharpe_delta": sharpe_delta,
        "parent_annualized_turnover": p_turn,
        "child_annualized_turnover": c_turn,
        "turnover_reduction": turnover_reduction,
        "parent_cvar_99_pct": parent_cvar,
        "child_cvar_99_pct": child_cvar,
        "cvar_99_reduction": cvar_99_reduction,
        "cvar_reduction_p_value": cvar_p_val,
        "parent_max_drawdown_pct": parent_max_dd,
        "child_max_drawdown_pct": child_max_dd,
        "max_dd_reduction": max_dd_reduction,
        "max_dd_reduction_p_value": dd_p_val,
    }
    return Stage1Report(
        "meta_overlay",
        passed,
        metrics,
        (
            f"TurnRed={turnover_reduction*100:.1f}%, CVaRRed={cvar_99_reduction*100:.1f}% (p={cvar_p_val:.3f}), "
            f"MaxDDRed={max_dd_reduction*100:.1f}% (p={dd_p_val:.3f}), SharpeDelta={sharpe_delta:+.3f}"
        ),
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
    import polars as pl

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
