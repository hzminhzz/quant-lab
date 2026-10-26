"""Human-only holdout qualification gate and one-shot lineage unsealing."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import polars as pl
import yaml

from warden.provenance import resolve_dataset
from warden.runner import (
    ExperimentRunner,
    _aligned_matrix,
    _funding_settlement_mask,
    calculate_metrics_from_returns,
    execution_returns,
    load_module_from_path,
    simulate_positions,
)


class HoldoutAlreadyUnsealedError(PermissionError): pass


def _require_approved_audit(conn, run_id: str) -> None:
    row = conn.execute("SELECT verdict,checks_json FROM audit_evidence WHERE run_id=? "
                       "ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
    if not row or row[0] != "APPROVED":
        raise ValueError(f"Run '{run_id}' requires an effective APPROVED six-check audit before qualification.")
    checks = json.loads(row[1] or "{}")
    if len(checks) < 6 or not all(bool(v) for v in checks.values()):
        raise ValueError(f"Run '{run_id}' has not passed all six audit checks; holdout stays sealed.")


def qualify_candidate(
    run_id: str,
    human_confirm_token: str,
    runner: ExperimentRunner,
    reviewer: str = "",
) -> dict[str, Any]:
    if os.environ.get("WARDEN_NON_INTERACTIVE") == "1":
        raise PermissionError("warden qualify is an interactive human-only gate.")
    if human_confirm_token != "YES_UNSEAL_HOLDOUT":
        raise PermissionError("Holdout unsealing requires confirmation token 'YES_UNSEAL_HOLDOUT'.")

    reg = runner.registry
    with reg._connect() as conn:
        t_row = conn.execute("SELECT * FROM trials WHERE run_id = ?", (run_id,)).fetchone()
        if not t_row:
            raise ValueError(f"Run ID '{run_id}' not found in registry.")
        trial = dict(t_row)
        hyp_id, cid = trial["hypothesis_id"], trial["contract_id"]
        q_entry = conn.execute("SELECT reason FROM trial_quarantine WHERE run_id = ?", (run_id,)).fetchone()
        if q_entry:
            raise ValueError(f"Run ID '{run_id}' is quarantined ({q_entry[0]}) and cannot be qualified on holdout.")
        if trial.get("synthetic") in (1, 1.0, "1", True):
            raise ValueError(f"Run ID '{run_id}' was generated on synthetic benchmark data and cannot be qualified on holdout.")
        eligibility = reg.effective_eligibility(run_id)
        if not eligibility["eligible"]:
            raise ValueError(
                f"Run ID '{run_id}' is not market-eligible for holdout qualification: {eligibility['reason']}")
        if trial.get("verdict") != "CANDIDATE_FOR_QUALIFICATION":
            raise ValueError(f"Run ID '{run_id}' has verdict '{trial.get('verdict')}'; only CANDIDATE_FOR_QUALIFICATION trials can be qualified.")
        val_sr = float(trial.get("net_sharpe", 0.0))
        if val_sr <= 0.0:
            raise ValueError(f"Trial '{run_id}' has non-positive validation Net Sharpe ({val_sr:.2f}); cannot qualify.")
        _require_approved_audit(conn, run_id)
        curr_hyp = hyp_id
        while True:
            p_row = conn.execute("SELECT parent_hypothesis_id FROM hypotheses WHERE hypothesis_id = ?", (curr_hyp,)).fetchone()
            if not p_row or not p_row[0] or p_row[0] == curr_hyp:
                lineage_id = curr_hyp
                break
            curr_hyp = p_row[0]
        q_row = conn.execute("SELECT qual_id, run_id FROM qualifications WHERE lineage_id = ?", (lineage_id,)).fetchone()
        if q_row:
            raise HoldoutAlreadyUnsealedError(f"Lineage '{lineage_id}' was already unsealed by run '{q_row[1]}'.")

    contract = runner.resolve_contract(cid)
    holdout = resolve_dataset(contract, runner.repo_root, role="holdout")
    if not holdout.eligible_market:
        raise ValueError(f"Holdout dataset for '{cid}' is not market-eligible: {holdout.reason}")
    h_path = runner.repo_root / contract.holdout_data_path
    if not h_path.is_file():
        raise FileNotFoundError(f"Holdout dataset not found at '{h_path}'.")

    raw_panel = pl.read_parquet(h_path).sort("timestamp", "asset")
    panel_df = raw_panel.filter((pl.col("timestamp") >= contract.holdout_start)
                                & (pl.col("timestamp") <= contract.holdout_end))
    if panel_df.height < 20:
        raise ValueError("Holdout window has too few bars; refuse to substitute another window.")
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    mod_str = trial.get("strategy_module") or (
        "strategies/baselines/cs_ridge_1h.py" if trial.get("strategy_family") == "cross_sectional_ml"
        else "strategies/candidates/vol_breakout_1h.py")
    strat_path = runner.repo_root / mod_str
    if not strat_path.is_file():
        strat_path = runner.repo_root / (
            "strategies/baselines/cs_ridge_1h.py" if trial.get("strategy_family") == "cross_sectional_ml"
            else "strategies/candidates/vol_breakout_1h.py")
    mod = load_module_from_path(strat_path)
    raw_cfg = trial.get("config_json", "{}")
    cfg_data = yaml.safe_load(raw_cfg) if raw_cfg else {}
    params = cfg_data.get("parameters", cfg_data) if isinstance(cfg_data, dict) else {}

    out = mod.generate_positions(panel_df, contract, **params)
    ret_p = execution_returns(panel_df, contract, assets)  # exact execution basis; never a close fallback
    fund_p = _aligned_matrix(panel_df, "funding_rate", assets) if "funding_rate" in panel_df.columns else None
    sim = simulate_positions(out["positions"], ret_p["long"], fund_p, contract, slice(0, len(ts)),
                             cost_multiplier=1.0, funding_mask=_funding_settlement_mask(ts, contract),
                             quote_returns=ret_p)
    m = calculate_metrics_from_returns(sim["gross_returns"], sim["net_returns"], sim["turnover"],
                                       contract.bars_per_year)

    h_net_sr = m["net_sharpe"]
    ratio = float(h_net_sr / max(float(trial.get("net_sharpe", 0.0)), 1e-6))
    reviewer = reviewer.strip()
    promoted = (h_net_sr > 0.80) and (ratio >= 0.50) and bool(reviewer)
    verdict = "PROMOTED" if promoted else "REJECTED_ON_HOLDOUT"
    qual_id = f"QUAL-{run_id}"

    with reg._connect() as conn:
        conn.execute("INSERT INTO qualifications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (qual_id, run_id, cid, hyp_id, lineage_id, m["gross_sharpe"], h_net_sr, m["max_drawdown_pct"],
                      ratio, reviewer or "UNVERIFIED_REVIEWER", time.time()))
        conn.execute("UPDATE hypotheses SET status = ? WHERE hypothesis_id = ?", (verdict, hyp_id))

    return {"qual_id": qual_id, "run_id": run_id, "lineage_id": lineage_id, "holdout_net_sharpe": h_net_sr,
            "val_to_holdout_ratio": ratio, "promoted": promoted, "verdict": verdict,
            "reviewer": reviewer or "UNVERIFIED_REVIEWER", "data_provenance": holdout.provenance}
