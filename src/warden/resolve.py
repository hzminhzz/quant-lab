"""Resolve a declarative :class:`DataRequirement` before any strategy code runs (#116).

A requirement says *what data a strategy needs*.  It never says what that data costs,
when the holdout starts, how much leverage is allowed or which search-debt group pays
for the attempt: those are evaluator policy (``warden_ops.venue_policy``) and evaluator
state (``warden.registry``), so a policy-named key is refused by the model, not ignored.

Resolution answers one question, in this order:

1. **reuse_signed** — an *active* signed contract already carries the required symbols
   at the required cadence, price basis and execution mode, verified through the same
   provenance gate the evaluator uses (``warden.provenance.resolve_dataset``), and its
   first bar precedes ``min_start``.  An exact symbol set is preferred; a superset is
   accepted only with the extra symbols named explicitly, never silently.
2. **compile_required** — no such contract, but the lake advertises every required
   symbol with enough history, so a contract can be compiled for it (lane J).
3. **fail_closed** — the requirement cannot be served: a symbol the lake does not carry,
   history that starts too late, a lake that cannot be read, a policy domain with no
   selection group, or a symbol outside the requested sub-family.

Nothing here writes: no contract, no dataset manifest, no Parquet, no registry row, and
no holdout byte is opened.  Archived contracts are not enumerated at all — the active
contract paths come from :mod:`warden.contract_source`, which is the single authority
for "what may carry new work".
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from warden.contract_source import active_contract_paths, dataset_manifest_path
from warden.contracts import EvaluationContract, load_contract
from warden.lake_root import LAKE_STORE_ENV, resolve_lake_root
from warden.provenance import DatasetProvenanceError, resolve_dataset
from warden_ops.venue_policy import (
    SelectionGroupResolutionError,
    VenuePolicy,
    VenuePolicyError,
    load_venue_policy,
    require_selection_group,
)

__all__ = [
    "DECISION_COMPILE_REQUIRED",
    "DECISION_FAIL_CLOSED",
    "DECISION_REUSE_SIGNED",
    "DataRequirement",
    "RequirementSourceError",
    "ResolutionReport",
    "load_requirement",
    "resolve_data_requirement",
]

DECISION_REUSE_SIGNED = "reuse_signed"
DECISION_COMPILE_REQUIRED = "compile_required"
DECISION_FAIL_CLOSED = "fail_closed"

CATALOG_REL = Path(".catalog") / "research-dataset-catalog.json"
METADATA_DIR = Path(".metadata")

#: Frequencies a minute store is resampled into (lane J owns the resampling itself).
RESAMPLED_FROM_MINUTE = frozenset({"5m", "15m", "30m", "1h", "4h"})
#: The cadence a requirement is *sourced* from: anything intraday below a day comes from
#: the canonical minute store, a daily requirement from the daily store.
DAILY_FREQUENCIES = frozenset({"1d"})

#: ``dataset_key`` prefixes whose remainder is the instrument symbol.  This mirrors
#: ``data.query.MarketDataQuery._symbol_from_dataset_key`` in ml4t-data: the lake's own
#: layout is the adapter boundary, so it is restated once, here, and never guessed.
_SYMBOL_PREFIXES = (
    "crypto_futures_ohlcv_1m_HYPERLIQUID_",
    "crypto_futures_open_interest_1m_HYPERLIQUID_",
    "crypto_futures_second_HYPERLIQUID_",
    "crypto_futures_minute_",
    "crypto_futures_funding_",
    "forex_minute_",
    "forex_daily_",
    "index_minute_",
    "index_second_",
    "index_daily_",
    "fred_daily_",
    "equity_daily_",
    "equity_hourly_",
)
_CADENCE_ALIASES = {
    "minute": "minute",
    "1m": "minute",
    "hourly": "hourly",
    "1h": "hourly",
    "daily": "daily",
    "1d": "daily",
    "second": "second",
    "1s": "second",
}
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

AssetClass = Literal["crypto_perps", "fx", "indices", "metals", "commodities", "multi_asset"]
Frequency = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
PriceBasis = Literal["ohlcv", "ohlcv_funding", "quote_ohlcv"]
ExecutionMode = Literal["NEXT_BAR", "QUOTE_SIDE"]


class RequirementSourceError(ValueError):
    """The requirement document cannot be read or is not an object of declared fields."""


def _utc_timestamp(value: object) -> datetime:
    """Accept an ISO-8601 string or datetime; require an explicit timezone."""

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"min_start is not an ISO-8601 datetime: {value!r}") from exc
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("min_start must be an ISO-8601 datetime with an explicit timezone")
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:  # pragma: no cover - defensive
        raise ValueError("min_start is outside the UTC datetime range") from exc


UTCDatetime = Annotated[AwareDatetime, BeforeValidator(_utc_timestamp)]
Symbol = Annotated[str, Field(pattern=SYMBOL_PATTERN.pattern)]


class DataRequirement(BaseModel):
    """What a strategy needs, and nothing a strategy may not decide.

    Strict (``extra="forbid"``) and immutable (``frozen=True``) exactly like
    :class:`warden.schemas.ExperimentSpec`.  There is deliberately no cost, holdout,
    leverage or ``selection_group_id`` field: those come from the signed venue policy,
    and an agent-authored document that tries to set one raises a ``ValidationError``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_class: AssetClass
    sub_family: str | None = Field(default=None, min_length=1)
    #: Either an explicit symbol list, or ``all_in_family`` — never both, never neither.
    symbols: tuple[Symbol, ...] = ()
    all_in_family: bool = False
    frequency: Frequency
    min_start: UTCDatetime
    price_basis: PriceBasis
    execution_mode: ExecutionMode

    @model_validator(mode="after")
    def _one_symbol_source(self) -> DataRequirement:
        if self.all_in_family and self.symbols:
            raise ValueError("declare either explicit `symbols` or `all_in_family`, not both")
        if not self.all_in_family and not self.symbols:
            raise ValueError("declare explicit `symbols` or set `all_in_family: true`")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("`symbols` must not repeat a symbol")
        return self

    @property
    def ordered_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self.symbols))


