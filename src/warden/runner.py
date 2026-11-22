"""Central backtest simulation, Engine integration, and Linear-First Gate."""

from __future__ import annotations

import bisect
import hashlib
import math
import pickle
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

from warden.codeid import code_closure
from warden.contracts import (
    ENGINE_SIMULATION_EPOCH,
    EvaluationContract,
    ExperimentResult,
    HypothesisSpec,
    Stage1Report,
    canonical_json,
    load_contract,
    validate_panel_feed_spec,
)
from warden.diagnostics import compute_shift_skill_pvalue, compute_trade_metrics
from warden.engine_sim import (
    ExecutionDataError,  # noqa: F401
    analytical_1p5x_sharpe,
    compute_engine_diagnostics,
    export_backtest_artifacts,
    extract_execution_policy,
    require_execution_columns,
    run_engine_simulation,
)
from warden.gates import (
    DSR_FLOOR,
    IC_T_FLOOR,
    MIN_SR,
    REVISE_SR_FLOOR,
    SR2_FLOOR,
    SRD_FLOOR,
    TRIMMED_SHARPE_FLOOR,
    annualised_sharpe,
    compute_cross_sectional_ic_for_screen,
    compute_trimmed_sharpe,
    empirical_holding_volatility,
    evaluate_neighbor_stability,
    evaluate_stage0_feasibility,
    evaluate_stage1_cross_sectional_ml,
    evaluate_stage1_rules_ts,
)
from warden.provenance import resolve_dataset
from warden.registry import ResearchRegistry, compute_selection_report, normalise_module


class LinearBaselineRequiredError(ValueError):
    """Raised when a non-linear ML strategy is executed before a linear baseline is logged."""


class DoubleLatencyError(ValueError):
    """A strategy pre-lags its own signals on top of Warden's mandatory execution delay."""


def aligned_matrix(panel_df: pl.DataFrame, value_col: str, sorted_assets: list[str]) -> np.ndarray:
    p = panel_df.pivot(index="timestamp", on="asset", values=value_col).fill_null(0.0)
    for a in sorted_assets:
        if a not in p.columns:
            p = p.with_columns(pl.lit(0.0).alias(a))
    return p.select(sorted_assets).to_numpy()


_aligned_matrix = aligned_matrix


def funding_settlement_mask(timestamps: list, contract: EvaluationContract) -> np.ndarray:
    if contract.asset_class == "crypto_perps" and contract.frequency != "1d":
        return np.array(
            [t.hour % 8 == 0 and t.minute == 0 and t.second == 0 for t in timestamps],
            dtype=bool,
        )
    return np.ones(len(timestamps), dtype=bool)


_funding_settlement_mask = funding_settlement_mask


def _evaluate_cs_ml_stage1(
    out: dict[str, Any], contract: EvaluationContract
) -> tuple[Stage1Report, np.ndarray | Sequence[float]]:
    ic_series = out.get("ic_series")
    if ic_series is None and "predictions" in out and "forward_returns" in out:
        ic_series = compute_cross_sectional_ic_for_screen(
            out["predictions"], out["forward_returns"]
        )
    if ic_series is None:
        raise ValueError(
            "cross_sectional_ml strategies must output 'ic_series' or ('predictions' and 'forward_returns')"
        )
    report = evaluate_stage1_cross_sectional_ml(
        ic_series,
        label_horizon=contract.label_horizon_bars + contract.execution_delay_bars,
    )
    return report, ic_series


def execution_returns(panel_df: pl.DataFrame, contract: EvaluationContract, assets: list[str]) -> dict:
    """NEXT_BAR: open-to-open. QUOTE_SIDE: long pays ask, receives bid; short the reverse."""
    require_execution_columns(panel_df, contract)
    if contract.execution_mode == "QUOTE_SIDE":
        df = panel_df.with_columns(
            long_ret=pl.col("bid").shift(-1).over("asset") / pl.col("ask") - 1.0,
            short_ret=-(pl.col("ask").shift(-1).over("asset") / pl.col("bid") - 1.0))
        return {"long": aligned_matrix(df, "long_ret", assets), "short": aligned_matrix(df, "short_ret", assets)}
    df = panel_df.with_columns(ret=pl.col("open").shift(-1).over("asset") / pl.col("open") - 1.0)
    return {"long": aligned_matrix(df, "ret", assets), "short": None}


