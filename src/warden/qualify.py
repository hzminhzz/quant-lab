"""Human-only holdout qualification gate and one-shot lineage unsealing."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from warden.provenance import resolve_dataset
from warden.runner import (
    ExperimentRunner,
    calculate_metrics_from_returns,
    load_module_from_path,
    simulate_positions,
)


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

    mod_str = trial.get("strategy_module") or ("strategies/baselines/cs_ridge_1h.py" if trial.get("strategy_family") == "cross_sectional_ml" else "strategies/candidates/vol_breakout_1h.py")
    strat_path = runner.repo_root / mod_str
    if not strat_path.is_file():
        strat_path = runner.repo_root / ("strategies/baselines/cs_ridge_1h.py" if trial.get("strategy_family") == "cross_sectional_ml" else "strategies/candidates/vol_breakout_1h.py")
    mod = load_module_from_path(strat_path)
    raw_cfg = trial.get("config_json", "{}")
    cfg_data = yaml.safe_load(raw_cfg) if raw_cfg else {}
    params = cfg_data.get("parameters", cfg_data) if isinstance(cfg_data, dict) else {}

    out = mod.generate_positions(panel_df, contract, **params)
    if "open" in panel_df.columns:
        ret_df = panel_df.with_columns(ret=(pl.col("open").shift(-1).over("asset") / pl.col("open") - 1.0).fill_null(0.0))
    else:
        ret_df = panel_df.with_columns(ret=pl.col("close").pct_change().fill_null(0.0).over("asset"))
    from warden.runner import _aligned_matrix, _funding_settlement_mask
    ret_p = _aligned_matrix(ret_df, "ret", assets)
    fund_p = _aligned_matrix(panel_df, "funding_rate", assets) if "funding_rate" in panel_df.columns else None
    fund_mask = _funding_settlement_mask(ts, contract)

    sim = simulate_positions(out["positions"], ret_p, fund_p, contract, slice(0, n_bars), cost_multiplier=1.0, funding_mask=fund_mask)
    m = calculate_metrics_from_returns(sim["gross_returns"], sim["net_returns"], sim["turnover"], contract.bars_per_year)

    h_net_sr = m["net_sharpe"]
    ratio = float(h_net_sr / max(float(trial.get("net_sharpe", 0.0)), 1e-6))
    promoted = (h_net_sr > 0.80) and (ratio >= 0.50)
    verdict = "PROMOTED" if promoted else "REJECTED_ON_HOLDOUT"
    qual_id = f"QUAL-{run_id}"

    with reg._connect() as conn:
        conn.execute("INSERT INTO qualifications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (qual_id, run_id, cid, hyp_id, lineage_id, m["gross_sharpe"], h_net_sr, m["max_drawdown_pct"], ratio, "HUMAN_OPERATOR", time.time()))
        conn.execute("UPDATE hypotheses SET status = ? WHERE hypothesis_id = ?", (verdict, hyp_id))

    return {"qual_id": qual_id, "run_id": run_id, "lineage_id": lineage_id, "holdout_net_sharpe": h_net_sr,
            "val_to_holdout_ratio": ratio, "promoted": promoted, "verdict": verdict,
            "holdout_override": holdout_override, "data_provenance": eligibility["data_provenance"]}