def load_requirement(source: DataRequirement | Mapping[str, Any] | str | Path) -> DataRequirement:
    """Read a requirement from a model, a mapping, a JSON/YAML path or inline JSON.

    Anything that is not an object of the declared fields is refused; a prose document
    is not a requirement, and guessing what an agent meant is not this command's job.
    """

    if isinstance(source, DataRequirement):
        return source
    if isinstance(source, Mapping):
        return DataRequirement(**dict(source))
    raw = Path(source) if isinstance(source, Path) else None
    if raw is not None or (isinstance(source, str) and source.strip()[:1] not in {"{", "["}):
        path = raw if raw is not None else Path(str(source))
        if not path.is_file():
            raise RequirementSourceError(f"requirement document not found: {path}")
        text = path.read_text(encoding="utf-8")
    else:
        text = str(source)
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RequirementSourceError(f"requirement document is not readable JSON/YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise RequirementSourceError(
            "requirement document must be a JSON/YAML object of the declared fields")
    return DataRequirement(**document)


# ---------------------------------------------------------------------------
# the lake as an availability index (catalogue + store metadata, no Parquet reads)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LakeSymbol:
    """One symbol the lake advertises, with the earliest start it can prove."""

    symbol: str
    dataset_key: str
    first_bar: str
    precision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_key": self.dataset_key,
            "first_bar": self.first_bar,
            "precision": self.precision,
        }


