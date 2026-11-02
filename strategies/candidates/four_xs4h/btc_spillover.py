"""Frozen one-factor btc_spillover 4h cross-sectional Warden candidate.

Fixed 4h next-bar horizon, 5/5 market-neutral target unless the sign-only
candidate uses equal-weight long/short sign cohorts. Not a paper replica.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract

# Warden loads candidates from a file path without adding repo root to
# sys.path. Load the pinned sibling implementation explicitly.
_common_spec = spec_from_file_location("_four_xs4h_common", Path(__file__).with_name("common.py"))
if _common_spec is None or _common_spec.loader is None:
    raise ImportError("Missing local source-complete factor implementation")
_common = module_from_spec(_common_spec)
_common_spec.loader.exec_module(_common)
run_factor, source_features = _common.run_factor, _common.source_features


class FeaturePipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame,
                contract: EvaluationContract) -> pl.DataFrame:
        if contract.contract_id != "CRYPTO_PERPS_TOP100_1H_V1":
            raise ValueError("Wrong frozen Warden source contract")
        return source_features(bars_df, "btc_spillover")


pipeline = FeaturePipeline()


def generate_positions(
    panel_df: pl.DataFrame | pl.LazyFrame,
    contract: EvaluationContract,
    rebalance_every: int = 4,
    top_k: int = 5,
    **kwargs: object,
) -> dict[str, np.ndarray]:
    if kwargs:
        raise ValueError("Unapproved configuration override")
    return run_factor(panel_df, contract, "btc_spillover",
                      rebalance_every=rebalance_every, top_k=top_k)
