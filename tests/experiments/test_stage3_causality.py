"""Causality evidence for the AF30 strategies that carry Stage-3 rows.

Audit check 3 accepts any `prefix_invariance_passed` row for the *contract*, so a rules
strategy passes only if ML feature evidence happens to exist on the same data. These
tests check the property check 3 is trying to establish, directly on the strategies:
positions computed on a truncated panel must equal the prefix of positions computed on
the full panel. Anything that peeks past the truncation shows up as a mismatch.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.contracts import load_contract
from warden.runner import load_module_from_path

CASES = [
    ("INDICES_1D_V1", "strategies/candidates/carver_daily/s02_buy_hold_risk_scaled.py",
     {"vol_target": 0.16, "vol_window": 1260, "min_samples": 60, "per_instrument_cap": 0.5, "warmup": 60}),
    ("INDICES_1D_V1", "strategies/candidates/carver_daily/s03_buy_hold_variable_risk.py",
     {"vol_target": 0.16, "vol_window": 60, "min_samples": 2, "per_instrument_cap": 0.5, "warmup": 60}),
    ("INDICES_1D_V1", "strategies/candidates/carver_daily/s04_buy_hold_portfolio_scaling.py",
     {"vol_target": 0.16, "vol_window": 60, "min_samples": 2, "idm": 2.30, "per_instrument_cap": 0.5, "warmup": 60}),
    ("MULTI_ASSET_1D_V1", "strategies/candidates/carver_daily/s01_buy_hold_single.py", {"warmup": 1}),
    ("MULTI_ASSET_1D_V1", "strategies/candidates/carver_daily/s05_slow_trend_long_only.py",
     {"fast": 64, "slow": 256, "vol_span": 35, "vol_target": 0.16, "per_instrument_cap": 0.5, "warmup": 1}),
    ("MULTI_ASSET_1D_V1", "strategies/candidates/carver_breakout_daily.py",
     {"channel_window": 80, "smooth_span": 20, "scalar": 0.73, "vol_window": 60, "vol_target": 0.20,
      "rebalance_bars": 1, "buffer_fraction": 0.10, "stop_multiple": 1000.0, "per_instrument_cap": 0.5}),
]


def _panel(contract) -> pl.DataFrame:
    return pl.read_parquet(contract.exploratory_data_path).sort("timestamp", "asset")


def _positions(module_path: str, contract, panel: pl.DataFrame, params: dict) -> np.ndarray:
    mod = load_module_from_path(Path(module_path))
    return np.asarray(mod.generate_positions(panel, contract, **params)["positions"], dtype=float)


@pytest.mark.parametrize("contract_id,module_path,params", CASES,
                         ids=[Path(c[1]).stem for c in CASES])
def test_positions_are_prefix_invariant(contract_id, module_path, params):
    contract = load_contract(Path(f"contracts/{contract_id.lower()}.yaml"))
    panel = _panel(contract)
    cut = int(len(panel["timestamp"].unique()) * 0.7)
    cutoff = sorted(panel["timestamp"].unique())[cut]

    full = _positions(module_path, contract, panel, params)
    head = _positions(module_path, contract, panel.filter(pl.col("timestamp") <= cutoff), params)

    assert head.shape[0] <= full.shape[0]
    np.testing.assert_allclose(
        head, full[: head.shape[0]], rtol=0, atol=0,
        err_msg=f"{Path(module_path).stem} reads data after the truncation point")
