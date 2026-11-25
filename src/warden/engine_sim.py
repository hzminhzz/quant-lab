"""Canonical ml4t.backtest.Engine simulation, artifact export, and ml4t.diagnostic integration."""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
from collections import deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from ml4t.backtest import BacktestConfig, BacktestResult, DataFeed, Engine, Strategy
from ml4t.backtest.config import (
    CommissionType,
    ExecutionMode,
    ExecutionPrice,
    ShareType,
    SlippageType,
)
from ml4t.backtest.execution.impact import SquareRootImpact
from ml4t.backtest.execution.limits import VolumeParticipationLimit
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor
from ml4t.diagnostic.evaluation.portfolio_analysis import PortfolioAnalysis
from ml4t.diagnostic.evaluation.stats import (
    compute_pbo,
    rademacher_complexity,
    ras_sharpe_adjustment,
)
from ml4t.diagnostic.evaluation.trade_analysis import TradeAnalysis, TradeRecord

from warden.contracts import EvaluationContract
from warden.gates import annualised_sharpe, per_bar_sharpe

logger = logging.getLogger(__name__)


class ExecutionDataError(ValueError):
    """Required execution prices are absent; Warden never guesses another price basis."""


class LeveragePolicyError(ValueError):
    """Raised when a strategy violates allow_leverage=False or allow_short_selling=False."""


#: Float-noise allowance on the realized gross-leverage ratio.  It exists only so
#: that a run sitting exactly on the cap is not rejected by summation error; it
#: is nowhere near wide enough to admit a real breach.
_GROSS_LEVERAGE_EPS = 1e-9

#: Session modes.  The name is recorded on every tape so a reader never has to
#: infer whether a run carried daily-session semantics.
SESSION_MODE_SINGLE_VENUE_DAILY = "single_venue_daily_session"
SESSION_MODE_MULTI_VENUE_INTRADAY = "multi_venue_intraday"
SESSION_MODE_VENUE_CALENDAR = "venue_calendar"


@dataclass(frozen=True)
class SessionPolicy:
    """Explicit session semantics for one Engine feed.

    ``session_col`` is what is handed to ``DataFeed``; ``mode`` says what that
    means.  A ``session_date`` column that exists but fails the strict
    completeness invariants is *never* dropped silently: the policy names the
    failed invariant in ``reason`` and repeats it in ``warnings``, which the
    caller logs.
    """

    mode: str
    session_col: str | None
    reason: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EngineSimOutput:
    """Aligned validation outputs from canonical ml4t.backtest.Engine execution."""

    positions: np.ndarray
    gross_returns: np.ndarray
    net_returns: np.ndarray
    turnover: np.ndarray
    backtest_result: BacktestResult
    terminal_equity: float
    session_col_used: str | None
    funding_events: int
    session_mode: str = SESSION_MODE_VENUE_CALENDAR
    session_reason: str = ""


def require_execution_columns(panel_df: pl.DataFrame, contract: EvaluationContract) -> None:
    """Fail closed on a missing price basis instead of silently changing basis."""
    needed = ["bid", "ask"] if contract.execution_mode == "QUOTE_SIDE" else ["open"]
    missing = [c for c in needed if c not in panel_df.columns]
    if missing:
        raise ExecutionDataError(
            f"{contract.execution_mode} contract {contract.contract_id} requires {missing}; "
            "a close-price fallback is not a valid execution basis"
        )
    if contract.asset_class == "crypto_perps" and "funding_rate" not in panel_df.columns:
        raise ExecutionDataError(
            f"{contract.contract_id} is a perpetual contract without funding_rate data"
        )
    for field in needed:
        if panel_df.select(pl.col(field).is_null().any()).item():
            raise ExecutionDataError(f"null {field} prices cannot be treated as a zero move")
        if panel_df.select((~pl.col(field).is_finite() | (pl.col(field) <= 0)).any()).item():
            raise ExecutionDataError(
                f"nonfinite or nonpositive {field} prices cannot be traded"
            )


def resolve_execution_limits(
    contract: EvaluationContract,
    allow_leverage: bool | None = None,
    max_gross_leverage: float | None = None,
) -> tuple[bool, float | None]:
    """Effective (allow_leverage, gross cap) for one contract.

    A ``spec_version >= 2`` contract owns both values and outranks every caller
    argument, so no experiment spec, strategy parameter block or replay flag can
    widen them.  A legacy v1 contract keeps its historical behaviour: the
    Warden-recorded ``allow_leverage`` flag, gross capped at 1.0 unless leverage
    was explicitly recorded.

    ``None`` as the returned cap means "no explicit cap" and only happens for a
    legacy leveraged replay, which is exactly the pre-#94 configuration.
    """
    policy = contract.governance
    if policy.declares_governance:
        allow = bool(policy.allow_leverage)
        cap: float | None = float(policy.max_gross_leverage)
    else:
        allow = bool(allow_leverage)
        cap = None if max_gross_leverage is None else float(max_gross_leverage)
        if cap is None and not allow:
            cap = 1.0
    if not allow:
        cap = 1.0 if cap is None else min(float(cap), 1.0)
    if cap is not None and (not math.isfinite(cap) or cap <= 0.0):
        raise LeveragePolicyError(f"invalid gross leverage cap {cap!r}")
    return allow, cap


