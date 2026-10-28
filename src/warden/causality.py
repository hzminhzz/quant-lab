"""Strategy-level prefix-invariance verification, bound to the code and data that ran.

Audit check 3 otherwise asks whether *any* feature set was verified for the contract, which a
rules strategy either inherits by accident or can never satisfy. This recomputes the strategy's own
positions on truncated panels and records the result against the code closure, the config and the
panel digest, so the check can be earned per run and cannot be borrowed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl

from warden.codeid import code_closure
from warden.contracts import canonical_json
from warden.runner import load_module_from_path

CUT_FRACTIONS = (0.5, 0.7, 0.9)
CUT_DATES = ("2023-06-30", "2024-07-01")


def _cut_points(stamps: list) -> list:
    cuts = [stamps[int(len(stamps) * f) - 1] for f in CUT_FRACTIONS]
    for day in CUT_DATES:
        inside = [s for s in stamps if s.isoformat().startswith(day)]
        if inside:
            cuts.append(inside[-1])
    return sorted(set(cuts))


def config_digest(config_json: str) -> str:
    return hashlib.sha256(canonical_json(json.loads(config_json or "{}")).encode()).hexdigest()


def verify_run(run_id: str, runner) -> dict:
    """Recompute the run's recorded configuration at several cut points and record the result."""
    registry = runner.registry
    with registry._connect() as conn:
        row = conn.execute(
            "SELECT contract_id, strategy_module, config_json, data_snapshot_sha256, strategy_family "
            "FROM trials WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown run_id '{run_id}'")
    contract_id, module, cfg_json, recorded_sha, _family = row
    contract = runner.resolve_contract(contract_id)
    params = json.loads(cfg_json or "{}")

    panel_path = Path(runner.repo_root) / contract.exploratory_data_path
    if not panel_path.is_file():
        raise FileNotFoundError(f"panel not found: {contract.exploratory_data_path}")
    data_sha = hashlib.sha256(panel_path.read_bytes()).hexdigest()
    if recorded_sha and data_sha != recorded_sha:
        raise ValueError("exploratory panel digest differs from the recorded trial; refusing to certify")

    code_sha, code_files = code_closure(Path(runner.repo_root), module)
    cfg_digest = config_digest(cfg_json)

    panel = pl.read_parquet(panel_path).sort("timestamp", "asset")
    strategy = load_module_from_path(Path(runner.repo_root) / module)
    full = np.asarray(strategy.generate_positions(panel, contract, **params)["positions"], dtype=float)

    stamps = sorted(panel["timestamp"].unique().to_list())
    cuts = _cut_points(stamps)
    passed, checked = True, []
    for cut in cuts:
        head_panel = panel.filter(pl.col("timestamp") <= cut)
        head = np.asarray(strategy.generate_positions(head_panel, contract, **params)["positions"], dtype=float)
        ok = 0 < head.shape[0] < full.shape[0] and np.array_equal(head, full[: head.shape[0]])
        checked.append(cut.isoformat())
        if not ok:
            passed = False
            break

    registry.record_causality(run_id, code_sha, cfg_digest, data_sha, checked, passed)
    return {"run_id": run_id, "passed": passed, "cuts_checked": checked, "code_files": code_files,
            "code_sha256": code_sha, "config_digest": cfg_digest, "data_sha256": data_sha}
