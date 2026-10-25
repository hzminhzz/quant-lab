"""Human-only holdout qualification gate and one-shot lineage unsealing."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import polars as pl
import yaml

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
        h_row = conn.execute("SELECT parent_hypothesis_id FROM hypotheses WHERE hypothesis_id = ?", (hyp_id,)).fetchone()
        lineage_id = (h_row[0] if h_row else None) or hyp_id

        q_row = conn.execute("SELECT qual_id, run_id FROM qualifications WHERE lineage_id = ?", (lineage_id,)).fetchone()
        if q_row:
            raise HoldoutAlreadyUnsealedError(f"Lineage '{lineage_id}' was already unsealed by run '{q_row[1]}'.")

    contract = runner.resolve_contract(cid)
    h_path = holdout_override_path or (runner.repo_root / contract.holdout_data_path)
    if not h_path.is_file():
        raise FileNotFoundError(f"Holdout dataset not found at '{h_path}'.")

    panel_df = pl.read_parquet(h_path).sort("timestamp", "asset")
    ts = sorted(panel_df["timestamp"].unique().to_list())
    n_bars, n_assets = len(ts), len(panel_df["asset"].unique())

    strat_path = runner.repo_root / ("strategies/baselines/cs_ridge_1h.py" if trial.get("strategy_family") == "cross_sectional_ml" else "strategies/candidates/vol_breakout_1h.py")
    mod = load_module_from_path(strat_path)
    params = yaml.safe_load(trial.get("config_json", "{}"))

    out = mod.generate_positions(panel_df, contract, **params)
    ret_col = panel_df.select(pl.col("close").pct_change().fill_null(0.0).over("asset")).to_numpy()
    ret_p = ret_col.reshape((n_bars, n_assets))
    fund_p = panel_df["funding_rate"].to_numpy().reshape((n_bars, n_assets)) if "funding_rate" in panel_df.columns else None

    sim = simulate_positions(out["positions"], ret_p, fund_p, contract, slice(0, n_bars), cost_multiplier=1.0)
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

    return {"qual_id": qual_id, "run_id": run_id, "lineage_id": lineage_id, "holdout_net_sharpe": h_net_sr, "val_to_holdout_ratio": ratio, "promoted": promoted, "verdict": verdict}
