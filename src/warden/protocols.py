"""Inverted causal strategy protocols: CausalModelStrategy and CausalRulesStrategy.

Legacy ``generate_positions`` modules are frozen for read-only historical replay only.
New ``ACTIVE`` hypotheses must implement either ``CausalModelStrategy`` or
``CausalRulesStrategy`` so Warden owns WalkForwardCV splitting, fold scaling,
forward-return labeling, IC evaluation, and ml4t.backtest.Engine execution.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterator, Sequence
from datetime import datetime
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


#: Series a ``carry_rv`` strategy must publish for Stage 1 to use its own mechanism.
CARRY_OUTPUT_KEYS = ("spread", "net_carry")
#: Prefix-invariance tolerance for the strategy-published carry series (same basis as
#: ``warden.leakage``: exact float equality up to accumulated summation noise).
CARRY_PREFIX_TOLERANCE = 1e-9
#: Policy labels recorded on every ``carry_rv`` trial, so an explicitly authorized replay is
#: always distinguishable from an active screen that was scored on the strategy's own series.
CARRY_MECHANISM_ACTIVE = "active_own_outputs_required"
CARRY_MECHANISM_LEGACY = "legacy_replay_authorized"


class CarryMechanismRequiredError(ValueError):
    """An active ``carry_rv`` run declared no strategy-owned carry outputs.

    Stage 1 for ``carry_rv`` must score the mechanism the strategy actually trades.  The
    equal-weight panel proxy survives only as an explicitly authorized legacy replay
    (``allow_legacy_replay``), which is an operator boundary -- never a strategy or spec
    field -- so an agent cannot opt itself onto the proxy.
    """


def carry_mechanism_policy(*, allow_legacy_replay: bool) -> str:
    """Which carry mechanism authority applies to this run.

    ``allow_legacy_replay`` is the sole authorization for the panel proxy: it is set on the
    runner by an operator/replay path, not read from a spec.
    """
    return CARRY_MECHANISM_LEGACY if allow_legacy_replay else CARRY_MECHANISM_ACTIVE


def carry_outputs_callable(mod: Any) -> Any | None:
    """Return the strategy's optional ``causal_carry_outputs`` hook, or ``None``."""
    for obj in (resolve_protocol_target(mod), mod):
        hook = getattr(obj, "causal_carry_outputs", None)
        if callable(hook):
            return hook
    return None


