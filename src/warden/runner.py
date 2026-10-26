"""Central backtest simulation, Engine integration, and Linear-First Gate."""

from __future__ import annotations

import bisect
import hashlib
import importlib.util
import math
import os
import pickle
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import replace as dc_replace
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
from warden.diagnostics import compute_shift_skill_pvalue, compute_trade_metrics
from warden.gates import (
    evaluate_stage0_feasibility,
    evaluate_stage1_cross_sectional_ml,
    evaluate_stage1_rules_ts,
)
from warden.provenance import resolve_dataset
from warden.registry import (
    HypothesisBudgetExhaustedError,
    ResearchRegistry,
    compute_selection_report,
    normalise_module,
)


class LinearBaselineRequiredError(ValueError):
    """Raised when a non-linear ML strategy is executed before a linear baseline is logged."""


class ExecutionDataError(ValueError):
    """Required execution prices are absent; Warden never guesses another price basis."""


class DoubleLatencyError(ValueError):
    """A strategy pre-lags its own signals on top of Warden's mandatory execution delay."""


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
    """Perpetual funding settles on eight-hour boundaries for every bar frequency."""
    if contract.asset_class != "crypto_perps":
        return np.ones(len(timestamps), dtype=bool)
    return np.asarray([t.hour % 8 == 0 and t.minute == 0 for t in timestamps], dtype=bool)


def require_execution_columns(panel_df: pl.DataFrame, contract: EvaluationContract) -> None:
    """Fail closed on a missing price basis instead of silently changing basis."""
    needed = ["bid", "ask"] if contract.execution_mode == "QUOTE_SIDE" else ["open"]
    missing = [c for c in needed if c not in panel_df.columns]
    if missing:
        raise ExecutionDataError(f"{contract.execution_mode} contract {contract.contract_id} requires {missing}; "
                                 "a close-price fallback is not a valid execution basis")
    if contract.asset_class == "crypto_perps" and "funding_rate" not in panel_df.columns:
        raise ExecutionDataError(f"{contract.contract_id} is a perpetual contract without funding_rate data")
    for column in needed:
        if panel_df.select(pl.col(column).is_null().any()).item():
            raise ExecutionDataError(f"null {column} prices cannot be treated as a zero move")


def execution_returns(panel_df: pl.DataFrame, contract: EvaluationContract, assets: list[str]) -> dict:
    """NEXT_BAR: open-to-open. QUOTE_SIDE: long pays ask, receives bid; short the reverse."""
    require_execution_columns(panel_df, contract)
    if contract.execution_mode == "QUOTE_SIDE":
        df = panel_df.with_columns(
            long_ret=pl.col("bid").shift(-1).over("asset") / pl.col("ask") - 1.0,
            short_ret=-(pl.col("ask").shift(-1).over("asset") / pl.col("bid") - 1.0))
        return {"long": _aligned_matrix(df, "long_ret", assets), "short": _aligned_matrix(df, "short_ret", assets)}
    df = panel_df.with_columns(ret=pl.col("open").shift(-1).over("asset") / pl.col("open") - 1.0)
    return {"long": _aligned_matrix(df, "ret", assets), "short": None}


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