@dataclass(frozen=True)
class LakeView:
    status: str
    reason: str
    root: str | None
    cadence: str
    symbols: Mapping[str, LakeSymbol]
    unattributed: tuple[str, ...] = ()
    outside_family: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.status == "available"

    def earliest_common_start(self, required: tuple[str, ...]) -> str | None:
        starts = [self.symbols[s].first_bar for s in required if s in self.symbols]
        if not starts or len(starts) != len(required):
            return None
        return max(starts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "root": self.root,
            "cadence": self.cadence,
            "symbols": {name: entry.to_dict() for name, entry in sorted(self.symbols.items())},
            "unattributed_dataset_keys": list(self.unattributed),
            "outside_family_symbols": list(self.outside_family),
        }


def _source_cadence(frequency: str) -> str:
    """The store cadence this frequency is sourced from."""

    return "daily" if frequency in DAILY_FREQUENCIES else "minute"


def _normalise_cadence(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return _CADENCE_ALIASES.get(value.strip().lower())


def _symbol_from_dataset_key(dataset_key: str) -> str | None:
    for prefix in _SYMBOL_PREFIXES:
        if dataset_key.startswith(prefix):
            symbol = dataset_key[len(prefix):]
            return re.sub(r"_PERP$", "", symbol) or None
    return None


def _month_bound(year: int, month: int) -> datetime:
    """The conservative first bar for a month-granular partition: the next month starts."""

    return datetime(year + 1, 1, 1, tzinfo=UTC) if month == 12 else datetime(
        year, month + 1, 1, tzinfo=UTC)


def _partition_bounds(partitions: list[str]) -> dict[str, tuple[int, int]]:
    """``symbol=`` partitions -> earliest ``(year, month)`` per symbol."""

    found: dict[str, tuple[int, int]] = {}
    for partition in partitions:
        parts = partition.split("/")
        symbol = next((p.split("=", 1)[1] for p in parts if p.startswith("symbol=")), None)
        if not symbol:
            continue
        year = month = None
        for part in parts:
            if part.startswith("year=") and part[5:].isdigit():
                year = int(part[5:])
            elif part.startswith("month=") and part[6:].isdigit():
                month = int(part[6:])
        if year is None or month is None:
            continue
        current = found.get(symbol)
        if current is None or (year, month) < current:
            found[symbol] = (year, month)
    return found


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_lake(
    requirement: DataRequirement, repo_root: Path, lake_root: Path | str | None
) -> LakeView:
    cadence = _source_cadence(requirement.frequency)
    if lake_root is not None:
        root = Path(lake_root).expanduser()
        if not root.is_dir():
            return LakeView("unavailable", f"lake root is not a directory: {root}", None, cadence, {})
        root = root.resolve()
    else:
        try:
            root = resolve_lake_root(repo_root)
        except (FileNotFoundError, OSError) as exc:
            return LakeView("unavailable", str(exc), None, cadence, {})
    catalog_path = root / CATALOG_REL
    if not catalog_path.is_file():
        return LakeView(
            "unavailable",
            f"no research dataset catalog at {catalog_path}; set {LAKE_STORE_ENV} to a store root",
            str(root),
            cadence,
            {},
        )
    try:
        catalog = _read_json(catalog_path)
    except (OSError, ValueError) as exc:
        return LakeView("unavailable", f"unreadable catalog {catalog_path}: {exc}", str(root),
                        cadence, {})
    entries = catalog.get("entries") if isinstance(catalog, dict) else None
    if not isinstance(entries, list):
        return LakeView("unavailable", f"catalog {catalog_path} declares no entries", str(root),
                        cadence, {})

    domain = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("asset_class") == requirement.asset_class
    ]
    # An absent or unknown `frequency` label is *unknown*, never a match for an explicit
    # cadence (ml4t-data taxonomy: absent old-catalog labels are not evidence).
    same_cadence = [e for e in domain if _normalise_cadence(e.get("frequency")) == cadence]
    in_family = (
        same_cadence
        if requirement.sub_family is None
        else [e for e in same_cadence if e.get("sub_family") == requirement.sub_family]
    )

    symbols: dict[str, LakeSymbol] = {}
    unattributed: list[str] = []
    for entry in in_family:
        key = str(entry.get("dataset_key") or "")
        if not key:
            continue
        metadata_path = root / METADATA_DIR / f"{key}.json"
        metadata: dict[str, Any] = {}
        if metadata_path.is_file():
            try:
                loaded = _read_json(metadata_path)
                metadata = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                metadata = {}
        partitions = [p for p in metadata.get("partitions") or [] if isinstance(p, str)]
        per_symbol = _partition_bounds(partitions)
        if per_symbol:
            for symbol, (year, month) in per_symbol.items():
                bound = _month_bound(year, month).isoformat()
                current = symbols.get(symbol)
                if current is None or bound < current.first_bar:
                    symbols[symbol] = LakeSymbol(symbol, key, bound, "month")
            continue
        symbol = _symbol_from_dataset_key(key)
        first = _parse_timestamp(metadata.get("first_timestamp"))
        if symbol is None or first is None:
            unattributed.append(key)
            continue
        current = symbols.get(symbol)
        if current is None or first.isoformat() < current.first_bar:
            symbols[symbol] = LakeSymbol(symbol, key, first.isoformat(), "timestamp")

    outside_family: list[str] = []
    if requirement.sub_family is not None and requirement.symbols:
        family_keys = {
            _symbol_from_dataset_key(str(e.get("dataset_key") or ""))
            for e in same_cadence
            if isinstance(e, dict)
        }
        outside_family = [s for s in requirement.ordered_symbols
                          if s not in symbols and s in family_keys]
    return LakeView(
        "available",
        "",
        str(root),
        cadence,
        symbols,
        tuple(sorted(set(unattributed))),
        tuple(outside_family),
    )


