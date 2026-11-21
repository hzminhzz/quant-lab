"""Actual taker quote imbalance 72h linear model for authorized Warden.

No candle-body or price proxy for aggressive trade direction. Strict
72h PIT targets, previous selection debt retained.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from strategies.candidates.recovery.binance_orderflow_factors import build_features
from strategies.candidates.recovery.warden_bridge import warden_output
from warden.contracts import EvaluationContract


class NTRFeaturePipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != "CRYPTO_PERPS_TOP100_ORDERFLOW_1H_H72_V1":
            raise ValueError("Wrong governed 72h contract")
        return build_features(bars_df, "ntr")


pipeline = NTRFeaturePipeline()


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
    return warden_output(panel_df, contract, "ntr", alpha, rebalance_every, top_k)
