"""Central backtest simulation, Engine integration, and Linear-First Gate."""

from __future__ import annotations

import hashlib
import importlib.util
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml
from ml4t.backtest import BacktestConfig, DataFeed, Engine, Strategy
from ml4t.backtest.config import CommissionType, ExecutionMode, ExecutionPrice, SlippageType

from warden.contracts import (
    EvaluationContract,
    ExperimentResult,
    HypothesisSpec,
    canonical_json,
    load_contract,
)
from warden.gates import (
    evaluate_stage0_feasibility,
    evaluate_stage1_cross_sectional_ml,
    evaluate_stage1_rules_ts,
)
from warden.registry import ResearchRegistry, compute_effective_trials_and_dsr


class LinearBaselineRequiredError(ValueError):
    """Raised when a non-linear ML strategy is executed before a linear baseline is logged."""


def load_module_from_path(module_path: Path):
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load strategy module from {module_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


class TargetWeightStrategy(Strategy):
    def __init__(self, target_weights_by_timestamp: dict[Any, dict[str, float]]):
        super().__init__()
        self.target_weights = target_weights_by_timestamp

    def on_data(self, timestamp: Any, data: dict, context: dict, broker: Any) -> None:
        weights = self.target_weights.get(timestamp)
        if weights:
            for asset, w in weights.items():
                broker.order_target_percent(asset, float(w))


def run_engine_backtest(
    panel_df: pl.DataFrame,
    target_weights: dict[Any, dict[str, float]],
    contract: EvaluationContract,
    cost_multiplier: float = 1.0,
    delay_offset: int = 0,
) -> dict[str, Any]:
    feed = DataFeed(prices_df=panel_df, timestamp_col="timestamp", entity_col="asset", open_col="open", high_col="high", low_col="low", close_col="close", volume_col="volume")
    exec_price = ExecutionPrice.QUOTE_SIDE if contract.execution_mode == "QUOTE_SIDE" else ExecutionPrice.OPEN
    config = BacktestConfig(
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=exec_price,
        commission_type=CommissionType.PERCENTAGE,
        commission_rate=contract.taker_fee_bps * 1e-4 * cost_multiplier,
        slippage_type=SlippageType.PERCENTAGE,
        slippage_rate=contract.slippage_bps * 1e-4 * cost_multiplier,
        allow_short_selling=True,
        allow_leverage=True,
    )
    engine = Engine(feed=feed, strategy=TargetWeightStrategy(target_weights), config=config)
    result = engine.run()
    eq = np.array([e[1] for e in result.equity_curve], dtype=float)
    return {"net_returns": np.diff(eq) / eq[:-1] if len(eq) > 1 else np.zeros(1), "engine_result": result}


def simulate_positions(
    positions: np.ndarray,
    returns_panel: np.ndarray,
    funding_panel: np.ndarray | None,
    contract: EvaluationContract,
    val_slice: slice,
    cost_multiplier: float = 1.0,
    delay_offset: int = 0,
) -> dict[str, np.ndarray]:
    pos = np.asarray(positions, dtype=float)
    total_delay = contract.execution_delay_bars + delay_offset
    if total_delay > 0:
        pos = np.roll(pos, total_delay, axis=0)
        pos[:total_delay] = 0.0
    pos_val = pos[val_slice]
    ret_val = returns_panel[val_slice]
    turnover = np.sum(np.abs(np.diff(pos_val, axis=0, prepend=0.0)), axis=1)
    gross = np.sum(pos_val * ret_val, axis=1)
    one_way_cost = contract.total_one_way_cost_decimal() * cost_multiplier
    funding_cost = np.sum(pos_val * funding_panel[val_slice], axis=1) if funding_panel is not None else 0.0
    net = gross - turnover * one_way_cost - funding_cost
    return {"positions": pos_val, "gross_returns": gross, "net_returns": net, "turnover": turnover}


def calculate_metrics_from_returns(
    gross_returns: np.ndarray, net_returns: np.ndarray, bar_turnover: np.ndarray, bars_per_year: int
) -> dict[str, float]:
    g, n, turn = np.asarray(gross_returns, dtype=float), np.asarray(net_returns, dtype=float), np.asarray(bar_turnover, dtype=float)
    ann = math.sqrt(bars_per_year)
    active = n[np.abs(n) > 1e-10]
    gains = float(np.sum(active[active > 0]))
    losses = float(np.abs(np.sum(active[active < 0]))) + 1e-12
    eq = np.cumprod(1.0 + n)
    peak = np.maximum.accumulate(eq)
    return {
        "gross_sharpe": float(np.mean(g) / (np.std(g, ddof=1) + 1e-12) * ann),
        "net_sharpe": float(np.mean(n) / (np.std(n, ddof=1) + 1e-12) * ann),
        "hit_rate": float(np.mean(active > 0)) if len(active) > 0 else 0.0,
        "profit_factor": float(gains / losses),
        "max_drawdown_pct": float(np.max((peak - eq) / peak)) * 100.0,
        "annualized_turnover": float(np.mean(turn) * bars_per_year),
        "breakeven_cost_bps": float(np.sum(g) / (np.sum(turn) + 1e-12)) * 1e4,
        "cvar_99_pct": float(np.abs(np.percentile(n, 1.0))),
    }


class ExperimentRunner:
    def __init__(self, registry: ResearchRegistry | None = None, repo_root: Path = Path(".")):
        self.repo_root = Path(repo_root)
        self.registry = registry or ResearchRegistry(self.repo_root / "registry" / "registry.db")

    def resolve_contract(self, contract_id: str) -> EvaluationContract:
        cid = contract_id.lower()
        for p in [self.repo_root / "contracts" / f"{cid}.yaml", self.repo_root / "contracts" / f"{cid}.yml"]:
            if p.is_file():
                return load_contract(p)
        raise FileNotFoundError(f"Contract not found: '{contract_id}'")

    def run_spec(self, exp_yaml_path: Path, override_run_id: str | None = None) -> ExperimentResult:
        cfg = yaml.safe_load(Path(exp_yaml_path).read_text(encoding="utf-8"))
        contract = self.resolve_contract(cfg["contract_id"])
        hyp_id, family = cfg["hypothesis_id"], cfg.get("strategy_family", "rules_ts")
        strat_name, model_type, params = cfg.get("strategy_name", Path(exp_yaml_path).stem), cfg.get("model_type", "rules"), cfg.get("parameters", {})

        if family == "cross_sectional_ml" and model_type in {"lightgbm", "lgbm", "tree", "neural"}:
            with self.registry._connect() as conn:
                r = conn.execute("SELECT COUNT(*) FROM trials WHERE hypothesis_id = ? AND (strategy_name LIKE '%Ridge%' OR config_json LIKE '%ridge%')", (hyp_id,)).fetchone()
                if not r or r[0] == 0:
                    raise LinearBaselineRequiredError(f"Linear baseline required for '{hyp_id}' before '{model_type}'.")

        with self.registry._connect() as conn:
            h = conn.execute("SELECT title, economic_mechanism, counterparty_or_friction, expected_holding_bars, falsification_criteria, trials_used FROM hypotheses WHERE hypothesis_id = ?", (hyp_id,)).fetchone()
            if not h:
                raise ValueError(f"Hypothesis '{hyp_id}' not found.")
            hyp = HypothesisSpec(hyp_id, contract.contract_id, h[0], family, h[1], h[2], h[3], h[4])
            trials_used = h[5]

        evaluate_stage0_feasibility(contract, hyp, 0.0085 * math.sqrt(hyp.expected_holding_bars), trials_used)
        mod = load_module_from_path(self.repo_root / cfg["strategy_module"])
        data_path = self.repo_root / contract.exploratory_data_path
        if not data_path.is_file():
            data_path = self.repo_root / "data" / "exploratory" / "crypto_perps_1h.parquet"

        panel_df = pl.read_parquet(data_path).sort("timestamp", "asset")
        ts = sorted(panel_df["timestamp"].unique().to_list())
        n_bars, n_assets = len(ts), len(panel_df["asset"].unique())
        ts_map = {t: i for i, t in enumerate(ts)}
        v_slice = slice(ts_map.get(contract.val_start, int(n_bars * 0.25)), ts_map.get(contract.val_end, n_bars))
        close_p = panel_df["close"].to_numpy().reshape((n_bars, n_assets))
        ret_p = np.diff(close_p, axis=0, prepend=close_p[:1]) / close_p
        fund_p = panel_df["funding_rate"].to_numpy().reshape((n_bars, n_assets)) if "funding_rate" in panel_df.columns else None

        out = mod.generate_positions(panel_df, contract, **params)
        raw_pos, ic_series = out["positions"], out.get("ic_series")

        mean_ic, ic_hac_t = None, None
        run_id = override_run_id or cfg.get("run_id") or f"RUN-{family[:2].upper()}-{trials_used+1:03d}"
        if family == "cross_sectional_ml":
            if ic_series is None:
                raise ValueError("cross_sectional_ml strategies must output 'ic_series'")
            s1 = evaluate_stage1_cross_sectional_ml(ic_series)
            mean_ic, ic_hac_t = s1.metrics.get("mean_ic"), s1.metrics.get("ic_hac_tstat")
            if not s1.passed:
                res = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "STAGE_1_SIGNAL", "REJECTED_EARLY", 0.0, 0.0, 0.0, 0.0, trials_used + 1, 1.0, mean_ic, ic_hac_t, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, f"Rejected at Stage 1: {s1.notes}")
                self.registry.record_trial(res, params, np.zeros(v_slice.stop - v_slice.start))
                return res

        sim1 = simulate_positions(raw_pos, ret_p, fund_p, contract, v_slice, cost_multiplier=1.0)
        net_ret, gross_ret, turnover = sim1["net_returns"], sim1["gross_returns"], sim1["turnover"]
        m = calculate_metrics_from_returns(gross_ret, net_ret, turnover, contract.bars_per_year)

        if family == "rules_ts":
            s1_rules = evaluate_stage1_rules_ts(net_ret.tolist(), gross_ret.tolist(), contract.total_one_way_cost_decimal(), sim1["positions"].tolist())
            if not s1_rules.passed:
                res = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "STAGE_1_SIGNAL", "REJECTED_EARLY", m["gross_sharpe"], m["net_sharpe"], 0.0, 0.0, trials_used + 1, 1.0, None, None, m["hit_rate"], m["profit_factor"], m["max_drawdown_pct"], m["annualized_turnover"], m["breakeven_cost_bps"], 0.0, 0.0, f"Rejected at Stage 1: {s1_rules.notes}")
                self.registry.record_trial(res, params, net_ret)
                return res

        sim2 = simulate_positions(raw_pos, ret_p, fund_p, contract, v_slice, cost_multiplier=2.0)
        sim_d = simulate_positions(raw_pos, ret_p, fund_p, contract, v_slice, delay_offset=1)
        sr2 = float(np.mean(sim2["net_returns"]) / (np.std(sim2["net_returns"], ddof=1) + 1e-12) * math.sqrt(contract.bars_per_year))
        srd = float(np.mean(sim_d["net_returns"]) / (np.std(sim_d["net_returns"], ddof=1) + 1e-12) * math.sqrt(contract.bars_per_year))

        ngrid = cfg.get("param_grid", [])
        if ngrid:
            n_sr = [float(np.mean(simulate_positions(mod.generate_positions(panel_df, contract, **c)["positions"], ret_p, fund_p, contract, v_slice)["net_returns"]) / (np.std(simulate_positions(mod.generate_positions(panel_df, contract, **c)["positions"], ret_p, fund_p, contract, v_slice)["net_returns"], ddof=1) + 1e-12) * math.sqrt(contract.bars_per_year)) for c in ngrid]
            stability = float(min(n_sr) / (max(n_sr) + 1e-8)) if n_sr else 1.0
        else:
            stability = 1.0

        hist = self.registry.load_prior_trial_returns(contract.contract_id, len(net_ret))
        dsr_p, exp_max, n_t, k_eff = compute_effective_trials_and_dsr(net_ret, hist, contract.bars_per_year)
        min_sr = 1.2 if family == "cross_sectional_ml" else 1.0
        ic_ok = (ic_hac_t >= 2.0) if family == "cross_sectional_ml" and ic_hac_t is not None else True
        verdict = "CANDIDATE_FOR_QUALIFICATION" if (m["net_sharpe"] > min_sr and sr2 > 0.50 and dsr_p >= 0.90 and ic_ok) else "REVISE"

        res = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "STAGE_3_STRESS", verdict, m["gross_sharpe"], m["net_sharpe"], dsr_p, float(exp_max), n_t, k_eff, mean_ic, ic_hac_t, m["hit_rate"], m["profit_factor"], m["max_drawdown_pct"], m["annualized_turnover"], m["breakeven_cost_bps"], sr2, stability, f"Simulated on {contract.contract_id}; 2xCostSR={sr2:.2f}.", srd, m["cvar_99_pct"])
        self.registry.record_trial(res, params, net_ret)
        return res