def _call_carry_outputs(
    hook: Any,
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    timestamps: Sequence[Any],
    assets: Sequence[str],
    val_slice: slice,
    params: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    out = hook(
        panel_df=panel_df,
        contract=contract,
        val_slice=val_slice,
        timestamps=list(timestamps),
        assets=list(assets),
        params=dict(params or {}),
    )
    if not isinstance(out, dict):
        raise TypeError("causal_carry_outputs must return a mapping")
    missing = [key for key in CARRY_OUTPUT_KEYS if key not in out]
    if missing:
        raise ValueError(f"causal_carry_outputs is missing {missing}")
    net = np.asarray(out["net_carry"], dtype=float).reshape(-1)
    spread = np.asarray(out["spread"], dtype=float).reshape(-1)
    expected = len(list(timestamps)[val_slice])
    if len(net) != expected or len(spread) != expected:
        raise ValueError(
            "causal_carry_outputs must return one entry per validation bar "
            f"(expected {expected}, got net_carry={len(net)}, spread={len(spread)})"
        )
    for name, series in (("net_carry", net), ("spread", spread)):
        if np.isinf(series).any():
            raise ValueError(f"causal_carry_outputs returned non-finite {name}")
    return net, spread


def verified_carry_outputs(
    mod: Any,
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ts: Sequence[Any],
    assets: Sequence[str],
    val_slice: slice,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Stage-1 carry series published by the strategy, with an evaluator-run prefix check.

    Returns ``None`` when the strategy declares no hook (the caller then falls back to
    the panel proxy, which is recorded as such).  When a hook exists it is authoritative:
    shape or causality failures propagate, so a broken declaration cannot silently
    degrade back to the proxy.

    Causality is checked the same way the leakage prover checks features: the hook is
    re-run on panels truncated inside the validation window and the overlapping prefix
    must be unchanged.  A series that peeks at later bars fails here.
    """
    hook = carry_outputs_callable(mod)
    if hook is None:
        return None
    from warden.leakage import LookaheadLeakageError

    ts_list = list(ts)
    start = int(val_slice.start or 0)
    stop = int(val_slice.stop if val_slice.stop is not None else len(ts_list))
    span = stop - start
    if span < 2:
        raise ValueError("causal_carry_outputs requires at least two validation bars")
    params = dict(params or {})
    net, spread = _call_carry_outputs(
        hook, panel_df, contract, ts_list, assets, slice(start, stop), params
    )

    cuts = sorted({start + span // 2, stop - 1})
    max_delta = 0.0
    for end in cuts:
        n_prefix = end + 1 - start
        prefix_ts = ts_list[: end + 1]
        prefix_panel = panel_df.filter(pl.col("timestamp") <= prefix_ts[-1])
        p_net, p_spread = _call_carry_outputs(
            hook, prefix_panel, contract, prefix_ts, assets, slice(start, end + 1), params
        )
        for full, truncated, name in (
            (net[:n_prefix], p_net, "net_carry"),
            (spread[:n_prefix], p_spread, "spread"),
        ):
            mask = np.isfinite(full) & np.isfinite(truncated)
            if not mask.any():
                raise ValueError(f"causal_carry_outputs returned no finite {name} before cut {end}")
            delta = float(np.max(np.abs(truncated[mask] - full[mask])))
            if delta > CARRY_PREFIX_TOLERANCE:
                raise LookaheadLeakageError(
                    f"causal_carry_outputs {name} changes when the panel is truncated at bar "
                    f"{end} (max delta {delta:.3e}): the series is not causal"
                )
            max_delta = max(max_delta, delta)
    return {
        "net_carry": net,
        "spread": spread,
        "source": "strategy_causal_outputs",
        "prefix_cuts": float(len(cuts)),
        "prefix_max_abs_delta": max_delta,
    }


#: Extra bars a single leg may skip before a label is treated as spanning a genuine
#: calendar hole instead of adjacent bars of the same asset.  Raising it would let a
#: label straddle a suspension/holiday and still look adjacent; lowering it to 0
#: rejects any panel with one missing bar.
DEFAULT_FORWARD_LABEL_MAX_GAP_BARS = 1


def compute_execution_forward_returns(
    panel_df: pl.DataFrame,
    contract: EvaluationContract,
    ts: Sequence[Any] | None = None,
    *,
    max_gap_bars: int | None = None,
) -> pl.DataFrame:
    """Compute causal execution-aligned forward returns on *per-asset bar indices*.

    Each asset is labelled on its own bar grid (``shift`` by bar, not by a shared
    timestamp index), so an asynchronous panel — 24h index futures next to an 8h
    cash session — no longer nulls a session asset's label every hour the other
    venue trades.  A leg is refused when the calendar time it spans exceeds its
    bar count plus ``max_gap_bars`` steps, so a label never bridges a genuine
    calendar gap (weekend, holiday, suspension) as if it were one bar.

    ``ts`` is accepted for call-site compatibility; the union grid it describes is
    exactly what this function must *not* use.
    """
    _ = ts
    df = (
        panel_df.rename({"symbol": "asset"})
        if "symbol" in panel_df.columns and "asset" not in panel_df.columns
        else panel_df
    )
    delay = int(contract.execution_delay_bars)
    horizon = int(contract.label_horizon_bars)
    gap_cap = (
        DEFAULT_FORWARD_LABEL_MAX_GAP_BARS if max_gap_bars is None else max(0, int(max_gap_bars))
    )

    if contract.execution_mode == "QUOTE_SIDE":
        px_expr = (0.5 * (pl.col("bid") + pl.col("ask"))).cast(pl.Float64)
    else:
        px_expr = pl.col("open").cast(pl.Float64)

    base = (
        df.sort("asset", "timestamp")
        .with_columns(
            px_expr.alias("_exec_px"),
            pl.int_range(pl.len()).over("asset").alias("_bar"),
            pl.col("timestamp").diff().dt.total_seconds().over("asset").alias("_step_secs"),
        )
        .with_columns(pl.col("_step_secs").median().over("asset").alias("_bar_secs"))
    )

    def _leg(offset: int, price_alias: str, stamp_alias: str) -> pl.DataFrame:
        # Label each row with the base bar it serves, so the join key is the base
        # index and the row carries the price ``offset`` bars later.
        return base.select(
            "asset",
            (pl.col("_bar") - offset).alias("_bar"),
            pl.col("_exec_px").alias(price_alias),
            pl.col("timestamp").alias(stamp_alias),
        )

    entry = _leg(delay, "_entry_px", "_entry_ts")
    exit_ = _leg(delay + horizon, "_exit_px", "_exit_ts")
    labeled = (
        base.join(entry, on=["asset", "_bar"], how="left")
        .join(exit_, on=["asset", "_bar"], how="left")
        .with_columns(
            ((pl.col("_entry_ts") - pl.col("timestamp")).dt.total_seconds()
             / pl.col("_bar_secs")).alias("_entry_steps"),
            ((pl.col("_exit_ts") - pl.col("_entry_ts")).dt.total_seconds()
             / pl.col("_bar_secs")).alias("_exit_steps"),
        )
        .with_columns(
            pl.when(
                pl.col("_entry_px").is_not_null()
                & pl.col("_exit_px").is_not_null()
                & (pl.col("_entry_px") > 0.0)
                & (pl.col("_bar_secs") > 0.0)
                & (pl.col("_entry_steps") <= float(delay + gap_cap) + 1e-6)
                & (pl.col("_exit_steps") <= float(horizon + gap_cap) + 1e-6)
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


class _HoldoutRefitCV:
    """Timestamp-safe holdout refit schedule (#97).

    Qualification evaluates a contract-declared holdout window whose panel is
    prefixed with the digest-verified pre-holdout bars.  The schedule below is
    defined on *unique sorted timestamps*, never on raw row order, so a
    multi-asset panel cannot shift a fold boundary onto the wrong bar.  Both
    modes keep every training timestamp strictly more than ``total_gap`` bars
    before the first test timestamp, i.e. before
    ``holdout_start - (label_horizon + execution_delay + embargo)``.

    ``purged_expanding`` splits the holdout window into ``n_folds`` consecutive
    test segments; fold ``k`` trains on every timestamp before
    ``test_start_k - total_gap`` (expanding over earlier holdout bars).
    ``frozen_pre_holdout`` yields exactly one fold: fit once on the pre-holdout
    prefix and predict the whole window without any refit.

    A prefix too short for the purge+embargo gap is an error, never a silent
    downgrade to in-window training.
    """

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
        mode: str,
    ) -> None:
        from warden.contracts import HOLDOUT_REFIT_MODES

        if mode not in HOLDOUT_REFIT_MODES:
            raise ValueError(
                f"unsupported holdout refit mode {mode!r}; contract modes are {list(HOLDOUT_REFIT_MODES)}"
            )
        self._panel_ts = list(panel_timestamps)
        self._unique_ts = list(unique_ts)
        self._val_start_idx = int(val_start_idx)
        self._val_end_idx = int(val_end_idx)
        self._n_folds = int(n_folds)
        self._total_gap = int(total_gap)
        self._valid_train_mask = np.asarray(valid_train_mask, dtype=bool)
        self._mode = mode

    def _rows_by_timestamp(self) -> dict[int, list[int]]:
        ts_lookup = {t: idx for idx, t in enumerate(self._unique_ts)}
        rows_by_ts_idx: dict[int, list[int]] = {}
        for row_idx, ts_val in enumerate(self._panel_ts):
            t_idx = ts_lookup.get(ts_val)
            if t_idx is not None:
                rows_by_ts_idx.setdefault(t_idx, []).append(row_idx)
        return rows_by_ts_idx

    def _test_segments(self) -> list[tuple[int, int]]:
        start, stop = self._val_start_idx, self._val_end_idx
        if stop <= start:
            raise ValueError("Holdout window contains no timestamps for the refit schedule.")
        if self._mode == "frozen_pre_holdout":
            return [(start, stop)]
        n_folds = max(1, min(self._n_folds, stop - start))
        length = stop - start
        base, extra = divmod(length, n_folds)
        segments: list[tuple[int, int]] = []
        cursor = start
        for fold_idx in range(n_folds):
            size = base + (1 if fold_idx < extra else 0)
            if size <= 0:
                continue
            segments.append((cursor, cursor + size))
            cursor += size
        return segments

    def split(
        self,
        X: Any,
        y: Any = None,
        groups: Any = None,
    ) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        _ = (X, y, groups)
        start = self._val_start_idx
        cutoff = start - self._total_gap
        if cutoff <= 0:
            raise ValueError(
                f"Insufficient pre-holdout timestamps ({start}) for purge+embargo gap "
                f"({self._total_gap}); qualification requires a pre-holdout warmup prefix."
            )
        rows_by_ts_idx = self._rows_by_timestamp()

        for fold_idx, (te_start, te_stop) in enumerate(self._test_segments()):
            tr_ts = np.arange(0, max(0, te_start - self._total_gap), dtype=np.intp)
            if tr_ts.size == 0:
                raise ValueError(f"Fold {fold_idx} has no pre-cutoff training timestamps.")
            gap = int(te_start) - int(tr_ts[-1])
            if gap < self._total_gap:
                raise ValueError(
                    f"Fold {fold_idx} gap {gap} < {self._total_gap} bars before the holdout window"
                )
            tr_rows: list[int] = []
            for t_idx in tr_ts:
                for r_idx in rows_by_ts_idx.get(int(t_idx), ()):
                    if self._valid_train_mask[r_idx]:
                        tr_rows.append(r_idx)
            te_rows: list[int] = []
            for t_idx in range(int(te_start), int(te_stop)):
                te_rows.extend(rows_by_ts_idx.get(t_idx, ()))
            if not tr_rows or not te_rows:
                raise ValueError(
                    f"Fold {fold_idx} produced empty stacked rows "
                    f"(train={len(tr_rows)}, test={len(te_rows)})."
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
    *,
    refit_mode: str | None = None,
) -> Iterator[tuple[pl.DataFrame, pl.DataFrame]]:
    """Yield fold-local scaled (train_df, val_features_df) pairs with labels stripped from val.

    ``refit_mode`` is the contract-declared qualification schedule (#97).  ``None``
    keeps the historical validation walk-forward untouched, so an existing
    validation spec evaluates on exactly the partitions it always did; a holdout
    qualification passes ``purged_expanding`` or ``frozen_pre_holdout`` and gets the
    timestamp-safe pre-holdout schedule instead.
    """
    from ml4t.engineer import MLDatasetBuilder, RobustScaler

    total_gap = (
        int(contract.label_horizon_bars)
        + int(contract.execution_delay_bars)
        + int(contract.embargo_bars)
    )
    if refit_mode is None:
        panel_cv: Any = _UniqueTimestampPanelCV(
            panel_timestamps=model_df["timestamp"].to_list(),
            unique_ts=ts,
            val_start_idx=val_start_idx,
            val_end_idx=val_end_idx,
            n_folds=int(contract.n_folds),
            total_gap=total_gap,
            valid_train_mask=valid_train_mask,
        )
    else:
        panel_cv = _HoldoutRefitCV(
            panel_timestamps=model_df["timestamp"].to_list(),
            unique_ts=ts,
            val_start_idx=val_start_idx,
            val_end_idx=val_end_idx,
            n_folds=int(contract.n_folds),
            total_gap=total_gap,
            valid_train_mask=valid_train_mask,
            mode=refit_mode,
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


def _stamp(value: Any) -> str:
    """JSON-safe timestamp for recorded schedule evidence."""
    return value.isoformat() if isinstance(value, datetime) else str(value)


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
    refit_mode: str | None = None,
) -> dict[str, Any]:
    """Execute CausalModelStrategy under Warden-owned WalkForwardCV, scaling, and IC.

    ``refit_mode`` selects the contract-declared qualification schedule (#97):
    ``purged_expanding`` or ``frozen_pre_holdout`` train fold 0 strictly on the
    pre-holdout prefix and predict every holdout bar; ``None`` keeps the
    historical validation walk-forward.  The returned ``refit`` block is the
    observed schedule evidence (fold boundaries + fit count), not a claim.
    """
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
    fold_log: list[dict[str, Any]] = []
    window_ts = [t for t in ts[val_start_idx:val_end_idx]]
    has_target_weights = True
    fold_records: list[dict[str, Any]] = []
    unique_ts = sorted(set(ts))
    required_gap = (
        int(contract.label_horizon_bars)
        + int(contract.execution_delay_bars)
        + int(contract.embargo_bars)
    )
    for fold_idx, (train_df, val_features_df) in enumerate(
        iter_causal_model_folds(
            model_df, feature_cols, valid_train_mask, ts, contract, val_start_idx, val_end_idx,
            refit_mode=refit_mode,
        )
    ):
        fold_log.append({
            "train_rows": int(train_df.height),
            "train_bars": int(train_df["timestamp"].n_unique()),
            "train_first_ts": _stamp(train_df["timestamp"].min()),
            "train_last_ts": _stamp(train_df["timestamp"].max()),
            "test_rows": int(val_features_df.height),
            "test_bars": int(val_features_df["timestamp"].n_unique()),
            "test_first_ts": _stamp(val_features_df["timestamp"].min()),
            "test_last_ts": _stamp(val_features_df["timestamp"].max()),
        })
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
        # Evaluator-observed fold record: the gap below is measured on the panel clock
        # between the last training bar and the first validation bar that actually ran,
        # not copied from a contract integer or a strategy claim.
        max_train_ts = train_df["timestamp"].max()
        min_val_ts = val_features_df["timestamp"].min()
        gap_bars = (
            bisect_left(unique_ts, min_val_ts) - bisect_left(unique_ts, max_train_ts)
            if max_train_ts is not None and min_val_ts is not None
            else 0
        )
        stripped = not any(
            col == label_col or any(k in col.lower() for k in ("label", "target", "forward", "fut_"))
            for col in val_features_df.columns
        )
        fold_records.append(
            {
                "fold": fold_idx,
                "n_train": int(train_df.height),
                "n_val": int(val_features_df.height),
                "gap_bars": int(gap_bars),
                "labels_stripped": bool(stripped),
            }
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
    pred_ts = oos_preds_df["timestamp"].unique().sort().to_list()
    window_ts_set = set(window_ts)
    covered = [t for t in pred_ts if t in window_ts_set]
    refit_block = {
        "mode": refit_mode or "walk_forward",
        "fits": len(fold_log),
        "folds": fold_log,
        "window_bars": len(window_ts),
        "prediction_bars": len(covered),
        # Every evaluation timestamp appears at least once in the OOS predictions.
        "covers_window": bool(window_ts) and len(covered) == len(window_ts),
    }
    fold_isolation = {
        "protocol": "causal_model",
        "folds": len(fold_records),
        "min_gap_bars": (
            float(min(record["gap_bars"] for record in fold_records)) if fold_records else 0.0
        ),
        "required_gap_bars": float(required_gap),
        "labels_stripped": bool(fold_records) and all(
            record["labels_stripped"] for record in fold_records
        ),
        "verified": bool(fold_records)
        and all(record["gap_bars"] >= required_gap for record in fold_records)
        and all(record["labels_stripped"] for record in fold_records),
        "records": fold_records,
    }
    return {
        "positions": positions,
        "ic_series": ic_series,
        "predictions": oos_preds_df,
        "forward_returns": valid_fwd,
        "refit": refit_block,
        "fold_isolation": fold_isolation,
    }


class RulesProofPipeline:
    """Deterministic FeaturePipelineProtocol wrapper for CausalRulesStrategy evidence.

    The pipeline's "features" are the strategy's own executed position tape.  It
    runs through the shared :class:`~warden.evaluation.EvaluationKernel`, so the
    tape the feature cache stores is the *same* execution the prefix-invariance
    proof already performed — no second Engine run for identical panel bytes.

    ``prefix_proof`` carries the kernel proof that this exact panel/contract
    parameterisation was already shown prefix-invariant; ``verify_prefix_invariance``
    consumes it instead of re-executing the strategy at four independent cut points
    (which is what made a rules ``verify`` cost 13 extra Engine runs).
    """

    def __init__(
        self,
        *,
        repo_root: str,
        strategy_module: str,
        code_sha256: str,
        params: dict[str, Any],
        policy_cfg: dict[str, Any] | None = None,
        kernel: Any | None = None,
        prefix_proof: Any | None = None,
    ) -> None:
        self.repo_root = str(repo_root)
        self.strategy_module = str(strategy_module)
        self.code_sha256 = str(code_sha256)
        self.params = dict(params)
        self.policy_cfg = dict(policy_cfg or {})
        self.kernel = kernel
        self.prefix_proof = prefix_proof

    def compute(
        self, bars_df: pl.DataFrame | pl.LazyFrame, contract: EvaluationContract
    ) -> pl.DataFrame:
        from pathlib import Path

        from warden.evaluation import EvaluationKernel
        from warden.strategy_loader import load_module_from_path

        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        if "symbol" in df.columns and "asset" not in df.columns:
            df = df.rename({"symbol": "asset"})
        df = df.sort("timestamp", "asset")
        ts = sorted(df["timestamp"].unique().to_list())
        assets = sorted(df["asset"].unique().to_list())

        mod = load_module_from_path(Path(self.repo_root) / self.strategy_module)
        kernel = self.kernel if self.kernel is not None else EvaluationKernel()
        tape = kernel.run_rules(
            mod,
            df,
            ts,
            assets,
            contract,
            slice(0, len(ts)),
            dict(self.params),
        )
        return _positions_tape(ts, assets, tape.positions)


def _positions_tape(ts: Sequence[Any], assets: Sequence[str], positions: np.ndarray) -> pl.DataFrame:
    """Long-format position tape built column-wise, never per cell."""
    matrix = np.asarray(positions, dtype=float)
    expected = (len(ts), len(assets))
    if matrix.shape != expected:
        raise ValueError(f"position tape shape {matrix.shape} does not match {expected}")
    wide = pl.DataFrame(
        {asset: matrix[:, idx] for idx, asset in enumerate(assets)},
    ).with_columns(pl.Series("timestamp", list(ts)))
    return (
        wide.unpivot(
            index="timestamp",
            on=list(assets),
            variable_name="asset",
            value_name="position_weight",
        )
        .select("timestamp", "asset", "position_weight")
        .sort("timestamp", "asset")
    )