def extract_execution_policy(
    cfg_dict: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | str | None = None,
    *,
    contract: EvaluationContract | None = None,
) -> dict[str, Any]:
    """Extract execution policy kwargs from Warden-recorded diagnostics only.

    ``cfg_dict`` and ``params`` are accepted for call-site compatibility but are
    deliberately ignored: an agent-authored experiment spec or strategy
    parameter block must never unlock leverage/short selling or manufacture a
    volume-participation/impact policy.  Historical replay reads the policy that
    Warden itself recorded in the trial's diagnostics; absent that record the
    engine runs fail-closed defaults (no leverage, shorts allowed, no
    participation cap, no market-impact model).

    When ``contract`` is supplied, ``allow_leverage`` and ``max_gross_leverage``
    come from the contract instead (see :func:`resolve_execution_limits`); the
    recorded diagnostics stay authoritative only for a legacy v1 contract that
    declares no leverage policy of its own.
    """
    if isinstance(diagnostics, str):
        try:
            diag_data = json.loads(diagnostics) if diagnostics.strip() else {}
        except json.JSONDecodeError:
            diag_data = {}
    elif isinstance(diagnostics, dict):
        diag_data = diagnostics
    else:
        diag_data = {}
    ep = diag_data.get("execution_policy") if isinstance(diag_data, dict) else None
    ep = ep if isinstance(ep, dict) else {}
    max_vp = ep.get("max_volume_participation")
    impact_c = ep.get("market_impact_coefficient")
    policy = {
        "allow_leverage": bool(ep.get("allow_leverage", False)),
        "allow_short_selling": bool(ep.get("allow_short_selling", True)),
        "max_volume_participation": float(max_vp) if max_vp is not None else None,
        "market_impact_coefficient": float(impact_c) if impact_c is not None else None,
    }
    if contract is None:
        return policy
    allow_leverage, gross_cap = resolve_execution_limits(contract, policy["allow_leverage"])
    return {**policy, "allow_leverage": allow_leverage, "max_gross_leverage": gross_cap}



def _session_date_failure(feed_df: pl.DataFrame) -> str:
    """Return the first failed ``session_date`` completeness invariant, or ``""``."""
    if feed_df.schema["session_date"] != pl.Date:
        return f"session_date dtype is {feed_df.schema['session_date']}, not Date"
    if feed_df["session_date"].null_count() > 0:
        return "session_date contains nulls"
    if feed_df.unique(subset=["session_date", "asset"]).height != feed_df.height:
        return "session_date/asset pairs are not unique"
    n_assets = feed_df["asset"].n_unique()
    incomplete = (
        feed_df.group_by("session_date")
        .agg(pl.col("asset").n_unique().alias("_n_assets"))
        .filter(pl.col("_n_assets") != n_assets)
    )
    if incomplete.height > 0:
        return (
            f"{incomplete.height} session(s) carry fewer than all {n_assets} assets "
            "(incomplete/holiday sessions)"
        )
    mixed = (
        feed_df.group_by("timestamp")
        .agg(pl.col("session_date").n_unique().alias("_n_sess"))
        .filter(pl.col("_n_sess") > 1)
    )
    if mixed.height > 0:
        return f"{mixed.height} timestamp(s) map to more than one session_date"
    if not feed_df.sort("timestamp", "asset")["session_date"].is_sorted():
        return "session_date is not monotonic in timestamp order"
    return ""


def _is_ragged_grid(feed_df: pl.DataFrame) -> bool:
    """True when the feed is an asynchronous union of per-venue bar times."""
    if feed_df.is_empty() or "asset" not in feed_df.columns:
        return False
    per_ts = feed_df.group_by("timestamp").agg(pl.col("asset").n_unique().alias("_n"))
    if per_ts["_n"].min() != per_ts["_n"].max():
        return True
    if feed_df["asset"].n_unique() < 2:
        return False
    steps = (
        feed_df.sort("asset", "timestamp")
        .with_columns(pl.col("timestamp").diff().dt.total_seconds().over("asset").alias("_step"))
        .group_by("asset")
        .agg(pl.col("_step").median().alias("_median_step"))
    )
    return steps["_median_step"].n_unique() > 1


def resolve_session_policy(feed_df: pl.DataFrame) -> SessionPolicy:
    """Decide — explicitly — whether this feed carries deadline-session semantics.

    ``session_date`` is used only when every strict ``DataFeed`` invariant holds.
    Whenever the column exists but cannot be used, the reason is recorded in the
    policy (and repeated in ``warnings``) instead of being dropped silently, so a
    degraded run is visible in the tape metadata and in the log.
    """
    if "session_date" not in feed_df.columns:
        if _is_ragged_grid(feed_df):
            return SessionPolicy(
                mode=SESSION_MODE_MULTI_VENUE_INTRADAY,
                session_col=None,
                reason="no session_date column on an asynchronous multi-venue bar union",
            )
        return SessionPolicy(
            mode=SESSION_MODE_VENUE_CALENDAR,
            session_col=None,
            reason="no session_date column; venue calendar bars are used as-is",
        )
    if feed_df.is_empty():
        return SessionPolicy(
            mode=SESSION_MODE_VENUE_CALENDAR,
            session_col=None,
            reason="empty feed; no session semantics to carry",
            warnings=("session_date present on an empty feed and therefore dropped",),
        )
    failure = _session_date_failure(feed_df)
    if failure:
        mode = (
            SESSION_MODE_MULTI_VENUE_INTRADAY
            if _is_ragged_grid(feed_df)
            else SESSION_MODE_VENUE_CALENDAR
        )
        reason = f"session_date dropped: {failure}"
        return SessionPolicy(mode=mode, session_col=None, reason=reason, warnings=(reason,))
    return SessionPolicy(
        mode=SESSION_MODE_SINGLE_VENUE_DAILY,
        session_col="session_date",
        reason="complete, monotonic session_date over every asset and bar",
    )


