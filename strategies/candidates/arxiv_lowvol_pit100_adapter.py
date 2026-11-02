"""Feature-only adapter for PIT low-volatility Stage 1 confirmation.

The frozen HYP-ARXIV-XS-LOWVOL model and its fold-local Ridge predictions
remain unmodified. The PIT source includes funding provenance datetime/bool
columns not consumed by the original model. Warden's prefix-invariance checker
expects numeric feature columns; isolate the *two* original numeric features
for leakage testing rather than changing the protected evaluator or dataset.

This adapter is *not* a new strategy, factor, cost model or research family.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import polars as pl

from warden.contracts import EvaluationContract

FEATURES = ("realized_volatility_24h", "liquidity_per_hour")


def _source_module() -> Any:
    filename = Path(__file__).with_name("arxiv_lowvol_xs_1h.py")
    spec = importlib.util.spec_from_file_location("_arxiv_pit_frozen_lowvol", filename)
    if spec is None or spec.loader is None:
        raise ImportError("Frozen low-volatility implementation unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


baseline = _source_module()


class NumericPITFeatures:
    def compute(
        self,
        bars_df: pl.DataFrame | pl.LazyFrame,
        contract: EvaluationContract,
    ) -> pl.DataFrame:
        result = baseline.pipeline.compute(bars_df, contract)
        # Keep only model inputs. In particular, exclude original funding
        # event_timestamp (datetime) and coverage flags from verifier inputs.
        return result.select("timestamp", "asset", *FEATURES)


pipeline = NumericPITFeatures()


def generate_positions(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    **params: Any,
) -> dict[str, Any]:
    # Exactly the same model, weights, labels, folds, train-only fitting
    # and output ICs as the frozen source model.
    return baseline.generate_positions(panel_df, contract, **params)
