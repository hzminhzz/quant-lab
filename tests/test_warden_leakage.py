"""Tests for Ticket TKT-06: Prefix-Invariance Leakage Prover and Feature Cache."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path

import polars as pl
import pytest

from warden.contracts import EvaluationContract, load_contract
from warden.leakage import (
    CrossAssetLeakageError,
    LookaheadLeakageError,
    compute_and_cache_features,
    purged_walk_forward_splits,
    verify_asset_independence,
    verify_fold_isolation,
    verify_prefix_invariance,
)
from warden.registry import ResearchRegistry
from warden.synthetic import generate_canonical_market_panel


@pytest.fixture
def test_dataset() -> tuple[pl.DataFrame, EvaluationContract]:
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    panel = generate_canonical_market_panel().filter(pl.col("timestamp") < contract.val_end)
    return panel, contract


# ---------------------------------------------------------------------------
# Planted Leakage Traps
# ---------------------------------------------------------------------------


class ShiftMinusOnePipeline:
    """Trap 1: Direct future shift (shift(-1))."""

    def compute(self, bars_df: pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
        return bars_df.with_columns(
            future_close=pl.col("close").shift(-1).over("asset")
        )


class GlobalStandardizationPipeline:
    """Trap 2: Full-sample z-score standardization before split."""

    def compute(self, bars_df: pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
        mean_val = bars_df["close"].mean()
        std_val = bars_df["close"].std()
        return bars_df.with_columns(
            global_zscore=(pl.col("close") - mean_val) / std_val
        )


class UngroupedRollingAcrossAssetsPipeline:
    """Trap 3: Ungrouped rolling window bleeding across asset panel boundaries."""

    def compute(self, bars_df: pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
        # Deliberately omits .over("asset")
        return bars_df.with_columns(
            ungrouped_roll=pl.col("close").rolling_mean(window_size=10)
        )


class BackwardFillFuturePipeline:
    """Trap 4: Backward fill of future values into past nulls."""

    def compute(self, bars_df: pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
        # Causal interior missing values (alternate hours null) backward filled from future
        sparse = pl.when(
            pl.col("timestamp").dt.hour() % 2 == 0
        ).then(None).otherwise(pl.col("close"))
        return bars_df.with_columns(
            bfill_feat=sparse.backward_fill().over("asset")
        )


# ---------------------------------------------------------------------------
# Causal Valid Pipelines
# ---------------------------------------------------------------------------


class CausalTimeSeriesPipeline:
    """Valid causal time-series pipeline: rolling returns & vol at t-1."""

    def compute(self, bars_df: pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
        return bars_df.with_columns(
            ret_lag1=pl.col("close").pct_change(1).shift(1).over("asset"),
            vol_lag1=pl.col("close").pct_change(1).rolling_std(window_size=12).shift(1).over("asset"),
        )


class CausalCrossSectionalPipeline:
    """Valid causal cross-sectional pipeline: demeaned relative funding at t-1."""

    def compute(self, bars_df: pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
        # Strictly lagged by 1 bar before cross-sectional demeaning
        return (
            bars_df.with_columns(
                funding_lag1=pl.col("funding_rate").shift(1).over("asset")
            )
            .with_columns(
                cs_funding_demeaned=(
                    pl.col("funding_lag1") - pl.col("funding_lag1").mean().over("timestamp")
                )
            )
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_planted_leakage_traps_fail_100_percent(test_dataset: tuple[pl.DataFrame, EvaluationContract]) -> None:
    panel, contract = test_dataset

    # Trap 1: shift(-1)
    with pytest.raises(LookaheadLeakageError, match="future_close"):
        verify_prefix_invariance(ShiftMinusOnePipeline(), panel, contract)

    # Trap 2: global standardization
    with pytest.raises(LookaheadLeakageError, match="global_zscore"):
        verify_prefix_invariance(GlobalStandardizationPipeline(), panel, contract)

    # Trap 3: backward fill
    with pytest.raises(LookaheadLeakageError, match="bfill_feat"):
        verify_prefix_invariance(BackwardFillFuturePipeline(), panel, contract)

    # Trap 4: cross-asset leakage from ungrouped rolling
    with pytest.raises((LookaheadLeakageError, CrossAssetLeakageError)):
        verify_asset_independence(UngroupedRollingAcrossAssetsPipeline(), panel, contract)


def test_valid_causal_pipelines_pass_verification(test_dataset: tuple[pl.DataFrame, EvaluationContract]) -> None:
    panel, contract = test_dataset

    # Time-series pipeline passes both prefix-invariance and asset independence
    verify_prefix_invariance(CausalTimeSeriesPipeline(), panel, contract)
    verify_asset_independence(CausalTimeSeriesPipeline(), panel, contract)

    # Cross-sectional pipeline passes prefix-invariance
    verify_prefix_invariance(CausalCrossSectionalPipeline(), panel, contract)


def test_content_addressed_feature_cache_hit_speed(
    test_dataset: tuple[pl.DataFrame, EvaluationContract], tmp_path: Path
) -> None:
    panel, contract = test_dataset
    cache_dir = tmp_path / "cache" / "features"
    registry = ResearchRegistry(tmp_path / "reg.db")
    pipeline = CausalTimeSeriesPipeline()

    # First call: computes and caches
    t0 = time.perf_counter()
    feat1 = compute_and_cache_features(pipeline, panel, contract, cache_dir=cache_dir, registry=registry)
    _ = time.perf_counter() - t0
    assert feat1.height == panel.height

    # Second call: hits Parquet cache directly
    t1 = time.perf_counter()
    feat2 = compute_and_cache_features(pipeline, panel, contract, cache_dir=cache_dir, registry=registry)
    t_second = time.perf_counter() - t1

    assert feat2.equals(feat1)
    # Cache hit must clear strict 50ms budget (< 0.050s)
    assert t_second < 0.05, f"Cache hit took {t_second * 1000:.1f}ms (expected < 50ms)"

    # Verify SQLite feature_sets record
    with registry._connect() as conn:
        row = conn.execute("SELECT prefix_invariance_passed, parquet_cache_path FROM feature_sets").fetchone()
        assert row is not None
        assert row[0] == 1
        assert Path(row[1]).is_file()


def test_purged_walk_forward_splits_and_fold_isolation(
    test_dataset: tuple[pl.DataFrame, EvaluationContract]
) -> None:
    panel, _ = test_dataset

    splits = purged_walk_forward_splits(
        n_timestamps=1000,
        n_folds=4,
        min_train_timestamps=200,
        label_horizon=4,
        embargo=2,
    )
    assert len(splits) == 4
    for train_idx, test_idx in splits:
        assert len(train_idx) > 0
        assert len(test_idx) > 0
        assert train_idx[-1] + 4 + 2 <= test_idx[0], "Lookahead leakage across fold boundary!"

    # Test fold isolation model execution
    class DummyModel:
        def fit_predict_fold(
            self, train_df: pl.DataFrame, val_features_df: pl.DataFrame,
            feature_cols: Sequence[str], label_col: str, seed: int = 42
        ) -> pl.DataFrame:
            # Crucial check: label_col must NOT be in val_features_df!
            assert label_col not in val_features_df.columns, "Validation DataFrame must not contain labels!"
            return val_features_df.select("timestamp", "asset").with_columns(
                prediction_value=pl.lit(0.05)
            )

    from datetime import datetime
    cut1 = datetime(2025, 2, 1, tzinfo=UTC)
    cut2 = datetime(2025, 2, 5, tzinfo=UTC)
    train_data = panel.filter(pl.col("timestamp") < cut1).with_columns(target=pl.lit(1.0))
    val_data = panel.filter(pl.col("timestamp") >= cut2).with_columns(target=pl.lit(1.0))

    preds = verify_fold_isolation(
        model=DummyModel(),
        train_df=train_data,
        val_df=val_data,
        feature_cols=["open", "close"],
        label_col="target",
    )
    assert "prediction_value" in preds.columns
