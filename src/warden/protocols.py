"""Inverted causal strategy protocols: CausalModelStrategy and CausalRulesStrategy.

Legacy ``generate_positions`` modules are frozen for read-only historical replay only.
New ``ACTIVE`` hypotheses must implement either ``CausalModelStrategy`` or
``CausalRulesStrategy`` so Warden owns WalkForwardCV splitting, fold scaling,
forward-return labeling, IC evaluation, and ml4t.backtest.Engine execution.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import numpy as np
import polars as pl

if TYPE_CHECKING:
    from ml4t.backtest import Strategy

    from warden.contracts import EvaluationContract
    from warden.leakage import FeaturePipelineProtocol, WalkForwardModelProtocol

StrategyProtocolKind = Literal["causal_model", "causal_rules", "legacy"]

_RAW_PANEL_COLUMNS = frozenset(
    {
        "timestamp",
        "asset",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "funding_rate",
        "bid",
        "ask",
        "session_date",
    }
)
_FORBIDDEN_FEATURE_KEYWORDS = ("label", "target", "forward", "fut_")


class LegacyStrategyProtocolError(ValueError):
    """Raised when an ACTIVE hypothesis attempts to use frozen generate_positions."""


@runtime_checkable
class CausalModelStrategy(Protocol):
    """Warden-owned WalkForwardCV, label construction, IC, and Engine simulation."""

    pipeline: FeaturePipelineProtocol

    def build_estimator(self, params: dict[str, Any]) -> WalkForwardModelProtocol: ...


@runtime_checkable
class CausalRulesStrategy(Protocol):
    """Warden-owned ml4t.backtest.Engine execution for causal rule strategies."""

    def required_symbols(self, params: dict[str, Any]) -> list[str] | None: ...

    def configure_strategy(
        self, contract: EvaluationContract, params: dict[str, Any]
    ) -> Strategy: ...


def _is_causal_model(obj: Any) -> bool:
    pipe = getattr(obj, "pipeline", None)
    return (
        pipe is not None
        and callable(getattr(pipe, "compute", None))
        and callable(getattr(obj, "build_estimator", None))
    )


def _is_causal_rules(obj: Any) -> bool:
    return callable(getattr(obj, "required_symbols", None)) and callable(
        getattr(obj, "configure_strategy", None)
    )


def resolve_protocol_target(mod: Any) -> Any:
    """Return module-level ``strategy`` object when present, otherwise the module itself."""
    candidate = getattr(mod, "strategy", None)
    if candidate is not None and (
        _is_causal_model(candidate) or _is_causal_rules(candidate)
    ):
        return candidate
    return mod


def detect_strategy_protocol(mod: Any) -> StrategyProtocolKind:
    """Identify whether a loaded strategy module implements a causal protocol or legacy."""
    target = resolve_protocol_target(mod)
    if _is_causal_model(target):
        return "causal_model"
    if _is_causal_rules(target):
        return "causal_rules"
    if callable(getattr(mod, "generate_positions", None)):
        return "legacy"
    raise LegacyStrategyProtocolError(
        "Strategy module must implement CausalModelStrategy (pipeline + build_estimator) "
        "or CausalRulesStrategy (required_symbols + configure_strategy)."
    )


def enforce_active_strategy_protocol(
    mod: Any,
    *,
    hypothesis_status: str,
    strategy_family: str | None = None,
    allow_legacy_replay: bool = False,
) -> StrategyProtocolKind:
    """Fail closed on legacy generate_positions for ACTIVE hypotheses and check family consistency."""
    kind = detect_strategy_protocol(mod)
    if kind == "legacy" and hypothesis_status == "ACTIVE" and not allow_legacy_replay:
        raise LegacyStrategyProtocolError(
            "Legacy generate_positions() is frozen for read-only historical replay only; "
            "ACTIVE hypotheses must implement CausalModelStrategy or CausalRulesStrategy."
        )
    if strategy_family is not None:
        if kind == "causal_model" and strategy_family not in {
            "cross_sectional_ml",
            "carry_rv",
            "meta_overlay",
        }:
            raise LegacyStrategyProtocolError(
                f"CausalModelStrategy requires strategy_family='cross_sectional_ml' "
                f"(or 'carry_rv'/'meta_overlay'), got '{strategy_family}'."
            )
        if kind == "causal_rules" and strategy_family not in {
            "rules_ts",
            "carry_rv",
            "meta_overlay",
        }:
            raise LegacyStrategyProtocolError(
                f"CausalRulesStrategy requires strategy_family='rules_ts' "
                f"(or 'carry_rv'/'meta_overlay'), got '{strategy_family}'."
            )
    return kind


def resolve_effective_params(
    mod: Any,
    protocol: StrategyProtocolKind,
    params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge CausalRulesStrategy.required_symbols(params) into effective panel filter params."""
    effective = dict(params or {})
    if protocol == "causal_rules":
        req_syms = resolve_protocol_target(mod).required_symbols(effective)
        if req_syms is not None and "target_symbols" not in effective:
            effective["target_symbols"] = req_syms
    return effective