def _select_session_col(feed_df: pl.DataFrame) -> str | None:
    """Return 'session_date' only when DataFeed's strict completeness invariants hold."""
    return resolve_session_policy(feed_df).session_col


def build_funding_df(
    feed_df: pl.DataFrame, contract: EvaluationContract
) -> pl.DataFrame | None:
    """Build Engine funding_df settled strictly on 8h boundaries (or daily for 1d)."""
    if contract.asset_class != "crypto_perps" or "funding_rate" not in feed_df.columns:
        return None
    if contract.frequency == "1d":
        filtered = feed_df.filter(pl.col("funding_rate").is_finite())
    else:
        filtered = feed_df.filter(
            (pl.col("timestamp").dt.hour() % 8 == 0)
            & (pl.col("timestamp").dt.minute() == 0)
            & (pl.col("timestamp").dt.second() == 0)
            & pl.col("funding_rate").is_finite()
        )
    return (
        filtered.select(
            "timestamp",
            "asset",
            pl.col("funding_rate").cast(pl.Float64).alias("rate"),
        )
        .unique(subset=["timestamp", "asset"], keep="last")
        .sort("timestamp", "asset")
    )


class _TargetWeightReplayStrategy(Strategy):
    """Submit precomputed target weights at each timestamp via TargetWeightExecutor."""

    def __init__(
        self,
        weights_by_ts: dict[datetime, dict[str, float]],
        rebalance_config: RebalanceConfig,
    ) -> None:
        self._weights_by_ts = weights_by_ts
        self._executor = TargetWeightExecutor(rebalance_config)

    def on_data(
        self,
        timestamp: datetime,
        data: dict[str, dict[str, Any]],
        context: dict[str, Any],
        broker: Any,
    ) -> None:
        targets = self._weights_by_ts.get(timestamp)
        if targets is None:
            return
        held = {
            asset: pos.quantity
            for asset, pos in broker.get_positions().items()
            if pos.quantity != 0
        }
        # Keep non-zero targets and currently held assets untouched if absent on holiday bars.
        active_targets = {
            asset: wt
            for asset, wt in targets.items()
            if wt != 0.0 or asset in held
        }
        for asset in held:
            if asset not in active_targets:
                active_targets[asset] = 0.0
        if not active_targets and not held:
            return
        self._executor.execute(active_targets, data, broker)


def _build_weights_by_ts(
    pos_matrix: np.ndarray,
    ts: list[Any],
    assets: list[str],
    start_idx: int,
    stop_idx: int,
) -> dict[Any, dict[str, float]]:
    weights_by_ts: dict[Any, dict[str, float]] = {}
    for idx in range(start_idx, stop_idx):
        row = pos_matrix[idx]
        if idx == start_idx:
            nonzero = np.flatnonzero(row)
            if len(nonzero) == 0:
                continue
        elif np.array_equal(row, pos_matrix[idx - 1]):
            continue
        else:
            nonzero = np.flatnonzero(row)
        weights_by_ts[ts[idx]] = {assets[j]: float(row[j]) for j in nonzero}
    return weights_by_ts


def _fill_cost(fill: Any) -> float:
    mult = float(fill.multiplier) if fill.multiplier is not None else 1.0
    return float(fill.commission) + abs(float(fill.quantity)) * float(fill.slippage) * mult


