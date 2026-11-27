"""The single market-data read path for every evaluator stage (issue #95).

Before #95 four loaders had drifted: ``warden verify`` inlined the Parquet load inside
``cmd_verify``, ``warden screen``/``backtest`` used ``runner.prepare_panel``, ``qualify``
opened the holdout file directly and each one normalised columns, filtered symbols and
fenced timestamps on its own.  The same contract could therefore be fenced, annualised or
symbol-filtered differently depending on which command ran it.

:class:`MarketDataGateway` is now the only reader of market bars.  In one place it

* resolves the contract's own pinned dataset (v1 manifest or v2 governance) and verifies its
  SHA-256, failing closed on an unpinned holdout **before** any holdout byte is touched;
* normalises a v1/v2 feed to one shape (``asset`` entity column, UTC-aware microsecond
  ``timestamp``, ``timestamp, asset`` ordering) and validates it against the contract's
  ``FeedSpec``;
* applies the exploratory temporal fence (no bar at or after ``holdout_start``);
* for ``role="holdout"`` concatenates the *verified* exploratory bars that precede
  ``holdout_start`` as a read-only warmup prefix, asserts the prefix ends before the first
  holdout bar, and exposes ``eval_slice`` covering every holdout bar;
* measures the bar calendar from the retained bars, so a panel narrowed to
  ``target_symbols`` is annualised on *its own* elapsed calendar instead of inheriting a
  multi-exchange union ``bars_per_year``;
* preserves the contract's whole validation timestamp grid instead of trimming it.

``pl.read_parquet`` in any other evaluator module is an integrity violation
(``warden_ops.integrity.check_ast_boundaries``).  The two permitted exceptions are the
feature cache (Warden-owned features) and the return-artifact reader (Warden-owned
simulated returns, never market bars).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from dataclasses import replace as _replace
from pathlib import Path
from typing import Any

import polars as pl

from warden.contracts import (
    ContractValidationError,
    EvaluationContract,
    validate_panel_feed_spec,
)
from warden.provenance import (
    DatasetProvenanceError,
    DatasetResolution,
    manifest_path_for,
    resolve_contract_governance,
    resolve_dataset,
)

ROLE_EXPLORATORY = "exploratory"
ROLE_HOLDOUT = "holdout"
ROLES: tuple[str, ...] = (ROLE_EXPLORATORY, ROLE_HOLDOUT)

#: A production holdout window with fewer bars than this is not a scientific result.
MIN_HOLDOUT_BARS = 20

#: Julian year in seconds.  Bar calendars are measured on elapsed calendar years.
SECONDS_PER_YEAR = 365.25 * 24.0 * 3600.0

#: ``contracts/datasets`` manifests written by the declarative builder carry this version.
BUILT_MANIFEST_SCHEMA_VERSION = 2
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_BUILD_DIGEST_KEYS = ("spec_sha256", "raw_inputs_merkle_sha256")

_UTC_ZONES = frozenset({"UTC", "Etc/UTC"})
_UTC_DTYPE = pl.Datetime("us", "UTC")


def read_parquet_bytes(payload: bytes) -> pl.DataFrame:
    """Parse in-memory Parquet bytes — the one reader the dataset builder is injected with.

    ``warden data build`` reads raw lake partitions and a pinned parent, not a
    contract-pinned dataset, so this is not a science read path: nothing is resolved,
    normalised, fenced or admitted here.  It lives in this module because ``pl.read_parquet``
    is permitted only in the market-data readers (``warden_ops.integrity.check_ast_boundaries``),
    and the alternative — a fourth allowlisted reader or a bare read in the CLI — would widen
    that boundary instead of reusing it.  The builder never imports a reader: the CLI hands it
    this function.  The caller has already verified any digest pin over ``payload``, and the
    builder's mandatory quality gate and temporal fence run on whatever this returns, before
    a byte is published.
    """
    return pl.read_parquet(io.BytesIO(payload))


def dataset_build_identity(record: dict[str, Any] | None) -> dict[str, Any]:
    """The builder evidence a v2 dataset manifest declares; ``{}`` for a legacy manifest.

    A v1 manifest carries no build identity and none is invented for it.  A v2 manifest is
    refused when the evidence it *does* declare is not a 64-hex digest: "unknown provenance
    plus a malformed builder claim" is not a build, it is a broken artifact, and the gateway
    is the last place that can say so before a trial records it as data.
    """
    if not isinstance(record, dict):
        return {}
    try:
        version = int(record.get("schema_version") or 1)
    except (TypeError, ValueError) as exc:
        raise DatasetProvenanceError(
            f"dataset manifest schema_version {record.get('schema_version')!r} is not an integer"
        ) from exc
    if version < BUILT_MANIFEST_SCHEMA_VERSION:
        return {}
    builder = record.get("builder")
    builder = builder if isinstance(builder, dict) else {}
    claimed = {key: record.get(key) for key in _BUILD_DIGEST_KEYS}
    claimed["code_closure_sha256"] = builder.get("code_closure_sha256")
    for key, value in claimed.items():
        if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
            raise DatasetProvenanceError(
                f"dataset manifest claims schema_version {version} but {key} is not a 64-hex digest"
            )
    return {
        "schema_version": version,
        "dataset_id": str(record.get("dataset_id") or ""),
        "spec_sha256": claimed["spec_sha256"],
        "builder_name": str(builder.get("name") or ""),
        "builder_version": str(builder.get("version") or ""),
        "builder_code_closure_sha256": claimed["code_closure_sha256"],
        "raw_inputs_merkle_sha256": claimed["raw_inputs_merkle_sha256"],
    }


@dataclass(eq=False)
class MarketPanel:
    """One normalised market feed plus the exact slice that may be evaluated.

    ``panel`` is the full normalised frame (a holdout panel includes its warmup prefix);
    ``eval_slice`` indexes ``ts`` and covers the contract's validation window or **all**
    holdout bars.  ``warmup_rows`` counts the pre-holdout training rows that precede it.
    ``build_identity`` is the builder's own evidence for a v2-built dataset and empty for
    anything a human or an older reader produced.
    """

    contract_id: str
    role: str
    panel: pl.DataFrame
    ts: tuple[Any, ...]
    assets: tuple[str, ...]
    eval_slice: slice
    warmup_rows: int
    bars_per_year: int
    contract_bars_per_year: int
    measured_bars_per_year: float | None
    symbols_filtered: bool
    spec_version: int
    resolution: DatasetResolution | None = None
    holdout_override: bool = False
    build_identity: dict[str, Any] = field(default_factory=dict)

    @property
    def dataset_is_built(self) -> bool:
        """True when these bars came from a declarative build, not a hand-pinned file."""
        return bool(self.build_identity)

    @property
    def total_bars(self) -> int:
        return len(self.ts)

    @property
    def eval_bars(self) -> int:
        start = self.eval_slice.start or 0
        stop = self.eval_slice.stop if self.eval_slice.stop is not None else len(self.ts)
        return stop - start

    @property
    def eval_ts(self) -> tuple[Any, ...]:
        return self.ts[self.eval_slice]

    @property
    def provenance(self) -> str:
        return self.resolution.provenance if self.resolution is not None else "override"

    @property
    def market_eligible(self) -> bool:
        return bool(self.resolution is not None and self.resolution.eligible_market)

    def validation_grid_preserved(self, contract: EvaluationContract) -> bool:
        """True when ``eval_slice`` holds *every* panel timestamp in ``[val_start, val_end)``."""
        start = self.eval_slice.start or 0
        stop = self.eval_slice.stop if self.eval_slice.stop is not None else len(self.ts)
        window = [t for t in self.ts if contract.val_start <= t < contract.val_end]
        return tuple(window) == self.ts[start:stop]


def select_symbols(
    panel: pl.DataFrame, symbols: Any, *, label: str = ""
) -> tuple[pl.DataFrame, bool]:
    """Filter to ``target_symbols``; report whether the symbol set was actually narrowed.

    Unknown symbols raise (never silently drop a requested asset), matching the
    pre-reservation guard the runner has always relied on.
    """
    if not symbols:
        return panel, False
    if not isinstance(symbols, (list, tuple, set)) or len(symbols) == 0:
        raise ValueError(f"target_symbols must be a non-empty list of symbols, got {symbols!r}")
    sym_col = "asset" if "asset" in panel.columns else "symbol"
    available = set(panel[sym_col].unique().to_list())
    unknown = set(symbols) - available
    if unknown:
        raise ValueError(
            f"target_symbols contains unknown symbols {sorted(unknown)}; "
            f"available in {label or 'panel'}: {sorted(available)}"
        )
    narrowed = set(symbols) != available
    return panel.filter(pl.col(sym_col).is_in(list(symbols))), narrowed


def normalise_feed(panel: pl.DataFrame, contract: EvaluationContract) -> pl.DataFrame:
    """One v1/v2 feed shape: ``asset`` entity, UTC-aware microsecond timestamp, sorted.

    A v1 dataset may name its entity column ``symbol``; a v2 feed is already unambiguous.
    Both converge here so every caller sees the same schema.  A timezone-naive timestamp is
    deliberately left untouched — ``validate_panel_feed_spec`` refuses it rather than
    having the gateway guess a zone.
    """
    if panel.is_empty():
        raise ContractValidationError(f"{contract.contract_id}: panel is empty")
    df = panel.rename({"symbol": "asset"}) if (
        "symbol" in panel.columns and "asset" not in panel.columns
    ) else panel
    if "timestamp" not in df.columns:
        raise ContractValidationError(
            f"{contract.contract_id}: panel has no 'timestamp' column"
        )
    dtype = df.schema["timestamp"]
    if isinstance(dtype, pl.Datetime) and not dtype.time_zone:
        raise ContractValidationError(
            f"{contract.contract_id}: 'timestamp' must be UTC-aware Datetime, got {dtype}"
        )
    if isinstance(dtype, pl.Datetime) and dtype.time_zone in _UTC_ZONES:
        if dtype != _UTC_DTYPE:
            df = df.with_columns(pl.col("timestamp").cast(_UTC_DTYPE))
    elif isinstance(dtype, pl.Datetime) and dtype.time_zone:
        df = df.with_columns(
            pl.col("timestamp").dt.convert_time_zone("UTC").cast(_UTC_DTYPE)
        )
    return df.sort("timestamp", "asset")


def measured_bars_per_year(ts: Any) -> float | None:
    """Bars per elapsed calendar year, measured on the timestamps actually retained.

    ``len(ts) - 1`` intervals over the elapsed span (not ``len(ts)``), so a contiguous
    hourly grid measures ~8766 instead of overstating the calendar by one bar.
    """
    if len(ts) < 2:
        return None
    elapsed = (ts[-1] - ts[0]).total_seconds()
    if elapsed <= 0:
        return None
    return (len(ts) - 1) * SECONDS_PER_YEAR / elapsed


def _validation_slice(ts: list[Any], contract: EvaluationContract) -> slice:
    import bisect

    start = bisect.bisect_left(ts, contract.val_start)
    stop = bisect.bisect_left(ts, contract.val_end)
    if start >= stop:
        raise ContractValidationError(
            f"{contract.contract_id}: validation slice {contract.val_start.isoformat()}.."
            f"{contract.val_end.isoformat()} contains no bars; refusing an empty validation panel"
        )
    return slice(start, stop)


def _assert_exploratory_fence(panel: pl.DataFrame, contract: EvaluationContract) -> None:
    """No exploratory bar may sit at or after ``holdout_start`` (issue #88)."""
    latest = panel["timestamp"].max()
    if latest is not None and latest >= contract.holdout_start:
        raise ContractValidationError(
            f"{contract.contract_id}: exploratory panel reaches {latest} at or past holdout_start "
            f"{contract.holdout_start.isoformat()}; holdout bars may not be loaded as exploratory data"
        )


def _window(panel: pl.DataFrame, start: Any, end: Any) -> pl.DataFrame:
    return panel.filter(
        (pl.col("timestamp") >= start) & (pl.col("timestamp") <= end)
    )


def _concat_same_feed(
    warmup: pl.DataFrame, window: pl.DataFrame, contract: EvaluationContract
) -> pl.DataFrame:
    """Strict vertical concat: a warmup prefix must describe the same feed as the holdout.

    ``how="relaxed"`` would silently fill a column the other side lacks with nulls, which
    turns a schema mismatch into a plausible-looking panel.  The two sides are compared by
    name and dtype and refused when they differ; only column *order* is normalised.
    """
    if set(warmup.columns) != set(window.columns):
        missing = sorted(set(window.columns) - set(warmup.columns))
        extra = sorted(set(warmup.columns) - set(window.columns))
        raise ContractValidationError(
            f"{contract.contract_id}: exploratory warmup and holdout feeds differ "
            f"(only in holdout {missing}, only in exploratory {extra}); refusing to fill nulls"
        )
    columns = list(window.columns)
    for column in columns:
        if warmup.schema[column] != window.schema[column]:
            raise ContractValidationError(
                f"{contract.contract_id}: '{column}' dtype differs between the exploratory "
                f"warmup ({warmup.schema[column]}) and the holdout ({window.schema[column]})"
            )
    return pl.concat([warmup.select(columns), window.select(columns)])


def _fence_free(contract: EvaluationContract) -> EvaluationContract:
    """The contract shifted one span later, its exploratory fence moved past the window.

    ``validate_panel_feed_spec``'s temporal clause is exploratory-only.  A holdout window
    legitimately contains bars at/after ``holdout_start``, so the gateway validates the
    holdout feed against the same ``FeedSpec`` with both holdout bounds shifted past the
    data (the contract invariant ``holdout_start < holdout_end`` still holds) and then
    asserts the real window bounds itself plus the warmup ordering.  Everything else the
    validator checks — UTC dtype, nulls, OHLCV, funding, quotes — still runs unchanged.
    """
    span = contract.holdout_end - contract.holdout_start
    shifted = contract.holdout_end + contract.bar_duration()
    return _replace(contract, holdout_start=shifted, holdout_end=shifted + span)


class MarketDataGateway:
    """The only evaluator-side reader of market bars."""

    def __init__(self, repo_root: Path | str = Path("."), *, validate: bool = True) -> None:
        self.repo_root = Path(repo_root)
        self.validate = bool(validate)

    # -- public API ---------------------------------------------------------

    def load(
        self,
        contract: EvaluationContract,
        role: str = ROLE_EXPLORATORY,
        symbols: Any = None,
        *,
        resolution: DatasetResolution | None = None,
        holdout_override_path: Path | str | None = None,
        min_holdout_bars: int = MIN_HOLDOUT_BARS,
        holdout_scope: dict | None = None,
    ) -> MarketPanel:
        if holdout_scope is not None and (role != ROLE_HOLDOUT or holdout_override_path is not None):
            raise ValueError("A governed scope applies only to the pinned holdout artifact")
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}, got {role!r}")
        spec_version = resolve_contract_governance(contract, self.repo_root).spec_version
        symbols_filtered = False
        if holdout_override_path is not None:
            if role != ROLE_HOLDOUT:
                raise ValueError("holdout_override_path is only meaningful for role='holdout'")
            frame, symbols_filtered = self._read_override(
                Path(holdout_override_path), contract, symbols
            )
            return self._holdout_override_panel(
                contract, frame, symbols_filtered, spec_version, min_holdout_bars
            )
        res = resolution or resolve_dataset(contract, self.repo_root, role=role)
        if role == ROLE_HOLDOUT and not res.eligible_market:
            # Pin rejection precedes the read, structurally: an unpinned or non-market
            # holdout never reaches ``pl.read_parquet`` (and never reaches its digest).
            raise DatasetProvenanceError(
                f"holdout dataset for '{contract.contract_id}' is not market-eligible: {res.reason}"
            )
        identity = self._build_identity(contract)
        if holdout_scope is not None:
            from warden.exposure import Scope, ScopeIncident

            scope = Scope.model_validate(holdout_scope)
            # Verify the exact bytes parsed, not merely a prior provenance read.
            # A changed file must not yield evidence for the approved digest.
            root = self.repo_root.resolve()
            path = root / res.path
            if (res.path != contract.holdout_data_path
                    or path.resolve() != path
                    or not path.is_relative_to(root / "data" / "holdout")):
                raise ScopeIncident("Holdout artifact path differs from its signed scope")
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != scope.dataset_sha256 or res.sha256 != scope.dataset_sha256:
                raise ScopeIncident("Holdout artifact digest differs from its signed scope")
            frame = normalise_feed(read_parquet_bytes(payload), contract)
            # Check every readable observation, before selecting the evaluation universe.
            if (not set(frame["asset"].unique().to_list()) <= set(scope.symbols)
                    or frame["timestamp"].min() < scope.start
                    or frame["timestamp"].max() > scope.end):
                raise ScopeIncident("Holdout artifact exceeds its signed exposure scope; claim remains spent")
            frame, symbols_filtered = select_symbols(frame, symbols, label=res.path)
        else:
            frame, symbols_filtered = self._read_resolved(res, contract, symbols)
        if role == ROLE_EXPLORATORY:
            panel = self._exploratory_panel(contract, frame, res, symbols_filtered, spec_version)
        else:
            panel = self._holdout_panel(
                contract, frame, res, symbols, symbols_filtered, spec_version, min_holdout_bars
            )
        # The manifest is resolved *before* the bars are read and carried on the panel, so a
        # trial can state which build produced its data instead of re-deriving it from a path.
        panel.build_identity = identity
        return panel

    def contract_validation_grid(
        self,
        contract: EvaluationContract,
        *,
        resolution: DatasetResolution | None = None,
    ) -> tuple[Any, ...]:
        """The contract's own validation clock: every timestamp its dataset presents.

        A ``target_symbols`` load narrows the panel to the symbols' own sessions, so its
        ``ts`` is the *subset's* calendar.  Selection evidence has to sit on the clock
        every other attempt on this contract was scored on, so this returns the
        unfiltered exploratory validation grid: one read of the same digest-verified
        dataset, no holdout, and no bar invented — the grid is exactly what the dataset
        contains inside the contract's validation window.
        """
        res = resolution or resolve_dataset(contract, self.repo_root, role=ROLE_EXPLORATORY)
        spec_version = resolve_contract_governance(contract, self.repo_root).spec_version
        frame, _ = self._read_resolved(res, contract, None)
        panel = self._exploratory_panel(contract, frame, res, False, spec_version)
        return tuple(panel.ts[panel.eval_slice])

    def _build_identity(self, contract: EvaluationContract) -> dict[str, Any]:
        """Builder evidence from the contract's dataset manifest, or ``{}`` when legacy.

        The manifest is contract-owned metadata already read for governance; this only adds
        the builder's claim to the panel.  It never promotes provenance: eligibility still
        comes from :func:`resolve_dataset` and the contract's own declaration.
        """
        manifest = manifest_path_for(contract.contract_id, self.repo_root)
        if not manifest.is_file():
            return {}
        try:
            record = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DatasetProvenanceError(
                f"{contract.contract_id}: dataset manifest '{manifest}' is unreadable: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise DatasetProvenanceError(
                f"{contract.contract_id}: dataset manifest '{manifest}' must be a JSON object"
            )
        return dataset_build_identity(record)

    # -- readers ------------------------------------------------------------

    def _read_override(
        self, path: Path, contract: EvaluationContract, symbols: Any
    ) -> tuple[pl.DataFrame, bool]:
        """Fixture-only override: one read, no market-eligibility or FeedSpec gate.

        ``holdout_override_path`` exists so tests can hand ``qualify`` a synthetic panel
        without provisioning a sealed holdout.  It never resolves the contract's dataset,
        so it cannot be used to reach production bytes, and the caller's explicit path is
        the only file opened.
        """
        if not path.is_file():
            raise FileNotFoundError(f"Holdout override dataset not found at '{path}'.")
        frame = normalise_feed(pl.read_parquet(path), contract)
        return select_symbols(frame, symbols, label=str(path))

    def _read_resolved(
        self, res: DatasetResolution, contract: EvaluationContract, symbols: Any
    ) -> tuple[pl.DataFrame, bool]:
        if not res.path:
            raise DatasetProvenanceError(
                f"missing dataset for {contract.contract_id}: {res.reason}"
            )
        frame = normalise_feed(pl.read_parquet(self.repo_root / res.path), contract)
        return select_symbols(frame, symbols, label=res.path)

    # -- roles --------------------------------------------------------------

    def _exploratory_panel(
        self,
        contract: EvaluationContract,
        frame: pl.DataFrame,
        res: DatasetResolution,
        symbols_filtered: bool,
        spec_version: int,
    ) -> MarketPanel:
        _assert_exploratory_fence(frame, contract)
        if self.validate:
            validate_panel_feed_spec(frame, contract)
        ts = frame["timestamp"].unique().sort().to_list()
        eval_slice = _validation_slice(ts, contract)
        return self._make_panel(
            contract, ROLE_EXPLORATORY, frame, ts, eval_slice, 0, symbols_filtered,
            spec_version, res, holdout_override=False,
        )

    def _holdout_panel(
        self,
        contract: EvaluationContract,
        holdout_frame: pl.DataFrame,
        res: DatasetResolution,
        symbols: Any,
        symbols_filtered: bool,
        spec_version: int,
        min_holdout_bars: int,
    ) -> MarketPanel:
        window = _window(holdout_frame, contract.holdout_start, contract.holdout_end)
        if window.height < min_holdout_bars:
            raise ContractValidationError(
                "No sufficient observations within the declared production holdout; "
                "no fallback window"
            )
        _assert_window_bounds(window, contract)
        warmup = self._warmup_prefix(contract, symbols)
        warmup_ts = warmup["timestamp"].unique().sort().to_list()
        # The prefix must end strictly before the first holdout bar: no overlap, no gap
        # that could let a holdout bar double as training evidence.
        latest_warmup = warmup_ts[-1]
        earliest_holdout = window["timestamp"].min()
        if not latest_warmup < earliest_holdout:
            raise ContractValidationError(
                f"{contract.contract_id}: exploratory warmup reaches {latest_warmup} "
                f"which is not strictly before the first holdout bar {earliest_holdout}"
            )
        panel = _concat_same_feed(warmup, window, contract).sort("timestamp", "asset")
        if self.validate:
            validate_panel_feed_spec(panel, _fence_free(contract))
        ts = panel["timestamp"].unique().sort().to_list()
        stop = len(ts)
        if stop - len(warmup_ts) != window["timestamp"].n_unique():
            raise ContractValidationError(
                f"{contract.contract_id}: holdout eval slice would not cover every holdout bar"
            )
        return self._make_panel(
            contract, ROLE_HOLDOUT, panel, ts, slice(len(warmup_ts), stop), len(warmup_ts),
            symbols_filtered, spec_version, res, holdout_override=False,
        )

    def _holdout_override_panel(
        self,
        contract: EvaluationContract,
        frame: pl.DataFrame,
        symbols_filtered: bool,
        spec_version: int,
        min_holdout_bars: int,
    ) -> MarketPanel:
        window = _window(frame, contract.holdout_start, contract.holdout_end)
        prefix = frame.filter(pl.col("timestamp") < contract.holdout_start)
        if window.height >= min_holdout_bars:
            # Fixture-only warmup prefix (#97): when a synthetic override carries bars
            # strictly before the declared window, they are the *prefix* exactly as in a
            # production read — training-only, never part of the eval slice.  This keeps a
            # fixture rehearsal structurally identical to the production panel instead of
            # silently dropping the bars that warm a slow indicator.  A fixture without a
            # prefix keeps the historical window-only shape.
            if prefix.height:
                # Strict vertical concat, exactly like the production prefix: a schema
                # mismatch is refused, never null-filled.
                if prefix["timestamp"].max() >= window["timestamp"].min():
                    raise ContractValidationError(
                        f"{contract.contract_id}: holdout override prefix reaches the window"
                    )
                panel = pl.concat([prefix, window], how="vertical").sort("timestamp", "asset")
                warmup_rows = len(prefix["timestamp"].unique())
                if len(panel["timestamp"].unique()) - warmup_rows != window["timestamp"].n_unique():
                    raise ContractValidationError(
                        f"{contract.contract_id}: holdout override prefix would not leave every window bar in eval"
                    )
            else:
                panel, warmup_rows = window, 0
        else:
            # Fixture-only: the override panel *is* the evaluation window when it does not
            # fill the declared holdout span.  No production contract can reach this branch.
            panel, warmup_rows = frame, 0
        ts = panel["timestamp"].unique().sort().to_list()
        if not ts:
            raise ContractValidationError(
                f"{contract.contract_id}: holdout override panel has no timestamps"
            )
        return self._make_panel(
            contract, ROLE_HOLDOUT, panel, ts, slice(warmup_rows, len(ts)), warmup_rows,
            symbols_filtered, spec_version, None, holdout_override=True,
        )

    def _warmup_prefix(self, contract: EvaluationContract, symbols: Any) -> pl.DataFrame:
        """Verified exploratory bars strictly before ``holdout_start``.

        A production holdout read without a verified pre-holdout prefix is refused: the
        alternative is burning holdout bars as training data, which is exactly what #95
        removes.  The exploratory dataset is resolved and digest-verified like any other
        market read, so the prefix is evidence, not a convenience copy.
        """
        res = resolve_dataset(contract, self.repo_root, role=ROLE_EXPLORATORY)
        # The prefix is filtered by the *same* symbols as the holdout window: a warmup that
        # reintroduced untargeted assets would train the evaluation on a different panel.
        warmup, _ = self._read_resolved(res, contract, symbols)
        _assert_exploratory_fence(warmup, contract)
        if self.validate:
            validate_panel_feed_spec(warmup, contract)
        return warmup

    # -- panel assembly -----------------------------------------------------

    def _make_panel(
        self,
        contract: EvaluationContract,
        role: str,
        panel: pl.DataFrame,
        ts: list[Any],
        eval_slice: slice,
        warmup_rows: int,
        symbols_filtered: bool,
        spec_version: int,
        res: DatasetResolution | None,
        *,
        holdout_override: bool,
    ) -> MarketPanel:
        measured = measured_bars_per_year(ts)
        # The contract's declared calendar describes the whole feed.  Narrowing the symbol
        # set changes the trading calendar (a multi-exchange union is denser than one venue),
        # so the annualisation has to be measured on the bars that survived the filter.
        if symbols_filtered and measured is not None:
            bars_per_year = max(1, round(measured))
        else:
            bars_per_year = int(contract.bars_per_year)
        assets = tuple(sorted(panel["asset"].unique().to_list()))
        return MarketPanel(
            contract_id=contract.contract_id,
            role=role,
            panel=panel,
            ts=tuple(ts),
            assets=assets,
            eval_slice=eval_slice,
            warmup_rows=int(warmup_rows),
            bars_per_year=bars_per_year,
            contract_bars_per_year=int(contract.bars_per_year),
            measured_bars_per_year=measured,
            symbols_filtered=bool(symbols_filtered),
            spec_version=int(spec_version),
            resolution=res,
            holdout_override=holdout_override,
        )


def _assert_window_bounds(window: pl.DataFrame, contract: EvaluationContract) -> None:
    first, last = window["timestamp"].min(), window["timestamp"].max()
    if first < contract.holdout_start or last > contract.holdout_end:
        raise ContractValidationError(
            f"{contract.contract_id}: holdout window {first}..{last} escapes the declared "
            f"span {contract.holdout_start.isoformat()}..{contract.holdout_end.isoformat()}"
        )