def load_and_enforce_strategy_protocol(
    strat_file: Path,
    *,
    hypothesis_status: str,
    strategy_family: str | None = None,
    allow_legacy_replay: bool = False,
) -> tuple[Any, StrategyProtocolKind]:
    """Load a strategy module and fail closed if an ACTIVE hypothesis uses legacy protocol."""
    from warden.strategy_loader import load_module_from_path

    mod = load_module_from_path(strat_file)
    kind = enforce_active_strategy_protocol(
        mod,
        hypothesis_status=hypothesis_status,
        strategy_family=strategy_family,
        allow_legacy_replay=allow_legacy_replay,
    )
    return mod, kind


def compute_execution_forward_returns(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ts: Sequence[Any],
) -> pl.DataFrame:
    """Compute causal execution-aligned forward returns on the unique timestamp grid.

    Uses ``ts`` (unique sorted timestamps) rather than raw stacked row indices or
    per-asset ``shift(-h)`` so ragged listings/delistings never jump across gaps.
    """
    df = panel_df.rename({"symbol": "asset"}) if "symbol" in panel_df.columns and "asset" not in panel_df.columns else panel_df
    delay = int(contract.execution_delay_bars)
    horizon = int(contract.label_horizon_bars)

    ts_grid = pl.DataFrame({"timestamp": list(ts), "_ts_idx": list(range(len(ts)))})
    if contract.execution_mode == "QUOTE_SIDE":
        px_expr = (0.5 * (pl.col("bid") + pl.col("ask"))).cast(pl.Float64)
    else:
        px_expr = pl.col("open").cast(pl.Float64)

    base = (
        df.select("timestamp", "asset", px_expr.alias("_exec_px"))
        .join(ts_grid, on="timestamp", how="inner")
        .with_columns(
            (pl.col("_ts_idx") + delay).alias("_entry_idx"),
            (pl.col("_ts_idx") + delay + horizon).alias("_exit_idx"),
        )
    )
    lookup = base.select(
        pl.col("asset"),
        pl.col("_ts_idx"),
        pl.col("_exec_px"),
    )
    labeled = (
        base.join(
            lookup.rename({"_ts_idx": "_entry_idx", "_exec_px": "_entry_px"}),
            on=["asset", "_entry_idx"],
            how="left",
        )
        .join(
            lookup.rename({"_ts_idx": "_exit_idx", "_exec_px": "_exit_px"}),
            on=["asset", "_exit_idx"],
            how="left",
        )
        .with_columns(
            pl.when(
                pl.col("_entry_px").is_not_null()
                & pl.col("_exit_px").is_not_null()
                & (pl.col("_entry_px") > 0.0)
            )
            .then((pl.col("_exit_px") - pl.col("_entry_px")) / pl.col("_entry_px"))
            .otherwise(None)
            .alias("forward_return")
        )
        .with_columns(
            (pl.col("forward_return") - pl.col("forward_return").mean().over("timestamp"))
            .alias("forward_label")
        )
        .select("timestamp", "asset", "forward_return", "forward_label")
        .sort("timestamp", "asset")
    )
    return labeled


