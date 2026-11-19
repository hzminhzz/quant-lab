"""Sampled causality evidence for the AF30 strategies that carry Stage-3 rows.

Audit check 3 accepts any `prefix_invariance_passed` row for the *contract*, so a rules
strategy passes only if unrelated ML feature evidence happens to exist on the same data.
These tests sample the property check 3 is trying to establish, on the exact recorded
configuration of each Stage-3 run: positions computed on a truncated panel must equal the
prefix of positions computed on the full panel. Several cut points are sampled, including
cuts inside the validation window, because a cut before `val_start` exercises none of the
bars the verdict was computed on.

This is sampled evidence at the tested cut points, not a proof of causality over the whole
series.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.contracts import load_contract
from warden.strategy_loader import load_module_from_path

pytestmark = pytest.mark.skipif(
    os.environ.get("AF30_LIVE_CHECKS") != "1",
    reason="set AF30_LIVE_CHECKS=1 to run; needs the live registry and market-data panels")

# Exact Stage-3 runs; module and parameters are read back from the registry rather than retyped.
RUN_IDS = ["RUN-HYP112-002", "RUN-HYP113-002", "RUN-HYP114-002",
           "RUN-HYP095-002", "RUN-HYP099-001", "RUN-HYP094-002"]

# Fractions of the panel plus absolute dates inside the 2023-01..2025-07 validation window.
CUT_FRACTIONS = (0.5, 0.7, 0.9)
CUT_DATES = ("2023-06-30", "2024-07-01")

DB = Path("registry/registry.db")


def _recorded(run_id: str):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT contract_id, strategy_module, config_json FROM trials WHERE run_id=?", (run_id,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None, f"{run_id} not in the registry"
    return row[0], row[1], json.loads(row[2])


def _positions(module_path: str, contract, panel: pl.DataFrame, params: dict) -> np.ndarray:
    mod = load_module_from_path(Path(module_path))
    return np.asarray(mod.generate_positions(panel, contract, **params)["positions"], dtype=float)


def _cut_points(panel: pl.DataFrame) -> list:
    stamps = sorted(panel["timestamp"].unique().to_list())
    cuts = [stamps[int(len(stamps) * f) - 1] for f in CUT_FRACTIONS]
    for d in CUT_DATES:
        inside = [s for s in stamps if s.isoformat().startswith(d)]
        if inside:
            cuts.append(inside[-1])
    return sorted(set(cuts))


@pytest.mark.parametrize("run_id", RUN_IDS)
def test_positions_are_prefix_invariant_at_sampled_cuts(run_id):
    contract_id, module_path, params = _recorded(run_id)
    contract = load_contract(Path(f"contracts/{contract_id.lower()}.yaml"))
    panel = pl.read_parquet(contract.exploratory_data_path).sort("timestamp", "asset")

    full = _positions(module_path, contract, panel, params)
    tested = 0
    for cutoff in _cut_points(panel):
        head_panel = panel.filter(pl.col("timestamp") <= cutoff)
        head = _positions(module_path, contract, head_panel, params)
        assert 0 < head.shape[0] < full.shape[0], f"degenerate cut at {cutoff}"
        np.testing.assert_allclose(
            head, full[: head.shape[0]], rtol=0, atol=0,
            err_msg=(f"{Path(module_path).stem} reads data after the truncation point "
                     f"({cutoff.isoformat()}) on the recorded config"))
        tested += 1
    assert tested >= 4, "expected at least four sampled cut points"