def _extract_aligned_series(
    res: BacktestResult,
    ts: list[Any],
    start: int,
    stop: int,
    initial_cash: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    val_ts = ts[start:stop]
    n_val = len(val_ts)
    eq_map = {t: float(eq) for t, eq in res.equity_curve}

    costs_by_ts: dict[Any, float] = {}
    notional_traded_by_ts: dict[Any, float] = {}
    for fill in res.fills:
        mult = float(fill.multiplier) if fill.multiplier is not None else 1.0
        costs_by_ts[fill.timestamp] = costs_by_ts.get(fill.timestamp, 0.0) + _fill_cost(fill)
        notional = abs(float(fill.quantity)) * float(fill.price) * mult
        notional_traded_by_ts[fill.timestamp] = (
            notional_traded_by_ts.get(fill.timestamp, 0.0) + notional
        )

    funding_by_ts: dict[Any, float] = {}
    for payment in res.funding_payments:
        funding_by_ts[payment.timestamp] = (
            funding_by_ts.get(payment.timestamp, 0.0) - float(payment.cash_delta)
        )

    net_ret = np.zeros(n_val, dtype=float)
    gross_ret = np.zeros(n_val, dtype=float)
    turnover = np.zeros(n_val, dtype=float)

    prev_eq = eq_map[ts[start - 1]] if start > 0 else float(initial_cash)
    eq_vals = np.empty(n_val, dtype=float)

    for k, t in enumerate(val_ts):
        eq_t = eq_map[t]
        eq_vals[k] = eq_t
        if k == 0 and start == 0:
            denom = eq_t if eq_t != 0.0 else float(initial_cash)
            net_ret[0] = 0.0
        else:
            denom = prev_eq if prev_eq != 0.0 else float(initial_cash)
            net_ret[k] = eq_t / denom - 1.0
        gross_ret[k] = net_ret[k] + (costs_by_ts.get(t, 0.0) + funding_by_ts.get(t, 0.0)) / denom
        turnover[k] = notional_traded_by_ts.get(t, 0.0) / denom
        prev_eq = eq_t

    return gross_ret, net_ret, turnover, float(eq_vals[-1])


class _WarmupGuardedBroker:
    """Suppress order submissions during warmup bars while allowing rule setup/queries."""

    def __init__(self, broker: Any) -> None:
        self._broker = broker

    def submit_order(self, *args: Any, **kwargs: Any) -> None:
        return None

    def close_position(self, *args: Any, **kwargs: Any) -> None:
        return None

    def order_target_percent(self, *args: Any, **kwargs: Any) -> None:
        return None

    def order_target_value(self, *args: Any, **kwargs: Any) -> None:
        return None

    def flatten_all_positions(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def reduce_all_positions(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def reduce_position(self, *args: Any, **kwargs: Any) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._broker, name)


class _LifecycleStrategyAdapter(Strategy):
    """Wrap a CausalRulesStrategy instance with warmup guarding and optional extra delay.

    Delay buffers the full ``(timestamp, data, context)`` triple and replays it
    untouched, so the inner strategy always sees a point-in-time-consistent bar.
    Handing the *current* timestamp to stale data (or the current data under a
    stale timestamp) would let a strategy attribute a decision to the wrong bar.
    The order is still submitted during the current Engine bar, so NEXT_BAR
    execution fills it one bar after the replayed bar: one extra bar of latency.

    The Engine pools and mutates its per-bar mappings, so the buffered bar is
    snapshotted at append time; holding the original reference would later read
    a different bar's prices.
    """

    def __init__(
        self,
        inner: Strategy,
        *,
        submit_from_ts: Any | None,
        extra_delay: int,
    ) -> None:
        self._inner = inner
        self._submit_from_ts = submit_from_ts
        self._extra_delay = max(0, int(extra_delay))
        self._buffer: deque[
            tuple[datetime, dict[str, dict[str, Any]], dict[str, Any]]
        ] = deque()

    def on_start(self, broker: Any) -> None:
        self._inner.on_start(broker)

    def on_end(self, broker: Any) -> None:
        self._inner.on_end(broker)

    def on_data(
        self,
        timestamp: datetime,
        data: dict[str, dict[str, Any]],
        context: dict[str, Any],
        broker: Any,
    ) -> None:
        active_broker = (
            _WarmupGuardedBroker(broker)
            if self._submit_from_ts is not None and timestamp < self._submit_from_ts
            else broker
        )
        if self._extra_delay > 0:
            self._buffer.append(
                (
                    timestamp,
                    {asset: dict(bar) for asset, bar in data.items()},
                    dict(context),
                )
            )
            if len(self._buffer) <= self._extra_delay:
                return
            delayed_ts, delayed_data, delayed_ctx = self._buffer.popleft()
            self._inner.on_data(delayed_ts, delayed_data, delayed_ctx, active_broker)
            return
        self._inner.on_data(timestamp, data, context, active_broker)


def _reconstruct_held_weights(
    res: BacktestResult,
    feed_df: pl.DataFrame,
    ts: list[Any],
    assets: list[str],
    start: int,
    stop: int,
) -> np.ndarray:
    n_val = stop - start
    n_assets = len(assets)
    weights = np.zeros((n_val, n_assets), dtype=float)
    asset_idx = {a: j for j, a in enumerate(assets)}
    eq_map = {t: float(eq) for t, eq in res.equity_curve}

    fills_by_ts: dict[Any, list[Any]] = {}
    for fill in res.fills:
        fills_by_ts.setdefault(fill.timestamp, []).append(fill)

    close_pivot = feed_df.pivot(index="timestamp", on="asset", values="close").sort("timestamp")
    close_by_ts: dict[Any, dict[str, float]] = {}
    present_cols = [a for a in assets if a in close_pivot.columns]
    for row in close_pivot.iter_rows(named=True):
        close_by_ts[row["timestamp"]] = {
            a: float(row[a]) for a in present_cols if row[a] is not None
        }

    qty_by_asset: dict[str, float] = {a: 0.0 for a in assets}
    val_ts_set = {t: idx for idx, t in enumerate(ts[start:stop])}
    for t in ts[:stop]:
        for fill in fills_by_ts.get(t, ()):
            if fill.asset not in qty_by_asset:
                continue
            side_raw = str(getattr(fill.side, "value", fill.side)).lower()
            sign = -1.0 if side_raw == "sell" else 1.0
            mult = float(fill.multiplier) if fill.multiplier is not None else 1.0
            qty_by_asset[fill.asset] += sign * abs(float(fill.quantity)) * mult
        k = val_ts_set.get(t)
        if k is None:
            continue
        eq_t = eq_map.get(t, 0.0)
        if eq_t == 0.0:
            continue
        px_map = close_by_ts.get(t, {})
        for a, q in qty_by_asset.items():
            if q != 0.0 and a in px_map:
                weights[k, asset_idx[a]] = (q * px_map[a]) / eq_t
    return weights


def _enforce_contract_gross_leverage(
    res: BacktestResult,
    ts: list[Any],
    start: int,
    stop: int,
    *,
    contract: EvaluationContract,
    max_gross_leverage: float,
    allow_leverage: bool,
) -> None:
    """Fail closed when the Engine's own per-bar gross exposure exceeds the contract cap.

    The measure is the Engine's recorded ``gross_exposure`` — ``sum(|qty * mark *
    multiplier|)`` over **every** position, at the Engine's own mark price — divided
    by that bar's ``equity``.  That makes it a multi-asset *gross* ratio rather than
    net exposure, and it is evaluated on **every** validation bar rather than on the
    final snapshot, so a run that spikes over the cap and flattens again is refused.

    Called after the Engine has run but before any output object (returns,
    positions, metrics, artifacts) exists: an over-cap run cannot publish accepted
    evidence.  Only a contract that declares its own governance (``spec_version >=
    2``) is audited this way; a legacy v1 replay owns no numeric cap and keeps its
    historical margin/rejected-order semantics untouched.
    """
    if not contract.governance.declares_governance:
        return
    state = {
        stamp: (float(equity), float(gross))
        for stamp, equity, _cash, gross, _net, _open in res.portfolio_state
    }
    worst_ratio = 0.0
    worst_ts: Any = None
    for stamp in ts[start:stop]:
        row = state.get(stamp)
        if row is None:
            raise ValueError(
                f"engine portfolio state is missing validation bar {stamp!r}; "
                "gross exposure cannot be verified against the contract cap"
            )
        equity, gross = row
        if equity <= 0.0:
            raise LeveragePolicyError(
                f"gross leverage is unmeasurable: equity {equity:.6g} at {stamp} is not positive "
                f"(contract max_gross_leverage={float(max_gross_leverage):g}, "
                f"allow_leverage={allow_leverage})"
            )
        ratio = gross / equity
        if ratio > worst_ratio:
            worst_ratio, worst_ts = ratio, stamp
    if worst_ratio > float(max_gross_leverage) + _GROSS_LEVERAGE_EPS:
        raise LeveragePolicyError(
            "gross exposure "
            f"{worst_ratio:.6g}x at {worst_ts} exceeds the contract's max_gross_leverage="
            f"{float(max_gross_leverage):g} (allow_leverage={allow_leverage}); "
            "refusing to produce over-cap evidence"
        )


def _execute_engine_run(
    feed_df: pl.DataFrame,
    strategy: Strategy,
    ts: list[Any],
    contract: EvaluationContract,
    start: int,
    stop: int,
    *,
    cost_multiplier: float,
    initial_cash: float,
    allow_leverage: bool,
    allow_short_selling: bool,
    max_gross_leverage: float | None,
    max_volume_participation: float | None,
    market_impact_coefficient: float | None,
    market_impact_volatility: float,
) -> tuple[
    BacktestResult,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    float,
    str | None,
    SessionPolicy,
]:
    is_quote = contract.execution_mode == "QUOTE_SIDE"
    comm_rate = float(contract.taker_fee_bps) * 1e-4 * float(cost_multiplier)
    slip_rate = float(contract.slippage_bps) * 1e-4 * float(cost_multiplier)
    cfg = BacktestConfig(
        initial_cash=float(initial_cash),
        allow_short_selling=allow_short_selling,
        allow_leverage=allow_leverage,
        initial_margin=0.05 if allow_leverage else 1.0,
        long_maintenance_margin=0.025 if allow_leverage else 0.25,
        short_maintenance_margin=0.025 if allow_leverage else 0.30,
        reject_on_insufficient_cash=not allow_leverage,
        execution_mode=ExecutionMode.NEXT_BAR,
        execution_price=ExecutionPrice.QUOTE_SIDE if is_quote else ExecutionPrice.OPEN,
        mark_price=ExecutionPrice.QUOTE_SIDE if is_quote else ExecutionPrice.CLOSE,
        share_type=ShareType.FRACTIONAL,
        commission_type=CommissionType.PERCENTAGE if comm_rate > 0 else CommissionType.NONE,
        commission_rate=comm_rate,
        slippage_type=SlippageType.PERCENTAGE if slip_rate > 0 else SlippageType.NONE,
        slippage_rate=slip_rate,
    )
    execution_limits = (
        VolumeParticipationLimit(max_participation=float(max_volume_participation))
        if max_volume_participation is not None
        else None
    )
    market_impact_model = (
        SquareRootImpact(
            coefficient=float(market_impact_coefficient),
            volatility=float(market_impact_volatility),
        )
        if market_impact_coefficient is not None
        else None
    )
    funding_df = build_funding_df(feed_df, contract)
    session = resolve_session_policy(feed_df)
    for warning in session.warnings:
        logger.warning("session semantics degraded: %s", warning)
    session_col_used = session.session_col
    feed = DataFeed(
        prices_df=feed_df,
        bid_col="bid" if is_quote else None,
        ask_col="ask" if is_quote else None,
        session_col=session_col_used,
    )
    res = Engine(
        feed,
        strategy,
        cfg,
        funding_df=funding_df,
        execution_limits=execution_limits,
        market_impact_model=market_impact_model,
    ).run()
    if not allow_leverage and res.rejected_orders:
        raise LeveragePolicyError(
            f"allow_leverage=False: {len(res.rejected_orders)} order(s) rejected for insufficient cash/margin"
        )
    if max_gross_leverage is not None:
        _enforce_contract_gross_leverage(
            res,
            ts,
            start,
            stop,
            contract=contract,
            max_gross_leverage=float(max_gross_leverage),
            allow_leverage=allow_leverage,
        )
    gross_ret, net_ret, turnover, terminal_equity = _extract_aligned_series(
        res, ts, start, stop, float(initial_cash)
    )
    return res, gross_ret, net_ret, turnover, terminal_equity, session_col_used, session


def run_engine_strategy(
    panel_df: pl.DataFrame,
    strategy: Strategy,
    ts: list[Any],
    assets: list[str],
    contract: EvaluationContract,
    val_slice: slice,
    *,
    cost_multiplier: float = 1.0,
    delay_offset: int = 0,
    initial_cash: float = 1_000_000.0,
    allow_leverage: bool = False,
    allow_short_selling: bool = True,
    max_gross_leverage: float | None = None,
    max_volume_participation: float | None = None,
    market_impact_coefficient: float | None = None,
    market_impact_volatility: float = 0.02,
) -> EngineSimOutput:
    """Run canonical ml4t.backtest.Engine on a CausalRulesStrategy Strategy object.

    A strategy chooses its own orders, so the contract's numeric
    ``max_gross_leverage`` cannot be checked before submission the way
    :func:`run_engine_simulation` checks target weights.  Instead the realized
    per-bar ratio ``gross_exposure / equity`` is audited across every validation
    bar on the Engine's own marked positions, and an over-cap run raises
    :class:`LeveragePolicyError` before this function returns any output — so no
    returns, positions, metrics or artifacts exist for it.  Enforcement applies
    only to a contract that declares its own governance (``spec_version >= 2``);
    a legacy v1 replay keeps its recorded ``allow_leverage`` margin semantics.
    """
    require_execution_columns(panel_df, contract)
    allow_leverage, max_gross_leverage = resolve_execution_limits(
        contract, allow_leverage, max_gross_leverage
    )
    start = val_slice.start or 0
    stop = val_slice.stop if val_slice.stop is not None else len(ts)
    if start < 0 or stop > len(ts) or start >= stop:
        raise ValueError(f"invalid val_slice {val_slice} for {len(ts)} timestamps")

    extra_delay = max(0, (int(contract.execution_delay_bars) - 1) + int(delay_offset))
    submit_from_ts = ts[max(0, start - 1)] if start > 0 else None

    feed_df = panel_df.filter(
        (pl.col("timestamp") >= ts[0]) & (pl.col("timestamp") <= ts[stop - 1])
    ).sort("timestamp", "asset")

    wrapped = _LifecycleStrategyAdapter(
        strategy,
        submit_from_ts=submit_from_ts,
        extra_delay=extra_delay,
    )
    res, gross_ret, net_ret, turnover, terminal_equity, session_col_used, session = _execute_engine_run(
        feed_df,
        wrapped,
        ts,
        contract,
        start,
        stop,
        cost_multiplier=cost_multiplier,
        initial_cash=initial_cash,
        allow_leverage=allow_leverage,
        allow_short_selling=allow_short_selling,
        max_gross_leverage=max_gross_leverage,
        max_volume_participation=max_volume_participation,
        market_impact_coefficient=market_impact_coefficient,
        market_impact_volatility=market_impact_volatility,
    )
    held_weights = _reconstruct_held_weights(res, feed_df, ts, assets, start, stop)
    return EngineSimOutput(
        positions=held_weights,
        gross_returns=gross_ret,
        net_returns=net_ret,
        turnover=turnover,
        backtest_result=res,
        terminal_equity=terminal_equity,
        session_col_used=session_col_used,
        funding_events=len(res.funding_payments),
        session_mode=session.mode,
        session_reason=session.reason,
    )


def run_engine_simulation(
    panel_df: pl.DataFrame,
    raw_positions: np.ndarray | Sequence[Sequence[float]],
    ts: list[Any],
    assets: list[str],
    contract: EvaluationContract,
    val_slice: slice,
    *,
    cost_multiplier: float = 1.0,
    delay_offset: int = 0,
    initial_cash: float = 1_000_000.0,
    allow_leverage: bool = False,
    allow_short_selling: bool = True,
    max_gross_leverage: float | None = None,
    max_volume_participation: float | None = None,
    market_impact_coefficient: float | None = None,
    market_impact_volatility: float = 0.02,
) -> EngineSimOutput:
    """Run canonical ml4t.backtest.Engine replay for a target-weight matrix."""
    require_execution_columns(panel_df, contract)
    # Contract-owned policy wins over every caller-supplied limit.
    allow_leverage, max_gross_leverage = resolve_execution_limits(
        contract, allow_leverage, max_gross_leverage
    )

    raw_pos = np.asarray(raw_positions, dtype=float)
    expected_shape = (len(ts), len(assets))
    if raw_pos.shape != expected_shape:
        raise ValueError(
            f"raw_positions shape {raw_pos.shape} does not match expected {expected_shape}"
        )
    if not np.isfinite(raw_pos).all():
        raise ValueError("raw_positions contains non-finite values")

    start = val_slice.start or 0
    stop = val_slice.stop if val_slice.stop is not None else len(ts)
    if start < 0 or stop > len(ts) or start >= stop:
        raise ValueError(f"invalid val_slice {val_slice} for {len(ts)} timestamps")

    extra_delay = max(0, (int(contract.execution_delay_bars) - 1) + int(delay_offset))
    if extra_delay > 0:
        pos_shifted = np.roll(raw_pos, extra_delay, axis=0)
        pos_shifted[:extra_delay] = 0.0
    else:
        pos_shifted = raw_pos

    feed_start = max(0, start - 1)
    target_check = pos_shifted[feed_start:stop]
    if not allow_short_selling and np.any(target_check < -1e-9):
        raise LeveragePolicyError("allow_short_selling=False violated by negative target weights")
    if (
        max_gross_leverage is not None
        and target_check.size > 0
        and float(np.max(np.sum(np.abs(target_check), axis=1))) > float(max_gross_leverage) + 1e-6
    ):
        raise LeveragePolicyError(
            "gross target weight exceeds the contract's max_gross_leverage="
            f"{float(max_gross_leverage):g} (allow_leverage={allow_leverage})"
        )

    feed_ts = ts[feed_start:stop]
    feed_df = panel_df.filter(
        (pl.col("timestamp") >= feed_ts[0]) & (pl.col("timestamp") <= feed_ts[-1])
    ).sort("timestamp", "asset")

    weights_by_ts = _build_weights_by_ts(pos_shifted, ts, assets, feed_start, stop)
    rebalance_cfg = RebalanceConfig(
        allow_short=allow_short_selling,
        allow_fractional=True,
        max_single_weight=10.0 if allow_leverage else 1.0,
        max_gross_leverage=max_gross_leverage,
    )
    strat = _TargetWeightReplayStrategy(weights_by_ts, rebalance_cfg)
    res, gross_ret, net_ret, turnover, terminal_equity, session_col_used, session = _execute_engine_run(
        feed_df,
        strat,
        ts,
        contract,
        start,
        stop,
        cost_multiplier=cost_multiplier,
        initial_cash=initial_cash,
        allow_leverage=allow_leverage,
        allow_short_selling=allow_short_selling,
        max_gross_leverage=max_gross_leverage,
        max_volume_participation=max_volume_participation,
        market_impact_coefficient=market_impact_coefficient,
        market_impact_volatility=market_impact_volatility,
    )
    held_positions = np.roll(pos_shifted, 1, axis=0)
    held_positions[0] = 0.0
    return EngineSimOutput(
        positions=held_positions[start:stop],
        gross_returns=gross_ret,
        net_returns=net_ret,
        turnover=turnover,
        backtest_result=res,
        terminal_equity=terminal_equity,
        session_col_used=session_col_used,
        funding_events=len(res.funding_payments),
        session_mode=session.mode,
        session_reason=session.reason,
    )


def analytical_1p5x_sharpe(
    base_res: BacktestResult,
    bars_per_year: int,
    *,
    val_ts: Sequence[Any] | None = None,
    start_idx: int = 0,
    initial_cash: float = 1_000_000.0,
) -> float:
    """Compute 1.5x cost-stressed annualised Sharpe analytically from baseline fills.

    ``val_ts`` must be the same validation timestamps handed to
    :func:`_extract_aligned_series` (``ts[val_slice]``). The stressed return series
    then spans exactly the validation window and takes its first denominator from
    the bar immediately before it, so a long warmup cannot shift or empty the
    sample. ``start_idx`` is the legacy positional form: the Engine feed starts at
    ``max(0, start_idx - 1)``, so that predecessor bar stays as the denominator and
    every later equity timestamp is validation evidence.
    """
    extra_by_ts: dict[Any, float] = {}
    for fill in base_res.fills:
        extra = 0.5 * _fill_cost(fill)
        extra_by_ts[fill.timestamp] = extra_by_ts.get(fill.timestamp, 0.0) + extra

    cum_extra = 0.0
    stressed_eq: dict[Any, float] = {}
    for bar_ts, eq in base_res.equity_curve:
        cum_extra += extra_by_ts.get(bar_ts, 0.0)
        stressed_eq[bar_ts] = float(eq) - cum_extra

    window: list[Any]
    if val_ts is None:
        curve_ts = list(stressed_eq)
        first = 1 if start_idx > 0 and len(curve_ts) > 1 else 0
        window = curve_ts[first:]
    else:
        window = list(val_ts)
    if len(window) < 2:
        return 0.0

    predecessor = max((t for t in stressed_eq if t < window[0]), default=None)
    prev_eq = (
        stressed_eq[predecessor] if predecessor is not None else float(initial_cash)
    )
    rets = np.empty(len(window), dtype=float)
    for k, bar_ts in enumerate(window):
        if bar_ts not in stressed_eq:
            raise ValueError(
                f"equity curve is missing validation timestamp {bar_ts!r}"
            )
        eq_t = stressed_eq[bar_ts]
        if k == 0 and predecessor is None:
            rets[k] = 0.0
        else:
            denom = prev_eq if prev_eq != 0.0 else float(initial_cash)
            rets[k] = eq_t / denom - 1.0
        prev_eq = eq_t
    return annualised_sharpe(rets, bars_per_year)


def export_backtest_artifacts(
    result: BacktestResult,
    out_dir: Path,
    repo_root: Path | None = None,
    simulation_epoch: str | None = None,
) -> dict[str, Any]:
    """Export Parquet artifacts from BacktestResult and record SHA-256 digests."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    paths = result.to_parquet(
        out_path,
        include=["trades", "fills", "portfolio_state", "funding", "equity"],
    )
    resolved_root = Path(repo_root).resolve() if repo_root is not None else None
    manifest: dict[str, Any] = {}
    for name, file_path in paths.items():
        p = Path(file_path)
        resolved_p = p.resolve()
        if resolved_root is not None and resolved_p.is_relative_to(resolved_root):
            path_str = str(resolved_p.relative_to(resolved_root))
        else:
            path_str = str(p)
        entry: dict[str, Any] = {
            "path": path_str,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        if simulation_epoch is not None:
            entry["simulation_epoch"] = simulation_epoch
        manifest[name] = entry
    return manifest


def _sanitize_json_value(value: Any) -> Any:
    """Recursively convert numpy types and replace non-finite floats with None."""
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        fval = float(value)
        return fval if math.isfinite(fval) else None
    if isinstance(value, np.ndarray):
        return [_sanitize_json_value(item) for item in value.tolist()]
    if isinstance(value, dict):
        return {str(k): _sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_value(item) for item in value]
    return value


def _empty_trade_statistics() -> dict[str, Any]:
    return {
        "n_trades": 0,
        "n_winners": 0,
        "n_losers": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
        "avg_pnl": 0.0,
        "pnl_std": 0.0,
        "pnl_skewness": None,
        "pnl_kurtosis": None,
        "pnl_quartiles": None,
        "avg_winner": 0.0,
        "avg_loser": 0.0,
        "profit_factor": None,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "max_pnl": 0.0,
        "min_pnl": 0.0,
        "avg_duration_days": None,
        "median_duration_days": None,
        "n_known_durations": 0,
        "duration_quartiles": None,
    }


def compute_engine_diagnostics(
    base_res: BacktestResult,
    net_returns: np.ndarray,
    val_ts: list[Any],
    contract: EvaluationContract,
    grid_return_matrix: np.ndarray | None = None,
) -> dict[str, Any]:
    """Compute sanitized ml4t-diagnostic portfolio, trade, PBO, and RAS analytics."""
    ret_arr = np.asarray(net_returns, dtype=float)
    pa_metrics = PortfolioAnalysis(
        returns=ret_arr,
        dates=val_ts,
        periods_per_year=contract.bars_per_year,
    ).compute_summary_stats()
    portfolio_analysis = _sanitize_json_value(pa_metrics.to_dict())

    closed_records = [
        rec
        for rec in base_res.to_trade_records()
        if rec.get("status", "closed") == "closed"
    ]
    if closed_records:
        recs: list[TradeRecord] = []
        for rec in closed_records:
            norm = dict(rec)
            qty = norm.get("quantity")
            if qty is not None and float(qty) < 0.0:
                norm["quantity"] = abs(float(qty))
                if not norm.get("direction"):
                    norm["direction"] = "short"
            recs.append(TradeRecord(**norm))
        trade_stats = TradeAnalysis(recs).compute_statistics().model_dump()
    else:
        trade_stats = _empty_trade_statistics()
    trade_analysis = _sanitize_json_value(trade_stats)

    pbo_diag: dict[str, Any] | None = None
    ras_diag: dict[str, Any] | None = None
    if grid_return_matrix is not None:
        mat = np.asarray(grid_return_matrix, dtype=float)
        if (
            mat.ndim == 2
            and mat.shape[1] >= 2
            and mat.shape[0] >= 16
            and np.isfinite(mat).all()
        ):
            n_bars, n_strat = mat.shape
            z_mat = mat / (np.std(mat, axis=0, ddof=1) + 1e-12)
            complexity = max(
                0.0,
                float(rademacher_complexity(z_mat, n_simulations=2000, random_state=42)),
            )
            sharpe_vec = np.asarray(
                [per_bar_sharpe(mat[:, j]) for j in range(n_strat)], dtype=float
            )
            ras_res = ras_sharpe_adjustment(
                sharpe_vec,
                complexity=complexity,
                n_samples=n_bars,
                n_strategies=n_strat,
                delta=0.05,
                return_result=True,
            )
            ras_diag = _sanitize_json_value(
                {
                    **asdict(ras_res),
                    "rademacher_complexity": complexity,
                    "n_samples": n_bars,
                    "n_strategies": n_strat,
                    "delta": 0.05,
                }
            )

            n_blocks = max(b for b in (4, 6, 8) if n_bars >= b * 4)
            usable = (n_bars // n_blocks) * n_blocks
            blocks = np.split(mat[:usable], n_blocks, axis=0)
            all_idx = set(range(n_blocks))
            is_rows: list[np.ndarray] = []
            oos_rows: list[np.ndarray] = []
            for is_idx in itertools.combinations(range(n_blocks), n_blocks // 2):
                oos_idx = tuple(sorted(all_idx - set(is_idx)))
                is_mat = np.concatenate([blocks[i] for i in is_idx], axis=0)
                oos_mat = np.concatenate([blocks[i] for i in oos_idx], axis=0)
                is_rows.append(
                    np.asarray([per_bar_sharpe(is_mat[:, j]) for j in range(n_strat)], dtype=float)
                )
                oos_rows.append(
                    np.asarray([per_bar_sharpe(oos_mat[:, j]) for j in range(n_strat)], dtype=float)
                )
            pbo_res = compute_pbo(np.asarray(is_rows), np.asarray(oos_rows))
            pbo_diag = _sanitize_json_value(
                {**asdict(pbo_res), "n_blocks": n_blocks}
            )

    return {
        "portfolio_analysis": portfolio_analysis,
        "trade_analysis": trade_analysis,
        "pbo": pbo_diag,
        "ras": ras_diag,
    }