class _UniqueTimestampPanelCV:
    """WalkForwardCV wrapper slicing strictly on unique sorted timestamps."""

    def __init__(
        self,
        *,
        panel_timestamps: Sequence[Any],
        unique_ts: Sequence[Any],
        val_start_idx: int,
        val_end_idx: int,
        n_folds: int,
        total_gap: int,
        valid_train_mask: np.ndarray,
    ) -> None:
        self._panel_ts = list(panel_timestamps)
        self._unique_ts = list(unique_ts)
        self._val_start_idx = int(val_start_idx)
        self._val_end_idx = int(val_end_idx)
        self._n_folds = int(n_folds)
        self._total_gap = int(total_gap)
        self._valid_train_mask = np.asarray(valid_train_mask, dtype=bool)

    def split(
        self,
        X: Any,
        y: Any = None,
        groups: Any = None,
    ) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        _ = (X, y, groups)
        cv_ts = self._unique_ts[: self._val_end_idx]
        n_val = len(cv_ts) - self._val_start_idx
        if n_val <= 0:
            raise ValueError("Validation slice contains no timestamps for WalkForwardCV.")
        test_size = n_val // self._n_folds
        if test_size <= 0:
            raise ValueError("Insufficient validation timestamps for requested n_folds.")
        if self._val_start_idx - self._total_gap <= 0:
            raise ValueError(
                f"Insufficient pre-validation timestamps ({self._val_start_idx}) "
                f"for purge+embargo gap ({self._total_gap})."
            )

        from ml4t.diagnostic.splitters import WalkForwardCV

        cv = WalkForwardCV(
            n_splits=self._n_folds,
            train_size=self._val_start_idx,
            test_size=test_size,
            gap=max(0, self._total_gap - 1),
            expanding=True,
            consecutive=True,
        )
        ts_frame = pl.DataFrame({"timestamp": cv_ts})

        rows_by_ts_idx: dict[int, list[int]] = {}
        ts_lookup = {t: idx for idx, t in enumerate(cv_ts)}
        for row_idx, ts_val in enumerate(self._panel_ts):
            t_idx = ts_lookup.get(ts_val)
            if t_idx is not None:
                rows_by_ts_idx.setdefault(t_idx, []).append(row_idx)

        splits = list(cv.split(ts_frame))
        for fold_idx, (ts_tr, ts_te) in enumerate(splits):
            if fold_idx == len(splits) - 1 and int(ts_te[-1]) < len(cv_ts) - 1:
                ts_te = np.arange(int(ts_te[0]), len(cv_ts), dtype=np.intp)
            if len(ts_tr) == 0 or len(ts_te) == 0:
                raise ValueError(f"Empty timestamp fold {fold_idx} in WalkForwardCV.")
            if int(ts_te[0]) - int(ts_tr[-1]) < self._total_gap:
                raise ValueError(
                    f"Fold {fold_idx} gap {int(ts_te[0]) - int(ts_tr[-1])} < {self._total_gap}"
                )

            tr_rows: list[int] = []
            for t_idx in ts_tr:
                for r_idx in rows_by_ts_idx.get(int(t_idx), ()):
                    if self._valid_train_mask[r_idx]:
                        tr_rows.append(r_idx)
            te_rows: list[int] = []
            for t_idx in ts_te:
                te_rows.extend(rows_by_ts_idx.get(int(t_idx), ()))

            if not tr_rows or not te_rows:
                raise ValueError(
                    f"Fold {fold_idx} produced empty stacked rows (train={len(tr_rows)}, test={len(te_rows)})."
                )
            yield np.asarray(tr_rows, dtype=np.intp), np.asarray(te_rows, dtype=np.intp)


def resolve_feature_columns(feat_df: pl.DataFrame, pipeline: Any) -> list[str]:
    """Resolve causal feature columns and reject label-like column names."""
    declared = getattr(pipeline, "feature_cols", None)
    if declared is not None:
        cols = [str(c) for c in declared]
    else:
        numeric_dtypes = {
            pl.Int8, pl.Int16, pl.Int32, pl.Int64,
            pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
            pl.Float32, pl.Float64,
        }
        cols = [
            c
            for c, dtype in feat_df.schema.items()
            if c not in _RAW_PANEL_COLUMNS and dtype in numeric_dtypes
        ]
    if not cols:
        raise ValueError("CausalModelStrategy pipeline produced no numeric feature columns.")
    missing = [c for c in cols if c not in feat_df.columns]
    if missing:
        raise ValueError(f"Pipeline declared feature_cols missing from output: {missing}")
    bad = [
        c for c in cols if any(k in c.lower() for k in _FORBIDDEN_FEATURE_KEYWORDS)
    ]
    if bad:
        raise ValueError(f"Pipeline feature columns cannot contain label/target keywords: {bad}")
    return cols


