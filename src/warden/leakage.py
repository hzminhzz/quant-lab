"""Prefix-invariance leakage prover, fold isolation, and feature cache."""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract, canonical_json
from warden.registry import ResearchRegistry


class LookaheadLeakageError(ValueError):
    """Raised when a feature computation at time t depends on future data (t' > t)."""


class CrossAssetLeakageError(ValueError):
    """Raised when asset-isolated features bleed cross-sectional information."""


class FeaturePipelineProtocol(Protocol):
    """Protocol for causal, deterministic feature pipelines."""

    def compute(self, bars_df: pl.LazyFrame | pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
        ...


class WalkForwardModelProtocol(Protocol):
    """Protocol for models fit within purged walk-forward cross-validation folds."""

    def fit_predict_fold(
        self,
        train_df: pl.DataFrame,
        val_features_df: pl.DataFrame,  # Labels stripped by warden!
        feature_cols: Sequence[str],
        label_col: str,
        seed: int = 42,
    ) -> pl.DataFrame:
        ...


def purged_walk_forward_splits(
    n_timestamps: int,
    n_folds: int,
    min_train_timestamps: int,
    label_horizon: int,
    embargo: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Generate walk-forward cross-validation splits with purging and embargo."""
    test_size = (n_timestamps - min_train_timestamps) // n_folds
    if test_size <= 0:
        raise ValueError("Insufficient timestamps for requested folds and min_train_timestamps.")
    splits = []
    for fold in range(n_folds):
        test_start = min_train_timestamps + fold * test_size
        test_end = test_start + test_size if fold < n_folds - 1 else n_timestamps
        train_end_purged = max(0, test_start - label_horizon - embargo)
        train_idx = np.arange(0, train_end_purged, dtype=np.int64)
        test_idx = np.arange(test_start, test_end, dtype=np.int64)
        if len(train_idx) > 0 and train_idx[-1] + label_horizon + embargo > test_idx[0]:
            raise LookaheadLeakageError("Purged train set overlaps test set label horizon")
        splits.append((train_idx, test_idx))
    return splits


def call_ml4t_compute_features(
    data: pl.DataFrame | pl.LazyFrame,
    features: list[str | dict] | str | Path,
    **kwargs,
) -> pl.DataFrame:
    """Version-safe adapter for ml4t.engineer.compute_features."""
    import ml4t.engineer

    sig = inspect.signature(ml4t.engineer.compute_features)
    if "calendar" in sig.parameters and "calendar" not in kwargs:
        kwargs["calendar"] = None
    res = ml4t.engineer.compute_features(data, features, **kwargs)
    return res.collect() if isinstance(res, pl.LazyFrame) else res


def verify_prefix_invariance(
    pipeline: FeaturePipelineProtocol,
    bars_df: pl.DataFrame,
    contract: EvaluationContract,
    cut_idx: int | None = None,
) -> None:
    """Assert that features at t <= t0 are bit-for-bit invariant to future data at t > t0."""
    timestamps = sorted(bars_df["timestamp"].unique().to_list())
    if len(timestamps) < 20:
        raise ValueError("Insufficient distinct timestamps for prefix-invariance testing")
    idx = len(timestamps) // 2 if cut_idx is None else cut_idx
    t0 = timestamps[idx]

    # Run on truncated dataset
    df_truncated = bars_df.filter(pl.col("timestamp") <= t0)
    feat_truncated = pipeline.compute(df_truncated, contract).sort("timestamp", "asset")

    # Run on perturbed full dataset with aggressive future price, volume, and funding shocks
    df_perturbed = bars_df.with_columns(
        pl.when(pl.col("timestamp") > t0)
        .then(pl.col("close") * 1.5)
        .otherwise(pl.col("close"))
        .alias("close"),
        pl.when(pl.col("timestamp") > t0)
        .then(pl.col("open") * 1.5)
        .otherwise(pl.col("open"))
        .alias("open"),
        pl.when(pl.col("timestamp") > t0)
        .then(pl.col("high") * 1.6)
        .otherwise(pl.col("high"))
        .alias("high"),
        pl.when(pl.col("timestamp") > t0)
        .then(pl.col("low") * 1.4)
        .otherwise(pl.col("low"))
        .alias("low"),
        pl.when(pl.col("timestamp") > t0)
        .then(pl.col("volume") * 3.0)
        .otherwise(pl.col("volume"))
        .alias("volume"),
        pl.when(pl.col("timestamp") > t0)
        .then(pl.col("funding_rate") + 0.0100)
        .otherwise(pl.col("funding_rate"))
        .alias("funding_rate"),
    )
    feat_perturbed = pipeline.compute(df_perturbed, contract).sort("timestamp", "asset")
    feat_perturbed_past = feat_perturbed.filter(pl.col("timestamp") <= t0)

    # Compare columns
    key_cols = {"timestamp", "asset"}
    feature_cols = [c for c in feat_truncated.columns if c not in key_cols]
    for col in feature_cols:
        if col not in feat_perturbed_past.columns:
            raise LookaheadLeakageError(f"Column '{col}' missing from perturbed feature output")

        trunc_s = feat_truncated[col]
        pert_s = feat_perturbed_past[col]

        # 1. Null mask must match identically
        null_diff = trunc_s.is_null() != pert_s.is_null()
        if null_diff.any():
            divergent = feat_truncated.filter(null_diff)["timestamp"].min()
            raise LookaheadLeakageError(
                f"Lookahead leakage in null mask of column '{col}' at timestamp {divergent}"
            )

        # 2. Values at non-null rows must match within numerical tolerance
        trunc_vals = trunc_s.drop_nulls().to_numpy()
        pert_vals = pert_s.drop_nulls().to_numpy()
        if len(trunc_vals) != len(pert_vals):
            raise LookaheadLeakageError(f"Row count mismatch in column '{col}'")
        if not np.allclose(trunc_vals, pert_vals, rtol=1e-10, atol=1e-10):
            diff = np.abs(trunc_vals - pert_vals)
            bad_idx = int(np.argmax(diff))
            divergent_ts = feat_truncated.filter(~pl.col(col).is_null())["timestamp"][bad_idx]
            raise LookaheadLeakageError(
                f"Lookahead leakage in values of column '{col}' at timestamp {divergent_ts}: "
                f"max diff={diff[bad_idx]:.2e}"
            )


def verify_asset_independence(
    pipeline: FeaturePipelineProtocol,
    bars_df: pl.DataFrame,
    contract: EvaluationContract,
    target_asset: str | None = None,
) -> None:
    """Verify that single-asset shocks do not bleed into other assets (cross-asset leakage)."""
    assets = bars_df["asset"].unique().to_list()
    if len(assets) < 2:
        return
    shock_asset = assets[0] if target_asset is None else target_asset
    other_assets = [a for a in assets if a != shock_asset]

    feat_baseline = pipeline.compute(bars_df, contract).filter(pl.col("asset").is_in(other_assets)).sort("timestamp", "asset")
    df_shocked = bars_df.with_columns(
        pl.when(pl.col("asset") == shock_asset)
        .then(pl.col("close") * 2.0)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    feat_shocked = pipeline.compute(df_shocked, contract).filter(pl.col("asset").is_in(other_assets)).sort("timestamp", "asset")

    for col in [c for c in feat_baseline.columns if c not in {"timestamp", "asset"}]:
        v1 = feat_baseline[col].drop_nulls().to_numpy()
        v2 = feat_shocked[col].drop_nulls().to_numpy()
        if not np.allclose(v1, v2, rtol=1e-10, atol=1e-10):
            raise CrossAssetLeakageError(
                f"Cross-asset leakage detected in column '{col}' when shocking asset '{shock_asset}'"
            )


def verify_fold_isolation(
    model: WalkForwardModelProtocol,
    train_df: pl.DataFrame,
    val_df: pl.DataFrame,
    feature_cols: Sequence[str],
    label_col: str,
    label_horizon_bars: int = 4,
    embargo_bars: int = 2,
    seed: int = 42,
) -> pl.DataFrame:
    """Verify chronological fold disjointness and physically strip labels before predict."""
    max_train_ts = train_df["timestamp"].max()
    min_val_ts = val_df["timestamp"].min()

    # Chronological order assertion
    if max_train_ts >= min_val_ts:
        raise LookaheadLeakageError(f"Train timestamp {max_train_ts} >= Val timestamp {min_val_ts}")

    # Strip label column from validation frame before passing to model
    val_features_only = val_df.drop(label_col) if label_col in val_df.columns else val_df

    # Verify model cannot see label_col
    preds_df = model.fit_predict_fold(
        train_df=train_df,
        val_features_df=val_features_only,
        feature_cols=feature_cols,
        label_col=label_col,
        seed=seed,
    )
    expected_cols = {"timestamp", "asset", "prediction_value"}
    if not expected_cols.issubset(set(preds_df.columns)):
        raise ValueError(f"Model output must contain columns {expected_cols}")
    return preds_df


def compute_and_cache_features(
    pipeline: FeaturePipelineProtocol,
    bars_df: pl.DataFrame,
    contract: EvaluationContract,
    cache_dir: Path = Path("cache/features"),
    registry: ResearchRegistry | None = None,
) -> pl.DataFrame:
    """Verify prefix invariance and return cached or newly materialized feature matrix."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Derive deterministic pipeline identity
    try:
        source_text = inspect.getsource(pipeline.__class__)
    except (TypeError, OSError):
        source_text = pipeline.__class__.__name__

    try:
        engineer_ver = importlib.metadata.version("ml4t-engineer")
    except importlib.metadata.PackageNotFoundError:
        engineer_ver = "unknown"

    hash_payload = canonical_json({
        "contract_spec_hash": contract.spec_hash(),
        "pipeline_source_hash": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "ml4t_engineer_version": engineer_ver,
    })
    feature_hash = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()[:16]
    cache_path = cache_dir / f"{feature_hash}.parquet"

    if cache_path.is_file():
        return pl.read_parquet(cache_path)

    # Verify causality before materializing
    verify_prefix_invariance(pipeline, bars_df, contract)
    features_df = pipeline.compute(bars_df, contract).sort("timestamp", "asset")
    features_df.write_parquet(cache_path)

    # Optionally register in SQLite feature_sets table
    if registry is not None:
        feature_names = [c for c in features_df.columns if c not in {"timestamp", "asset"}]
        with registry._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO feature_sets
                (feature_hash, contract_id, feature_names_json, ml4t_engineer_version,
                 prefix_invariance_passed, parquet_cache_path, created_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    feature_hash,
                    contract.contract_id,
                    canonical_json(feature_names),
                    engineer_ver,
                    str(cache_path),
                    float(Path(cache_path).stat().st_mtime),
                ),
            )

    return features_df
