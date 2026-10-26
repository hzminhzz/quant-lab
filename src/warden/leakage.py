"""Prefix-invariance leakage prover, fold isolation, and feature cache."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
import polars as pl

from warden.contracts import EvaluationContract, canonical_json
from warden.registry import ResearchRegistry


class LookaheadLeakageError(ValueError): pass
class CrossAssetLeakageError(ValueError): pass


class FeaturePipelineProtocol(Protocol):
    def compute(self, bars_df: pl.LazyFrame | pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
        ...


class WalkForwardModelProtocol(Protocol):
    def fit_predict_fold(self, train_df: pl.DataFrame, val_features_df: pl.DataFrame, feature_cols: Sequence[str], label_col: str, seed: int = 42) -> pl.DataFrame:
        ...


def purged_walk_forward_splits(n_timestamps: int, n_folds: int, min_train_timestamps: int, label_horizon: int, embargo: int, execution_delay: int = 0) -> list[tuple[np.ndarray, np.ndarray]]:
    """Purged/embargoed folds over the *complete* information span of a forward label.

    A training label observed at t is only realised at t + execution_delay + label_horizon,
    so the span purged here is label_horizon + execution_delay. Passing them separately makes
    the earlier off-by-delay behaviour impossible to reintroduce silently.
    """
    span = label_horizon + execution_delay
    test_size = (n_timestamps - min_train_timestamps) // n_folds
    if test_size <= 0:
        raise ValueError("Insufficient timestamps for requested folds.")
    splits = []
    for fold in range(n_folds):
        t_start = min_train_timestamps + fold * test_size
        t_end = t_start + test_size if fold < n_folds - 1 else n_timestamps
        train_end = max(0, t_start - span - embargo)
        train_idx, test_idx = np.arange(0, train_end, dtype=np.int64), np.arange(t_start, t_end, dtype=np.int64)
        if len(train_idx) > 0 and train_idx[-1] + span + embargo > test_idx[0]:
            raise LookaheadLeakageError("Purged train set overlaps test set label horizon")
        splits.append((train_idx, test_idx))
    return splits


def executable_label_span(contract: EvaluationContract) -> int:
    """Bars from decision to label exit: execution delay plus the holding horizon."""
    return contract.execution_delay_bars + contract.label_horizon_bars


def compute_open_forward_label(open_mat: np.ndarray, delay: int, horizon: int) -> np.ndarray:
    """Causal open-to-open forward cross-sectionally demeaned returns matching runner execution.

    Label at t enters at Open[t+delay] and exits at Open[t+delay+horizon]: no close-to-open
    gap before the fill, and no second lag on top of the evaluator's execution delay.
    """
    n_bars, n_assets = open_mat.shape
    forward_label = np.zeros((n_bars, n_assets), dtype=float)
    max_t = n_bars - delay - horizon
    for t in range(max_t):
        entry_open = open_mat[t + delay]
        exit_open = open_mat[t + delay + horizon]
        fwd = np.divide(exit_open - entry_open, entry_open, out=np.zeros_like(entry_open), where=entry_open > 0)
        forward_label[t] = fwd - np.mean(fwd)
    return forward_label

def call_ml4t_compute_features(data: pl.DataFrame | pl.LazyFrame, features: list[str | dict] | str | Path, **kwargs) -> pl.DataFrame:
    import ml4t.engineer
    sig = inspect.signature(ml4t.engineer.compute_features)
    if "calendar" in sig.parameters and "calendar" not in kwargs:
        kwargs["calendar"] = None
    res = ml4t.engineer.compute_features(data, features, **kwargs)
    return res.collect() if isinstance(res, pl.LazyFrame) else res


def verify_prefix_invariance(pipeline: FeaturePipelineProtocol, bars_df: pl.DataFrame, contract: EvaluationContract, cut_idx: int | None = None) -> None:
    """Sampled leakage detection at 3 cut points (default 1/4, 1/2, 3/4), tolerance 1e-10.

    This is adversarial sampling, not a formal proof: a feature that only differs
    after the last cut point is not detected. Deterministic fixtures cover boundary,
    global-normalisation, future-ratio and null-mask classes; new classes need new tests.
    """
    timestamps = sorted(bars_df["timestamp"].unique().to_list())
    if len(timestamps) < 20:
        raise ValueError("Insufficient distinct timestamps")
    cut_indices = [cut_idx] if cut_idx is not None else [len(timestamps) // 4, len(timestamps) // 2, 3 * len(timestamps) // 4]

    for c_idx in cut_indices:
        t0 = timestamps[c_idx]
        feat_trunc = pipeline.compute(bars_df.filter(pl.col("timestamp") <= t0), contract).sort("timestamp", "asset")

        # Per-timestamp varying shock destroys future return-ratio invariance
        epoch_mod = (pl.col("timestamp").dt.epoch("s") % 97) * 0.005 + 0.10
        shocks = {"close": 1.5, "open": 1.5, "high": 1.6, "low": 1.4, "volume": 3.0}
        cols = [pl.when(pl.col("timestamp") > t0).then(pl.col(c) * (m + epoch_mod)).otherwise(pl.col(c)).alias(c) for c, m in shocks.items()]
        cols.append(pl.when(pl.col("timestamp") > t0).then(pl.col("funding_rate") + 0.0100 + epoch_mod * 0.01).otherwise(pl.col("funding_rate")).alias("funding_rate"))

        feat_pert = pipeline.compute(bars_df.with_columns(cols), contract).sort("timestamp", "asset").filter(pl.col("timestamp") <= t0)
        feat_full_pref = pipeline.compute(bars_df, contract).sort("timestamp", "asset").filter(pl.col("timestamp") <= t0)

        col_union = set(feat_trunc.columns) | set(feat_pert.columns) | set(feat_full_pref.columns)
        for col in [c for c in sorted(col_union) if c not in {"timestamp", "asset"}]:
            if col not in feat_trunc.columns or col not in feat_pert.columns or col not in feat_full_pref.columns:
                raise LookaheadLeakageError(f"Missing column '{col}' across prefix-invariance comparison frames")
            t_s, p_s, f_s = feat_trunc[col], feat_pert[col], feat_full_pref[col]
            null_diff = (t_s.is_null() != p_s.is_null()) | (t_s.is_null() != f_s.is_null())
            if null_diff.any():
                ts = feat_trunc.filter(null_diff)["timestamp"].min()
                raise LookaheadLeakageError(f"Lookahead leakage in null mask of column '{col}' at timestamp {ts}")
            v_t, v_p, v_f = t_s.drop_nulls().to_numpy(), p_s.drop_nulls().to_numpy(), f_s.drop_nulls().to_numpy()
            if len(v_t) != len(v_p) or len(v_t) != len(v_f) or not np.allclose(v_t, v_p, rtol=1e-10, atol=1e-10) or not np.allclose(v_t, v_f, rtol=1e-10, atol=1e-10):
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


def verify_fold_isolation(model: WalkForwardModelProtocol, train_df: pl.DataFrame, val_df: pl.DataFrame, feature_cols: Sequence[str], label_col: str, label_horizon_bars: int = 0, embargo_bars: int = 0, seed: int = 42, execution_delay_bars: int = 0) -> pl.DataFrame:
    from datetime import datetime, timedelta
    max_train_ts = train_df["timestamp"].max()
    min_val_ts = val_df["timestamp"].min()
    if max_train_ts >= min_val_ts:
        raise LookaheadLeakageError(f"Train timestamp ({max_train_ts}) overlaps validation timestamp ({min_val_ts})")
    required_gap = label_horizon_bars + embargo_bars + execution_delay_bars
    if required_gap > 0:
        if isinstance(max_train_ts, (int, float)) and max_train_ts + required_gap > min_val_ts:
            raise LookaheadLeakageError(f"Fold purge/embargo violation: max_train ({max_train_ts}) + {required_gap} > min_val ({min_val_ts})")
        elif isinstance(max_train_ts, datetime) and (min_val_ts - max_train_ts) < timedelta(hours=required_gap):
            raise LookaheadLeakageError(f"Fold purge/embargo violation: gap {min_val_ts - max_train_ts} < {required_gap} bars")
    drop_cols = [c for c in val_df.columns if c == label_col or any(k in c.lower() for k in ("label", "target", "forward", "fut_"))]
    val_feat = val_df.drop(drop_cols)
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
    ver = "0.2.0"
    fhash = hashlib.sha256(canonical_json({"contract": contract.spec_hash(), "source": hashlib.sha256(source_text.encode()).hexdigest(), "ver": ver}).encode()).hexdigest()[:16]
    cpath = cache_dir / f"{fhash}.parquet"
    if cpath.is_file():
        return pl.read_parquet(cpath)
    verify_prefix_invariance(pipeline, bars_df, contract)
    feats = pipeline.compute(bars_df, contract).sort("timestamp", "asset")
    feats.write_parquet(cpath)
    if registry is not None:
        import time
        with registry._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO feature_sets (feature_hash, contract_id, feature_names_json, ml4t_engineer_version, prefix_invariance_passed, parquet_cache_path, created_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (fhash, contract.contract_id, "[]", ver, str(cpath), time.time()),
            )
    return feats