def _build_panel_presence_and_matrix(
    oos_preds_df: pl.DataFrame,
    panel_df: pl.DataFrame,
    ts: Sequence[Any],
    assets: Sequence[str],
    value_col: str,
) -> tuple[list[np.ndarray], np.ndarray]:
    n_bars, n_assets = len(ts), len(assets)
    ts_map = {t: i for i, t in enumerate(ts)}
    asset_map = {a: j for j, a in enumerate(assets)}

    present_by_t: list[np.ndarray] = [np.zeros(n_assets, dtype=bool) for _ in range(n_bars)]
    for row_ts, row_asset in panel_df.select("timestamp", "asset").iter_rows():
        ti = ts_map.get(row_ts)
        aj = asset_map.get(row_asset)
        if ti is not None and aj is not None:
            present_by_t[ti][aj] = True

    val_mat = np.full((n_bars, n_assets), np.nan, dtype=float)
    for row_ts, row_asset, val in oos_preds_df.select("timestamp", "asset", value_col).iter_rows():
        ti = ts_map.get(row_ts)
        aj = asset_map.get(row_asset)
        if ti is not None and aj is not None and val is not None:
            fval = float(val)
            if np.isfinite(fval):
                val_mat[ti, aj] = fval
    return present_by_t, val_mat


def _apply_rebalance_schedule(
    oos_preds_df: pl.DataFrame,
    panel_df: pl.DataFrame,
    ts: Sequence[Any],
    assets: Sequence[str],
    *,
    val_start_idx: int,
    val_end_idx: int,
    rebalance_every: int,
    value_col: str,
    row_weight_fn: Any,
) -> np.ndarray:
    n_bars, n_assets = len(ts), len(assets)
    positions = np.zeros((n_bars, n_assets), dtype=float)
    present_by_t, val_mat = _build_panel_presence_and_matrix(
        oos_preds_df, panel_df, ts, assets, value_col
    )
    reb = max(1, int(rebalance_every))
    for t in range(val_start_idx, val_end_idx):
        active_mask = present_by_t[t]
        prev = positions[t - 1] if t > 0 else np.zeros(n_assets, dtype=float)
        if (t - val_start_idx) % reb != 0:
            positions[t] = np.where(active_mask, prev, 0.0)
            continue
        new_weights = row_weight_fn(val_mat[t], active_mask, n_assets)
        positions[t] = new_weights if new_weights is not None else np.where(active_mask, prev, 0.0)
    return positions


