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
    test_dataset: tuple[pl.DataFrame, EvaluationContract], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel, contract = test_dataset
    cache_dir = tmp_path / "cache" / "features"
    registry = ResearchRegistry(tmp_path / "reg.db")
    pipeline = CausalTimeSeriesPipeline()

    # First call: computes and caches
    t0 = time.perf_counter()
    feat1 = compute_and_cache_features(pipeline, panel, contract, cache_dir=cache_dir, registry=registry)
    cold_seconds = time.perf_counter() - t0
    assert feat1.height == panel.height

    # A hit must not recompute features or revalidate the prover.
    import warden.leakage as leakage

    calls = {"compute": 0, "verify": 0}
    real_compute, real_verify = CausalTimeSeriesPipeline.compute, leakage.verify_prefix_invariance

    def spy_compute(self, *args, **kwargs):
        calls["compute"] += 1
        return real_compute(self, *args, **kwargs)

    def spy_verify(*args, **kwargs):
        calls["verify"] += 1
        return real_verify(*args, **kwargs)

    # Patch the class, not the instance: instance attributes enter feature_identity's params.
    monkeypatch.setattr(CausalTimeSeriesPipeline, "compute", spy_compute)
    monkeypatch.setattr(leakage, "verify_prefix_invariance", spy_verify)

    # Second call: hits Parquet cache directly. Best of three warm hits clears the
    # 50ms budget without reporting scheduler noise; the call counters are the real proof.
    hits = []
    for _ in range(3):
        t1 = time.perf_counter()
        feat2 = compute_and_cache_features(pipeline, panel, contract, cache_dir=cache_dir, registry=registry)
        hits.append(time.perf_counter() - t1)

    assert feat2.equals(feat1)
    assert calls == {"compute": 0, "verify": 0}, calls
    # A hit must be a small fraction of the cold recompute even on a loaded machine.
    budget = max(0.05, cold_seconds / 16)
    assert min(hits) < budget, (
        f"Cache hit took {min(hits) * 1000:.1f}ms (budget {budget * 1000:.1f}ms, "
        f"cold {cold_seconds:.3f}s)"
    )

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


class _FoldCausalPipeline:
    def compute(self, bars_df: pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
        return bars_df.sort("timestamp", "asset").with_columns(
            feat=pl.col("close").pct_change(1).shift(1).over("asset")
        )


class _LaterFoldLeakyEstimator:
    """Causal in the first fold; peeks at the next validation bar from ``leak_from`` on."""

    def __init__(self, leak_from) -> None:
        self.leak_from = leak_from
        self.calls = 0

    def fit_predict_fold(
        self, train_df: pl.DataFrame, val_features_df: pl.DataFrame,
        feature_cols: Sequence[str], label_col: str, seed: int = 42
    ) -> pl.DataFrame:
        self.calls += 1
        if val_features_df["timestamp"].min() >= self.leak_from:
            return (
                val_features_df.sort("timestamp", "asset")
                .with_columns(
                    prediction_value=pl.col(feature_cols[0]).shift(-1).over("asset").fill_null(0.0)
                )
                .select("timestamp", "asset", "prediction_value")
            )
        return (
            val_features_df.with_columns(prediction_value=pl.col(feature_cols[0]))
            .select("timestamp", "asset", "prediction_value")
        )


def test_within_fold_lookahead_in_later_folds_is_detected(
    test_dataset: tuple[pl.DataFrame, EvaluationContract], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #3: a leak planted only in fold 2+ must not hide behind fold 0."""
    import warden.protocols as protocols
    from types import SimpleNamespace

    from warden.leakage import verify_model_prefix_invariance

    panel, contract = test_dataset
    timestamps = sorted(panel["timestamp"].unique().to_list())[:60]
    panel = panel.filter(pl.col("timestamp").is_in(timestamps)).sort("timestamp", "asset")
    fold_a = timestamps[30:35]
    fold_b = timestamps[40:45]
    created: list[_LaterFoldLeakyEstimator] = []

    def build_estimator(params: dict) -> _LaterFoldLeakyEstimator:
        estimator = _LaterFoldLeakyEstimator(leak_from=fold_b[0])
        created.append(estimator)
        return estimator

    mod = SimpleNamespace(pipeline=_FoldCausalPipeline(), build_estimator=build_estimator)

    def fake_prepare(*args, **kwargs):
        model_df = pl.DataFrame(
            {"timestamp": fold_a + fold_b, "asset": ["AAA"] * 10, "feat": [1.0] * 10}
        )
        return None, model_df, None, ["feat"], None

    def fake_folds(*args, **kwargs):
        for fold_ts in (fold_a, fold_b):
            val = pl.DataFrame(
                {"timestamp": fold_ts, "asset": ["AAA"] * len(fold_ts), "feat": [1.0] * len(fold_ts)}
            )
            yield val, val

    empty = pl.DataFrame(
        {"timestamp": [], "asset": [], "prediction_value": []},
        schema={"timestamp": pl.Datetime("us", "UTC"), "asset": pl.String, "prediction_value": pl.Float64},
    )
    monkeypatch.setattr(protocols, "prepare_causal_model_dataset", fake_prepare)
    monkeypatch.setattr(protocols, "iter_causal_model_folds", fake_folds)
    monkeypatch.setattr(protocols, "run_causal_model_strategy",
                        lambda *args, **kwargs: {"predictions": empty})

    with pytest.raises(LookaheadLeakageError, match=r"fold 1"):
        verify_model_prefix_invariance(mod, panel, contract, {})
    # Both folds were exercised (two fit_predict_fold calls each: full and truncated).
    assert created[-1].calls == 4
