"""Human-only holdout qualification gate and one-shot lineage unsealing."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from warden.codeid import code_closure
from warden.gates import HOLDOUT_RATIO_FLOOR, HOLDOUT_SR_FLOOR, REVISE_SR_FLOOR, SR2_FLOOR
from warden.provenance import resolve_dataset
from warden.runner import (
    ExperimentRunner,
    aligned_matrix,
    calculate_metrics_from_returns,
    funding_settlement_mask,
    simulate_positions,
)
from warden.strategy_loader import load_module_from_path


class HoldoutAlreadyUnsealedError(PermissionError): pass


def qualify_candidate(
    run_id: str,
    human_confirm_token: str,
    runner: ExperimentRunner,
    holdout_override_path: Path | None = None,
) -> dict[str, Any]:
    if os.environ.get("WARDEN_NON_INTERACTIVE") == "1":
        raise PermissionError("warden qualify is an interactive human-only gate.")
    if human_confirm_token != "YES_UNSEAL_HOLDOUT":
        raise PermissionError("Holdout unsealing requires confirmation token 'YES_UNSEAL_HOLDOUT'.")

    reg = runner.registry
    trial = reg.trial_row(run_id)
    if not trial:
        raise ValueError(f"Run ID '{run_id}' not found in registry.")
    hyp_id, cid = trial["hypothesis_id"], trial["contract_id"]
    q_reason = reg.quarantine_reason(run_id)
    if q_reason:
        raise ValueError(f"Run ID '{run_id}' is quarantined ({q_reason}) and cannot be qualified on holdout.")
    if trial.get("synthetic") in (1, 1.0, "1", True):
        raise ValueError(f"Run ID '{run_id}' was generated on synthetic benchmark data and cannot be qualified on holdout.")
    eligibility = reg.effective_eligibility(run_id)
    if not eligibility["eligible"]:
        raise ValueError(
            f"Run ID '{run_id}' is not market-eligible for holdout qualification: {eligibility['reason']}")
    val_sr = float(trial.get("net_sharpe", 0.0))
    if val_sr <= 0.0:
        raise ValueError(f"Trial '{run_id}' has non-positive validation Net Sharpe ({val_sr:.2f}); cannot qualify.")
    is_candidate = trial.get("verdict") == "CANDIDATE_FOR_QUALIFICATION"
    is_human_approved = (trial.get("verdict") == "REVISE" and val_sr >= REVISE_SR_FLOOR
                         and float(trial.get("stress_2x_cost_sharpe", 0.0)) >= SR2_FLOOR)
    if not (is_candidate or is_human_approved):
        raise ValueError(f"Run ID '{run_id}' has verdict '{trial.get('verdict')}'; only CANDIDATE_FOR_QUALIFICATION or human-authorized high-Sharpe REVISE trials can be qualified.")
    lineage_id = reg.lineage_root(hyp_id)
    q_row = reg.lineage_qualification(lineage_id)
    if q_row:
        raise HoldoutAlreadyUnsealedError(f"Lineage '{lineage_id}' was already unsealed by run '{q_row['run_id']}'.")

    contract = runner.resolve_contract(cid)
    holdout_override = holdout_override_path is not None
    if not holdout_override:
        holdout_res = resolve_dataset(contract, runner.repo_root, role="holdout")
        if not holdout_res.eligible_market:
            raise ValueError(f"Holdout dataset for '{cid}' is not market-eligible: {holdout_res.reason}")
    h_path = holdout_override_path or (runner.repo_root / contract.holdout_data_path)
    if not h_path.is_file():
        raise FileNotFoundError(f"Holdout dataset not found at '{h_path}'.")

    raw_panel = pl.read_parquet(h_path).sort("timestamp", "asset")
    window_df = raw_panel.filter(
        (pl.col("timestamp") >= contract.holdout_start) & (pl.col("timestamp") <= contract.holdout_end)
    )
    panel_df = window_df if window_df.height >= 20 else raw_panel
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    n_bars = len(ts)

    strat_module = trial.get("strategy_module")
    if not strat_module:
        raise ValueError(f"Trial '{run_id}' has no recorded strategy_module; qualification cannot bind to code.")
    strat_path = runner.repo_root / strat_module
    if not strat_path.is_file():
        raise FileNotFoundError(f"Recorded strategy module '{strat_module}' for trial '{run_id}' is absent; refusing to qualify on substituted code.")
    code_sha, _ = code_closure(runner.repo_root, strat_module)
    recorded_code_sha = trial.get("strategy_code_sha256")
    if not recorded_code_sha:
        raise ValueError(f"Trial '{run_id}' has no recorded strategy_code_sha256; qualification requires a code binding (re-run the trial).")
    if code_sha != recorded_code_sha:
        raise ValueError(f"Strategy code for trial '{run_id}' changed since the trial; refusing to qualify on different code.")
    mod = load_module_from_path(strat_path)
    raw_cfg = trial.get("config_json", "{}")
    cfg_data = yaml.safe_load(raw_cfg) if raw_cfg else {}
    params = cfg_data.get("parameters", cfg_data) if isinstance(cfg_data, dict) else {}

    out = mod.generate_positions(panel_df, contract, **params)
    # Legacy holdout panels may lack open prices; qualification keeps its historical close
    # fallback. The exploratory runner forbids that basis — the divergence is deliberate.
    if "open" in panel_df.columns:
        ret_df = panel_df.with_columns(ret=(pl.col("open").shift(-1).over("asset") / pl.col("open") - 1.0).fill_null(0.0))
    else:
        ret_df = panel_df.with_columns(ret=pl.col("close").pct_change().fill_null(0.0).over("asset"))
    ret_p = aligned_matrix(ret_df, "ret", assets)
    fund_p = aligned_matrix(panel_df, "funding_rate", assets) if "funding_rate" in panel_df.columns else None
    fund_mask = funding_settlement_mask(ts, contract)

    sim = simulate_positions(out["positions"], ret_p, fund_p, contract, slice(0, n_bars), cost_multiplier=1.0, funding_mask=fund_mask)
    m = calculate_metrics_from_returns(sim["gross_returns"], sim["net_returns"], sim["turnover"], contract.bars_per_year)

    h_net_sr = m["net_sharpe"]
    ratio = float(h_net_sr / max(float(trial.get("net_sharpe", 0.0)), 1e-6))
    promoted = (h_net_sr > HOLDOUT_SR_FLOOR) and (ratio >= HOLDOUT_RATIO_FLOOR)
    verdict = "PROMOTED" if promoted else "REJECTED_ON_HOLDOUT"
    qual_id = f"QUAL-{run_id}"

    with reg._connect() as conn:
        conn.execute("INSERT INTO qualifications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (qual_id, run_id, cid, hyp_id, lineage_id, m["gross_sharpe"], h_net_sr, m["max_drawdown_pct"], ratio, "HUMAN_OPERATOR", time.time()))
        conn.execute("UPDATE hypotheses SET status = ? WHERE hypothesis_id = ?", (verdict, hyp_id))

    return {"qual_id": qual_id, "run_id": run_id, "lineage_id": lineage_id, "holdout_net_sharpe": h_net_sr,
            "val_to_holdout_ratio": ratio, "promoted": promoted, "verdict": verdict,
            "holdout_override": holdout_override, "data_provenance": eligibility["data_provenance"]}