def _predictions_to_positions(
    oos_preds_df: pl.DataFrame,
    panel_df: pl.DataFrame,
    ts: Sequence[Any],
    assets: Sequence[str],
    *,
    val_start_idx: int,
    val_end_idx: int,
    rebalance_every: int,
    top_k: int,
) -> np.ndarray:
    k_req = max(1, int(top_k))

    def _top_k_weights(scores: np.ndarray, active_mask: np.ndarray, n_assets: int) -> np.ndarray | None:
        valid_idx = np.flatnonzero(active_mask & np.isfinite(scores))
        if len(valid_idx) < 2:
            return None
        k = min(k_req, len(valid_idx) // 2)
        order = valid_idx[np.argsort(scores[valid_idx])]
        w = np.zeros(n_assets, dtype=float)
        w[order[-k:]] = 0.5 / float(k)
        w[order[:k]] = -0.5 / float(k)
        return w

    return _apply_rebalance_schedule(
        oos_preds_df,
        panel_df,
        ts,
        assets,
        val_start_idx=val_start_idx,
        val_end_idx=val_end_idx,
        rebalance_every=rebalance_every,
        value_col="prediction_value",
        row_weight_fn=_top_k_weights,
    )


def _target_weights_to_positions(
    oos_preds_df: pl.DataFrame,
    panel_df: pl.DataFrame,
    ts: Sequence[Any],
    assets: Sequence[str],
    *,
    val_start_idx: int,
    val_end_idx: int,
    rebalance_every: int,
) -> np.ndarray:
    """Map direct estimator target_weight columns to bounded, active-masked position weights."""

    def _bounded_weights(w_raw: np.ndarray, active_mask: np.ndarray, _n_assets: int) -> np.ndarray | None:
        valid = active_mask & np.isfinite(w_raw)
        if not valid.any():
            return None
        w = np.where(valid, w_raw, 0.0)
        gross = float(np.sum(np.abs(w)))
        if gross > 1.0 + 1e-9:
            w = w / gross
        return w

    return _apply_rebalance_schedule(
        oos_preds_df,
        panel_df,
        ts,
        assets,
        val_start_idx=val_start_idx,
        val_end_idx=val_end_idx,
        rebalance_every=rebalance_every,
        value_col="target_weight",
        row_weight_fn=_bounded_weights,
    )


def prepare_causal_model_dataset(
    mod: Any,
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ts: Sequence[Any],
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, list[str], np.ndarray]:
    """Compute features, execution forward returns, and finite-feature ML dataset."""
    target = resolve_protocol_target(mod)
    if not _is_causal_model(target):
        raise LegacyStrategyProtocolError("Strategy does not implement CausalModelStrategy.")

    norm_panel = (
        panel_df.rename({"symbol": "asset"})
        if "symbol" in panel_df.columns and "asset" not in panel_df.columns
        else panel_df
    ).sort("timestamp", "asset")

    feat_df = target.pipeline.compute(norm_panel, contract)
    if isinstance(feat_df, pl.LazyFrame):
        feat_df = feat_df.collect()
    if "symbol" in feat_df.columns and "asset" not in feat_df.columns:
        feat_df = feat_df.rename({"symbol": "asset"})
    feat_df = feat_df.sort("timestamp", "asset")

    feature_cols = resolve_feature_columns(feat_df, target.pipeline)
    fwd_df = compute_execution_forward_returns(norm_panel, contract, ts)

    joined = feat_df.select("timestamp", "asset", *feature_cols).join(
        fwd_df, on=["timestamp", "asset"], how="inner"
    ).sort("timestamp", "asset")

    finite_feat_expr = pl.all_horizontal(
        [pl.col(c).is_not_null() & pl.col(c).cast(pl.Float64).is_finite() for c in feature_cols]
    )
    model_df = joined.filter(finite_feat_expr)
    if model_df.is_empty():
        raise ValueError("No rows with finite features remain after warmup filtering.")

    valid_train_mask = (
        model_df["forward_label"].is_not_null()
        & model_df["forward_label"].cast(pl.Float64).is_finite()
    ).to_numpy()
    return norm_panel, model_df, fwd_df, feature_cols, valid_train_mask


def iter_causal_model_folds(
    model_df: pl.DataFrame,
    feature_cols: Sequence[str],
    valid_train_mask: np.ndarray,
    ts: Sequence[Any],
    contract: EvaluationContract,
    val_start_idx: int,
    val_end_idx: int,
) -> Iterator[tuple[pl.DataFrame, pl.DataFrame]]:
    """Yield fold-local scaled (train_df, val_features_df) pairs with labels stripped from val."""
    from ml4t.engineer import MLDatasetBuilder, RobustScaler

    total_gap = (
        int(contract.label_horizon_bars)
        + int(contract.execution_delay_bars)
        + int(contract.embargo_bars)
    )
    panel_cv = _UniqueTimestampPanelCV(
        panel_timestamps=model_df["timestamp"].to_list(),
        unique_ts=ts,
        val_start_idx=val_start_idx,
        val_end_idx=val_end_idx,
        n_folds=int(contract.n_folds),
        total_gap=total_gap,
        valid_train_mask=valid_train_mask,
    )
    builder = MLDatasetBuilder(
        features=model_df.select(feature_cols).cast({c: pl.Float64 for c in feature_cols}),
        labels=model_df["forward_label"].cast(pl.Float64),
        dates=model_df["timestamp"],
    ).set_scaler(RobustScaler(columns=list(feature_cols)))

    for fold in builder.split(panel_cv):
        train_meta = model_df[fold.train_indices].select("timestamp", "asset")
        val_meta = model_df[fold.test_indices].select("timestamp", "asset")
        train_df = pl.concat(
            [train_meta, fold.X_train, pl.DataFrame({"forward_label": fold.y_train})],
            how="horizontal",
        )
        # Physical exclusion: val_features_df contains ONLY timestamp, asset, and scaled features.
        val_features_df = pl.concat([val_meta, fold.X_test], how="horizontal")
        yield train_df, val_features_df


def run_causal_model_strategy(
    mod: Any,
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ts: Sequence[Any],
    assets: Sequence[str],
    val_slice: slice,
    params: dict[str, Any],
    *,
    seed: int = 42,
) -> dict[str, Any]:
    """Execute CausalModelStrategy under Warden-owned WalkForwardCV, scaling, and IC."""
    from warden.gates import compute_cross_sectional_ic_for_screen
    from warden.leakage import verify_fold_isolation

    target = resolve_protocol_target(mod)
    norm_panel, model_df, fwd_df, feature_cols, valid_train_mask = prepare_causal_model_dataset(
        mod, panel_df, contract, ts
    )
    val_start_idx = int(val_slice.start or 0)
    val_end_idx = int(val_slice.stop if val_slice.stop is not None else len(ts))

    estimator = target.build_estimator(dict(params or {}))
    label_col = "forward_label"
    oos_chunks: list[pl.DataFrame] = []
    has_target_weights = True
    for train_df, val_features_df in iter_causal_model_folds(
        model_df, feature_cols, valid_train_mask, ts, contract, val_start_idx, val_end_idx
    ):
        preds = verify_fold_isolation(
            estimator,
            train_df,
            val_features_df,
            feature_cols=feature_cols,
            label_col=label_col,
            label_horizon_bars=int(contract.label_horizon_bars) + int(contract.execution_delay_bars),
            embargo_bars=int(contract.embargo_bars),
            seed=seed,
            panel_timestamps=ts,
        )
        if "target_weight" in preds.columns:
            oos_chunks.append(preds.select("timestamp", "asset", "prediction_value", "target_weight"))
        else:
            has_target_weights = False
            oos_chunks.append(preds.select("timestamp", "asset", "prediction_value"))

    if not oos_chunks:
        raise ValueError("CausalModelStrategy produced zero walk-forward validation folds.")
    if not has_target_weights:
        oos_chunks = [c.select("timestamp", "asset", "prediction_value") for c in oos_chunks]
    oos_preds_df = pl.concat(oos_chunks).sort("timestamp", "asset")

    # Compute IC strictly via ml4t.diagnostic.metrics.cross_sectional_ic_series
    valid_fwd = fwd_df.filter(
        pl.col("forward_return").is_not_null() & pl.col("forward_return").is_finite()
    ).select("timestamp", "asset", "forward_return")
    min_obs = min(5, max(2, len(assets)))
    ic_series = compute_cross_sectional_ic_for_screen(
        oos_preds_df.select("timestamp", "asset", "prediction_value").rename({"prediction_value": "prediction"}),
        valid_fwd,
        pred_col="prediction",
        ret_col="forward_return",
        date_col="timestamp",
        entity_col="asset",
        min_obs=min_obs,
    )

    rebalance_every = int((params or {}).get("rebalance_every", contract.label_horizon_bars))
    top_k = int((params or {}).get("top_k", 3))
    if has_target_weights:
        positions = _target_weights_to_positions(
            oos_preds_df,
            norm_panel,
            ts,
            assets,
            val_start_idx=val_start_idx,
            val_end_idx=val_end_idx,
            rebalance_every=rebalance_every,
        )
    else:
        positions = _predictions_to_positions(
            oos_preds_df,
            norm_panel,
            ts,
            assets,
            val_start_idx=val_start_idx,
            val_end_idx=val_end_idx,
            rebalance_every=rebalance_every,
            top_k=top_k,
        )
    return {
        "positions": positions,
        "ic_series": ic_series,
        "predictions": oos_preds_df,
        "forward_returns": valid_fwd,
    }


class RulesProofPipeline:
    """Deterministic FeaturePipelineProtocol wrapper for CausalRulesStrategy evidence."""

    def __init__(
        self,
        *,
        repo_root: str,
        strategy_module: str,
        code_sha256: str,
        params: dict[str, Any],
        policy_cfg: dict[str, Any] | None = None,
    ) -> None:
        self.repo_root = str(repo_root)
        self.strategy_module = str(strategy_module)
        self.code_sha256 = str(code_sha256)
        self.params = dict(params)
        self.policy_cfg = dict(policy_cfg or {})

    def compute(
        self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract
    ) -> pl.DataFrame:
        from pathlib import Path

        from warden.engine_sim import extract_execution_policy, run_engine_strategy
        from warden.strategy_loader import load_module_from_path

        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        df = df.sort("timestamp", "asset")
        ts = sorted(df["timestamp"].unique().to_list())
        assets = sorted(df["asset"].unique().to_list())

        mod = load_module_from_path(Path(self.repo_root) / self.strategy_module)
        strat = resolve_protocol_target(mod).configure_strategy(contract, dict(self.params))
        policy = extract_execution_policy(self.policy_cfg, dict(self.params))
        sim = run_engine_strategy(
            df,
            strat,
            ts,
            assets,
            contract,
            slice(0, len(ts)),
            **policy,
        )
        rows = [
            {"timestamp": t_val, "asset": asset_name, "position_weight": float(sim.positions[i, j])}
            for i, t_val in enumerate(ts)
            for j, asset_name in enumerate(assets)
        ]
        return pl.DataFrame(rows).sort("timestamp", "asset")