# ---------------------------------------------------------------------------
# signed candidates
# ---------------------------------------------------------------------------


def _manifest_price_basis(record: Mapping[str, Any]) -> str | None:
    declared = record.get("price_basis")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    dataset = record.get("dataset")
    if isinstance(dataset, Mapping):
        declared = dataset.get("price_basis")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
    return None


def _manifest_coverage(
    record: Mapping[str, Any],
) -> tuple[list[str] | None, datetime | None, datetime | None]:
    """Symbols and the coverage window a dataset manifest declares.

    The builder's v2 manifest states them under ``dataset``; the legacy manifest states
    them under ``coverage``.  Neither shape is guessed at: a manifest that declares no
    assets or no first bar is *unknown*, and unknown never satisfies a requirement.
    """

    assets: list[str] | None = None
    first: datetime | None = None
    last: datetime | None = None
    dataset = record.get("dataset")
    if isinstance(dataset, Mapping):
        raw_assets = dataset.get("assets")
        if isinstance(raw_assets, list) and all(isinstance(a, str) for a in raw_assets):
            assets = [str(a) for a in raw_assets]
        first = _parse_timestamp(dataset.get("first_bar"))
        last = _parse_timestamp(dataset.get("last_bar"))
    coverage = record.get("coverage")
    if isinstance(coverage, Mapping):
        per_asset = coverage.get("per_asset")
        if assets is None and isinstance(per_asset, Mapping):
            assets = [str(a) for a in per_asset]
        first = first or _parse_timestamp(coverage.get("min"))
        last = last or _parse_timestamp(coverage.get("max"))
    return assets, first, last


def _manifest_relative(manifest_path: Path, repo_root: Path) -> str:
    try:
        return manifest_path.relative_to(repo_root).as_posix()
    except ValueError:  # a cwd-relative root: the path is already repo-relative
        return manifest_path.as_posix()


