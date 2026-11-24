"""Prefix-invariance leakage prover, fold isolation, and feature cache."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any, Protocol

import numpy as np
import polars as pl

from warden.contracts import FREQUENCY_BAR_DURATION, EvaluationContract


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


def _resolve_bar_duration(
    train_df: pl.DataFrame,
    val_df: pl.DataFrame,
    contract: EvaluationContract | None,
    frequency: str | None,
    bar_duration: timedelta | None,
) -> tuple[timedelta, str]:
    if bar_duration is not None:
        if bar_duration <= timedelta(0):
            raise ValueError("bar_duration must be positive")
        return bar_duration, "explicit"
    if frequency is not None and contract is not None and frequency != contract.frequency:
        raise ValueError(
            f"frequency '{frequency}' conflicts with contract frequency '{contract.frequency}'"
        )
    freq = frequency if frequency is not None else (contract.frequency if contract is not None else None)
    if freq is not None:
        if freq not in FREQUENCY_BAR_DURATION:
            raise ValueError(f"Unsupported frequency '{freq}'")
        return FREQUENCY_BAR_DURATION[freq], freq
    train_ts = sorted(set(train_df["timestamp"].to_list()))
    val_ts = sorted(set(val_df["timestamp"].to_list()))
    deltas = [b - a for ts_list in (train_ts, val_ts) for a, b in pairwise(ts_list) if b > a]
    if not deltas:
        raise ValueError(
            "Cannot infer bar duration from single-timestamp splits without contract, frequency, or bar_duration"
        )
    return min(deltas), "inferred"


def _check_ordered_boundary(train_df: pl.DataFrame, val_df: pl.DataFrame, col: str, label: str):
    max_train = train_df[col].max()
    min_val = val_df[col].min()
    if max_train is None or min_val is None:
        raise LookaheadLeakageError(f"Null {label} values in '{col}'")
    if max_train >= min_val:
        raise LookaheadLeakageError(
            f"{label} overlap on '{col}': train max ({max_train}) >= val min ({min_val})"
        )
    return max_train, min_val


def verify_fold_isolation(
    model: WalkForwardModelProtocol,
    train_df: pl.DataFrame,
    val_df: pl.DataFrame,
    feature_cols: Sequence[str],
    label_col: str,
    label_horizon_bars: int | None = None,
    embargo_bars: int | None = None,
    seed: int = 42,
    contract: EvaluationContract | None = None,
    frequency: str | None = None,
    bar_duration: timedelta | None = None,
    panel_timestamps: Sequence | None = None,
    session_col: str | None = None,
) -> pl.DataFrame:
    if train_df.is_empty() or val_df.is_empty():
        raise LookaheadLeakageError("Train and validation DataFrames must be non-empty")
    horizon = (
        contract.label_horizon_bars
        if label_horizon_bars is None and contract is not None
        else (0 if label_horizon_bars is None else label_horizon_bars)
    )
    embargo = (
        contract.embargo_bars
        if embargo_bars is None and contract is not None
        else (0 if embargo_bars is None else embargo_bars)
    )
    if horizon < 0 or embargo < 0:
        raise ValueError("label_horizon_bars and embargo_bars must be non-negative")

    max_train_ts, min_val_ts = _check_ordered_boundary(train_df, val_df, "timestamp", "Timestamp")

    if session_col is not None:
        if session_col not in train_df.columns or session_col not in val_df.columns:
            raise ValueError(f"session_col '{session_col}' missing from train_df or val_df")
        _check_ordered_boundary(train_df, val_df, session_col, "Session")

    required_gap = horizon + embargo
    if required_gap > 0:
        if panel_timestamps is not None:
            ts_seq = panel_timestamps.to_list() if isinstance(panel_timestamps, pl.Series) else list(panel_timestamps)
            unique_ts = sorted(set(ts_seq))
            train_idx = bisect_left(unique_ts, max_train_ts)
            val_idx = bisect_left(unique_ts, min_val_ts)
            if (
                train_idx >= len(unique_ts)
                or unique_ts[train_idx] != max_train_ts
                or val_idx >= len(unique_ts)
                or unique_ts[val_idx] != min_val_ts
            ):
                raise ValueError(
                    f"Split boundaries ({max_train_ts}, {min_val_ts}) must exist in panel_timestamps"
                )
            bar_gap = val_idx - train_idx
            if bar_gap < required_gap:
                raise LookaheadLeakageError(
                    f"Fold purge/embargo violation: bar gap {bar_gap} < {required_gap} bars"
                )
        elif isinstance(max_train_ts, (int, float)) and not isinstance(max_train_ts, bool):
            if max_train_ts + required_gap > min_val_ts:
                raise LookaheadLeakageError(
                    f"Fold purge/embargo violation: max_train ({max_train_ts}) + {required_gap} > min_val ({min_val_ts})"
                )
        elif isinstance(max_train_ts, (datetime, date)):
            step, label = _resolve_bar_duration(train_df, val_df, contract, frequency, bar_duration)
            if (min_val_ts - max_train_ts) < step * required_gap:
                raise LookaheadLeakageError(
                    f"Fold purge/embargo violation ({label}): gap {min_val_ts - max_train_ts} < {step * required_gap} ({required_gap} bars)"
                )
        else:
            raise TypeError(f"Unsupported timestamp type: {type(max_train_ts).__name__}")

    drop_cols = [c for c in val_df.columns if c == label_col or any(k in c.lower() for k in ("label", "target", "forward", "fut_"))]
    val_feat = val_df.drop(drop_cols)
    preds = model.fit_predict_fold(train_df=train_df, val_features_df=val_feat, feature_cols=feature_cols, label_col=label_col, seed=seed)
    if not {"timestamp", "asset", "prediction_value"}.issubset(set(preds.columns)):
        raise ValueError("Model output missing prediction_value columns")
    return preds


def _apply_future_shock(bars_df: pl.DataFrame, cutoff_ts: Any) -> pl.DataFrame:
    epoch_mod = (pl.col("timestamp").dt.epoch("s") % 97) * 0.005 + 0.10
    shocks = {"close": 1.5, "open": 1.5, "high": 1.6, "low": 1.4, "volume": 3.0}
    cols = [
        pl.when(pl.col("timestamp") > cutoff_ts)
        .then(pl.col(c) * (m + epoch_mod))
        .otherwise(pl.col(c))
        .alias(c)
        for c, m in shocks.items()
        if c in bars_df.columns
    ]
    if "funding_rate" in bars_df.columns:
        cols.append(
            pl.when(pl.col("timestamp") > cutoff_ts)
            .then(pl.col("funding_rate") + 0.0100 + epoch_mod * 0.01)
            .otherwise(pl.col("funding_rate"))
            .alias("funding_rate")
        )
    return bars_df.with_columns(cols)


def _assert_preds_close(
    left: pl.DataFrame, right: pl.DataFrame, message: str
) -> None:
    if left.height != right.height or not np.allclose(
        left["prediction_value"].to_numpy(),
        right["prediction_value"].to_numpy(),
        rtol=1e-9,
        atol=1e-9,
        equal_nan=True,
    ):
        raise LookaheadLeakageError(message)
    if (
        "target_weight" in left.columns
        and "target_weight" in right.columns
        and not np.allclose(
            left["target_weight"].to_numpy(),
            right["target_weight"].to_numpy(),
            rtol=1e-9,
            atol=1e-9,
            equal_nan=True,
        )
    ):
        raise LookaheadLeakageError(f"{message} (target_weight)")


def verify_model_prefix_invariance(
    mod: Any,
    bars_df: pl.DataFrame,
    contract: EvaluationContract,
    params: dict[str, Any] | None = None,
) -> None:
    """Prove prefix invariance on both pipeline features and CausalModelStrategy fold predictions."""
    import bisect

    from warden.protocols import (
        iter_causal_model_folds,
        prepare_causal_model_dataset,
        resolve_protocol_target,
        run_causal_model_strategy,
    )

    target = resolve_protocol_target(mod)
    norm_df = (
        bars_df.rename({"symbol": "asset"})
        if "symbol" in bars_df.columns and "asset" not in bars_df.columns
        else bars_df
    ).sort("timestamp", "asset")

    verify_prefix_invariance(target.pipeline, norm_df, contract)

    ts = sorted(norm_df["timestamp"].unique().to_list())
    assets = sorted(norm_df["asset"].unique().to_list())
    v_start = bisect.bisect_left(ts, contract.val_start)
    v_stop = bisect.bisect_left(ts, contract.val_end)
    if v_stop <= v_start + 4:
        v_start = max(16, len(ts) // 3)
        v_stop = len(ts)
    v_slice = slice(v_start, v_stop)

    full_out = run_causal_model_strategy(mod, norm_df, contract, ts, assets, v_slice, dict(params or {}))
    full_preds = full_out["predictions"].sort("timestamp", "asset")

    val_ts = ts[v_start:v_stop]
    cut_points = [val_ts[len(val_ts) // 3], val_ts[(2 * len(val_ts)) // 3]]
    for t0 in cut_points:
        pert_df = _apply_future_shock(norm_df, t0)
        pert_out = run_causal_model_strategy(mod, pert_df, contract, ts, assets, v_slice, dict(params or {}))
        pert_preds = pert_out["predictions"].sort("timestamp", "asset")

        f_pref = full_preds.filter(pl.col("timestamp") <= t0).sort("timestamp", "asset")
        p_pref = pert_preds.filter(pl.col("timestamp") <= t0).sort("timestamp", "asset")
        if f_pref.height != p_pref.height:
            raise LookaheadLeakageError(
                f"Model prediction row count changed at cutoff {t0} under future perturbation"
            )
        if f_pref.height > 0:
            _assert_preds_close(
                f_pref,
                p_pref,
                f"Lookahead leakage in model predictions before cutoff {t0}",
            )

    # Also test estimator invariance when future validation rows are truncated within a fold.
    _, model_df, _, feature_cols, valid_train_mask = prepare_causal_model_dataset(
        mod, norm_df, contract, ts
    )
    estimator = target.build_estimator(dict(params or {}))
    for train_df, val_feat in iter_causal_model_folds(
        model_df, feature_cols, valid_train_mask, ts, contract, v_start, v_stop
    ):
        fold_ts = sorted(val_feat["timestamp"].unique().to_list())
        if len(fold_ts) < 4:
            continue
        t_mid = fold_ts[len(fold_ts) // 2]
        p_full = estimator.fit_predict_fold(train_df, val_feat, feature_cols, "forward_label", seed=42).filter(pl.col("timestamp") <= t_mid).sort("timestamp", "asset")
        p_trunc = estimator.fit_predict_fold(train_df, val_feat.filter(pl.col("timestamp") <= t_mid), feature_cols, "forward_label", seed=42).sort("timestamp", "asset")
        _assert_preds_close(
            p_full,
            p_trunc,
            f"Model fit_predict_fold predictions at <= {t_mid} depend on future validation rows",
        )
        break


def _fills_signature(fills: Sequence[Any], cutoff_ts: Any) -> list[tuple[Any, str, str, float]]:
    return [
        (f.timestamp, str(f.asset), str(getattr(f.side, "value", f.side)), round(float(f.quantity), 8))
        for f in fills
        if f.timestamp <= cutoff_ts
    ]


def verify_rules_strategy_prefix_invariance(
    mod: Any,
    bars_df: pl.DataFrame,
    contract: EvaluationContract,
    params: dict[str, Any] | None = None,
    *,
    policy_cfg: dict[str, Any] | None = None,
) -> None:
    """Prove prefix invariance on CausalRulesStrategy's actual Engine orders/positions."""
    import bisect
    import tempfile
    from dataclasses import replace as dc_replace
    from pathlib import Path

    from warden.engine_sim import extract_execution_policy, run_engine_strategy
    from warden.protocols import resolve_protocol_target

    target = resolve_protocol_target(mod)
    norm_df = (
        bars_df.rename({"symbol": "asset"})
        if "symbol" in bars_df.columns and "asset" not in bars_df.columns
        else bars_df
    ).sort("timestamp", "asset")
    if hasattr(mod, "pipeline") and callable(getattr(mod.pipeline, "compute", None)):
        verify_prefix_invariance(mod.pipeline, norm_df, contract)

    ts = sorted(norm_df["timestamp"].unique().to_list())
    assets = sorted(norm_df["asset"].unique().to_list())
    if len(ts) < 20:
        raise ValueError("Insufficient distinct timestamps for prefix-invariance verification")

    v_start = bisect.bisect_left(ts, contract.val_start)
    v_stop = bisect.bisect_left(ts, contract.val_end)
    if v_stop <= v_start + 6:
        v_start = max(4, len(ts) // 4)
        v_stop = len(ts)

    policy = extract_execution_policy(policy_cfg or {}, dict(params or {}))
    full_sim = run_engine_strategy(
        norm_df,
        target.configure_strategy(contract, dict(params or {})),
        ts,
        assets,
        contract,
        slice(v_start, v_stop),
        **policy,
    )

    span = v_stop - v_start
    cut_indices = sorted({v_start + max(4, span // 3), v_start + max(5, (2 * span) // 3)})
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        for idx, c_idx in enumerate(cut_indices):
            if c_idx >= v_stop:
                continue
            t0 = ts[c_idx]
            n_prefix = c_idx + 1 - v_start
            trunc_df = norm_df.filter(pl.col("timestamp") <= t0)
            trunc_pq = tmp_root / f"trunc_{idx}.parquet"
            trunc_df.write_parquet(trunc_pq)
            trunc_contract = dc_replace(contract, exploratory_data_path=str(trunc_pq))
            ts_trunc = ts[: c_idx + 1]
            trunc_sim = run_engine_strategy(
                trunc_df,
                target.configure_strategy(trunc_contract, dict(params or {})),
                ts_trunc,
                assets,
                trunc_contract,
                slice(v_start, c_idx + 1),
                **policy,
            )

            pert_df = _apply_future_shock(norm_df, t0)
            pert_pq = tmp_root / f"pert_{idx}.parquet"
            pert_df.write_parquet(pert_pq)
            pert_contract = dc_replace(contract, exploratory_data_path=str(pert_pq))
            pert_sim = run_engine_strategy(
                pert_df,
                target.configure_strategy(pert_contract, dict(params or {})),
                ts,
                assets,
                pert_contract,
                slice(v_start, v_stop),
                **policy,
            )

            full_sigs = _fills_signature(full_sim.backtest_result.fills, t0)
            trunc_sigs = _fills_signature(trunc_sim.backtest_result.fills, t0)
            pert_sigs = _fills_signature(pert_sim.backtest_result.fills, t0)
            if full_sigs != trunc_sigs or full_sigs != pert_sigs:
                raise LookaheadLeakageError(
                    f"Lookahead leakage detected in CausalRulesStrategy fills at cutoff {t0}"
                )
            if not np.allclose(full_sim.positions[:n_prefix], trunc_sim.positions, rtol=1e-8, atol=1e-8) or not np.allclose(
                full_sim.positions[:n_prefix], pert_sim.positions[:n_prefix], rtol=1e-8, atol=1e-8
            ):
                raise LookaheadLeakageError(
                    f"Lookahead leakage detected in CausalRulesStrategy positions at cutoff {t0}"
                )


def verify_rules_strategy_asset_independence(
    mod: Any,
    bars_df: pl.DataFrame,
    contract: EvaluationContract,
    params: dict[str, Any] | None = None,
    target_asset: str | None = None,
    *,
    policy_cfg: dict[str, Any] | None = None,
) -> None:
    """Verify per-asset rules strategies do not leak signals/orders across assets."""
    import bisect

    from warden.engine_sim import extract_execution_policy, run_engine_strategy
    from warden.protocols import resolve_protocol_target

    target = resolve_protocol_target(mod)
    if getattr(mod, "cross_sectional", False) or getattr(mod, "portfolio_coupled", False) or getattr(target, "cross_sectional", False) or getattr(target, "portfolio_coupled", False):
        return

    norm_df = (
        bars_df.rename({"symbol": "asset"})
        if "symbol" in bars_df.columns and "asset" not in bars_df.columns
        else bars_df
    ).sort("timestamp", "asset")
    if hasattr(mod, "per_asset_pipeline") and callable(getattr(mod.per_asset_pipeline, "compute", None)):
        verify_asset_independence(mod.per_asset_pipeline, norm_df, contract, target_asset=target_asset)

    assets = sorted(norm_df["asset"].unique().to_list())
    if len(assets) < 2:
        return
    shock_asset = assets[0] if target_asset is None else target_asset
    ts = sorted(norm_df["timestamp"].unique().to_list())
    v_start = bisect.bisect_left(ts, contract.val_start)
    v_stop = bisect.bisect_left(ts, contract.val_end)
    if v_stop <= v_start + 4:
        v_start = max(4, len(ts) // 4)
        v_stop = len(ts)

    policy = extract_execution_policy(policy_cfg or {}, dict(params or {}))
    base_sim = run_engine_strategy(
        norm_df,
        target.configure_strategy(contract, dict(params or {})),
        ts,
        assets,
        contract,
        slice(v_start, v_stop),
        **policy,
    )
    shocked_df = norm_df.with_columns(
        [
            pl.when(pl.col("asset") == shock_asset).then(pl.col(c) * 2.0).otherwise(pl.col(c)).alias(c)
            for c in ("open", "high", "low", "close")
            if c in norm_df.columns
        ]
    )
    shock_sim = run_engine_strategy(
        shocked_df,
        target.configure_strategy(contract, dict(params or {})),
        ts,
        assets,
        contract,
        slice(v_start, v_stop),
        **policy,
    )

    for j, asset in enumerate(assets):
        if asset == shock_asset:
            continue
        if not np.allclose(base_sim.positions[:, j], shock_sim.positions[:, j], rtol=1e-4, atol=1e-4):
            raise CrossAssetLeakageError(
                f"Cross-asset leakage detected in '{asset}' when shocking '{shock_asset}'"
            )


from warden_evidence.feature_cache import compute_and_cache_features  # noqa: F401
