"""Order-flow ATS 72h linear model for future approved Warden contract.

No synthetic trade_count or quote_volume fallback; no double-lag. This
researcher adaptation supersedes the unsupported experiment spec for
HYP-2026-050 only after a new governed lineage is authorized.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from strategies.candidates.recovery.binance_orderflow_factors import build_features
from strategies.candidates.recovery.warden_bridge import warden_output
from warden.contracts import EvaluationContract


class ATSFeaturePipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != "CRYPTO_PERPS_TOP100_ORDERFLOW_1H_H72_V1":
            raise ValueError("Wrong governed 72h contract")
        return build_features(bars_df, "ats")


pipeline = ATSFeaturePipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    alpha: float = 10.0,
    rebalance_every: int = 72,
    top_k: int = 5,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError(f"Unreviewed parameter overrides: {sorted(kwargs)}")
    return warden_output(panel_df, contract, "ats", alpha, rebalance_every, top_k)