def _candidate_for(
    contract: EvaluationContract,
    requirement: DataRequirement,
    required: tuple[str, ...],
    group: str | None,
    repo_root: Path,
    contract_path: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(candidate, None)`` when the contract satisfies the requirement.

    ``(None, None)`` means the contract is outside the requirement's domain (another
    asset class or cadence) and was never a candidate.  ``(None, reason)`` is a refusal
    a reviewer must be able to read.
    """

    if contract.asset_class != requirement.asset_class:
        return None, None
    if contract.frequency != requirement.frequency:
        return None, None
    if contract.execution_mode != requirement.execution_mode:
        return None, (f"execution_mode {contract.execution_mode} != "
                      f"{requirement.execution_mode}")
    declared_group = contract.selection_group_id
    if declared_group and group and declared_group != group:
        return None, (f"selection_group {declared_group} contradicts the venue policy group "
                      f"{group}")
    try:
        resolution = resolve_dataset(contract, repo_root)
    except DatasetProvenanceError as exc:
        return None, str(exc)
    if not resolution.eligible_market:
        return None, f"dataset not market eligible: {resolution.reason}"
    manifest_path = dataset_manifest_path(repo_root, contract.contract_id, allow_archive=False)
    if manifest_path is None:
        return None, (f"missing dataset manifest contracts/datasets/{contract.contract_id}.json")
    try:
        record = _read_json(manifest_path)
    except (OSError, ValueError) as exc:
        return None, f"unreadable dataset manifest {manifest_path}: {exc}"
    if not isinstance(record, Mapping):
        return None, f"dataset manifest {manifest_path} is not a JSON object"
    basis = _manifest_price_basis(record)
    if basis is None:
        return None, ("price_basis undeclared in the dataset manifest: unknown is not "
                      f"{requirement.price_basis}")
    if basis != requirement.price_basis:
        return None, f"price_basis {basis} != {requirement.price_basis}"
    assets, first_bar, last_bar = _manifest_coverage(record)
    if not assets:
        return None, "coverage undeclared: the dataset manifest names no assets"
    if first_bar is None:
        return None, "coverage undeclared: the dataset manifest names no first bar"
    missing = tuple(sorted(set(required) - set(assets)))
    if missing:
        return None, f"missing symbols: {', '.join(missing)}"
    if first_bar > requirement.min_start:
        return None, ("insufficient_history: contract first bar "
                      f"{first_bar.isoformat()} is after min_start "
                      f"{requirement.min_start.isoformat()}")
    return {
        "contract_id": contract.contract_id,
        "path": _manifest_relative(contract_path, repo_root),
        "spec_hash": contract.spec_hash(),
        "provenance": resolution.provenance,
        "data_snapshot_sha256": resolution.sha256,
        "frequency": contract.frequency,
        "execution_mode": contract.execution_mode,
        "price_basis": basis,
        "selection_group_id": declared_group or None,
        "symbols": sorted(set(assets)),
        "first_bar": first_bar.isoformat(),
        "last_bar": last_bar.isoformat() if last_bar is not None else None,
        "dataset_path": resolution.path,
        "dataset_sha256": resolution.sha256,
        "dataset_manifest": _manifest_relative(manifest_path, repo_root),
        "coverage_source": "dataset" if isinstance(record.get("dataset"), Mapping) else "coverage",
    }, None


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolutionReport:
    """What the resolver decided, and every fact the decision rests on."""

    requirement: DataRequirement
    decision: str
    reason: str
    lake: dict[str, Any]
    policy: dict[str, Any]
    selection_group_id: str | None
    selection_group_source: str
    resample: dict[str, Any]
    matched_contract: dict[str, Any] | None = None
    available_symbols: tuple[str, ...] = ()
    missing_symbols: tuple[str, ...] = ()
    symbols_to_drop: tuple[str, ...] = ()
    earliest_common_start: str | None = None
    shortfall_start: str | None = None
    rejected_candidates: tuple[dict[str, Any], ...] = ()
    gap: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.decision != DECISION_FAIL_CLOSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement.model_dump(mode="json"),
            "decision": self.decision,
            "reason": self.reason,
            "lake": dict(self.lake),
            "policy": dict(self.policy),
            "selection_group_id": self.selection_group_id,
            "selection_group_source": self.selection_group_source,
            "resample": dict(self.resample),
            "matched_contract": self.matched_contract,
            "available_symbols": list(self.available_symbols),
            "missing_symbols": list(self.missing_symbols),
            "symbols_to_drop": list(self.symbols_to_drop),
            "earliest_common_start": self.earliest_common_start,
            "shortfall_start": self.shortfall_start,
            "rejected_candidates": [dict(item) for item in self.rejected_candidates],
            "gap": self.gap,
        }


def _resample_block(requirement: DataRequirement) -> dict[str, Any]:
    required = requirement.frequency in RESAMPLED_FROM_MINUTE
    return {
        "required": required,
        "source_frequency": "1m" if required else requirement.frequency,
        "target_frequency": requirement.frequency,
        "rule": ("left-labelled, left-closed [t, t+interval) aggregation of the canonical "
                 "minute store" if required else "native cadence; no resampling"),
    }


def resolve_data_requirement(
    requirement: DataRequirement | Mapping[str, Any] | str | Path,
    *,
    repo_root: Path | str = ".",
    lake_root: Path | str | None = None,
    policy: VenuePolicy | None = None,
) -> ResolutionReport:
    """Resolve one requirement against active signed contracts and the lake.

    Read-only by construction: it opens contracts, their dataset manifests and the
    lake catalogue/metadata, and returns a report.  It never constructs the registry,
    never writes a contract or a dataset, and never opens a holdout path.
    """

    req = load_requirement(requirement)
    root = Path(repo_root)

    policy_document: dict[str, Any] = {"trusted": False, "sha256": None, "reason": ""}
    resolved_policy = policy
    if resolved_policy is None:
        try:
            resolved_policy = load_venue_policy(root)
        except VenuePolicyError as exc:
            policy_document["reason"] = str(exc)
    group: str | None = None
    if resolved_policy is not None:
        policy_document.update(trusted=True, sha256=resolved_policy.sha256, reason="")
        try:
            group = require_selection_group(
                resolved_policy, req.asset_class, req.frequency)
        except SelectionGroupResolutionError as exc:
            policy_document["reason"] = str(exc)
            return _fail_closed(req, "policy_unmapped_domain", str(exc), lake={}, policy=policy_document)

    lake = _load_lake(req, root, lake_root)
    lake_payload = lake.to_dict()
    resample = _resample_block(req)

    if lake.outside_family:
        return _fail_closed(
            req,
            "symbol_outside_family",
            (f"symbols outside sub-family {req.sub_family!r}: "
             f"{', '.join(lake.outside_family)}"),
            lake=lake_payload,
            policy=policy_document,
            group=group,
            resample=resample,
            missing=lake.outside_family,
        )

    required: tuple[str, ...]
    if req.all_in_family:
        if not lake.available:
            return _fail_closed(
                req, "lake_unavailable",
                f"`all_in_family` needs the lake to enumerate the family: {lake.reason}",
                lake=lake_payload, policy=policy_document, group=group, resample=resample,
            )
        symbols = tuple(sorted(lake.symbols))
        if not symbols:
            detail = (
                f"the lake cannot attribute symbols to {req.asset_class!r} at cadence "
                f"{lake.cadence!r}: unattributed dataset keys {list(lake.unattributed)}"
                if lake.available and lake.unattributed
                else (f"the lake advertises no {req.asset_class!r}/{req.sub_family or '*'} "
                      f"symbol at cadence {lake.cadence!r}")
            )
            return _fail_closed(
                req, "family_enumeration_unavailable", detail,
                lake=lake_payload, policy=policy_document, group=group, resample=resample,
            )
        required = symbols
    else:
        required = req.ordered_symbols

    rejected: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for path in active_contract_paths(root):
        try:
            contract = load_contract(path)
        except (OSError, ValueError) as exc:
            rejected.append({"contract_id": path.stem, "reason": f"unreadable contract: {exc}"})
            continue
        candidate, reason = _candidate_for(contract, req, required, group, root, path)
        if reason is not None:
            rejected.append({"contract_id": contract.contract_id, "reason": reason})
        elif candidate is not None:
            eligible.append(candidate)

    if eligible:
        eligible.sort(key=lambda c: (len(c["symbols"]) - len(required), c["contract_id"]))
        chosen = eligible[0]
        exact = sorted(chosen["symbols"]) == sorted(required)
        return ResolutionReport(
            requirement=req,
            decision=DECISION_REUSE_SIGNED,
            reason="exact_signed_match" if exact else "superset_signed_match",
            lake=lake_payload,
            policy=policy_document,
            selection_group_id=group,
            selection_group_source="policy" if group else "none",
            resample=resample,
            matched_contract=chosen,
            available_symbols=tuple(chosen["symbols"]),
            missing_symbols=(),
            symbols_to_drop=tuple(sorted(set(chosen["symbols"]) - set(required))),
            earliest_common_start=chosen["first_bar"],
            shortfall_start=None,
            rejected_candidates=tuple(rejected),
        )

    missing = tuple(sorted(s for s in required if s not in lake.symbols))
    if not lake.available:
        return _fail_closed(
            req, "lake_unavailable",
            (f"no eligible signed contract and the lake is unavailable: {lake.reason}"),
            lake=lake_payload, policy=policy_document, group=group, resample=resample,
            rejected=rejected,
        )
    if missing:
        return _fail_closed(
            req, "missing_symbols",
            f"the lake carries no data for {', '.join(missing)}",
            lake=lake_payload, policy=policy_document, group=group, resample=resample,
            missing=missing, rejected=rejected,
        )
    common_start = lake.earliest_common_start(required)
    if common_start is None or datetime.fromisoformat(common_start) > req.min_start:
        return _fail_closed(
            req, "insufficient_history",
            f"the lake's earliest common start {common_start} is after min_start "
            f"{req.min_start.isoformat()}",
            lake=lake_payload, policy=policy_document, group=group, resample=resample,
            shortfall=common_start, rejected=rejected,
        )
    return ResolutionReport(
        requirement=req,
        decision=DECISION_COMPILE_REQUIRED,
        reason="no_matching_contract",
        lake=lake_payload,
        policy=policy_document,
        selection_group_id=group,
        selection_group_source="policy" if group else "none",
        resample=resample,
        matched_contract=None,
        available_symbols=required,
        missing_symbols=(),
        symbols_to_drop=(),
        earliest_common_start=common_start,
        shortfall_start=None,
        rejected_candidates=tuple(rejected),
    )


def _fail_closed(
    requirement: DataRequirement,
    reason: str,
    detail: str,
    *,
    lake: Mapping[str, Any],
    policy: Mapping[str, Any],
    group: str | None = None,
    resample: Mapping[str, Any] | None = None,
    missing: tuple[str, ...] = (),
    shortfall: str | None = None,
    rejected: list[dict[str, Any]] | None = None,
) -> ResolutionReport:
    gap: dict[str, Any] = {"kind": reason, "detail": detail}
    if missing:
        gap["missing_symbols"] = list(missing)
    if shortfall is not None:
        gap["shortfall_start"] = shortfall
    return ResolutionReport(
        requirement=requirement,
        decision=DECISION_FAIL_CLOSED,
        reason=reason,
        lake=dict(lake),
        policy=dict(policy),
        selection_group_id=group,
        selection_group_source="policy" if group else "none",
        resample=dict(resample or _resample_block(requirement)),
        matched_contract=None,
        available_symbols=(),
        missing_symbols=missing,
        symbols_to_drop=(),
        earliest_common_start=None,
        shortfall_start=shortfall,
        rejected_candidates=tuple(rejected or ()),
        gap=gap,
    )