def simulate_positions(positions, returns_panel, funding_panel, contract, val_slice, cost_multiplier=1.0,
                       delay_offset=0, funding_mask=None, quote_returns=None) -> dict[str, np.ndarray]:
    pos = np.asarray(positions, dtype=float)
    total_delay = contract.execution_delay_bars + delay_offset
    if total_delay > 0:
        pos = np.roll(pos, total_delay, axis=0)
        pos[:total_delay] = 0.0
    pos_val = pos[val_slice]
    ret_val = returns_panel[val_slice]
    turnover = np.sum(np.abs(np.diff(pos_val, axis=0, prepend=0.0)), axis=1)
    if quote_returns is not None and quote_returns.get("short") is not None:
        long_r, short_r = quote_returns["long"][val_slice], quote_returns["short"][val_slice]
        # short_ret is already the per-unit-short PnL, so exposure is |position|.
        gross = np.sum(np.where(pos_val > 0, pos_val * long_r, np.abs(pos_val) * short_r), axis=1)
    else:
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
        root = Path(repo_root)
        if str(root) == "." and os.environ.get("WARDEN_REPO_ROOT"):  # explicit repo_root always wins
            root = Path(os.environ["WARDEN_REPO_ROOT"])
        self.repo_root = root
        db = os.environ.get("WARDEN_REGISTRY") or self.repo_root / "registry" / "registry.db"
        self.registry = registry or ResearchRegistry(db)

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
        strat_module = normalise_module(str(cfg.get("strategy_module", "")))
        with self.registry._connect() as conn:
            h = conn.execute("SELECT title, economic_mechanism, counterparty_or_friction, expected_holding_bars, falsification_criteria, trials_used FROM hypotheses WHERE hypothesis_id = ?", (hyp_id,)).fetchone()
            if not h:
                raise ValueError(f"Hypothesis '{hyp_id}' not found.")
            hyp = HypothesisSpec(hyp_id, contract.contract_id, h[0], family, h[1], h[2], int(h[3]), h[4])
            trials_used = h[5]
        try:
            evaluate_stage0_feasibility(contract, hyp, 0.0085 * math.sqrt(hyp.expected_holding_bars), trials_used)
            dataset = resolve_dataset(contract, self.repo_root)
            panel_df = pl.read_parquet(self.repo_root / dataset.path)
            if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
                panel_df = panel_df.rename({"symbol": "asset"})
            panel_df = panel_df.sort("timestamp", "asset")
            out = _run_strategy_isolated(self.repo_root / strat_module, panel_df, contract, params)
            ts = sorted(panel_df["timestamp"].unique().to_list())
            assets = sorted(panel_df["asset"].unique().to_list())
            v_slice = slice(bisect.bisect_left(ts, contract.val_start), bisect.bisect_left(ts, contract.val_end))
            ret_p = execution_returns(panel_df, contract, assets)
            fund_p = _aligned_matrix(panel_df, "funding_rate", assets) if "funding_rate" in panel_df.columns else None
            fund_mask = _funding_settlement_mask(ts, contract)
            sim1 = simulate_positions(out["positions"], ret_p["long"], fund_p, contract, v_slice,
                                      cost_multiplier=1.0, funding_mask=fund_mask, quote_returns=ret_p)
            if family == "cross_sectional_ml":
                report = evaluate_stage1_cross_sectional_ml(out["ic_series"])
            else:
                report = evaluate_stage1_rules_ts(sim1["net_returns"].tolist(), sim1["gross_returns"].tolist(),
                                                  contract.total_one_way_cost_decimal(), sim1["positions"].tolist())
            diagnostics = {**report.metrics, "dataset": dataset.to_dict()}
            if family == "cross_sectional_ml":
                diagnostics["ic_series"] = np.asarray(out["ic_series"], dtype=float).tolist()
            self.registry.record_selection_attempt(
                hypothesis_id=hyp_id, contract_id=contract.contract_id, kind="standalone_screen",
                stage="STAGE_1_SIGNAL", outcome="PASS" if report.passed else "REJECT", strategy_family=family,
                strategy_module=strat_module, parameters=params, data_provenance=dataset.provenance,
                returns=sim1["net_returns"], diagnostics=diagnostics)
            return report
        except HypothesisBudgetExhaustedError:
            raise
        except Exception as exc:
            self._note_failure(contract, hyp_id, family, strat_module, params, None, "STAGE_1_SIGNAL", exc)
            raise

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

        strat_module = strat_module_str = normalise_module(str(cfg.get("strategy_module", "")))
        try:
            evaluate_stage0_feasibility(contract, hyp, 0.0085 * math.sqrt(hyp.expected_holding_bars), trials_used)
        except ValueError as exc:
            self._note_failure(contract, hyp_id, family, strat_module_str, params, None, "STAGE_0", exc)
            raise
        run_id = override_run_id or cfg.get("run_id") or f"RUN-{family[:2].upper()}-{trials_used+1:03d}"
        try:
            dataset = resolve_dataset(contract, self.repo_root)  # missing data is never substituted
        except ValueError as exc:
            self._note_failure(contract, hyp_id, family, strat_module_str, params, None, "DATA", exc)
            raise
        self.registry.record_hypothesis_module(hyp_id, strat_module_str)
        trial_num = self.registry.register_trial_start(hyp_id, run_id)
        provenance_kw = {"data_provenance": dataset.provenance}
        data_sha = dataset.sha256
        is_synthetic = int(not dataset.eligible_market)
        prior_keff, prior_noise, family_key = 1.0, 0.0, ""
        try:
            panel_df = pl.read_parquet(self.repo_root / dataset.path)
            if "symbol" in panel_df.columns and "asset" not in panel_df.columns: panel_df = panel_df.rename({"symbol": "asset"})
            panel_df = panel_df.sort("timestamp", "asset")
            ts = sorted(panel_df["timestamp"].unique().to_list())
            assets = sorted(panel_df["asset"].unique().to_list())
            v_slice = slice(bisect.bisect_left(ts, contract.val_start), bisect.bisect_left(ts, contract.val_end))
            ret_p = execution_returns(panel_df, contract, assets)
            fund_p = _aligned_matrix(panel_df, "funding_rate", assets) if "funding_rate" in panel_df.columns else None
            fund_mask = _funding_settlement_mask(ts, contract)

            strat_file = self.repo_root / strat_module
            out = _run_strategy_isolated(strat_file, panel_df, contract, params)
            if int(out.get("signal_delay_bars", 0)) > 0:
                raise DoubleLatencyError(
                    f"'{strat_name}' declares signal_delay_bars={out['signal_delay_bars']} while Warden already "
                    f"applies execution_delay_bars={contract.execution_delay_bars}; signals must not be pre-lagged.")
            raw_pos, ic_series = out["positions"], out.get("ic_series")

            prior_keff, prior_noise, family_key = self.registry.prior_search_floor(contract)
            sim2 = simulate_positions(raw_pos, ret_p["long"], fund_p, contract, v_slice, cost_multiplier=2.0,
                                      funding_mask=fund_mask, quote_returns=ret_p)
            sim_d = simulate_positions(raw_pos, ret_p["long"], fund_p, contract, v_slice, delay_offset=1,
                                       funding_mask=fund_mask, quote_returns=ret_p)
            sr2 = float(np.mean(sim2["net_returns"]) / (np.std(sim2["net_returns"], ddof=1) + 1e-12) * math.sqrt(contract.bars_per_year))
            srd = float(np.mean(sim_d["net_returns"]) / (np.std(sim_d["net_returns"], ddof=1) + 1e-12) * math.sqrt(contract.bars_per_year))
            sim1 = simulate_positions(raw_pos, ret_p["long"], fund_p, contract, v_slice, cost_multiplier=1.0,
                                      funding_mask=fund_mask, quote_returns=ret_p)
            net_ret, gross_ret, turnover = sim1["net_returns"], sim1["gross_returns"], sim1["turnover"]
            m = calculate_metrics_from_returns(gross_ret, net_ret, turnover, contract.bars_per_year)
            m.update(compute_trade_metrics(sim1["positions"], net_ret, ret_p["long"][v_slice],
                                           contract.bars_per_year, contract.total_one_way_cost_decimal()))

            s1_rules = evaluate_stage1_rules_ts(net_ret.tolist(), gross_ret.tolist(), contract.total_one_way_cost_decimal(), sim1["positions"].tolist()) if family == "rules_ts" else None

            mean_ic, ic_hac_t = None, None
            if family == "cross_sectional_ml":
                if ic_series is None:
                    raise ValueError("cross_sectional_ml strategies must output 'ic_series'")
                s1 = evaluate_stage1_cross_sectional_ml(ic_series)
                mean_ic, ic_hac_t = s1.metrics.get("mean_ic"), s1.metrics.get("ic_hac_tstat")
                if not s1.passed:
                    # Store the observed stage-1 evidence (IC series and realized signal returns),
                    # never a placeholder zero return vector.
                    diag = {"dataset": dataset, "stage1_signal": {"metrics": s1.metrics, "notes": s1.notes,
                                                                  "vector": np.asarray(ic_series, dtype=float).tolist()}}
                    res = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "STAGE_1_SIGNAL", "REJECTED_EARLY", 0.0, 0.0, 0.0, 0.0, self.registry.validation_attempt_count(contract.contract_id), float(max(prior_keff, 1.0)), mean_ic, ic_hac_t, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, f"Rejected at Stage 1: {s1.notes}", 0.0, 0.0, int(is_synthetic), data_sha, strat_module_str, str(model_type), "")
                    res = dc_replace(res, **provenance_kw, diagnostics_json=canonical_json(diag))
                    self.registry.record_trial(res, params, net_ret, trial_number=trial_num, diagnostics=diag)
                    return res

            # Every candidate and grid configuration is registered BEFORE the selection-history
            # snapshot and the gate: a pending, failed or same-run grid can never be free.
            self.registry.record_selection_attempt(
                hypothesis_id=hyp_id, contract_id=contract.contract_id, kind="standalone_backtest",
                stage="STAGE_3_STRESS", outcome="PENDING", strategy_family=family,
                strategy_module=strat_module_str, parameters=params,
                config_hash=hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12],
                data_provenance=dataset.provenance, returns=net_ret,
                diagnostics={"net_sharpe": m["net_sharpe"]}, run_id=run_id)
            ngrid, n_sr = cfg.get("param_grid", []), []
            for grid_params in ngrid:
                grid_hash = hashlib.sha256(canonical_json(grid_params).encode()).hexdigest()[:12]
                try:
                    grid_sim = simulate_positions(
                        _run_strategy_isolated(strat_file, panel_df, contract, grid_params)["positions"],
                        ret_p["long"], fund_p, contract, v_slice, funding_mask=fund_mask, quote_returns=ret_p)
                except Exception as grid_exc:
                    self.registry.record_selection_attempt(
                        hypothesis_id=hyp_id, contract_id=contract.contract_id, kind="param_grid",
                        stage="STAGE_3_STRESS", outcome="GRID_ERROR", strategy_family=family,
                        strategy_module=strat_module_str, parameters=grid_params, config_hash=grid_hash,
                        data_provenance=dataset.provenance, diagnostics={"error": str(grid_exc)})
                    raise
                n_sr.append(float(np.mean(grid_sim["net_returns"]) / (np.std(grid_sim["net_returns"], ddof=1) + 1e-12)
                                  * math.sqrt(contract.bars_per_year)))
                self.registry.record_selection_attempt(
                    hypothesis_id=hyp_id, contract_id=contract.contract_id, kind="param_grid",
                    stage="STAGE_3_STRESS", outcome="GRID", strategy_family=family,
                    strategy_module=strat_module_str, parameters=grid_params, config_hash=grid_hash,
                    data_provenance=dataset.provenance, returns=grid_sim["net_returns"],
                    diagnostics={"net_sharpe": n_sr[-1]})
            stability = float(min(n_sr) / (max(n_sr) + 1e-8)) if n_sr else 1.0

            hist = self.registry.selection_history(contract.contract_id, v_slice.stop - v_slice.start,
                                                   exclude_run_id=run_id)
            report = compute_selection_report(net_ret, hist["streams"], contract.bars_per_year,
                                              raw_attempt_count=self.registry.validation_attempt_count(contract.contract_id),
                                              prior_max_keff=prior_keff, prior_max_noise=prior_noise)

            self.registry.update_search_floor(contract.contract_id, report["k_eff_gate"],
                                             report["expected_max_sharpe_noise"])
            dsr_p, exp_max, k_eff = report["probability"], report["expected_max_sharpe_noise"], report["k_eff_gate"]
            diag = {"dataset": dataset, "family_key": family_key,
                    "selection": {k: report[k] for k in ("raw_attempt_count", "streams_used", "unvectorized_attempts",
                                                         "prior_max_keff", "k_eff_dedup", "k_eff_gate",
                                                         "noise_floor_applied", "correction_policy",
                                                         "non_promotable_reason")},
                    "excluded_streams": hist["excluded"]}
            if s1_rules is not None:
                diag["stage1_rules"] = {"metrics": s1_rules.metrics, "notes": s1_rules.notes}
            diag["metrics"] = m
            diag["timing_skill"] = compute_shift_skill_pvalue(sim1["positions"], ret_p["long"][v_slice],
                                                              contract.label_horizon_bars)
            min_sr = 1.2 if family == "cross_sectional_ml" else 1.0
            ic_ok = (ic_hac_t >= 2.0) if family == "cross_sectional_ml" and ic_hac_t is not None else True
            rules_ok = s1_rules.passed if s1_rules is not None else True
            statistical = (m["net_sharpe"] > min_sr and sr2 > 0.50 and srd > 0.0 and dsr_p >= 0.90
                           and not report["non_promotable_reason"] and ic_ok and rules_ok)
            if not dataset.eligible_market:
                verdict = "NON_PROMOTABLE"
            else:
                verdict = "CANDIDATE_FOR_QUALIFICATION" if statistical else "REVISE"
            res = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "STAGE_3_STRESS", verdict, m["gross_sharpe"], m["net_sharpe"], dsr_p, float(exp_max), report["raw_attempt_count"], k_eff, mean_ic, ic_hac_t, m["hit_rate"], m["profit_factor"], m["max_drawdown_pct"], m["annualized_turnover"], m["breakeven_cost_bps"], sr2, stability, f"Simulated on {contract.contract_id}; 2xCostSR={sr2:.2f}.", srd, m["cvar_99_pct"], int(is_synthetic), data_sha, strat_module_str, str(model_type), "")
            res = dc_replace(res, **provenance_kw, diagnostics_json=canonical_json(diag))
            self.registry.record_trial(res, params, net_ret, trial_number=trial_num, diagnostics=diag)
            return res
        except Exception as exc:
            # Raw N is the real attempt count, never a hardcoded 1; evidence-write failures are
            # surfaced instead of silently discarded.
            raw_n = self.registry.validation_attempt_count(contract.contract_id)
            res_failed = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "FAILED", "FAILED", 0.0, 0.0, 0.0, 0.0, raw_n, float(max(prior_keff, 1.0)), None, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, f"Execution failed: {exc}", 0.0, 0.0, int(is_synthetic), data_sha, strat_module_str, str(model_type), "")
            try:
                self.registry.record_trial(res_failed, params, np.zeros(0), trial_number=trial_num,
                                           diagnostics={"error": str(exc), "dataset": dataset})
            except (ValueError, sqlite3.Error, OSError) as rec_exc:
                print(f"WARN: failed-trial evidence could not be recorded: {rec_exc}", file=sys.stderr)
            raise

    def _note_failure(self, contract, hyp_id, family, strat_module, params, dataset, stage, exc):
        """Record validation-consuming failures that never reach a trial row."""
        try:
            self.registry.record_selection_attempt(
                hypothesis_id=hyp_id, contract_id=contract.contract_id, kind="failed_reservation", stage=stage,
                outcome="ERROR", strategy_family=family, strategy_module=strat_module, parameters=params,
                data_provenance=(dataset or {}).get("provenance", "unknown"), diagnostics={"error": str(exc)})
        except HypothesisBudgetExhaustedError:
            pass  # the exhausted lineage already refuses further validation; the budget binds.
