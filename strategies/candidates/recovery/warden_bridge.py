"""Warden-compatible output bridge for externally authorized 72h order-flow contracts.

The preview and future Warden share exactly the same causal fitting path.
Any attempt without the approved 72-hour contract must fail closed.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from scripts.screen_binance_orderflow_provisional import evaluate
from warden.contracts import EvaluationContract


def warden_output(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    kind: str,
    alpha: float = 10.0,
    rebalance_every: int = 72,
    top_k: int = 5,
) -> dict[str, np.ndarray]:
    if (
        contract.contract_id != "CRYPTO_PERPS_TOP100_ORDERFLOW_1H_H72_V1"
        or contract.label_horizon_bars != 72
        or contract.execution_delay_bars != 1
        or contract.execution_mode != "NEXT_BAR"
        or contract.taker_fee_bps != 4.0
        or contract.slippage_bps != 2.0
        or contract.funding_bps_per_8h != 1.0
        or alpha != 10.0 or rebalance_every != 72 or top_k != 5
    ):
        raise ValueError("Wrong contract or unregistered parameter change")
    frame = panel_df.collect() if isinstance(panel_df, pl.LazyFrame) else panel_df
    report, positions = evaluate(frame, contract, kind, export_positions=True)
    return {"positions": positions, "ic_series": np.asarray(report["ic_series"], dtype=float)}
