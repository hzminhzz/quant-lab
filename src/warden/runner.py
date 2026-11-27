"""Canonical research operations, native evaluation, and idempotent progression."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace as dc_replace
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from warden import incremental
from warden.candidate_admission import freeze_candidate
from warden.codeid import code_closure
from warden.contract_source import load_active_contract, load_recorded_contract
from warden.contracts import (
    ENGINE_SIMULATION_EPOCH,
    EvaluationContract,
    ExperimentResult,
    HypothesisSpec,
    Stage1Report,
    canonical_json,
)
from warden.diagnostics import compute_shift_skill_pvalue, compute_trade_metrics
from warden.engine_sim import (
    ExecutionDataError,  # noqa: F401
    analytical_1p5x_sharpe,
    compute_engine_diagnostics,
    export_backtest_artifacts,
    extract_execution_policy,
    require_execution_columns,
)
from warden.evaluation import EvaluationKernel
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
    evaluate_stage1_carry_rv,
    evaluate_stage1_cross_sectional_ml,
    evaluate_stage1_meta_overlay,
    evaluate_stage1_rules_ts,
    measure_trading_cadence,
)
from warden.market_data import (
    ROLE_EXPLORATORY,
    MarketDataGateway,
    MarketPanel,
    select_symbols,
)
from warden.params_model import (
    classify_numeric_neighbors,
    evaluate_neighbor_requirement,
    validate_strategy_params,
)
from warden.protocols import (
    CARRY_MECHANISM_ACTIVE,
    CARRY_MECHANISM_LEGACY,
    CarryMechanismRequiredError,
    carry_mechanism_policy,
    carry_outputs_callable,
    load_and_enforce_strategy_protocol,
    resolve_effective_params,
    resolve_protocol_target,
    verified_carry_outputs,
)
from warden.provenance import resolve_dataset
from warden.registry import ResearchRegistry, compute_selection_report, normalise_module
from warden.robustness import build_robustness_plan
from warden.schemas import load_experiment_spec
from warden_evidence.selection_alignment import project_onto_grid, utc_ns


class LinearBaselineRequiredError(ValueError):
    """Raised when a non-linear ML strategy is executed before a linear baseline is logged."""


class DoubleLatencyError(ValueError):
    """A strategy pre-lags its own signals on top of Warden's mandatory execution delay."""


LINEAR_MODEL_TYPES = frozenset(
    {
        "ridge",
        "linear",
        "elasticnet",
        "ols",
        "lasso",
        "linear_portfolio",
        "linearfeatureportfoliomodel",
        "ipca",
        "ipcamodel",
    }
)
DEEP_ML4T_MODEL_TYPES = frozenset(
    {
        "deep",
        "cae",
        "caemodel",
        "sae",
        "saemodel",
        "sdf",
        "stochastic_discount_factor",
        "stochasticdiscountfactormodel",
        "deep_portfolio",
        "deepportfoliomodel",
        "lstm_portfolio",
        "lstmportfoliomodel",
    }
)


def _classify_model_tier(model_type: str, cfg: dict[str, Any], strat_mod: Any) -> tuple[str, str]:
    """Classify a strategy config/module into ('linear'|'deep'|'nonlinear', model_class)."""
    target = resolve_protocol_target(strat_mod)
    params = cfg.get("parameters") or {}
    raw_class = (
        cfg.get("model_class")
        or params.get("model_class")
        or getattr(target, "MODEL_CLASS", None)
        or getattr(strat_mod, "MODEL_CLASS", None)
        or model_type
    )
    model_class = str(raw_class or model_type or "")
    raw_tier = (
        cfg.get("model_tier")
        or getattr(target, "MODEL_TIER", None)
        or getattr(strat_mod, "MODEL_TIER", None)
        or ""
    )
    tokens = {
        str(model_type or "").strip().lower(),
        model_class.strip().lower(),
        str(raw_tier).strip().lower(),
    }
    tokens.discard("")
    if "deep" in tokens or (tokens & DEEP_ML4T_MODEL_TYPES):
        return "deep", model_class
    if "linear" in tokens or (tokens & LINEAR_MODEL_TYPES):
        return "linear", model_class
    return "nonlinear", model_class


def _trial_is_linear_baseline(
    row_model_type: str | None,
    row_strategy_name: str | None,
    row_strategy_module: str | None,
    row_diagnostics_json: str | None,
) -> bool:
    """Identify logged linear baselines from structured model metadata, model_type, or legacy names."""
    mt = (row_model_type or "").strip().lower()
    if mt in LINEAR_MODEL_TYPES:
        return True
    if row_diagnostics_json:
        try:
            diag = json.loads(row_diagnostics_json)
        except (TypeError, ValueError):
            diag = None
        if isinstance(diag, dict):
            meta = diag.get("model_metadata")
            if isinstance(meta, dict):
                tier = str(meta.get("model_tier") or "").strip().lower()
                mclass = str(meta.get("model_class") or "").strip().lower()
                mtype = str(meta.get("model_type") or "").strip().lower()
                if tier == "linear" or mclass in LINEAR_MODEL_TYPES or mtype in LINEAR_MODEL_TYPES:
                    return True
    if "ridge" in (row_strategy_name or "").lower():
        return True
    return "baseline" in (row_strategy_module or "").lower()


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