def filter_target_symbols(panel_df: pl.DataFrame, params: dict, path: str = "") -> pl.DataFrame:
    if not params or "target_symbols" not in params or params["target_symbols"] is None:
        return panel_df
    tgt = params["target_symbols"]
    if not isinstance(tgt, (list, tuple, set)) or len(tgt) == 0:
        raise ValueError(f"target_symbols must be a non-empty list of symbols, got {tgt!r}")
    sym_col = "asset" if "asset" in panel_df.columns else "symbol"
    available = set(panel_df[sym_col].unique().to_list())
    unknown = set(tgt) - available
    if unknown:
        raise ValueError(
            f"target_symbols contains unknown symbols {sorted(unknown)}; available in {path or 'panel'}: {sorted(available)}"
        )
    return panel_df.filter(pl.col(sym_col).is_in(list(tgt)))


def prepare_panel(repo_root: Path, dataset, params: dict, contract: EvaluationContract) -> tuple[pl.DataFrame, list, list, slice]:
    panel_df = pl.read_parquet(repo_root / dataset.path)
    if "symbol" in panel_df.columns and "asset" not in panel_df.columns:
        panel_df = panel_df.rename({"symbol": "asset"})
    panel_df = filter_target_symbols(panel_df, params, str(dataset.path)).sort("timestamp", "asset")
    ts = panel_df["timestamp"].unique().sort().to_list()
    assets = sorted(panel_df["asset"].unique().to_list())
    v_slice = slice(bisect.bisect_left(ts, contract.val_start), bisect.bisect_left(ts, contract.val_end))
    return panel_df, ts, assets, v_slice


def simulation_inputs(panel_df: pl.DataFrame, contract: EvaluationContract, ts: list, assets: list) -> tuple[dict, np.ndarray | None, np.ndarray]:
    ret_p = execution_returns(panel_df, contract, assets)
    fund_p = aligned_matrix(panel_df, "funding_rate", assets) if "funding_rate" in panel_df.columns else None
    return ret_p, fund_p, funding_settlement_mask(ts, contract)


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
            "from warden.strategy_loader import load_module_from_path\n"
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


def default_run_id(hypothesis_id: str, trial_number: int) -> str:
    """Stable, globally unique default ID for a hypothesis's consumed trial slot."""
    digest = hashlib.sha256(hypothesis_id.encode()).hexdigest()[:12]
    return f"RUN-{hypothesis_id[:32]}-{digest}-{trial_number:03d}"


