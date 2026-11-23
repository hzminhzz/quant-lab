"""Human-only holdout qualification gate and one-shot lineage unsealing."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from warden.codeid import code_closure
from warden.engine_sim import extract_execution_policy, run_engine_simulation, run_engine_strategy
from warden.gates import HOLDOUT_RATIO_FLOOR, HOLDOUT_SR_FLOOR
from warden.protocols import (
    detect_strategy_protocol,
    resolve_effective_params,
    resolve_protocol_target,
    run_causal_model_strategy,
)
from warden.provenance import resolve_dataset
from warden.runner import (
    ExperimentRunner,
    calculate_metrics_from_returns,
    filter_target_symbols,
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
    # Qualification may not silently execute an unrelated strategy when the
    # original source was moved or never recorded.
    module = trial.get("strategy_module") or ""
    rel = Path(module)
    strat_path = (runner.repo_root / rel).resolve()
    allowed = (runner.repo_root / "strategies").resolve()
    if not module or rel.is_absolute() or ".." in rel.parts or not strat_path.is_relative_to(allowed):
        raise ValueError("Missing or unsafe frozen strategy module; holdout remains sealed")
    if not strat_path.is_file():
        raise FileNotFoundError(f"Frozen strategy module unavailable: {module}")
    code_sha, _ = code_closure(runner.repo_root, module)
    recorded_code_sha = trial.get("strategy_code_sha256")
    if not recorded_code_sha:
        raise ValueError(f"Trial '{run_id}' has no recorded strategy_code_sha256; qualification requires a code binding (re-run the trial).")
    if code_sha != recorded_code_sha:
        raise ValueError(f"Strategy code for trial '{run_id}' changed since the trial; refusing to qualify on different code.")
    holdout_override = holdout_override_path is not None
    if not holdout_override:
        # The six-check audit is a required production admission, not merely
        # a suggested CLI step. Fail before opening or claiming the holdout.
        with reg._connect() as conn:
            approved = conn.execute(
                "SELECT verdict FROM audit_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if not approved or approved[0] != "APPROVED":
            raise PermissionError("Production qualification requires an APPROVED six-check audit")
        proof = reg.causal_proof_for_run(run_id, runner.repo_root)
        if not proof["verified"]:
            raise PermissionError(f"Production qualification requires run-bound causal proof: {proof['reason']}")
    h_path = holdout_override_path or (runner.repo_root / contract.holdout_data_path)
    if not h_path.is_file():
        raise FileNotFoundError(f"Holdout dataset not found at '{h_path}'.")

    # Critical ordering: resolve_dataset hashes the entire holdout file.
    # Therefore even provenance verification must happen only after the
    # exclusive claim and durable read-start record have committed.
    try:
        reg.reserve_holdout_lineage(lineage_id, run_id)
    except PermissionError as exc:
        raise HoldoutAlreadyUnsealedError(str(exc)) from exc
    reg.mark_holdout_read_started(lineage_id, run_id)
    if not holdout_override:
        holdout_res = resolve_dataset(contract, runner.repo_root, role="holdout")
        if not holdout_res.eligible_market:
            raise ValueError(f"Holdout dataset for '{cid}' is not market-eligible: {holdout_res.reason}")

    raw_cfg = trial.get("config_json", "{}")
    cfg_data = yaml.safe_load(raw_cfg) if raw_cfg else {}
    cfg_dict = cfg_data if isinstance(cfg_data, dict) else {}
    params = cfg_dict.get("parameters", cfg_dict)

    policy_kw = extract_execution_policy(cfg_dict, params, trial.get("diagnostics_json"))

    mod = load_module_from_path(strat_path)
    protocol = detect_strategy_protocol(mod)
    effective_params = resolve_effective_params(mod, protocol, params)

    raw_panel = pl.read_parquet(h_path)
    if "symbol" in raw_panel.columns and "asset" not in raw_panel.columns:
        raw_panel = raw_panel.rename({"symbol": "asset"})
    raw_panel = filter_target_symbols(raw_panel, effective_params, str(h_path)).sort("timestamp", "asset")
    window_df = raw_panel.filter(
        (pl.col("timestamp") >= contract.holdout_start) & (pl.col("timestamp") <= contract.holdout_end)
    )
    if window_df.height < 20 and not holdout_override:
        raise ValueError("No sufficient observations within the declared production holdout; no fallback window")
    panel_df = window_df if window_df.height >= 20 else raw_panel
    ts = sorted(panel_df["timestamp"].unique().to_list())
    assets = sorted(panel_df["asset"].unique().to_list())
    n_bars = len(ts)

    if protocol == "causal_rules":
        strat_obj = resolve_protocol_target(mod).configure_strategy(contract, params)
        sim = run_engine_strategy(
            panel_df, strat_obj, ts, assets, contract, slice(0, n_bars), **policy_kw
        )
    elif protocol == "causal_model":
        min_train = max(
            int(contract.label_horizon_bars) + int(contract.execution_delay_bars) + int(contract.embargo_bars) + 4,
            n_bars // 4,
        )
        holdout_slice = slice(min_train, n_bars)
        out = run_causal_model_strategy(mod, panel_df, contract, ts, assets, holdout_slice, params)
        sim = run_engine_simulation(
            panel_df, out["positions"], ts, assets, contract, holdout_slice, **policy_kw
        )
    else:
        out = mod.generate_positions(panel_df, contract, **params)
        sim = run_engine_simulation(
            panel_df, out["positions"], ts, assets, contract, slice(0, n_bars), **policy_kw
        )
    m = calculate_metrics_from_returns(
        sim.gross_returns, sim.net_returns, sim.turnover, contract.bars_per_year
    )

    h_net_sr = m["net_sharpe"]
    ratio = float(h_net_sr / max(float(trial.get("net_sharpe", 0.0)), 1e-6))
    promoted = (h_net_sr > HOLDOUT_SR_FLOOR) and (ratio >= HOLDOUT_RATIO_FLOOR)
    verdict = "PROMOTED" if promoted else "REJECTED_ON_HOLDOUT"
    qual_id = f"QUAL-{run_id}"

    with reg._connect() as conn:
        conn.execute("INSERT INTO qualifications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (qual_id, run_id, cid, hyp_id, lineage_id, m["gross_sharpe"], h_net_sr, m["max_drawdown_pct"], ratio, "HUMAN_OPERATOR", time.time()))
        conn.execute("UPDATE hypotheses SET status = ? WHERE hypothesis_id = ?", (verdict, hyp_id))
        conn.execute("UPDATE holdout_claims SET state=?,finalized_at=? WHERE lineage_id=? AND run_id=? AND state='READ_STARTED'",
                     (verdict, time.time(), lineage_id, run_id))

    return {"qual_id": qual_id, "run_id": run_id, "lineage_id": lineage_id, "holdout_net_sharpe": h_net_sr,
            "val_to_holdout_ratio": ratio, "promoted": promoted, "verdict": verdict,
            "holdout_override": holdout_override, "data_provenance": eligibility["data_provenance"],
            "terminal_equity": sim.terminal_equity,
            "engine_fills_count": len(sim.backtest_result.fills),
            "engine_trades_count": len(sim.backtest_result.trades)}
