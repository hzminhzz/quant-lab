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
    """Feature at time t depends on future data (t' > t)."""


class CrossAssetLeakageError(ValueError):
    """Asset-isolated feature bleeds cross-sectional information."""


class FeaturePipelineProtocol(Protocol):
    def compute(self, bars_df: pl.LazyFrame | pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
        ...


class WalkForwardModelProtocol(Protocol):
    def fit_predict_fold(self, train_df: pl.DataFrame, val_features_df: pl.DataFrame, feature_cols: Sequence[str], label_col: str, seed: int = 42) -> pl.DataFrame:
        ...


def purged_walk_forward_splits(n_timestamps: int, n_folds: int, min_train_timestamps: int, label_horizon: int, embargo: int) -> list[tuple[np.ndarray, np.ndarray]]:
    test_size = (n_timestamps - min_train_timestamps) // n_folds
    if test_size <= 0:
        raise ValueError("Insufficient timestamps for requested folds.")
    splits = []
    for fold in range(n_folds):
        t_start = min_train_timestamps + fold * test_size
        t_end = t_start + test_size if fold < n_folds - 1 else n_timestamps
        train_end = max(0, t_start - label_horizon - embargo)
        train_idx, test_idx = np.arange(0, train_end, dtype=np.int64), np.arange(t_start, t_end, dtype=np.int64)
        if len(train_idx) > 0 and train_idx[-1] + label_horizon + embargo > test_idx[0]:
            raise LookaheadLeakageError("Purged train set overlaps test set label horizon")
        splits.append((train_idx, test_idx))
    return splits


def call_ml4t_compute_features(data: pl.DataFrame | pl.LazyFrame, features: list[str | dict] | str | Path, **kwargs) -> pl.DataFrame:
    import ml4t.engineer
    sig = inspect.signature(ml4t.engineer.compute_features)
    if "calendar" in sig.parameters and "calendar" not in kwargs:
        kwargs["calendar"] = None
    res = ml4t.engineer.compute_features(data, features, **kwargs)
    return res.collect() if isinstance(res, pl.LazyFrame) else res


def verify_prefix_invariance(pipeline: FeaturePipelineProtocol, bars_df: pl.DataFrame, contract: EvaluationContract, cut_idx: int | None = None) -> None:
    timestamps = sorted(bars_df["timestamp"].unique().to_list())
    if len(timestamps) < 20:
        raise ValueError("Insufficient distinct timestamps")
    t0 = timestamps[len(timestamps) // 2 if cut_idx is None else cut_idx]
    feat_trunc = pipeline.compute(bars_df.filter(pl.col("timestamp") <= t0), contract).sort("timestamp", "asset")

    shocks = {"close": 1.5, "open": 1.5, "high": 1.6, "low": 1.4, "volume": 3.0}
    cols = [pl.when(pl.col("timestamp") > t0).then(pl.col(c) * m).otherwise(pl.col(c)).alias(c) for c, m in shocks.items()]
    cols.append(pl.when(pl.col("timestamp") > t0).then(pl.col("funding_rate") + 0.0100).otherwise(pl.col("funding_rate")).alias("funding_rate"))
    feat_pert = pipeline.compute(bars_df.with_columns(cols), contract).sort("timestamp", "asset").filter(pl.col("timestamp") <= t0)

    for col in [c for c in feat_trunc.columns if c not in {"timestamp", "asset"}]:
        if col not in feat_pert.columns:
            raise LookaheadLeakageError(f"Missing column '{col}' in perturbed output")
        t_s, p_s = feat_trunc[col], feat_pert[col]
        null_diff = t_s.is_null() != p_s.is_null()
        if null_diff.any():
            ts = feat_trunc.filter(null_diff)["timestamp"].min()
            raise LookaheadLeakageError(f"Lookahead leakage in null mask of column '{col}' at timestamp {ts}")
        v_t, v_p = t_s.drop_nulls().to_numpy(), p_s.drop_nulls().to_numpy()
        if len(v_t) != len(v_p) or not np.allclose(v_t, v_p, rtol=1e-10, atol=1e-10):
            diff = np.abs(v_t - v_p)
            ts = feat_trunc.filter(~pl.col(col).is_null())["timestamp"][int(np.argmax(diff))]
            raise LookaheadLeakageError(f"Lookahead leakage in values of column '{col}' at timestamp {ts}")


def verify_asset_independence(pipeline: FeaturePipelineProtocol, bars_df: pl.DataFrame, contract: EvaluationContract, target_asset: str | None = None) -> None:
    assets = bars_df["asset"].unique().to_list()
    if len(assets) < 2:
        return
    shock = assets[0] if target_asset is None else target_asset
    others = [a for a in assets if a != shock]
    f1 = pipeline.compute(bars_df, contract).filter(pl.col("asset").is_in(others)).sort("timestamp", "asset")
    f2 = pipeline.compute(bars_df.with_columns(pl.when(pl.col("asset") == shock).then(pl.col("close") * 2.0).otherwise(pl.col("close")).alias("close")), contract).filter(pl.col("asset").is_in(others)).sort("timestamp", "asset")
    for col in [c for c in f1.columns if c not in {"timestamp", "asset"}]:
        if not np.allclose(f1[col].drop_nulls().to_numpy(), f2[col].drop_nulls().to_numpy(), rtol=1e-10, atol=1e-10):
            raise CrossAssetLeakageError(f"Cross-asset leakage detected in column '{col}' when shocking '{shock}'")


def verify_fold_isolation(model: WalkForwardModelProtocol, train_df: pl.DataFrame, val_df: pl.DataFrame, feature_cols: Sequence[str], label_col: str, label_horizon_bars: int = 4, embargo_bars: int = 2, seed: int = 42) -> pl.DataFrame:
    if train_df["timestamp"].max() >= val_df["timestamp"].min():
        raise LookaheadLeakageError("Train timestamp overlaps validation timestamp")
    val_feat = val_df.drop(label_col) if label_col in val_df.columns else val_df
    preds = model.fit_predict_fold(train_df=train_df, val_features_df=val_feat, feature_cols=feature_cols, label_col=label_col, seed=seed)
    if not {"timestamp", "asset", "prediction_value"}.issubset(set(preds.columns)):
        raise ValueError("Model output missing prediction_value columns")
    return preds


def compute_and_cache_features(pipeline: FeaturePipelineProtocol, bars_df: pl.DataFrame, contract: EvaluationContract, cache_dir: Path = Path("cache/features"), registry: ResearchRegistry | None = None) -> pl.DataFrame:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        source_text = inspect.getsource(pipeline.__class__)
    except (TypeError, OSError):
        source_text = pipeline.__class__.__name__
    try:
        ver = importlib.metadata.version("ml4t-engineer")
    except importlib.metadata.PackageNotFoundError:
        ver = "unknown"

    fhash = hashlib.sha256(canonical_json({"contract": contract.spec_hash(), "source": hashlib.sha256(source_text.encode()).hexdigest(), "ver": ver}).encode()).hexdigest()[:16]
    cpath = cache_dir / f"{fhash}.parquet"
    if registry is not None:
        with registry._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO feature_sets (feature_hash, contract_id, feature_names_json, ml4t_engineer_version, prefix_invariance_passed, parquet_cache_path, created_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (fhash, contract.contract_id, "[]", ver, str(cpath), 1.0),
            )
    if cpath.is_file():
        return pl.read_parquet(cpath)
    verify_prefix_invariance(pipeline, bars_df, contract)
    feats = pipeline.compute(bars_df, contract).sort("timestamp", "asset")
    feats.write_parquet(cpath)
    return feats