class ExperimentRunner:
    def __init__(self, registry: ResearchRegistry | None = None, repo_root: Path = Path(".")):
        self.repo_root = Path(repo_root)
        self.registry = registry or ResearchRegistry(self.repo_root / "registry" / "registry.db")
        self._stage0_vol_cache: dict[tuple[str, tuple[str, ...], int], float] = {}

    def resolve_contract(self, contract_id: str) -> EvaluationContract:
        cid = contract_id.lower()
        for p in [self.repo_root / "contracts" / f"{cid}.yaml", self.repo_root / "contracts" / f"{cid}.yml"]:
            if p.is_file():
                return load_contract(p)
        raise FileNotFoundError(f"Contract not found: '{contract_id}'")

    def _prepare_and_check_stage0(
        self, contract, hyp, hyp_id, family, strat_module, params, trials_used,
        simulation_epoch: str = ENGINE_SIMULATION_EPOCH,
    ):
        try:
            dataset = resolve_dataset(contract, self.repo_root)  # missing data is never substituted
            panel_df, ts, assets, v_slice = prepare_panel(self.repo_root, dataset, params, contract)
            require_execution_columns(panel_df, contract)
            validate_panel_feed_spec(panel_df, contract)
        except (ValueError, OSError) as exc:
            self._note_failure(contract, hyp_id, family, strat_module, params, None, "DATA", exc,
                               simulation_epoch=simulation_epoch)
            raise
        vol_key = (dataset.sha256, tuple(assets), hyp.expected_holding_bars)
        if vol_key not in self._stage0_vol_cache:
            self._stage0_vol_cache[vol_key] = empirical_holding_volatility(panel_df, hyp.expected_holding_bars)
        try:
            evaluate_stage0_feasibility(contract, hyp, self._stage0_vol_cache[vol_key], trials_used)
        except ValueError as exc:
            self._note_failure(contract, hyp_id, family, strat_module, params, dataset.to_dict(), "STAGE_0", exc,
                               simulation_epoch=simulation_epoch)
            raise
        code_sha, _ = code_closure(self.repo_root, strat_module)
        return dataset, panel_df, ts, assets, v_slice, code_sha

    def run_stage1_screen(self, exp_yaml_path: Path) -> Stage1Report:
        cfg = yaml.safe_load(Path(exp_yaml_path).read_text(encoding="utf-8"))
        contract = self.resolve_contract(cfg["contract_id"])
        hyp_id, family = cfg["hypothesis_id"], cfg.get("strategy_family", "rules_ts")
        params = cfg.get("parameters", {})
        simulation_epoch = str(cfg.get("simulation_epoch") or ENGINE_SIMULATION_EPOCH)
        strat_module = normalise_module(str(cfg.get("strategy_module", "")))
        with self.registry._connect() as conn:
            h = conn.execute("SELECT title, economic_mechanism, counterparty_or_friction, expected_holding_bars, falsification_criteria, trials_used FROM hypotheses WHERE hypothesis_id = ?", (hyp_id,)).fetchone()
            if not h:
                raise ValueError(f"Hypothesis '{hyp_id}' not found.")
            hyp = HypothesisSpec(hyp_id, contract.contract_id, h[0], family, h[1], h[2], int(h[3]), h[4])
            trials_used = h[5]
        dataset, panel_df, ts, assets, v_slice, _ = self._prepare_and_check_stage0(
            contract, hyp, hyp_id, family, strat_module, params, trials_used, simulation_epoch=simulation_epoch)
        screen_key = self.registry.reserve_screen(hyp_id, contract.contract_id, strat_module, params, dataset.sha256)
        strat_file = self.repo_root / strat_module
        out = _run_strategy_isolated(strat_file, panel_df, contract, params)
        if family == "cross_sectional_ml":
            report, ic_series = _evaluate_cs_ml_stage1(out, contract)
            self.registry.record_selection_attempt(
                hypothesis_id=hyp_id, contract_id=contract.contract_id, kind="standalone_screen",
                stage="STAGE_1_SIGNAL", outcome="PASS" if report.passed else "REJECT", strategy_family=family,
                strategy_module=strat_module, parameters=params, data_provenance=dataset.provenance,
                returns=ic_series, diagnostics={**report.metrics, "dataset": dataset.to_dict()},
                screen_key=screen_key, simulation_epoch=simulation_epoch)
            return report
        ret_p, fund_p, fund_mask = simulation_inputs(panel_df, contract, ts, assets)
        sim1 = simulate_positions(out["positions"], ret_p["long"], fund_p, contract, v_slice, cost_multiplier=1.0,
                                  funding_mask=fund_mask, quote_returns=ret_p)
        report = evaluate_stage1_rules_ts(
            sim1["net_returns"],
            sim1["gross_returns"],
            contract.total_one_way_cost_decimal(),
            sim1["positions"],
            bars_per_year=contract.bars_per_year,
            holding_horizon_bars=hyp.expected_holding_bars,
        )
        self.registry.record_selection_attempt(
            hypothesis_id=hyp_id, contract_id=contract.contract_id, kind="standalone_screen",
            stage="STAGE_1_SIGNAL", outcome="PASS" if report.passed else "REJECT", strategy_family=family,
            strategy_module=strat_module, parameters=params, data_provenance=dataset.provenance,
            returns=sim1["net_returns"], diagnostics={**report.metrics, "dataset": dataset.to_dict()},
            screen_key=screen_key, simulation_epoch=simulation_epoch)
        return report

    def run_spec(self, exp_yaml_path: Path, override_run_id: str | None = None) -> ExperimentResult:
        cfg = yaml.safe_load(Path(exp_yaml_path).read_text(encoding="utf-8"))
        contract = self.resolve_contract(cfg["contract_id"])
        hyp_id, family = cfg["hypothesis_id"], cfg.get("strategy_family", "rules_ts")
        strat_name, model_type, params = cfg.get("strategy_name", Path(exp_yaml_path).stem), cfg.get("model_type", "rules"), cfg.get("parameters", {})
        simulation_epoch = str(cfg.get("simulation_epoch") or ENGINE_SIMULATION_EPOCH)
        policy_dict = extract_execution_policy(cfg, params)

        with self.registry._connect() as conn:
            h = conn.execute("SELECT title, economic_mechanism, counterparty_or_friction, expected_holding_bars, falsification_criteria, trials_used, strategy_family, source_published_at FROM hypotheses WHERE hypothesis_id = ?", (hyp_id,)).fetchone()
            if not h:
                raise ValueError(f"Hypothesis '{hyp_id}' not found.")
            reg_family = h[6]
            if family != reg_family:
                raise ValueError(f"Experiment spec strategy_family '{family}' does not match registered hypothesis family '{reg_family}'.")
            hyp = HypothesisSpec(hyp_id, contract.contract_id, h[0], reg_family, h[1], h[2], int(h[3]), h[4],
                                 source_published_at=h[7])
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
        dataset, panel_df, ts, assets, v_slice, code_sha = self._prepare_and_check_stage0(
            contract, hyp, hyp_id, family, strat_module_str, params, trials_used, simulation_epoch=simulation_epoch)
        run_id = override_run_id or cfg.get("run_id") or default_run_id(hyp_id, trials_used + 1)
        self.registry.record_hypothesis_module(hyp_id, strat_module_str)
        trial_num = self.registry.register_trial_start(hyp_id, run_id)
        provenance_kw = {"data_provenance": dataset.provenance, "strategy_code_sha256": code_sha,
                         "simulation_epoch": simulation_epoch}
        data_sha = dataset.sha256
        is_synthetic = int(not dataset.eligible_market)
        try:
            ret_p = execution_returns(panel_df, contract, assets)

            strat_file = self.repo_root / strat_module
            out = _run_strategy_isolated(strat_file, panel_df, contract, params)
            if int(out.get("signal_delay_bars", 0)) > 0:
                raise DoubleLatencyError(
                    f"'{strat_name}' declares signal_delay_bars={out['signal_delay_bars']} while Warden already "
                    f"applies execution_delay_bars={contract.execution_delay_bars}; signals must not be pre-lagged.")
            raw_pos = out["positions"]

            prior_keff, prior_noise, family_key = self.registry.prior_search_floor(contract)

            mean_ic, ic_hac_t = None, None
            if family == "cross_sectional_ml":
                s1, ic_series = _evaluate_cs_ml_stage1(out, contract)
                mean_ic, ic_hac_t = s1.metrics.get("mean_ic"), s1.metrics.get("ic_hac_tstat")
                if not s1.passed:
                    n_attempts = self.registry.validation_attempt_count(
                        contract.contract_id, simulation_epoch=simulation_epoch) + 1
                    # Store the observed validation signal, never a placeholder zero return vector.
                    diag = {"dataset": dataset, "neighbor_tested": False, "failed_gates": ["ic_hac"],
                            "stage1_signal": {"metrics": s1.metrics, "notes": s1.notes,
                                              "vector": np.asarray(ic_series, dtype=float).tolist()}}
                    res = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "STAGE_1_SIGNAL", "REJECTED_EARLY", 0.0, 0.0, 0.0, 0.0, n_attempts, float(prior_keff), mean_ic, ic_hac_t, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None, f"Rejected at Stage 1: {s1.notes}", 0.0, 0.0, int(is_synthetic), data_sha, strat_module_str, str(model_type), "")
                    res = dc_replace(res, **provenance_kw, diagnostics_json=canonical_json(diag))
                    self.registry.record_trial(res, params, np.zeros(0), trial_number=trial_num, diagnostics=diag)
                    return res

            sim1 = run_engine_simulation(
                panel_df, raw_pos, ts, assets, contract, v_slice, cost_multiplier=1.0, **policy_dict
            )
            sr_1p5 = analytical_1p5x_sharpe(
                sim1.backtest_result, contract.bars_per_year, start_idx=v_slice.start or 0
            )
            net_ret, gross_ret, turnover = sim1.net_returns, sim1.gross_returns, sim1.turnover
            m = calculate_metrics_from_returns(gross_ret, net_ret, turnover, contract.bars_per_year)
            m.update(compute_trade_metrics(sim1.positions, net_ret, ret_p["long"][v_slice],
                                           contract.bars_per_year, contract.total_one_way_cost_decimal()))

            s1_rules = (
                evaluate_stage1_rules_ts(
                    net_ret,
                    gross_ret,
                    contract.total_one_way_cost_decimal(),
                    sim1.positions,
                    bars_per_year=contract.bars_per_year,
                    holding_horizon_bars=hyp.expected_holding_bars,
                )
                if family == "rules_ts"
                else None
            )

            sim2 = run_engine_simulation(
                panel_df, raw_pos, ts, assets, contract, v_slice, cost_multiplier=2.0, **policy_dict
            )
            sim_d = run_engine_simulation(
                panel_df, raw_pos, ts, assets, contract, v_slice, delay_offset=1, **policy_dict
            )
            sr2 = annualised_sharpe(sim2.net_returns, contract.bars_per_year)
            srd = annualised_sharpe(sim_d.net_returns, contract.bars_per_year)

            ngrid, n_sr = cfg.get("param_grid") or [], []
            grid_returns_list: list[np.ndarray] = [net_ret]
            for grid_params in ngrid:
                grid_pos = _run_strategy_isolated(strat_file, panel_df, contract, grid_params)["positions"]
                grid_sim = run_engine_simulation(
                    panel_df, grid_pos, ts, assets, contract, v_slice, **policy_dict
                )
                grid_returns_list.append(grid_sim.net_returns)
                n_sr.append(annualised_sharpe(grid_sim.net_returns, contract.bars_per_year))
                # Grid configurations consume validation evidence: count them, never silently.
                self.registry.record_selection_attempt(
                    hypothesis_id=hyp_id, contract_id=contract.contract_id, kind="param_grid",
                    stage="STAGE_3_STRESS", outcome="GRID", strategy_family=family,
                    strategy_module=strat_module_str, parameters=grid_params,
                    config_hash=hashlib.sha256(canonical_json(grid_params).encode()).hexdigest()[:12],
                    data_provenance=dataset.provenance, returns=grid_sim.net_returns,
                    diagnostics={"net_sharpe": n_sr[-1]}, simulation_epoch=simulation_epoch)
            neighbor_report = evaluate_neighbor_stability(n_sr, m["net_sharpe"])
            stability = neighbor_report["stability"]
            trimmed_sr = compute_trimmed_sharpe(net_ret, contract.bars_per_year)

            artifacts_dir = (
                self.registry.db_path.parent
                / "backtest_results"
                / simulation_epoch
                / contract.contract_id
                / run_id
            )
            engine_artifacts = export_backtest_artifacts(
                sim1.backtest_result,
                artifacts_dir,
                repo_root=self.repo_root,
                simulation_epoch=simulation_epoch,
            )
            grid_mat = np.column_stack(grid_returns_list) if len(grid_returns_list) >= 2 else None
            engine_diag = compute_engine_diagnostics(
                sim1.backtest_result,
                net_ret,
                ts[v_slice],
                contract,
                grid_return_matrix=grid_mat,
            )

            # Record param_grid attempts before evaluating n_attempts and effective_rank.
            hist = self.registry.selection_history(
                contract.contract_id, v_slice.stop - v_slice.start, simulation_epoch=simulation_epoch)
            n_attempts = hist["raw_attempt_count"] + 1
            report = compute_selection_report(net_ret, hist["streams"], contract.bars_per_year,
                                              raw_attempt_count=n_attempts, prior_max_keff=prior_keff,
                                              prior_max_noise=prior_noise)
            self.registry.update_search_floor(contract.contract_id, report["k_eff_gate"],
                                             report["expected_max_sharpe_noise"])
            dsr_p, exp_max, k_eff = report["probability"], report["expected_max_sharpe_noise"], report["k_eff_gate"]
            retrospective = hyp.is_retrospective_for(contract)
            diag = {"dataset": dataset, "family_key": family_key,
                    "retrospective_replication_only": retrospective,
                    "source_published_at": hyp.source_published_at,
                    "execution_policy": policy_dict,
                    "engine_artifacts": engine_artifacts,
                    "session_col_used": sim1.session_col_used,
                    "funding_events": sim1.funding_events,
                    **engine_diag,
                    "selection": {**{k: report[k] for k in ("raw_attempt_count", "streams_used", "unvectorized_attempts",
                                                            "prior_max_keff", "k_eff_dedup", "k_eff_effective",
                                                            "k_eff_gate", "noise_floor_applied", "correction_policy",
                                                            "non_promotable_reason")},
                                  "simulation_epoch": simulation_epoch},
                    "excluded_streams": hist["excluded"]}
            if s1_rules is not None:
                diag["stage1_rules"] = {"metrics": s1_rules.metrics, "notes": s1_rules.notes}
            diag["metrics"] = m
            diag["timing_skill"] = compute_shift_skill_pvalue(sim1.positions, ret_p["long"][v_slice],
                                                              contract.label_horizon_bars)
            diag["neighbor_tested"] = neighbor_report["neighbor_tested"]
            diag["neighbor_stability"] = neighbor_report
            diag["trimmed_sharpe"] = trimmed_sr

            min_sr = MIN_SR.get(family, REVISE_SR_FLOOR)
            failed_gates: list[str] = []
            if not (m["net_sharpe"] > min_sr):
                failed_gates.append("net_sharpe")
            if not (dsr_p >= DSR_FLOOR) or bool(report["non_promotable_reason"]):
                failed_gates.append("dsr")
            if not (sr2 > SR2_FLOOR):
                failed_gates.append("cost_2x")
            if not (srd > SRD_FLOOR):
                failed_gates.append("latency_plus1")
            if neighbor_report["neighbor_tested"] and not neighbor_report["passed"]:
                failed_gates.append("neighbor_stability")
            if trimmed_sr is None or not (trimmed_sr > TRIMMED_SHARPE_FLOOR):
                failed_gates.append("trimmed_sharpe")
            if s1_rules is not None and not s1_rules.passed:
                failed_gates.append("stage1_rules")
            if family == "cross_sectional_ml" and (ic_hac_t is None or not (ic_hac_t >= IC_T_FLOOR)):
                failed_gates.append("ic_hac")
            diag["failed_gates"] = failed_gates
            statistical = len(failed_gates) == 0
            if retrospective:
                verdict = "RETROSPECTIVE_REPLICATION_ONLY"
            elif not dataset.eligible_market:
                verdict = "NON_PROMOTABLE"
            else:
                verdict = "CANDIDATE_FOR_QUALIFICATION" if statistical else "REVISE"
            res = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "STAGE_3_STRESS", verdict, m["gross_sharpe"], m["net_sharpe"], dsr_p, float(exp_max), report["raw_attempt_count"], k_eff, mean_ic, ic_hac_t, m["hit_rate"], m["profit_factor"], m["max_drawdown_pct"], m["annualized_turnover"], m["breakeven_cost_bps"], sr2, stability, f"Simulated on {contract.contract_id}; 2xCostSR={sr2:.2f}.", srd, m["cvar_99_pct"], int(is_synthetic), data_sha, strat_module_str, str(model_type), "")
            res = dc_replace(res, **provenance_kw, stress_1p5_cost_sharpe=sr_1p5, diagnostics_json=canonical_json(diag))
            self.registry.record_trial(res, params, net_ret, trial_number=trial_num, diagnostics=diag)
            return res
        except Exception as exc:
            res_failed = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "FAILED", "FAILED", 0.0, 0.0, 0.0, 0.0, 1, 1.0, None, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None, f"Execution failed: {exc}", 0.0, 0.0, int(is_synthetic), data_sha, strat_module_str, str(model_type), "", strategy_code_sha256=code_sha, simulation_epoch=simulation_epoch)
            try:
                self.registry.record_trial(res_failed, params, np.zeros(0), trial_number=trial_num,
                                           diagnostics={"error": str(exc), "dataset": dataset})
            except (ValueError, sqlite3.Error, OSError):
                pass
            raise

    def _note_failure(self, contract, hyp_id, family, strat_module, params, dataset, stage, exc,
                      simulation_epoch: str = ENGINE_SIMULATION_EPOCH):
        """Record validation-consuming failures that never reach a trial row."""
        self.registry.record_selection_attempt(
            hypothesis_id=hyp_id, contract_id=contract.contract_id, kind="failed_reservation", stage=stage,
            outcome="ERROR", strategy_family=family, strategy_module=strat_module, parameters=params,
            data_provenance=(dataset or {}).get("provenance", "unknown"), diagnostics={"error": str(exc)},
            simulation_epoch=simulation_epoch)
