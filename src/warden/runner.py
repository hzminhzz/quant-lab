"""Central backtest simulation, Engine integration, and Linear-First Gate."""

from __future__ import annotations

import hashlib
import importlib.util
import math
import pickle
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

from warden.contracts import (
    EvaluationContract,
    ExperimentResult,
    HypothesisSpec,
    Stage1Report,
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


def _aligned_matrix(panel_df: pl.DataFrame, value_col: str, sorted_assets: list[str]) -> np.ndarray:
    p = panel_df.pivot(index="timestamp", on="asset", values=value_col).fill_null(0.0)
    for a in sorted_assets:
        if a not in p.columns:
            p = p.with_columns(pl.lit(0.0).alias(a))
    return p.select(sorted_assets).to_numpy()


def _funding_settlement_mask(timestamps: list, contract: EvaluationContract) -> np.ndarray:
    if contract.asset_class == "crypto_perps" and contract.frequency == "1h":
        return np.array([t.hour % 8 == 0 for t in timestamps], dtype=bool)
    return np.ones(len(timestamps), dtype=bool)


def _run_strategy_isolated(strategy_module_path: Path, panel_df: pl.DataFrame, contract: EvaluationContract, params: dict) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile("wb", delete=False) as f:
        pickle.dump((str(strategy_module_path.resolve()), panel_df, contract, params), f)
        inp_path = f.name
    out_path = inp_path + ".out"
    cmd = [
        sys.executable,
        "-c",
        (
            "import sys, pickle\n"
            "from pathlib import Path\n"
            "from warden.runner import load_module_from_path\n"
            "inp, out = sys.argv[1], sys.argv[2]\n"
            "with open(inp, 'rb') as f: mod_str, p, c, kwargs = pickle.load(f)\n"
            "mod = load_module_from_path(Path(mod_str))\n"
            "res = mod.generate_positions(p, c, **kwargs)\n"
            "with open(out, 'wb') as f: pickle.dump(res, f)\n"
        ),
        inp_path,
        out_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            err = proc.stderr.strip() or f"Strategy subprocess failed with code {proc.returncode}"
            raise RuntimeError(err)
        with open(out_path, "rb") as f:
            return pickle.load(f)
    finally:
        Path(inp_path).unlink(missing_ok=True)
        Path(out_path).unlink(missing_ok=True)


def simulate_positions(positions, returns_panel, funding_panel, contract, val_slice, cost_multiplier=1.0, delay_offset=0, funding_mask=None) -> dict[str, np.ndarray]:
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
    if funding_panel is not None:
        fund_slice = funding_panel[val_slice]
        if funding_mask is not None:
            f_mask = np.asarray(funding_mask, dtype=bool)[val_slice, None]
            funding_cost = np.sum(pos_val * fund_slice * f_mask, axis=1)
        else:
            funding_cost = np.sum(pos_val * fund_slice, axis=1)
    else:
        funding_cost = 0.0
    net = gross - turnover * one_way_cost - funding_cost
    return {"positions": pos_val, "gross_returns": gross, "net_returns": net, "turnover": turnover}


def calculate_metrics_from_returns(gross_returns, net_returns, bar_turnover, bars_per_year) -> dict[str, float]:
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

    def run_stage1_screen(self, exp_yaml_path: Path) -> Stage1Report:
        cfg = yaml.safe_load(Path(exp_yaml_path).read_text(encoding="utf-8"))
        contract = self.resolve_contract(cfg["contract_id"])
        hyp_id, family = cfg["hypothesis_id"], cfg.get("strategy_family", "rules_ts")
        params = cfg.get("parameters", {})
        with self.registry._connect() as conn:
            h = conn.execute("SELECT title, economic_mechanism, counterparty_or_friction, expected_holding_bars, falsification_criteria, trials_used FROM hypotheses WHERE hypothesis_id = ?", (hyp_id,)).fetchone()
            if not h:
                raise ValueError(f"Hypothesis '{hyp_id}' not found.")
            hyp = HypothesisSpec(hyp_id, contract.contract_id, h[0], family, h[1], h[2], int(h[3]), h[4])
            trials_used = h[5]
        evaluate_stage0_feasibility(contract, hyp, 0.0085 * math.sqrt(hyp.expected_holding_bars), trials_used)
        data_path = self.repo_root / contract.exploratory_data_path
        if not data_path.is_file():
            data_path = self.repo_root / "data" / "exploratory" / "crypto_perps_1h.parquet"
        panel_df = pl.read_parquet(data_path)
        if "symbol" in panel_df.columns and "asset" not in panel_df.columns: panel_df = panel_df.rename({"symbol": "asset"})
        panel_df = panel_df.sort("timestamp", "asset")
        strat_file = self.repo_root / cfg["strategy_module"]
        out = _run_strategy_isolated(strat_file, panel_df, contract, params)
        if family == "cross_sectional_ml":
            return evaluate_stage1_cross_sectional_ml(out["ic_series"])
        ts = sorted(panel_df["timestamp"].unique().to_list())
        assets = sorted(panel_df["asset"].unique().to_list())
        ts_map = {t: i for i, t in enumerate(ts)}
        v_slice = slice(ts_map.get(contract.val_start, int(len(ts) * 0.25)), ts_map.get(contract.val_end, len(ts)))
        if "open" in panel_df.columns:
            ret_df = panel_df.with_columns(ret=(pl.col("open").shift(-1).over("asset") / pl.col("open") - 1.0).fill_null(0.0))
        else:
            ret_df = panel_df.with_columns(ret=pl.col("close").pct_change().fill_null(0.0).over("asset"))
        ret_p = _aligned_matrix(ret_df, "ret", assets)
        fund_p = _aligned_matrix(panel_df, "funding_rate", assets) if "funding_rate" in panel_df.columns else None
        fund_mask = _funding_settlement_mask(ts, contract)
        sim1 = simulate_positions(out["positions"], ret_p, fund_p, contract, v_slice, cost_multiplier=1.0, funding_mask=fund_mask)
        return evaluate_stage1_rules_ts(sim1["net_returns"].tolist(), sim1["gross_returns"].tolist(), contract.total_one_way_cost_decimal(), sim1["positions"].tolist())

    def run_spec(self, exp_yaml_path: Path, override_run_id: str | None = None) -> ExperimentResult:
        cfg = yaml.safe_load(Path(exp_yaml_path).read_text(encoding="utf-8"))
        contract = self.resolve_contract(cfg["contract_id"])
        hyp_id, family = cfg["hypothesis_id"], cfg.get("strategy_family", "rules_ts")
        strat_name, model_type, params = cfg.get("strategy_name", Path(exp_yaml_path).stem), cfg.get("model_type", "rules"), cfg.get("parameters", {})

        with self.registry._connect() as conn:
            h = conn.execute("SELECT title, economic_mechanism, counterparty_or_friction, expected_holding_bars, falsification_criteria, trials_used, strategy_family FROM hypotheses WHERE hypothesis_id = ?", (hyp_id,)).fetchone()
            if not h:
                raise ValueError(f"Hypothesis '{hyp_id}' not found.")
            reg_family = h[6]
            if family != reg_family:
                raise ValueError(f"Experiment spec strategy_family '{family}' does not match registered hypothesis family '{reg_family}'.")
            hyp = HypothesisSpec(hyp_id, contract.contract_id, h[0], reg_family, h[1], h[2], int(h[3]), h[4])
            trials_used = h[5]

        is_linear = model_type.lower() in {"ridge", "linear", "elasticnet", "ols", "lasso"}
        if family == "cross_sectional_ml" and not is_linear:
            with self.registry._connect() as conn:
                r = conn.execute(
                    "SELECT COUNT(*) FROM trials WHERE hypothesis_id = ? AND (strategy_name LIKE '%Ridge%' OR strategy_module LIKE '%baseline%') AND verdict != 'REJECTED_EARLY'",
                    (hyp_id,)
                ).fetchone()
                if not r or r[0] == 0:
                    raise LinearBaselineRequiredError(f"Linear baseline required for '{hyp_id}' before non-linear model '{model_type}'.")

        evaluate_stage0_feasibility(contract, hyp, 0.0085 * math.sqrt(hyp.expected_holding_bars), trials_used)
        run_id = override_run_id or cfg.get("run_id") or f"RUN-{family[:2].upper()}-{trials_used+1:03d}"
        trial_num = self.registry.register_trial_start(hyp_id, run_id)
        strat_module_str = str(cfg.get("strategy_module", ""))
        data_path = self.repo_root / contract.exploratory_data_path
        if not data_path.is_file():
            data_path = self.repo_root / "data" / "exploratory" / "crypto_perps_1h.parquet"
        data_sha = hashlib.sha256(data_path.read_bytes()).hexdigest()
        is_synthetic = (data_sha != contract.data_snapshot_sha256) or ("synthetic" in str(data_path).lower())
        try:
            panel_df = pl.read_parquet(data_path)
            if "symbol" in panel_df.columns and "asset" not in panel_df.columns: panel_df = panel_df.rename({"symbol": "asset"})
            panel_df = panel_df.sort("timestamp", "asset")
            ts = sorted(panel_df["timestamp"].unique().to_list())
            assets = sorted(panel_df["asset"].unique().to_list())
            n_bars = len(ts)
            ts_map = {t: i for i, t in enumerate(ts)}
            v_slice = slice(ts_map.get(contract.val_start, int(n_bars * 0.25)), ts_map.get(contract.val_end, n_bars))
            if "open" in panel_df.columns:
                ret_df = panel_df.with_columns(ret=(pl.col("open").shift(-1).over("asset") / pl.col("open") - 1.0).fill_null(0.0))
            else:
                ret_df = panel_df.with_columns(ret=pl.col("close").pct_change().fill_null(0.0).over("asset"))
            ret_p = _aligned_matrix(ret_df, "ret", assets)
            fund_p = _aligned_matrix(panel_df, "funding_rate", assets) if "funding_rate" in panel_df.columns else None
            fund_mask = _funding_settlement_mask(ts, contract)

            strat_file = self.repo_root / cfg["strategy_module"]
            out = _run_strategy_isolated(strat_file, panel_df, contract, params)
            raw_pos, ic_series = out["positions"], out.get("ic_series")

            with self.registry._connect() as conn:
                n_prior = conn.execute("SELECT COUNT(*) FROM trials WHERE contract_id = ?", (contract.contract_id,)).fetchone()[0]
            total_contract_n = n_prior + 1
            hist = self.registry.load_prior_trial_returns(contract.contract_id, v_slice.stop - v_slice.start)
            k_eff_prior = 1.0
            if hist:
                _, _, _, k_eff_prior = compute_effective_trials_and_dsr(hist[-1], hist[:-1], contract.bars_per_year)

            mean_ic, ic_hac_t = None, None
            if family == "cross_sectional_ml":
                if ic_series is None:
                    raise ValueError("cross_sectional_ml strategies must output 'ic_series'")
                s1 = evaluate_stage1_cross_sectional_ml(ic_series)
                mean_ic, ic_hac_t = s1.metrics.get("mean_ic"), s1.metrics.get("ic_hac_tstat")
                if not s1.passed:
                    res = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "STAGE_1_SIGNAL", "REJECTED_EARLY", 0.0, 0.0, 0.0, 0.0, total_contract_n, k_eff_prior, mean_ic, ic_hac_t, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, f"Rejected at Stage 1: {s1.notes}", 0.0, 0.0, int(is_synthetic), data_sha, strat_module_str, str(model_type), "")
                    self.registry.record_trial(res, params, np.zeros(v_slice.stop - v_slice.start), trial_number=trial_num)
                    return res

            sim1 = simulate_positions(raw_pos, ret_p, fund_p, contract, v_slice, cost_multiplier=1.0, funding_mask=fund_mask)
            net_ret, gross_ret, turnover = sim1["net_returns"], sim1["gross_returns"], sim1["turnover"]
            m = calculate_metrics_from_returns(gross_ret, net_ret, turnover, contract.bars_per_year)

            s1_rules = evaluate_stage1_rules_ts(net_ret.tolist(), gross_ret.tolist(), contract.total_one_way_cost_decimal(), sim1["positions"].tolist()) if family == "rules_ts" else None

            sim2 = simulate_positions(raw_pos, ret_p, fund_p, contract, v_slice, cost_multiplier=2.0, funding_mask=fund_mask)
            sim_d = simulate_positions(raw_pos, ret_p, fund_p, contract, v_slice, delay_offset=1, funding_mask=fund_mask)
            sr2 = float(np.mean(sim2["net_returns"]) / (np.std(sim2["net_returns"], ddof=1) + 1e-12) * math.sqrt(contract.bars_per_year))
            srd = float(np.mean(sim_d["net_returns"]) / (np.std(sim_d["net_returns"], ddof=1) + 1e-12) * math.sqrt(contract.bars_per_year))

            ngrid = cfg.get("param_grid", [])
            if ngrid:
                n_sr = [float(np.mean(simulate_positions(_run_strategy_isolated(strat_file, panel_df, contract, c)["positions"], ret_p, fund_p, contract, v_slice, funding_mask=fund_mask)["net_returns"]) / (np.std(simulate_positions(_run_strategy_isolated(strat_file, panel_df, contract, c)["positions"], ret_p, fund_p, contract, v_slice, funding_mask=fund_mask)["net_returns"], ddof=1) + 1e-12) * math.sqrt(contract.bars_per_year)) for c in ngrid]
                stability = float(min(n_sr) / (max(n_sr) + 1e-8)) if n_sr else 1.0
            else:
                stability = 1.0

            hist = self.registry.load_prior_trial_returns(contract.contract_id, len(net_ret))
            dsr_p, exp_max, n_t, k_eff = compute_effective_trials_and_dsr(net_ret, hist, contract.bars_per_year, total_trials_n=total_contract_n)
            min_sr = 1.2 if family == "cross_sectional_ml" else 1.0
            ic_ok = (ic_hac_t >= 2.0) if family == "cross_sectional_ml" and ic_hac_t is not None else True
            rules_ok = s1_rules.passed if s1_rules is not None else True
            verdict = "CANDIDATE_FOR_QUALIFICATION" if (m["net_sharpe"] > min_sr and sr2 > 0.50 and srd > 0.0 and dsr_p >= 0.90 and ic_ok and rules_ok) else "REVISE"
            res = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "STAGE_3_STRESS", verdict, m["gross_sharpe"], m["net_sharpe"], dsr_p, float(exp_max), n_t, k_eff, mean_ic, ic_hac_t, m["hit_rate"], m["profit_factor"], m["max_drawdown_pct"], m["annualized_turnover"], m["breakeven_cost_bps"], sr2, stability, f"Simulated on {contract.contract_id}; 2xCostSR={sr2:.2f}.", srd, m["cvar_99_pct"], int(is_synthetic), data_sha, strat_module_str, str(model_type), "")
            self.registry.record_trial(res, params, net_ret, trial_number=trial_num)
            return res
        except Exception as exc:
            res_failed = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "FAILED", "FAILED", 0.0, 0.0, 0.0, 0.0, 1, 1.0, None, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, f"Execution failed: {exc}", 0.0, 0.0, int(is_synthetic), data_sha, strat_module_str, str(model_type), "")
            try:
                self.registry.record_trial(res_failed, params, np.zeros(0), trial_number=trial_num)
            except (ValueError, sqlite3.Error, OSError):
                pass
            raise