def _extract_carry_rv_series(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ts: list,
    assets: list[str],
    v_slice: slice,
    params: dict[str, Any],
    expected_holding_bars: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Extract OOS (net_carry, spread) series on ``v_slice`` without executing Engine."""
    close_mat = aligned_matrix(panel_df, "close", assets)
    fund_mat = (
        aligned_matrix(panel_df, "funding_rate", assets)
        if "funding_rate" in panel_df.columns
        else np.zeros_like(close_mat)
    )
    f_mask = funding_settlement_mask(ts, contract)
    v_start = int(v_slice.start or 0)
    v_stop = int(v_slice.stop if v_slice.stop is not None else len(ts))
    train_end = max(1, v_start)

    cost_per_bar = (2.0 * contract.total_one_way_cost_decimal()) / max(1, int(expected_holding_bars))
    if len(assets) == 2:
        spread_full = np.log(np.maximum(close_mat[:, 0], 1e-12)) - np.log(
            np.maximum(close_mat[:, 1], 1e-12)
        )
        direction = (params or {}).get("carry_direction")
        if direction is not None:
            w0, w1 = float(direction[0]), float(direction[1])
        else:
            # Causal carry direction estimated strictly on pre-validation training bars:
            # short higher-funding asset, long lower-funding asset (funding > 0 => longs pay shorts).
            mean_diff = float(np.mean(fund_mat[:train_end, 0] - fund_mat[:train_end, 1]))
            w0, w1 = (-1.0, 1.0) if mean_diff >= 0.0 else (1.0, -1.0)
        gross_carry = -(w0 * fund_mat[v_start:v_stop, 0] + w1 * fund_mat[v_start:v_stop, 1]) * f_mask[
            v_start:v_stop
        ]
        net_carry = gross_carry - cost_per_bar
        return net_carry, spread_full[v_start:v_stop]

    if len(assets) == 1:
        spread_full = fund_mat[:, 0]
        mean_f = float(np.mean(fund_mat[:train_end, 0]))
        sign = -1.0 if mean_f >= 0.0 else 1.0
        gross_carry = -sign * fund_mat[v_start:v_stop, 0] * f_mask[v_start:v_stop]
        return gross_carry - cost_per_bar, spread_full[v_start:v_stop]

    # Multi-asset cross-sectional carry spread: high-funding half vs low-funding half
    train_means = np.mean(fund_mat[:train_end], axis=0)
    order = np.argsort(train_means)
    half = max(1, len(assets) // 2)
    low_idx, high_idx = order[:half], order[-half:]
    spread_full = np.mean(fund_mat[:, high_idx], axis=1) - np.mean(fund_mat[:, low_idx], axis=1)
    gross_carry = spread_full[v_start:v_stop] * f_mask[v_start:v_stop]
    return gross_carry - cost_per_bar, spread_full[v_start:v_stop]


def _panel_carry_basis(n_assets: int) -> str:
    if n_assets <= 1:
        return "single_asset_funding"
    if n_assets == 2:
        return "pair_log_price_spread"
    return "equal_weight_funding_halves"


def _carry_rv_stage1_series(
    strat_mod: Any,
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ts: list,
    assets: list[str],
    v_slice: slice,
    params: dict[str, Any],
    expected_holding_bars: int,
    *,
    mechanism_policy: str = CARRY_MECHANISM_ACTIVE,
    strategy_module: str = "",
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Series for the ``carry_rv`` Stage-1 gate, plus the evidence of where they came from.

    A strategy must publish ``causal_carry_outputs`` -- its own spread and net-carry series
    for the validation slice.  Those are preferred, and they pass an evaluator-run prefix
    check first, so the gate scores the mechanism the strategy actually trades. The runner
    checks for a missing hook before admission, then executes this series only after
    admission. A missing hook is allowed only for an explicitly authorized legacy replay (``mechanism_policy == CARRY_MECHANISM_LEGACY``), in
    which case the panel proxy is used and recorded as such.
    """
    published = verified_carry_outputs(
        strat_mod, panel_df, contract, ts, assets, v_slice, params
    )
    if published is not None:
        return published["net_carry"], published["spread"], {
            "carry_series_source": published["source"],
            "carry_prefix_cuts": published["prefix_cuts"],
            "carry_prefix_max_abs_delta": published["prefix_max_abs_delta"],
            "carry_panel_basis": "strategy_published",
            "carry_mechanism_policy": mechanism_policy,
        }
    if mechanism_policy != CARRY_MECHANISM_LEGACY:
        raise CarryMechanismRequiredError(
            f"carry_rv '{strategy_module or 'strategy'}' declares no causal_carry_outputs: "
            "Stage 1 must score the strategy's own prefix-verified spread and net carry. "
            "The equal-weight panel proxy is available only as an explicitly authorized "
            "legacy replay."
        )
    net_carry, spread = _extract_carry_rv_series(
        panel_df,
        contract,
        ts,
        assets,
        v_slice,
        params,
        expected_holding_bars,
    )
    return net_carry, spread, {
        "carry_series_source": "panel_proxy",
        "carry_prefix_cuts": 0.0,
        "carry_prefix_max_abs_delta": 0.0,
        "carry_panel_basis": _panel_carry_basis(len(assets)),
        "carry_mechanism_policy": mechanism_policy,
    }


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
    """Kept for callers/tests; the one implementation lives in ``market_data``."""
    if not params or "target_symbols" not in params or params["target_symbols"] is None:
        return panel_df
    return select_symbols(panel_df, params["target_symbols"], label=path)[0]


def prepare_market_panel(
    repo_root: Path, dataset, params: dict, contract: EvaluationContract
) -> MarketPanel:
    """Load one normalised market feed through the single gateway reader (#95)."""
    return MarketDataGateway(repo_root).load(
        contract, ROLE_EXPLORATORY, (params or {}).get("target_symbols"), resolution=dataset
    )


def prepare_panel(repo_root: Path, dataset, params: dict, contract: EvaluationContract) -> tuple[pl.DataFrame, list, list, slice]:
    """Thin adapter over :func:`prepare_market_panel`; the tuple shape is public."""
    market = prepare_market_panel(repo_root, dataset, params, contract)
    return market.panel, list(market.ts), list(market.assets), market.eval_slice


def simulation_inputs(panel_df: pl.DataFrame, contract: EvaluationContract, ts: list, assets: list) -> tuple[dict, np.ndarray | None, np.ndarray]:
    ret_p = execution_returns(panel_df, contract, assets)
    fund_p = aligned_matrix(panel_df, "funding_rate", assets) if "funding_rate" in panel_df.columns else None
    return ret_p, fund_p, funding_settlement_mask(ts, contract)


def _run_strategy_isolated(strategy_module_path: Path, panel_df: pl.DataFrame, contract: EvaluationContract, params: dict) -> dict[str, Any]:
    """Legacy replay only: the single call site lives in :mod:`warden.legacy_replay`."""
    from warden.legacy_replay import run_generate_positions_isolated

    return run_generate_positions_isolated(strategy_module_path, panel_df, contract, params)


def simulate_positions(*args: Any, **kwargs: Any) -> dict[str, np.ndarray]:
    """Legacy PnL arithmetic, re-exported from :mod:`warden.legacy_replay`."""
    from warden.legacy_replay import simulate_positions as _simulate

    return _simulate(*args, **kwargs)


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


def _resolve_hypothesis_file(repo_root: Path, hypothesis_id: str, explicit_path: str) -> Path | None:
    if explicit_path:
        cand = Path(explicit_path)
        if cand.is_file():
            return cand
        rel_cand = repo_root / cand
        return rel_cand if rel_cand.is_file() else None
    hyp_dir = repo_root / ".workspace" / "hypotheses"
    for ext in (".md", ".yaml", ".yml"):
        cand = hyp_dir / f"{hypothesis_id}{ext}"
        if cand.is_file():
            return cand
    return None


def _protocol_kind_from_source(module_path: Path) -> str:
    """Classify a strategy module by parsing it, never by importing it.

    A scan must not execute module-level code: a legacy module may do anything at import
    time, including raising or touching the filesystem. The vocabulary matches
    ``warden.protocols.detect_strategy_protocol``; ``invalid`` defers to the import-time
    protocol check that verify/screen already perform fail-closed.
    """
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    except (OSError, SyntaxError, ValueError):
        return "invalid"

    names: set[str] = set()

    def _collect(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(
                        node.value, (ast.Lambda, ast.Name, ast.Attribute)
                    ):
                        names.add(target.id)
            elif isinstance(node, ast.ClassDef):
                _collect(node.body)

    _collect(tree.body)
    if "build_estimator" in names:
        return "causal_model"
    if {"required_symbols", "configure_strategy"} <= names:
        return "causal_rules"
    if "generate_positions" in names:
        return "legacy"
    return "invalid"


@dataclass(frozen=True)
class _PreparedExperiment:
    contract: EvaluationContract
    hypothesis: HypothesisSpec
    intent: Any
    trials_used: int
    strategy: Any
    protocol: str
    effective_params: dict
    params_model_report: Any
    robustness_plan: dict
    fixed_grid: list
    benchmark: Any
    candidate: Any
    dataset: Any
    market: MarketPanel
    code_sha: str


class ExperimentRunner:
    def __init__(
        self,
        registry: ResearchRegistry | None = None,
        repo_root: Path = Path("."),
        *,
        allow_legacy_replay: bool = False,
    ):
        self.repo_root = Path(repo_root)
        self.registry = registry or ResearchRegistry(self.repo_root / "registry" / "registry.db")
        self.allow_legacy_replay = bool(allow_legacy_replay)
        self._stage0_vol_cache: dict[tuple[str, tuple[str, ...], int], float] = {}

    def resolve_contract(self, contract_id: str) -> EvaluationContract:
        """**Recorded** lookup: the same bytes a past trial was recorded against.

        Live first, then the read-only archive (#114), so ``status``/``spec-status``
        readback and any replay of an archived contract keep resolving.  This is *not*
        authorization for new work: execution paths call :meth:`resolve_active_contract`.

        A compiled sandbox contract (#117) resolves too — registration, spec-status and the
        advance/audit paths read the same document the trial was recorded against, and
        ``qualify`` is the one gate that refuses the namespace.  A sandbox document is
        sealed to its artifact here (see :mod:`warden.sandbox_seal`), so an edited YAML is
        refused instead of executed.
        """
        contract = load_recorded_contract(self.repo_root, contract_id, allow_sandbox=True)
        self._seal_sandbox_contract(contract)
        return contract

    def resolve_active_contract(self, contract_id: str) -> EvaluationContract:
        """**Active** lookup: refuses an archived contract, naming its archive location.

        A compiled sandbox contract (#117) resolves here on purpose: it may run the ordinary
        pipeline (verify/screen/backtest/advance).  It is not *reusable* (the resolver reads
        only live contracts) and not *qualifiable* (``qualify`` refuses the namespace), so
        resolving it cannot promote un-promoted evidence.  Its bytes must still be the ones
        it was compiled with: a sandbox contract is sealed to its artifact before it runs.
        """
        contract = load_active_contract(self.repo_root, contract_id, allow_sandbox=True)
        self._seal_sandbox_contract(contract)
        return contract

    def _seal_sandbox_contract(self, contract: EvaluationContract) -> None:
        """Refuse a sandbox contract whose document no longer matches its artifact (#117)."""
        from warden.sandbox_seal import verify_sandbox_contract

        verify_sandbox_contract(
            self.repo_root,
            contract,
            registered_spec_hash=self._registered_spec_hash(contract.contract_id),
        )

    def _registered_spec_hash(self, contract_id: str) -> str | None:
        """The contract digest this registry recorded, or ``None`` before first registration."""
        try:
            with self.registry._connect() as conn:
                row = conn.execute(
                    "SELECT spec_hash FROM contracts WHERE contract_id=?", (str(contract_id),)
                ).fetchone()
        except (OSError, sqlite3.Error):  # a projection with no contracts table yet
            return None
        return str(row[0]) if row and row[0] else None

    def _prepare_and_check_stage0(
        self, contract, hyp, hyp_id, family, strat_module, params, trials_used,
        simulation_epoch: str = ENGINE_SIMULATION_EPOCH,
        dataset=None,
    ):
        try:
            dataset = dataset or resolve_dataset(contract, self.repo_root)  # missing data is never substituted
            market = prepare_market_panel(self.repo_root, dataset, params, contract)
            panel_df, ts, assets, v_slice = (
                market.panel, list(market.ts), list(market.assets), market.eval_slice
            )
            # validate_panel_feed_spec already ran inside the gateway; keeping the direct
            # call would assert the same feed twice and mask which reader produced it.
            require_execution_columns(panel_df, contract)
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
        return dataset, panel_df, ts, assets, v_slice, code_sha, market

    def _enforce_linear_first_gate(
        self,
        *,
        hyp_id: str,
        family: str,
        model_type: str,
        spec_payload: dict[str, Any],
        strat_mod: Any,
        contract: EvaluationContract,
    ) -> None:
        """Refuse a nonlinear/deep ML entry point until a logged linear baseline clears.

        Shared by ``screen`` and ``backtest`` so the funnel cannot be entered through the
        cheaper command.  Callers run it before reserving a screen slot or a trial, so a
        refusal leaves no selection attempt and no consumed trial behind.
        """
        model_tier, _model_class = _classify_model_tier(str(model_type), spec_payload, strat_mod)
        if family != "cross_sectional_ml" or model_tier == "linear":
            return
        with self.registry._connect() as conn:
            rows = conn.execute(
                "SELECT model_type, strategy_name, strategy_module, breakeven_cost_bps, diagnostics_json "
                "FROM trials WHERE hypothesis_id = ? AND verdict NOT IN ('REJECTED_EARLY', 'FAILED')",
                (hyp_id,),
            ).fetchall()
        linear_rows = [
            (breakeven_bps,)
            for row_mt, row_name, row_mod, breakeven_bps, row_diag in rows
            if _trial_is_linear_baseline(row_mt, row_name, row_mod, row_diag)
        ]
        if not linear_rows:
            raise LinearBaselineRequiredError(
                f"Linear baseline required for '{hyp_id}' before {model_tier} model '{model_type}'."
            )
        if model_tier == "deep":
            cost_bps = contract.total_one_way_cost_decimal() * 1e4
            cleared = any(
                breakeven_bps is not None and float(breakeven_bps) > cost_bps
                for (breakeven_bps,) in linear_rows
            )
            if not cleared:
                raise LinearBaselineRequiredError(
                    f"Linear baseline required for '{hyp_id}' to clear cost-breakeven hurdle "
                    f"(> {cost_bps:.2f} bps) before deep model '{model_type}'."
                )

    def _check_carry_declaration(self, family, strategy, module):
        """Refuse a missing declaration without executing a validation-series hook."""
        if (family == "carry_rv" and not self.allow_legacy_replay
                and carry_outputs_callable(strategy) is None):
            raise CarryMechanismRequiredError(
                f"carry_rv '{module}' declares no causal_carry_outputs: "
                "Stage 1 must score the strategy's own prefix-verified spread and net carry."
            )

    def approve_research_intent(self, hypothesis_id, plan, principal):
        with self.registry._connect() as conn:
            row = conn.execute("SELECT contract_id FROM hypotheses WHERE hypothesis_id=?",
                               (hypothesis_id,)).fetchone()
        if not row:
            raise ValueError("Register the hypothesis first")
        contract = self.resolve_active_contract(row[0])
        return self.registry.approve_research_intent(
            hypothesis_id, plan, principal, contract=contract, repo_root=self.repo_root)

    def _require_intent(self, spec, contract):
        from warden.research_intent import benchmark_binding, require_resolution

        closure = self.registry.terminal_status(spec.hypothesis_id)
        if closure:
            raise ValueError(f"Terminal hypothesis {closure['status']}: {closure['reason']}")
        document = self.registry.require_research_intent(
            spec.hypothesis_id, spec.research_intent_sha256, contract.contract_id)
        require_resolution(self.repo_root, contract, document)
        if benchmark_binding(self.repo_root, document["plan"]["benchmark_spec"], contract.contract_id) != document["benchmark"]:
            raise ValueError("Frozen research intent benchmark fingerprint changed")
        return document

    def _freeze_review_candidate(self, spec, contract, effective_params, *, dataset=None):
        dataset = dataset or resolve_dataset(contract, self.repo_root)
        code_sha, _ = code_closure(self.repo_root, normalise_module(spec.strategy_module))
        candidate = freeze_candidate(
            self.repo_root, spec, contract, dataset, effective_params, code_sha,
            ENGINE_SIMULATION_EPOCH, legacy_replay=self.allow_legacy_replay)
        return candidate, dataset

    def _reviewed_candidate_for_run(self, spec, contract, effective_params, robustness_plan, intent):
        from warden.research_intent import require_dataset_resolution

        try:
            dataset = resolve_dataset(contract, self.repo_root)
        except (ValueError, OSError) as exc:
            self._note_failure(contract, spec.hypothesis_id, spec.strategy_family,
                               spec.strategy_module, effective_params, None, "DATA", exc)
            raise
        require_dataset_resolution(dataset, intent)
        candidate, _ = self._freeze_review_candidate(spec, contract, effective_params, dataset=dataset)
        self.registry.require_candidate_conformance(candidate, robustness_plan, spec.research_intent_sha256)
        return candidate, dataset

    def describe_review_candidate(self, path):
        """Resolve exact executable identity, without preparing panels or scoring."""
        spec = load_experiment_spec(path)
        if not spec.hypothesis_id:
            raise ValueError("Research intent requires hypothesis_id")
        contract = self.resolve_active_contract(spec.contract_id)
        intent = self._require_intent(spec, contract)
        with self.registry._connect() as conn:
            status = conn.execute("SELECT status FROM hypotheses WHERE hypothesis_id=?",
                                  (spec.hypothesis_id,)).fetchone()[0]
        mod, protocol = load_and_enforce_strategy_protocol(
            self.repo_root / normalise_module(spec.strategy_module),
            hypothesis_status=status, strategy_family=spec.strategy_family,
            allow_legacy_replay=self.allow_legacy_replay)
        validate_strategy_params(mod, spec.parameters)
        effective = resolve_effective_params(mod, protocol, spec.parameters)
        plan, _ = build_robustness_plan(mod, protocol, spec.parameters, spec.param_grid)
        plan = incremental.extend_plan(plan, intent)
        incremental.prepare_benchmark(self.repo_root, contract, intent, effective)
        candidate, dataset = self._freeze_review_candidate(spec, contract, effective)
        from warden.research_intent import require_dataset_resolution

        require_dataset_resolution(dataset, intent)
        return spec, candidate, plan

    def describe_candidate_intent(self, path):
        spec, candidate, plan = self.describe_review_candidate(path)
        return self.registry.describe_candidate_intent(candidate, plan, spec.research_intent_sha256,
                                                       repo_root=self.repo_root)

    def record_conformance_review(self, path, review, principal):
        from warden.research_intent import Review

        spec, candidate, plan = self.describe_review_candidate(path)
        return self.registry.record_conformance_review(
            candidate, plan, spec.research_intent_sha256, Review.model_validate(review), principal,
            repo_root=self.repo_root)

    def verify_spec(self, path: Path) -> dict:
        """Prove causal features and record their exact spec/code/data binding."""
        from warden.codeid import code_closure
        from warden.contracts import canonical_json
        from warden.evaluation import EvaluationKernel
        from warden.leakage import (
            compute_and_cache_features,
            verify_model_prefix_invariance,
            verify_prefix_invariance,
            verify_rules_strategy_asset_independence,
            verify_rules_strategy_prefix_invariance,
        )
        from warden.market_data import ROLE_EXPLORATORY, MarketDataGateway
        from warden.params_model import validate_strategy_params
        from warden.protocols import (
            RulesProofPipeline,
            enforce_active_strategy_protocol,
            resolve_effective_params,
            resolve_protocol_target,
        )
        from warden.provenance import resolve_dataset
        from warden.registry import normalise_module
        from warden.schemas import load_experiment_spec
        from warden.strategy_loader import load_module_from_path
        from warden_evidence.feature_cache import verified_feature_evidence
        spec = load_experiment_spec(path)
        contract = self.resolve_contract(spec.contract_id)
        params = spec.parameters
        strat_module = normalise_module(spec.strategy_module)
        mod = load_module_from_path(self.repo_root / strat_module)
        hyp_status = "ACTIVE"
        hyp_id = spec.hypothesis_id
        if hyp_id and self.registry.db_path.is_file():
            with self.registry._connect() as conn:
                row = conn.execute(
                    "SELECT status FROM hypotheses WHERE hypothesis_id=?",
                    (hyp_id,),
                ).fetchone()
                if row:
                    hyp_status = str(row[0])
        family = spec.strategy_family if "strategy_family" in spec.model_fields_set else None
        protocol = enforce_active_strategy_protocol(
            mod,
            hypothesis_status=hyp_status,
            strategy_family=family,
            allow_legacy_replay=self.allow_legacy_replay,
        )
        # Bind every proof/cache row to one executable closure before any artifact write.
        closure_sha = code_closure(self.repo_root, strat_module)[0]
        # Reject invalid declarations before reading market data or writing feature evidence.
        validate_strategy_params(mod, params)
        dataset = resolve_dataset(contract, self.repo_root)  # exact contract path, never a substitute
        effective_params = resolve_effective_params(mod, protocol, params)
        # The gateway applies the exploratory fence and retains the full contract clock.
        market = MarketDataGateway(self.repo_root).load(
            contract, ROLE_EXPLORATORY, effective_params.get("target_symbols"), resolution=dataset
        )
        panel = market.panel

        cache_dir = self.repo_root / "cache" / "features"
        if protocol == "causal_model":
            verify_model_prefix_invariance(mod, panel, contract, params)
            pipeline = resolve_protocol_target(mod).pipeline
        elif protocol == "causal_rules":
            # One kernel session serves both proofs and the feature-cache tape: the
            # full-panel execution is reused instead of repeated for every cut point.
            kernel = EvaluationKernel()
            prefix_proof = verify_rules_strategy_prefix_invariance(
                mod, panel, contract, params, policy_cfg=spec.model_dump(), kernel=kernel,
                code_sha256=closure_sha,
            )
            verify_rules_strategy_asset_independence(
                mod, panel, contract, params, policy_cfg=spec.model_dump(), kernel=kernel
            )
            pipeline = RulesProofPipeline(
                repo_root=str(self.repo_root.resolve()),
                strategy_module=strat_module,
                code_sha256=closure_sha,
                params=dict(params),
                policy_cfg=spec.model_dump(),
                kernel=kernel,
                prefix_proof=prefix_proof,
            )
        else:
            pipeline = getattr(mod, "pipeline", None) or (mod if hasattr(mod, "compute") else None)
            if not pipeline:
                raise ValueError("Strategy module does not expose a pipeline or compute() method.")
            verify_prefix_invariance(pipeline, panel, contract)

        compute_and_cache_features(pipeline, panel, contract, cache_dir=cache_dir, registry=self.registry,
                                   code_closure_digest=closure_sha)
        proof = verified_feature_evidence(pipeline, panel, contract, self.registry, cache_dir,
                                          code_closure_digest=closure_sha)
        if not proof["verified"]:
            raise ValueError("Causal cache artifact could not be independently reconciled")
        if not hyp_id:
            raise ValueError("spec declares no hypothesis_id; causal evidence cannot be recorded")
        evidence = self.registry.record_spec_causality(
            hypothesis_id=hyp_id, contract_id=contract.contract_id,
            strategy_module=strat_module,
            config_hash=hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12],
            data_sha256=dataset.sha256,
            module_sha256=closure_sha,
            feature_identity=proof["feature_identity"], artifact_sha256=proof["artifact_sha256"],
            proof_sha256=hashlib.sha256((cache_dir / f"{proof['feature_identity']}.proof.json").read_bytes()).hexdigest())
        res = {"status": "PASS", "prefix_invariance": True, "cached": True, "rows": len(panel),
               "data_provenance": dataset.provenance, "market_eligible": dataset.eligible_market,
               "provenance_reason": dataset.reason, "causal_evidence_key": evidence}
        return res

    def audit_run(self, run_id: str) -> dict:
        """Run and persist the six checks against the original trial evidence."""
        from warden.gates import (
            AUDIT_DSR_FLOOR,
            SR2_FLOOR,
            SRD_FLOOR,
            TRIMMED_SHARPE_FLOOR,
            compute_trimmed_sharpe,
            evaluate_cadence_alignment,
            evaluate_fold_isolation_evidence,
        )
        with self.registry._connect() as conn:
            t = conn.execute("SELECT * FROM trials WHERE run_id = ?", (run_id,)).fetchone()
            if not t:
                raise ValueError(f"Run ID '{run_id}' not found.")
            trial = dict(t)
        has_causal_proof = self.registry.causal_proof_for_run(run_id, self.repo_root)["verified"]
        contract = self.resolve_contract(trial["contract_id"])
        eligibility = self.registry.effective_eligibility(run_id)
        diag_raw = trial.get("diagnostics_json")
        try:
            diag = json.loads(diag_raw) if isinstance(diag_raw, str) and diag_raw.strip() else {}
        except json.JSONDecodeError:
            diag = {}
        if not isinstance(diag, dict):
            diag = {}
        # Check 1 and Check 4 read what the run recorded about itself (executed tape cadence,
        # folds that actually ran), not the contract's static integers.  A trial with no such
        # record cannot pass an ML fold check and is reported as evidence-absent for cadence.
        cadence = evaluate_cadence_alignment(
            diag.get("cadence_evidence"),
            label_horizon_bars=contract.label_horizon_bars,
            execution_protocol=diag.get("execution_protocol"),
        )
        fold_evidence = evaluate_fold_isolation_evidence(
            family=trial.get("strategy_family"),
            protocol=diag.get("execution_protocol"),
            evidence=diag.get("fold_isolation"),
            contract=contract,
        )
        r_path = self.repo_root / (trial.get("returns_parquet_path") or "")
        trimmed_sharpe: float | None = None
        if r_path.is_file():
            # A return artifact is Warden's own simulation output, not market data: it is read
            # through the narrowed return-artifact reader (registry/returns only), never through
            # the market gateway and never with a bare pl.read_parquet.
            from warden_evidence.return_artifacts import read_return_artifact

            r_series = read_return_artifact(
                r_path, self.registry.db_path.parent / "returns"
            ).to_numpy()
            trimmed_sharpe = compute_trimmed_sharpe(r_series, contract.bars_per_year)
        trimmed_ok = (trimmed_sharpe is not None) and (trimmed_sharpe > TRIMMED_SHARPE_FLOOR)

        c = {
            "1_target_rebalance_alignment": trial.get("stage_reached") in {"STAGE_2_BACKTEST", "STAGE_3_STRESS"} and bool(cadence["passed"]),
            "2_outlier_sensitivity": trimmed_ok and trial.get("net_sharpe", TRIMMED_SHARPE_FLOOR) > TRIMMED_SHARPE_FLOOR and trial.get("verdict") == "CANDIDATE_FOR_QUALIFICATION",
            "3_point_in_time_causality": has_causal_proof and contract.execution_delay_bars >= 1,
            "4_fold_local_fitting": bool(fold_evidence["passed"]),
            "5_cost_and_latency_survival": (trial.get("stress_2x_cost_sharpe", -1.0) > SR2_FLOOR) and (trial.get("delay_plus1_sharpe", -1.0) > SRD_FLOOR),
            "6_selection_bias_accounting": (trial.get("total_trials_n", 0) >= 1) and (trial.get("deflated_sharpe_prob", 0.0) >= AUDIT_DSR_FLOOR),
        }
        historical_passed = all(c.values())
        approved = historical_passed and eligibility["eligible"]
        verdict = "APPROVED" if approved else ("REVOKED_INELIGIBLE" if historical_passed else "REJECTED")
        self.registry.record_audit(run_id, trial["contract_id"], verdict, c, eligibility["reason"])
        checks = {k: {"passed": v} for k, v in c.items()}
        checks["2_outlier_sensitivity"]["trimmed_sharpe"] = trimmed_sharpe
        checks["1_target_rebalance_alignment"].update(cadence)
        checks["4_fold_local_fitting"].update(fold_evidence)

        simulation_epoch = trial.get("simulation_epoch")
        portfolio_analysis = diag.get("portfolio_analysis")
        trade_analysis = diag.get("trade_analysis")
        pbo_diag = diag.get("pbo")
        ras_diag = diag.get("ras")
        engine_artifacts = diag.get("engine_artifacts")

        audit = {
            "run_id": run_id,
            "simulation_epoch": simulation_epoch,
            "checks": checks,
            "trimmed_sharpe": trimmed_sharpe,
            "portfolio_analysis": portfolio_analysis,
            "trade_analysis": trade_analysis,
            "pbo": pbo_diag,
            "ras": ras_diag,
            "engine_artifacts": engine_artifacts,
            "audit_passed": approved,
            "historical_checks_passed": historical_passed,
            "verdict": verdict,
            "effective_eligibility": eligibility,
        }
        out = self.repo_root / ".workspace" / "hypotheses" / f"AUDIT_{run_id}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        trimmed_line = f"{trimmed_sharpe:.4f}" if trimmed_sharpe is not None else "N/A"
        extra_lines: list[str] = []
        if simulation_epoch:
            extra_lines.append(f"Simulation epoch: {simulation_epoch}")
        if isinstance(portfolio_analysis, dict):
            pa_sr = portfolio_analysis.get("sharpe_ratio")
            pa_ret = portfolio_analysis.get("total_return")
            extra_lines.append(f"Portfolio analysis: Sharpe={pa_sr}, TotalReturn={pa_ret}")
        if isinstance(trade_analysis, dict):
            extra_lines.append(
                f"Trade analysis: trades={trade_analysis.get('n_trades')}, "
                f"win_rate={trade_analysis.get('win_rate')}, pf={trade_analysis.get('profit_factor')}"
            )
        if isinstance(pbo_diag, dict):
            extra_lines.append(f"PBO: {pbo_diag.get('pbo')} (blocks={pbo_diag.get('n_blocks')})")
        if isinstance(ras_diag, dict):
            extra_lines.append(f"RAS: complexity={ras_diag.get('rademacher_complexity')}")
        if isinstance(engine_artifacts, dict) and engine_artifacts:
            extra_lines.append(f"Engine artifacts: {', '.join(sorted(engine_artifacts))}")
        extra_lines.append(f"Cadence check: {cadence.get('reason', '')}")
        if cadence.get("evidence") == "recorded":
            extra_lines.append(
                "Cadence observations: "
                f"decision={cadence.get('observed_rebalance_cadence_bars')} bars vs label horizon "
                f"{cadence.get('label_horizon_bars')}; "
                f"holding={cadence.get('observed_holding_completed_cadence_bars')} bars "
                f"(completed epochs={cadence.get('holding_completed_epochs')}) vs declared holding "
                f"horizon {cadence.get('holding_horizon_bars')}; "
                f"basis={cadence.get('holding_cadence_basis')}"
            )
        extra_lines.append(
            f"Fold isolation: {fold_evidence.get('reason') or fold_evidence.get('basis', '')}"
        )
        extra_block = ("\n".join(extra_lines) + "\n") if extra_lines else ""
        out.write_text(
            f"# 6-Check Audit: {run_id}\nVerdict: {verdict}\n"
            f"Effective eligibility: {eligibility['eligible']} ({eligibility['reason']})\n"
            f"Trimmed Sharpe (ex top-5%): {trimmed_line}\n"
            + extra_block
            + "\n".join(f"- [{ 'PASS' if v else 'FAIL' }] {k}" for k, v in c.items()) + "\n"
        )
        return audit

    def advance_spec(self, spec_path: Path, *, hypothesis_path: str = "") -> dict[str, Any]:
        """Advance one immutable spec, taking a writer lease only once real work is owed.

        The status gates run against a lock-free projection; only a spec that still has work
        (registration, verify/screen, backtest, audit) acquires ``mutation_lease``, and it is
        released before the next spec is considered.
        """
        import yaml

        from warden.lease import mutation_lease
        from warden.sandbox_seal import SandboxArtifactError
        from warden_ops.integrity import check_worktree

        from warden.contracts import ContractValidationError, load_hypothesis
        from warden.registry import PER_HYPOTHESIS_CAP, RUNNABLE_HYPOTHESIS_STATUSES, normalise_module

        repo = Path(self.repo_root)
        try:
            spec_rel = spec_path.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            spec_rel = str(spec_path)

        def _result(action: str, status_info: dict[str, Any], reason: str, **extra: Any) -> dict[str, Any]:
            return {
                "spec": spec_rel,
                "hypothesis_id": cfg.get("hypothesis_id", ""),
                "contract_id": cfg.get("contract_id", ""),
                "state": status_info.get("state", "UNKNOWN"),
                "action": action,
                "run_id": status_info.get("run_id"),
                "verdict": status_info.get("verdict"),
                "audit_verdict": status_info.get("audit_verdict"),
                "reason": reason,
                **extra,
            }

        try:
            cfg = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            cfg = {}
            return _result("REVIEW_REQUIRED", {}, f"Invalid spec YAML: {exc}")
        if not isinstance(cfg, dict) or not cfg.get("hypothesis_id") or not cfg.get("contract_id"):
            cfg = cfg if isinstance(cfg, dict) else {}
            return _result("REVIEW_REQUIRED", {}, "Spec missing hypothesis_id or contract_id")

        hyp_id = str(cfg["hypothesis_id"])
        cid = str(cfg["contract_id"])
        module = normalise_module(cfg.get("strategy_module", ""))
        params = cfg.get("parameters", {}) or {}

        from warden.contract_source import is_sandbox_contract

        if is_sandbox_contract(repo, cid):
            # A compiled sandbox contract is sealed to its artifact before any gate runs, so a
            # tampered YAML is a refused request (exit 2) rather than a REVIEW_REQUIRED item
            # discovered halfway through verify.  Non-sandbox ids keep their existing handling.
            self.resolve_contract(cid)

        def refresh() -> dict[str, Any]:
            return self.registry.spec_status(hyp_id, cid, module, params)

        def _terminal_or_review(state: dict[str, Any]) -> dict[str, Any] | None:
            if state["state"] in {"STAGE1_REJECT", "STAGE3_FINAL", "HYPOTHESIS_TERMINAL"}:
                return _result("SKIP_TERMINAL", state, state["reason"])
            if state["state"] == "REQUIRES_RECONCILIATION":
                return _result("REVIEW_REQUIRED", state, state["reason"])
            return None

        def _budget_skip(state: dict[str, Any]) -> dict[str, Any] | None:
            # Only a registered hypothesis has a runnable status/budget to judge here.
            if not state["hypothesis_registered"]:
                return None
            if state["state"] in {"NEW", "STAGE1_PASS"} and (
                state["hypothesis_status"] not in RUNNABLE_HYPOTHESIS_STATUSES
                or int(state.get("hypothesis_trials_used") or 0) >= PER_HYPOTHESIS_CAP
            ):
                return _result(
                    "SKIP_TERMINAL",
                    state,
                    f"Hypothesis {hyp_id} non-runnable or budget exhausted "
                    f"(status={state['hypothesis_status']}, trials_used={state.get('hypothesis_trials_used')})",
                )
            ledger = self.registry
            if state["state"] == "NEW" and getattr(ledger, "schema_version", 0) >= 4:
                try:
                    budget = ledger.candidate_budget(hyp_id)
                except (ValueError, sqlite3.Error) as exc:
                    return _result("REVIEW_REQUIRED", state, f"Candidate budget unavailable: {exc}")
                if (budget["used"] >= budget["hypothesis_cap"]
                        or budget["lineage_used"] >= budget["lineage_cap"]):
                    return _result("SKIP_TERMINAL", state,
                                   f"Candidate budget exhausted: {budget['used']}/{budget['hypothesis_cap']}, "
                                   f"lineage {budget['lineage_used']}/{budget['lineage_cap']}")
            return None

        def _legacy_frozen(state: dict[str, Any]) -> dict[str, Any] | None:
            # An unregistered hypothesis is registered first; its runnable status is unknown here.
            if not state["hypothesis_registered"]:
                return None
            if state["state"] != "NEW" or state["hypothesis_status"] != "ACTIVE":
                return None
            if getattr(self, "allow_legacy_replay", False):
                return None
            mod_path = repo / module if module else None
            if not (mod_path and mod_path.is_file()):
                return None
            if _protocol_kind_from_source(mod_path) != "legacy":
                return None
            return _result(
                "BLOCKED_LEGACY_PROTOCOL",
                state,
                "Legacy generate_positions() is frozen for historical replay only; migrate to "
                "CausalModelStrategy or CausalRulesStrategy",
            )

        def _outcome_memory(state: dict[str, Any]) -> dict[str, Any] | None:
            if not state["hypothesis_registered"]:
                return None
            if state["state"] != "NEW":
                return None
            # Governed admission owns the stronger prospective novelty check. The
            # old sidecar gate remains a precheck for unbound historical specs.
            if cfg.get("research_intent_sha256"):
                return None
            index_path = repo / "docs" / "research-outcomes" / "branch-result-index-v1.json"
            if not index_path.is_file():
                return _result(
                    "REVIEW_REQUIRED",
                    state,
                    "Research outcome memory index is missing: "
                    "docs/research-outcomes/branch-result-index-v1.json; review before screening",
                )
            try:
                from warden_ops.research_outcome_memory import check_spec as check_outcome_spec

                mem = check_outcome_spec(spec_path, self.registry.db_path, index_path)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                return _result(
                    "REVIEW_REQUIRED",
                    state,
                    f"Research outcome memory index is unreadable or malformed: {type(exc).__name__}: {exc}",
                )
            if not mem.get("eligible"):
                return _result(
                    "BLOCKED_OUTCOME_MEMORY",
                    state,
                    str(mem.get("reason", "Blocked by research_outcome_memory")),
                    outcome_memory=mem,
                )
            return None

        def _read_only_gate(state: dict[str, Any]) -> dict[str, Any] | None:
            for gate in (_terminal_or_review, _budget_skip, _legacy_frozen, _outcome_memory):
                outcome = gate(state)
                if outcome is not None:
                    return outcome
            return None

        def _attempt(operation, subject):
            if not check_worktree(repo):
                return 2, "worktree integrity failed"
            try:
                return 0, operation(subject)
            except SandboxArtifactError as exc:
                return 2, str(exc)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError,
                    sqlite3.Error, pl.exceptions.PolarsError) as exc:
                return 1, f"{type(exc).__name__}: {exc}"

        def _advance(state: dict[str, Any]) -> dict[str, Any]:
            if not state["hypothesis_registered"]:
                hyp_file = _resolve_hypothesis_file(repo, hyp_id, hypothesis_path)
                if hyp_file is None:
                    return _result(
                        "BLOCKED_UNREGISTERED",
                        state,
                        f"Hypothesis '{hyp_id}' is not registered and no file was found in .workspace/hypotheses/",
                    )
                try:
                    hyp = load_hypothesis(hyp_file)
                    if hyp.hypothesis_id != hyp_id or hyp.contract_id != cid:
                        return _result(
                            "REVIEW_REQUIRED",
                            state,
                            f"Hypothesis file {hyp_file.name} ({hyp.hypothesis_id}/{hyp.contract_id}) "
                            f"does not match spec ({hyp_id}/{cid})",
                        )
                    contract = self.resolve_contract(hyp.contract_id)
                    # Same lifecycle gate as ``register-hypothesis``: ``advance`` must not be the
                    # cheap way to auto-register onto a contract the lifecycle document closes.
                    from warden.lifecycle import ContractLifecycleBlocked, load_lifecycle_registry

                    try:
                        load_lifecycle_registry(repo).assert_open(contract.contract_id)
                    except ContractLifecycleBlocked as exc:
                        return _result("REVIEW_REQUIRED", state, f"Lifecycle gate: {exc}")
                    self.registry.register_contract(
                        contract, repo_root=self.repo_root, policy_optional=True
                    )
                    self.registry.register_hypothesis(hyp)
                except (ContractValidationError, FileNotFoundError, ValueError) as exc:
                    return _result("REVIEW_REQUIRED", state, f"Hypothesis registration failed: {exc}")
                state = refresh()

            blocked = _read_only_gate(state)
            if blocked is not None:
                return blocked

            if state["state"] == "NEW":
                v_rc, v_detail = _attempt(self.verify_spec, spec_path)
                if v_rc != 0:
                    return _result("REVIEW_REQUIRED", state, f"verify failed (exit={v_rc}): {v_detail}")

                s_rc, s_detail = _attempt(self.run_stage1_screen, spec_path)
                if s_rc == 0 and not s_detail.passed:
                    s_rc = 1
                state = refresh()
                if state["state"] == "HYPOTHESIS_TERMINAL":
                    return _result("STAGE1_REJECTED", state, state["reason"])
                if state["state"] == "STAGE1_REJECT":
                    return _result("STAGE1_REJECTED", state, f"Stage 1 rejected (exit={s_rc})")
                if state["state"] != "STAGE1_PASS":
                    return _result(
                        "REVIEW_REQUIRED",
                        state,
                        f"Unexpected post-screen state {state['state']} (exit={s_rc}): {s_detail}",
                    )

            if state["state"] == "STAGE1_PASS":
                b_rc, b_detail = _attempt(self.run_spec, spec_path)
                state = refresh()
                if state["state"] == "HYPOTHESIS_TERMINAL":
                    return _result("BACKTEST_FINALIZED", state, state["reason"])
                if state["state"] not in {"STAGE3_FINAL", "STAGE3_CANDIDATE_UNAUDITED"}:
                    return _result(
                        "REVIEW_REQUIRED",
                        state,
                        f"Backtest did not reach finalized Stage 3 state ({state['state']}, exit={b_rc}): {b_detail}",
                    )
                if state["state"] == "STAGE3_FINAL":
                    return _result("BACKTEST_FINALIZED", state, f"Finalized {state.get('run_id')} ({state.get('verdict')})")

            if state["state"] == "STAGE3_CANDIDATE_UNAUDITED":
                run_id = state.get("run_id")
                if not run_id:
                    return _result("REVIEW_REQUIRED", state, "Missing run_id for unaudited candidate")
                a_rc, a_detail = _attempt(self.audit_run, run_id)
                if a_rc == 0:
                    a_rc = 0 if a_detail["audit_passed"] else 1
                    a_detail = a_detail["verdict"]
                state = refresh()
                if state.get("audit_verdict") is None:
                    return _result(
                        "REVIEW_REQUIRED",
                        state,
                        f"audit-check failed without recording audit_evidence for {run_id} (exit={a_rc})"
                        + (f": {a_detail}" if a_detail else ""),
                    )
                return _result(
                    "AUDITED",
                    state,
                    f"Audited {run_id}: {state.get('audit_verdict')} (exit={a_rc})" + (f" ({a_detail})" if a_detail and a_rc != 0 else ""),
                )

            return _result("REVIEW_REQUIRED", state, f"Unhandled spec state: {state['state']}")

        status = refresh()
        early = _read_only_gate(status)
        if early is not None:
            return early
        with mutation_lease(self.registry.db_path):
            # Recheck the installed runtime after lease acquisition, not merely the plan.
            from warden_ops.runtime_boundary import require_mutation_context

            try:
                require_mutation_context(Path(self.repo_root),
                                         self.registry.db_path)
            except (OSError, ValueError) as exc:
                return _result("REVIEW_REQUIRED", status, str(exc))
            # Double-check under the lease: another writer may have advanced this spec between
            # the lock-free read and the acquisition. Never act on a stale read.
            fresh = refresh()
            if (fresh["state"], bool(fresh["hypothesis_registered"])) != (
                status["state"], bool(status["hypothesis_registered"])
            ):
                return _result(
                    "REVIEW_REQUIRED",
                    fresh,
                    f"spec state changed between the read-only scan and the registry lease "
                    f"({status['state']} -> {fresh['state']}); re-run advance",
                )
            return _advance(fresh)

    def _prepare_experiment(self, spec) -> _PreparedExperiment:
        """Resolve the frozen subject, approvals and data before any operation reservation."""
        contract = self.resolve_active_contract(spec.contract_id)
        hyp, trials_used, status = self.registry.execution_hypothesis(
            spec.hypothesis_id, contract.contract_id, spec.strategy_family)
        intent = self._require_intent(spec, contract)
        module = normalise_module(spec.strategy_module)
        strategy, protocol = load_and_enforce_strategy_protocol(
            self.repo_root / module, hypothesis_status=status,
            strategy_family=spec.strategy_family, allow_legacy_replay=self.allow_legacy_replay)
        params_report = validate_strategy_params(strategy, spec.parameters)
        self._enforce_linear_first_gate(
            hyp_id=spec.hypothesis_id, family=spec.strategy_family,
            model_type=str(spec.model_type or ""), spec_payload=spec.model_dump(),
            strat_mod=strategy, contract=contract)
        effective = resolve_effective_params(strategy, protocol, spec.parameters)
        plan, grid = build_robustness_plan(strategy, protocol, spec.parameters, spec.param_grid)
        plan = incremental.extend_plan(plan, intent)
        benchmark = incremental.prepare_benchmark(self.repo_root, contract, intent, effective)
        candidate, approved_dataset = self._reviewed_candidate_for_run(
            spec, contract, effective, plan, intent)
        dataset, _, _, _, _, code_sha, market = self._prepare_and_check_stage0(
            contract, hyp, spec.hypothesis_id, spec.strategy_family, module,
            effective, trials_used, dataset=approved_dataset)
        return _PreparedExperiment(
            contract, hyp, intent, trials_used, strategy, protocol, effective, params_report,
            plan, grid, benchmark, candidate, dataset, market, code_sha)

    def run_stage1_screen(self, exp_yaml_path: Path) -> Stage1Report:
        spec = load_experiment_spec(exp_yaml_path)
        if not spec.hypothesis_id:
            raise ValueError("Experiment spec must declare hypothesis_id for screening")
        prepared = self._prepare_experiment(spec)
        contract, hyp = prepared.contract, prepared.hypothesis
        dataset, market = prepared.dataset, prepared.market
        strategy, protocol = prepared.strategy, prepared.protocol
        module = normalise_module(spec.strategy_module)
        family, params = spec.strategy_family, spec.parameters
        panel, ts, assets, window = market.panel, list(market.ts), list(market.assets), market.eval_slice
        self._check_carry_declaration(family, strategy, module)
        if (prepared.candidate.strategy_code_sha256 != prepared.code_sha
                or prepared.candidate.dataset_sha256 != dataset.sha256):
            raise ValueError("Reviewed candidate changed during preparation")
        screen_key = self.registry.reserve_screen(
            spec.hypothesis_id, contract.contract_id, module, params, dataset.sha256,
            candidate=prepared.candidate, robustness_plan=prepared.robustness_plan,
            intent_sha=spec.research_intent_sha256, repo_root=self.repo_root)

        returns, timestamps = None, None
        parent = self.registry.parent_validation_stream(
            hyp.parent_hypothesis_id, contract.contract_id,
            simulation_epoch=ENGINE_SIMULATION_EPOCH,
        ) if family == "meta_overlay" else None
        if family == "carry_rv":
            net_carry, spread, evidence = _carry_rv_stage1_series(
                strategy, panel, contract, ts, assets, window, prepared.effective_params,
                hyp.expected_holding_bars,
                mechanism_policy=carry_mechanism_policy(allow_legacy_replay=self.allow_legacy_replay),
                strategy_module=module)
            report = evaluate_stage1_carry_rv(net_carry, spread)
            report.metrics.update(evidence)
        elif family == "meta_overlay" and parent is None:
            report = Stage1Report(
                "meta_overlay", False, {"parent_stream_found": 0.0},
                f"Missing recorded validation stream for parent '{hyp.parent_hypothesis_id}'")
        else:
            if protocol == "causal_rules" and family not in {"rules_ts", "meta_overlay"}:
                raise ValueError(f"Unsupported strategy_family '{family}' for Stage 1 screen")
            simulation = EvaluationKernel().run(
                strategy, panel, ts, assets, contract, window, params, protocol=protocol,
                policy=extract_execution_policy(contract=contract),
            ) if protocol in {"causal_rules", "causal_model"} or family == "meta_overlay" else None
            output = (simulation.strategy_output() if simulation is not None
                      else _run_strategy_isolated(self.repo_root / module, panel, contract, params))
            if family == "meta_overlay":
                declared_delay = int(output.get("signal_delay_bars", 0))
                if declared_delay > 0:
                    raise DoubleLatencyError(
                        f"'{spec.strategy_name or Path(exp_yaml_path).stem}' declares "
                        f"signal_delay_bars={declared_delay} while Warden already applies "
                        f"execution_delay_bars={contract.execution_delay_bars}; signals must not be pre-lagged.")
                report = evaluate_stage1_meta_overlay(
                    parent["net_returns"], simulation.net_returns,
                    parent["annualized_turnover"],
                    float(np.mean(simulation.turnover) * market.bars_per_year), market.bars_per_year,
                    parent_run_id=parent["run_id"],
                    parent_data_sha256=parent.get("data_snapshot_sha256", ""),
                    child_data_sha256=dataset.sha256)
                returns, timestamps = simulation.net_returns, ts[window]
            elif family == "cross_sectional_ml":
                report, returns = _evaluate_cs_ml_stage1(output, contract)
            elif family == "rules_ts":
                if simulation is None:
                    quote_returns, funding, funding_mask = simulation_inputs(panel, contract, ts, assets)
                    legacy = simulate_positions(
                        output["positions"], quote_returns["long"], funding, contract, window,
                        cost_multiplier=1.0, funding_mask=funding_mask, quote_returns=quote_returns)
                    net, gross, positions = legacy["net_returns"], legacy["gross_returns"], legacy["positions"]
                else:
                    net, gross, positions = simulation.net_returns, simulation.gross_returns, simulation.positions
                report = evaluate_stage1_rules_ts(
                    net, gross, contract.total_one_way_cost_decimal(), positions,
                    bars_per_year=market.bars_per_year, holding_horizon_bars=hyp.expected_holding_bars)
                returns, timestamps = net, ts[window]
            else:
                raise ValueError(f"Unsupported strategy_family '{family}' for Stage 1 screen")
        self.registry.record_selection_attempt(
            hypothesis_id=spec.hypothesis_id, contract_id=contract.contract_id,
            kind="standalone_screen", stage="STAGE_1_SIGNAL",
            outcome="PASS" if report.passed else "REJECT", strategy_family=family,
            strategy_module=module, parameters=params, data_provenance=dataset.provenance,
            returns=returns, timestamps=timestamps,
            diagnostics={**report.metrics, "notes": report.notes, "dataset": dataset.to_dict()},
            screen_key=screen_key, simulation_epoch=ENGINE_SIMULATION_EPOCH)
        return report

    def run_spec(self, exp_yaml_path: Path, override_run_id: str | None = None) -> ExperimentResult:
        spec = load_experiment_spec(exp_yaml_path)
        if not spec.hypothesis_id:
            raise ValueError("Experiment spec must declare hypothesis_id for backtesting")
        prepared = self._prepare_experiment(spec)
        contract, hyp = prepared.contract, prepared.hypothesis
        dataset, market = prepared.dataset, prepared.market
        hyp_id, family = spec.hypothesis_id, spec.strategy_family
        strat_name = spec.strategy_name or Path(exp_yaml_path).stem
        model_type, params = spec.model_type, spec.parameters
        intent = prepared.intent
        simulation_epoch = ENGINE_SIMULATION_EPOCH
        policy_dict = extract_execution_policy(contract=contract)
        strat_module_str = normalise_module(spec.strategy_module)
        strat_mod, protocol = prepared.strategy, prepared.protocol
        model_tier, model_class = _classify_model_tier(str(model_type), spec.model_dump(), strat_mod)
        effective_params = prepared.effective_params
        params_model_report = prepared.params_model_report
        robustness_plan, fixed_grid, benchmark = (
            prepared.robustness_plan, prepared.fixed_grid, prepared.benchmark)
        neighbor_grid = classify_numeric_neighbors(fixed_grid, params_model_report)
        candidate, code_sha, trials_used = prepared.candidate, prepared.code_sha, prepared.trials_used
        panel_df, ts, assets, v_slice = (
            market.panel, list(market.ts), list(market.assets), market.eval_slice)
        bpy = market.bars_per_year
        # #100: the *selection* clock.  A ``target_symbols`` trial observes only the bars its
        # own symbols present, but its evidence must be published on the contract's validation
        # grid: scoring it on the subset's calendar put the same history on a different index
        # and a different annualisation than every other attempt on this contract, so the DSR
        # basis was not reciprocal.  The panel's own clock is kept for the engine statistics
        # below (they describe the execution that actually ran); only the recorded selection
        # series moves onto the contract grid, with a structural zero on bars where the trial
        # held no position (``project_onto_grid`` refuses a stamp the grid does not contain).
        panel_validation_ts = list(ts[v_slice])
        validation_ts = panel_validation_ts
        contract_clock = False
        if market.symbols_filtered:
            contract_ts = list(
                MarketDataGateway(self.repo_root).contract_validation_grid(
                    contract, resolution=dataset
                )
            )
            if contract_ts != panel_validation_ts:
                validation_ts = contract_ts
                contract_clock = True
        # Ordering: the alignment check is a pure read and runs *before* the slot is reserved,
        # so a history that cannot be placed on this clock costs nothing -- no trial row, no
        # attempt row, no search-floor write.  A refusal is evidence, never a consumed trial.
        preflight = self.registry.preflight_selection_alignment(
            contract, validation_ts, expected_len=len(validation_ts),
            simulation_epoch=simulation_epoch)
        self._check_carry_declaration(family, strat_mod, strat_module_str)
        run_id = override_run_id or spec.run_id or default_run_id(hyp_id, trials_used + 1)
        if candidate.strategy_code_sha256 != code_sha or candidate.dataset_sha256 != dataset.sha256:
            raise ValueError("Reviewed candidate changed during preparation")
        trial_num = self.registry.register_trial_start(
            hyp_id, run_id, candidate=candidate, robustness_plan=robustness_plan,
            intent_sha=spec.research_intent_sha256, repo_root=self.repo_root)
        provenance_kw = {"data_provenance": dataset.provenance, "strategy_code_sha256": code_sha,
                         "simulation_epoch": simulation_epoch}
        data_sha = dataset.sha256
        is_synthetic = int(not dataset.eligible_market)
        try:
            prior_keff, prior_noise, family_key = self.registry.prior_search_floor(contract)
            carry_series: tuple[np.ndarray, np.ndarray, dict[str, Any]] | None = None
            if family == "carry_rv":
                carry_series = _carry_rv_stage1_series(
                    strat_mod,
                    panel_df,
                    contract,
                    ts,
                    assets,
                    v_slice,
                    effective_params,
                    hyp.expected_holding_bars,
                    mechanism_policy=carry_mechanism_policy(
                        allow_legacy_replay=self.allow_legacy_replay
                    ),
                    strategy_module=strat_module_str,
                )
            s1_carry: Stage1Report | None = None
            if family == "carry_rv":
                assert carry_series is not None
                net_carry, spread, carry_evidence = carry_series
                s1_carry = evaluate_stage1_carry_rv(net_carry, spread)
                s1_carry.metrics.update(carry_evidence)
                if not s1_carry.passed:
                    return self._record_early_rejection(
                        run_id=run_id, hyp_id=hyp_id, contract_id=contract.contract_id,
                        strat_name=strat_name, family=family, params=params, prior_keff=prior_keff,
                        is_synthetic=is_synthetic, data_sha=data_sha, strat_module_str=strat_module_str,
                        model_type=str(model_type), trial_num=trial_num, provenance_kw=provenance_kw,
                        simulation_epoch=simulation_epoch, dataset=dataset,
                        gate_key="stage1_carry_rv", notes=s1_carry.notes,
                        gate_payload={"metrics": s1_carry.metrics, "notes": s1_carry.notes},
                    )

            parent_stream: dict[str, Any] | None = None
            if family == "meta_overlay":
                parent_stream = self.registry.parent_validation_stream(
                    hyp.parent_hypothesis_id,
                    contract.contract_id,
                    simulation_epoch=simulation_epoch,
                )
                if parent_stream is None:
                    missing_notes = f"Missing recorded validation stream for parent '{hyp.parent_hypothesis_id}'"
                    return self._record_early_rejection(
                        run_id=run_id, hyp_id=hyp_id, contract_id=contract.contract_id,
                        strat_name=strat_name, family=family, params=params, prior_keff=prior_keff,
                        is_synthetic=is_synthetic, data_sha=data_sha, strat_module_str=strat_module_str,
                        model_type=str(model_type), trial_num=trial_num, provenance_kw=provenance_kw,
                        simulation_epoch=simulation_epoch, dataset=dataset,
                        gate_key="stage1_meta_overlay", notes=missing_notes,
                        gate_payload={"metrics": {"parent_stream_found": 0.0}, "notes": missing_notes},
                    )

            ret_p = execution_returns(panel_df, contract, assets)

            kernel = EvaluationKernel()
            sim1 = kernel.run(
                strat_mod,
                panel_df,
                ts,
                assets,
                contract,
                v_slice,
                params,
                protocol=protocol,
                policy=policy_dict,
            )
            declared_delay = int(sim1.strategy_output().get("signal_delay_bars", 0))
            if declared_delay > 0:
                raise DoubleLatencyError(
                    f"'{strat_name}' declares signal_delay_bars={declared_delay} while Warden already "
                    f"applies execution_delay_bars={contract.execution_delay_bars}; signals must not be pre-lagged.")

            mean_ic, ic_hac_t = None, None
            if family == "cross_sectional_ml":
                s1, ic_series = _evaluate_cs_ml_stage1(sim1.strategy_output(), contract)
                mean_ic, ic_hac_t = s1.metrics.get("mean_ic"), s1.metrics.get("ic_hac_tstat")
                if not s1.passed:
                    return self._record_early_rejection(
                        run_id=run_id, hyp_id=hyp_id, contract_id=contract.contract_id,
                        strat_name=strat_name, family=family, params=params, prior_keff=prior_keff,
                        is_synthetic=is_synthetic, data_sha=data_sha, strat_module_str=strat_module_str,
                        model_type=str(model_type), trial_num=trial_num, provenance_kw=provenance_kw,
                        simulation_epoch=simulation_epoch, dataset=dataset,
                        gate_key="ic_hac", stage_diag_key="stage1_signal", notes=s1.notes,
                        gate_payload={"metrics": s1.metrics, "notes": s1.notes,
                                      "vector": np.asarray(ic_series, dtype=float).tolist()},
                        mean_ic=mean_ic, ic_hac_t=ic_hac_t,
                    )

            net_ret, gross_ret, turnover = sim1.net_returns, sim1.gross_returns, sim1.turnover
            # The series that is recorded, aligned and deflated lives on the selection clock
            # decided above; the Engine's own gate statistics keep the tape they ran on.  A
            # structural zero is written only where the trial held no position, and an observed
            # stamp the contract grid does not carry is refused rather than moved.
            selection_net = net_ret
            if contract_clock:
                selection_net = project_onto_grid(
                    utc_ns(panel_validation_ts), net_ret, utc_ns(validation_ts)
                )
            m = calculate_metrics_from_returns(gross_ret, net_ret, turnover, bpy)
            m.update(compute_trade_metrics(sim1.positions, net_ret, ret_p["long"][v_slice],
                                           bpy, contract.total_one_way_cost_decimal()))

            s1_rules = (
                evaluate_stage1_rules_ts(
                    net_ret,
                    gross_ret,
                    contract.total_one_way_cost_decimal(),
                    sim1.positions,
                    bars_per_year=bpy,
                    holding_horizon_bars=hyp.expected_holding_bars,
                )
                if family == "rules_ts"
                else None
            )
            s1_meta: Stage1Report | None = None
            if family == "meta_overlay":
                assert parent_stream is not None
                s1_meta = evaluate_stage1_meta_overlay(
                    parent_stream["net_returns"],
                    net_ret,
                    parent_stream["annualized_turnover"],
                    m["annualized_turnover"],
                    bpy,
                    parent_run_id=parent_stream["run_id"],
                    parent_data_sha256=parent_stream.get("data_snapshot_sha256", ""),
                    child_data_sha256=data_sha,
                )
                if not s1_meta.passed:
                    return self._record_early_rejection(
                        run_id=run_id, hyp_id=hyp_id, contract_id=contract.contract_id,
                        strat_name=strat_name, family=family, params=params, prior_keff=prior_keff,
                        is_synthetic=is_synthetic, data_sha=data_sha, strat_module_str=strat_module_str,
                        model_type=str(model_type), trial_num=trial_num, provenance_kw=provenance_kw,
                        simulation_epoch=simulation_epoch, dataset=dataset,
                        gate_key="stage1_meta_overlay", notes=s1_meta.notes,
                        gate_payload={
                            "metrics": s1_meta.metrics,
                            "notes": s1_meta.notes,
                            "parent_run_id": parent_stream["run_id"],
                        },
                        metrics=m, returns_vec=selection_net, returns_timestamps=validation_ts,
                    )

            comparison = None
            if benchmark is not None:
                from warden.evaluation import panel_digest
                from warden.research_intent import benchmark_binding

                scenario = self.registry.reserve_robustness(run_id, incremental.SCENARIO)
                benchmark_tape, benchmark_proof = incremental.execute_benchmark(
                    benchmark, panel_df, ts, assets, contract, v_slice)
                if (benchmark_tape.val_slice != sim1.val_slice
                        or benchmark_tape.session != sim1.session):
                    raise ValueError("Candidate and benchmark execution clocks differ")
                if (benchmark_binding(self.repo_root, intent["benchmark"]["spec_path"],
                                      contract.contract_id) != intent["benchmark"]
                        or code_closure(self.repo_root, strat_module_str)[0] != code_sha):
                    raise ValueError("Frozen paired strategy closure changed during execution")
                comparison = self.registry.finalize_benchmark(
                    scenario, contract, panel_digest(panel_df), policy_dict,
                    net_ret, benchmark_tape.net_returns, panel_validation_ts, benchmark_proof)

            scenario = self.registry.reserve_robustness(run_id, "cost_1p5")
            sr_1p5 = analytical_1p5x_sharpe(
                sim1.backtest_result,
                bpy,
                val_ts=ts[v_slice],
            )
            self.registry.finalize_robustness(scenario, {"net_sharpe": sr_1p5})
            scenario = self.registry.reserve_robustness(run_id, "cost_2x")
            sim2 = kernel.reprice(
                sim1, strat_mod, panel_df, ts, assets, contract, v_slice, params,
                policy=policy_dict, cost_multiplier=2.0,
            )
            sr2 = annualised_sharpe(sim2.net_returns, bpy)
            self.registry.finalize_robustness(
                scenario, {"net_sharpe": sr2}, returns=sim2.net_returns, timestamps=ts[v_slice])
            scenario = self.registry.reserve_robustness(run_id, "latency_plus1")
            sim_d = kernel.reprice(
                sim1, strat_mod, panel_df, ts, assets, contract, v_slice, params,
                policy=policy_dict, delay_offset=1,
            )
            srd = annualised_sharpe(sim_d.net_returns, bpy)
            self.registry.finalize_robustness(
                scenario, {"net_sharpe": srd}, returns=sim_d.net_returns, timestamps=ts[v_slice])

            ngrid, n_sr = fixed_grid, []
            grid_returns_list: list[np.ndarray] = [net_ret]
            for index, grid_params in enumerate(ngrid):
                scenario = self.registry.reserve_robustness(run_id, f"neighbor:{index}")
                grid_sim = kernel.run(
                    strat_mod,
                    panel_df,
                    ts,
                    assets,
                    contract,
                    v_slice,
                    grid_params,
                    protocol=protocol,
                    policy=policy_dict,
                    legacy_isolated=True,
                )
                grid_returns_list.append(grid_sim.net_returns)
                n_sr.append(annualised_sharpe(grid_sim.net_returns, bpy))
                # Fixed neighbors are raw operations; never use them to select a winner.
                self.registry.record_selection_attempt(
                    hypothesis_id=hyp_id, contract_id=contract.contract_id, kind="param_grid",
                    stage="STAGE_3_STRESS", outcome="GRID", strategy_family=family,
                    strategy_module=strat_module_str, parameters=grid_params,
                    config_hash=hashlib.sha256(canonical_json(grid_params).encode()).hexdigest()[:12],
                    data_provenance=dataset.provenance, returns=grid_sim.net_returns,
                    diagnostics={"net_sharpe": n_sr[-1]}, simulation_epoch=simulation_epoch,
                    run_id=run_id, scenario_id=scenario, timestamps=ts[v_slice])
            # Only *distinct, genuine, numeric* configurations are neighbor
            # evidence.  Duplicates, entries that restate the effective baseline
            # and non-numeric edits stay executed and recorded (their selection
            # attempts are already above) but can never stand in for a perturbed
            # run -- an empty or degenerate grid is an untested neighbor axis.
            neighbor_requirement = evaluate_neighbor_requirement(
                params_model_report, neighbor_grid
            )
            neighbor_sharpes = [n_sr[i] for i in neighbor_grid.evaluated_indices]
            neighbor_report = evaluate_neighbor_stability(neighbor_sharpes, m["net_sharpe"])
            neighbor_report["distinct_numeric_neighbors"] = neighbor_grid.distinct_numeric
            stability = neighbor_report["stability"]
            trimmed_sr = compute_trimmed_sharpe(net_ret, bpy)

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

            hist = self.registry.selection_history(
                contract.contract_id, len(validation_ts), simulation_epoch=simulation_epoch,
                timestamps=validation_ts, candidate_returns=selection_net, contract=contract,
                exclude_candidate_id=candidate.candidate_id)
            alignment = hist["alignment"]
            n_attempts = hist["raw_attempt_count"] + 1
            # A UTC-day panel compounds the candidate onto the shared daily index, so the DSR
            # base is the observation rate measured from that index -- never the contract's bar
            # calendar, which would overstate the sample for a daily panel.  A series projected
            # onto the contract grid is annualised on that grid's rate, the same basis every
            # other attempt on this contract is scored on.
            periods = alignment.get("periods_per_year")
            if alignment["frequency"] == "utc_day" and periods:
                bars_for_dsr = periods
            elif contract_clock:
                bars_for_dsr = int(contract.bars_per_year)
            else:
                bars_for_dsr = bpy
            candidate_returns = (hist["candidate_returns"] if hist["candidate_returns"] is not None
                                 else net_ret)
            report = compute_selection_report(candidate_returns, hist["streams"], bars_for_dsr,
                                              raw_attempt_count=n_attempts, prior_max_keff=prior_keff,
                                              prior_max_noise=prior_noise)
            self.registry.update_search_floor(contract.contract_id, report["k_eff_gate"],
                                             report["expected_max_sharpe_noise"])
            dsr_p, exp_max, k_eff = report["probability"], report["expected_max_sharpe_noise"], report["k_eff_gate"]
            retrospective = hyp.is_retrospective_for(contract)
            diag = {"dataset": dataset, "family_key": family_key,
                    "retrospective_replication_only": retrospective,
                    "source_published_at": hyp.source_published_at,
                    "execution_protocol": protocol,
                    "model_metadata": {
                        "model_type": str(model_type),
                        "model_class": model_class,
                        "model_tier": model_tier,
                    },
                    "execution_policy": policy_dict,
                    "engine_artifacts": engine_artifacts,
                    "session_col_used": sim1.session_col_used,
                    "funding_events": sim1.funding_events,
                    **engine_diag,
                    "selection": {**{k: report[k] for k in ("raw_attempt_count", "streams_used", "unvectorized_attempts",
                                                            "prior_max_keff", "k_eff_dedup", "k_eff_effective",
                                                            "k_eff_gate", "noise_floor_applied", "correction_policy",
                                                            "non_promotable_reason")},
                                  "simulation_epoch": simulation_epoch,
                                  "raw_operation_count": hist["raw_operation_count"],
                                  "alignment": alignment,
                                  "bars_per_year_used": float(bars_for_dsr),
                                  "selection_clock": {
                                      "projected_onto_contract_grid": bool(contract_clock),
                                      "contract_validation_bars": len(validation_ts),
                                      "panel_validation_bars": len(panel_validation_ts),
                                      "structural_zero_bars": (
                                          int(len(validation_ts) - len(panel_validation_ts))
                                          if contract_clock else 0),
                                  },
                                  "preflight": {key: preflight[key] for key in
                                                ("frequency", "aligned_bars", "overlap_fraction",
                                                 "selection_group_id", "scope")}},
                    "excluded_streams": hist["excluded"]}
            if market.build_identity:
                # A v2-built dataset states which builder and spec produced these bars; a legacy
                # dataset has no build identity and none is invented for it.
                diag["dataset_build_identity"] = market.build_identity
            if s1_rules is not None:
                diag["stage1_rules"] = {"metrics": s1_rules.metrics, "notes": s1_rules.notes}
            if s1_carry is not None:
                diag["stage1_carry_rv"] = {"metrics": s1_carry.metrics, "notes": s1_carry.notes}
            if s1_meta is not None:
                diag["stage1_meta_overlay"] = {
                    "metrics": s1_meta.metrics,
                    "notes": s1_meta.notes,
                    "parent_run_id": parent_stream["run_id"] if parent_stream else "",
                }
            diag["metrics"] = m
            diag["timing_skill"] = compute_shift_skill_pvalue(sim1.positions, ret_p["long"][v_slice],
                                                              contract.label_horizon_bars)
            diag["neighbor_tested"] = neighbor_report["neighbor_tested"]
            diag["neighbor_stability"] = neighbor_report
            diag["params_model"] = params_model_report.to_diagnostics()
            diag["neighbor_grid"] = neighbor_grid.to_diagnostics()
            diag["neighbor_requirement"] = neighbor_requirement
            if neighbor_requirement["exemption"] is not None:
                # Explicit, machine-readable exemption label; its presence is the
                # only thing that lets an untested neighbor axis qualify.
                diag["neighbor_exemption"] = neighbor_requirement["exemption"]
            diag["trimmed_sharpe"] = trimmed_sr
            # Audit Check 1 evidence: measured on the tape the Engine actually executed on
            # this validation slice, plus any alignment the spec declared up front.  A later
            # audit compares this against the contract's label horizon; the strategy cannot
            # write these numbers.
            declared_alignment = params.get("label_horizon_alignment")
            diag["cadence_evidence"] = {
                **measure_trading_cadence(sim1.positions),
                "label_horizon_bars": float(contract.label_horizon_bars),
                "expected_holding_bars": float(hyp.expected_holding_bars),
                "declared_alignment": (
                    dict(declared_alignment) if isinstance(declared_alignment, dict) else None
                ),
                "measured_on": "evaluation_tape_positions",
            }
            # Audit Check 4 evidence: only a causal_model execution can carry this record,
            # and it comes from the folds that ran, not from the contract's integers.
            diag["fold_isolation"] = sim1.fold_isolation

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
            if not (neighbor_requirement["met"]):
                # A parameterized strategy with fewer than two distinct numeric
                # neighbors has not exercised the axis at all; an untested grid
                # is a REVISE, never a candidate.  Only a genuine parameter-free
                # declaration is exempt, and that is recorded explicitly.
                failed_gates.append("neighbor_stability")
            elif neighbor_report["neighbor_tested"] and not neighbor_report["passed"]:
                failed_gates.append("neighbor_stability")
            if trimmed_sr is None or not (trimmed_sr > TRIMMED_SHARPE_FLOOR):
                failed_gates.append("trimmed_sharpe")
            if s1_rules is not None and not s1_rules.passed:
                failed_gates.append("stage1_rules")
            if s1_carry is not None and not s1_carry.passed:
                failed_gates.append("stage1_carry_rv")
            if s1_meta is not None and not s1_meta.passed:
                failed_gates.append("stage1_meta_overlay")
            if family == "cross_sectional_ml" and (ic_hac_t is None or not (ic_hac_t >= IC_T_FLOOR)):
                failed_gates.append("ic_hac")
            if comparison is not None:
                diag["incremental_comparison"] = comparison
                if comparison["status"] != "PASS":
                    failed_gates.append("incremental_comparison")
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
            # The artifact carries the same clock the trial was scored on; a legacy artifact
            # (and a failed reservation below) stays clockless rather than inventing one.
            self.registry.record_trial(res, params, selection_net, trial_number=trial_num,
                                       diagnostics=diag, timestamps=validation_ts)
            return res
        except Exception as exc:
            res_failed = ExperimentResult(run_id, hyp_id, contract.contract_id, strat_name, family, hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12], "FAILED", "FAILED", 0.0, 0.0, 0.0, 0.0, 1, 1.0, None, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None, f"Execution failed: {exc}", 0.0, 0.0, int(is_synthetic), data_sha, strat_module_str, str(model_type), "", strategy_code_sha256=code_sha, simulation_epoch=simulation_epoch)
            try:
                self.registry.record_trial(res_failed, params, np.zeros(0), trial_number=trial_num,
                                           diagnostics={"error": str(exc), "dataset": dataset})
            except (ValueError, sqlite3.Error, OSError):
                pass
            raise

    def _record_early_rejection(
        self,
        *,
        run_id: str,
        hyp_id: str,
        contract_id: str,
        strat_name: str,
        family: str,
        params: dict[str, Any],
        prior_keff: float,
        is_synthetic: int,
        data_sha: str,
        strat_module_str: str,
        model_type: str,
        trial_num: int,
        provenance_kw: dict[str, Any],
        simulation_epoch: str,
        dataset: Any,
        gate_key: str,
        notes: str,
        gate_payload: dict[str, Any],
        stage_diag_key: str | None = None,
        mean_ic: float | None = None,
        ic_hac_t: float | None = None,
        metrics: dict[str, float] | None = None,
        returns_vec: np.ndarray | None = None,
        returns_timestamps: list | None = None,
    ) -> ExperimentResult:
        n_attempts = (
            self.registry.selection_attempt_count(contract_id, simulation_epoch=simulation_epoch)
        )
        diag = {
            "dataset": dataset,
            "neighbor_tested": False,
            "failed_gates": [gate_key],
            stage_diag_key or gate_key: gate_payload,
        }
        m_vals = metrics or {}
        res = ExperimentResult(
            run_id,
            hyp_id,
            contract_id,
            strat_name,
            family,
            hashlib.sha256(canonical_json(params).encode()).hexdigest()[:12],
            "STAGE_1_SIGNAL",
            "REJECTED_EARLY",
            float(m_vals.get("gross_sharpe", 0.0)),
            float(m_vals.get("net_sharpe", 0.0)),
            0.0,
            0.0,
            n_attempts,
            float(prior_keff),
            mean_ic,
            ic_hac_t,
            float(m_vals.get("hit_rate", 0.0)),
            float(m_vals.get("profit_factor", 0.0)),
            float(m_vals.get("max_drawdown_pct", 0.0)),
            float(m_vals.get("annualized_turnover", 0.0)),
            float(m_vals.get("breakeven_cost_bps", 0.0)),
            0.0,
            None,
            f"Rejected at Stage 1: {notes}",
            0.0,
            float(m_vals.get("cvar_99_pct", 0.0)),
            int(is_synthetic),
            data_sha,
            strat_module_str,
            model_type,
            "",
        )
        res = dc_replace(res, **provenance_kw, diagnostics_json=canonical_json(diag))
        self.registry.record_trial(
            res,
            params,
            np.zeros(0) if returns_vec is None else returns_vec,
            trial_number=trial_num,
            diagnostics=diag,
            # An early rejection that holds a real validation vector publishes it on the trial's
            # own clock.  Without a vector there is no clock: the artifact stays legacy.
            timestamps=returns_timestamps if returns_vec is not None else None,
        )
        return res

    def _note_failure(self, contract, hyp_id, family, strat_module, params, dataset, stage, exc,
                      simulation_epoch: str = ENGINE_SIMULATION_EPOCH):
        """Record technical pre-admission refusals, never a candidate outcome."""
        self.registry.record_selection_attempt(
            hypothesis_id=hyp_id, contract_id=contract.contract_id, kind="technical_preflight", stage=stage,
            outcome="ERROR", strategy_family=family, strategy_module=strat_module, parameters=params,
            data_provenance=(dataset or {}).get("provenance", "unknown"), diagnostics={"error": str(exc)},
            simulation_epoch=simulation_epoch)
