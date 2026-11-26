"""Compile a ``compile_required`` :class:`DataRequirement` into a sandbox artifact (#117).

Lane I answers *which* data a strategy needs and whether an existing signed contract
already carries it.  This module is the write path it deliberately leaves out: when the
answer is ``compile_required``, one fenced exploratory Parquet, one v2 sandbox contract
and one dataset manifest are produced — and nothing else.

What a compile is allowed to decide, and what it may not:

=====================  ==========================================================
signed venue policy    costs, leverage, fences (train/val/holdout), the
                       ``selection_group_id``.  Injected; never overridable.
requirement            asset class, sub-family, symbol basket, frequency,
                       ``min_start``, price basis, execution mode.
this module            deterministic mechanics only (labels, embargo, delay,
                       folds, bars per year, quality tolerances), each recorded
                       in the manifest so a reviewer can diff them.
=====================  ==========================================================

The fence is the whole point.  ``holdout_start`` is the wall: a compile reads only raw
partitions that end at or before it (names first, bytes second), asserts that no input row
reaches it, requires the fence to sit on the target bar grid, and then proves that every
emitted bar *closes* at or before it.  A fence that would straddle one bar is a refusal —
silently truncating the trailing bucket is exactly the leak this ticket exists to stop.

Write-once: the three artifacts are created with ``O_EXCL`` and never overwritten.  A
second compile of the same requirement re-verifies the recorded Parquet digest, the raw
Merkle root (recomputed over the exact input files), the spec digest, the policy digest,
the fence and the contract bytes; identical evidence reuses the artifact and changes no
byte, anything else raises :class:`CompileWriteOnce` naming the field that moved.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from warden.contracts import FREQUENCY_BAR_DURATION, EvaluationContract, canonical_json
from warden.resolve import (
    DECISION_COMPILE_REQUIRED,
    DECISION_FAIL_CLOSED,
    DECISION_REUSE_SIGNED,
    RESAMPLED_FROM_MINUTE,
    DataRequirement,
    ResolutionReport,
    load_requirement,
    resolve_data_requirement,
)
from warden_evidence.dataset_builder.build import build_dataset
from warden_evidence.dataset_builder.containment import (
    LocalRawFileSystem,
    canonical_source_path,
)
from warden_evidence.dataset_builder.merkle import merkle_sha256
from warden_evidence.dataset_builder.spec import parse_dataset_spec
from warden_ops.venue_policy import (
    VenuePolicy,
    VenuePolicyError,
    load_venue_policy,
    require_selection_group,
)

__all__ = [
    "ARTIFACT_CONTRACT",
    "POLICY_OWNED_KEYS",
    "CompileError",
    "CompileFenceError",
    "CompileGap",
    "CompileNotRequired",
    "CompileRefused",
    "CompileWriteOnce",
    "CompiledArtifact",
    "compile_data_requirement",
]

#: Tensor the manifest's ``sandbox`` block is written under.  Promotion (lane K) validates
#: this exact version; a different schema is a different artifact contract.
ARTIFACT_SCHEMA = "spec109.sandbox-artifact.v1"

#: Keys that belong to the signed venue policy.  An agent-supplied value for any of them is
#: refused *before* the first read: the policy is the evaluator's, not the researcher's.
POLICY_OWNED_KEYS = frozenset(
    {
        "taker_fee_bps",
        "slippage_bps",
        "funding_bps_per_8h",
        "allow_leverage",
        "max_gross_leverage",
        "min_expected_move_to_cost_ratio",
        "train_start",
        "val_start",
        "val_end",
        "holdout_start",
        "holdout_end",
        "selection_group_id",
        "selection_groups",
        "policy_sha256",
    }
)

#: Canonical exploratory roots.  The dataset lives under ``data/``; the contract and its
#: manifest share the one sandbox namespace so a reader can tell them from production
#: evidence by path alone, and so a compile never invalidates the human-signed manifest.
PARQUET_DIR = "data/exploratory/sandbox"
SANDBOX_CONTRACTS_DIR = "contracts/sandbox"
SANDBOX_MANIFEST_DIR = "contracts/sandbox/datasets"
HOLDOUT_DIR = "data/holdout/sandbox"
SANDBOX_README = "contracts/sandbox/README.md"

#: Percent move a single target bar may carry before the gate refuses the panel.  These are
#: *data-quality* tolerances (a compiled artifact never silently accepts a broken feed), by
#: target frequency, not research policy.
MAX_ABS_BAR_RETURN = {
    "1m": 0.02,
    "5m": 0.02,
    "15m": 0.05,
    "30m": 0.05,
    "1h": 0.10,
    "4h": 0.20,
    "1d": 0.25,
}
#: MAD return-outlier threshold in robust standard deviations.
MAD_THRESHOLD = 8.0
#: Fraction of the declared window the source grid must actually carry.  A 24/7 venue is
#: near-total; a five-session market week is 5/7 = 0.714, so 0.60 leaves holidays on top.
MIN_COVERAGE = {"crypto_perps": 0.98, "default": 0.60}
#: ML4T ``FeedSpec`` calendar per asset class.  Recorded, never used to shift a bar: a
#: compiled artifact declares no ``daily_roll_anchor``, so the UTC grid is the bar grid.
SESSION_CALENDAR = {
    "crypto_perps": "CRYPTO",
    "fx": "FX_24x5",
    "indices": "CME_Equity",
    "metals": "CME_FX",
    "commodities": "24/7",
    "multi_asset": "24/7",
}
#: Declared simulation mechanics.  A requirement cannot choose these (it has no field for
#: them) and the policy does not own them; the manifest records them verbatim so a reviewer
#: reads the same numbers the contract was frozen with.
MECHANICS = {
    "n_folds": 4,
    "label_horizon_bars": 1,
    "embargo_bars": 1,
    "execution_delay_bars": 1,
}


class CompileError(ValueError):
    """The compile could not produce an artifact."""


class CompileRefused(CompileError):
    """The request is refused before the first read or write (policy, override, input)."""


class CompileGap(CompileError):
    """The resolver answered ``fail_closed``: the requirement is not satisfiable."""

    def __init__(self, report: ResolutionReport) -> None:
        self.report = report
        gap = report.gap or {}
        super().__init__(
            f"data requirement is not satisfiable: {report.reason}: "
            f"{gap.get('detail', 'no detail')}"
        )


class CompileNotRequired(CompileError):
    """The resolver answered ``reuse_signed``: there is nothing to compile."""

    def __init__(self, report: ResolutionReport) -> None:
        self.report = report
        matched = report.matched_contract or {}
        super().__init__(
            f"a signed contract already carries this requirement "
            f"({matched.get('contract_id')}, {report.reason}); nothing to compile"
        )


class CompileFenceError(CompileError):
    """The fence cannot be held: a bar, a partition or the window would cross it."""


class CompileWriteOnce(CompileError):
    """The artifact exists and its evidence is not identical; write-once refuses the write."""


#: What lane K must validate on a compiled artifact before promoting it, in the manifest's
#: own vocabulary.  Exposed as data (not prose alone) so a promotion test can assert it.
ARTIFACT_CONTRACT: dict[str, Any] = {
    "schema": ARTIFACT_SCHEMA,
    "qualification_eligible": False,
    "paths": {
        "parquet": f"{PARQUET_DIR}/<dataset_id>.parquet",
        "contract": f"{SANDBOX_CONTRACTS_DIR}/<contract_id lower>.yaml",
        "manifest": f"{SANDBOX_MANIFEST_DIR}/<CONTRACT_ID>.json",
        "holdout": f"{HOLDOUT_DIR}/<dataset_id>.parquet (declared, never written)",
    },
    "manifest_fields": {
        "sha256": "SHA-256 of the Parquet bytes; equals the contract's data_snapshot_sha256",
        "spec_sha256": "digest of the compiled DatasetSpec (recompilation identity)",
        "raw_inputs_merkle_sha256": "Merkle root over the exact raw files read",
        "raw_inputs": "one {path, bytes, sha256} record per raw file read",
        "dataset.assets": "the compiled basket, sorted",
        "dataset.first_bar": "first emitted bar label (UTC)",
        "dataset.last_bar": "last emitted bar label; + one interval must be <= the fence",
        "dataset.price_basis": "the requirement's price basis",
        "sandbox.requirement": "the normalised requirement the artifact was compiled for",
        "sandbox.requirement_sha256": "digest of that requirement",
        "sandbox.policy.sha256": "digest of the signed venue policy injected",
        "sandbox.policy.rules": "the cost/leverage floors the contract was frozen with",
        "sandbox.policy.selection_group_id": "the policy-derived search-debt group",
        "sandbox.fence": "holdout_start, the grid, the last bar close, and the fence state",
        "sandbox.lake.catalog_identity": "lake catalogue provenance",
        "sandbox.lake.acceptance": "per-store acceptance identity from the catalogue",
        "sandbox.contract_spec_hash": (
            "derived: load the sandbox contract and call spec_hash() — the contract pins this "
            "manifest's Parquet digest, so the digest cannot be a field of the manifest"
        ),
    },
    "promotion_requires": [
        "the sandbox contract and this manifest still exist at their recorded paths",
        "the Parquet digest, raw-input Merkle root and spec digest still match",
        "the lake catalogue provenance still resolves for every recorded dataset_key",
        "sandbox.policy.sha256 equals the current signed venue policy digest",
        "the contract is unexpired and sandbox.fence.holdout_start is still the fence",
        "the contract is not already promoted (one promotion per contract id)",
    ],
}


@dataclass(frozen=True)
class CompiledArtifact:
    """One compiled sandbox artifact, described by every digest that binds it."""

    contract_id: str
    dataset_id: str
    requirement: dict[str, Any]
    requirement_sha256: str
    parquet_path: str
    parquet_sha256: str
    contract_path: str
    contract_sha256: str
    contract_spec_hash: str
    manifest_path: str
    manifest_sha256: str
    spec_sha256: str
    raw_inputs_merkle_sha256: str
    raw_inputs: tuple[dict[str, Any], ...]
    policy_sha256: str
    selection_group_id: str
    fence: str
    window_start: str
    first_bar: str
    last_bar: str
    last_bar_close: str
    rows: int
    assets: tuple[str, ...]
    frequency: str
    source_frequency: str
    price_basis: str
    lake: dict[str, Any] = field(default_factory=dict)
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "contract_id": self.contract_id,
            "dataset_id": self.dataset_id,
            "decision": DECISION_COMPILE_REQUIRED,
            "requirement": self.requirement,
            "requirement_sha256": self.requirement_sha256,
            "parquet_path": self.parquet_path,
            "parquet_sha256": self.parquet_sha256,
            "contract_path": self.contract_path,
            "contract_sha256": self.contract_sha256,
            "contract_spec_hash": self.contract_spec_hash,
            "manifest_path": self.manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "spec_sha256": self.spec_sha256,
            "raw_inputs_merkle_sha256": self.raw_inputs_merkle_sha256,
            "raw_inputs": [dict(record) for record in self.raw_inputs],
            "policy_sha256": self.policy_sha256,
            "selection_group_id": self.selection_group_id,
            "fence": self.fence,
            "window_start": self.window_start,
            "first_bar": self.first_bar,
            "last_bar": self.last_bar,
            "last_bar_close": self.last_bar_close,
            "rows": self.rows,
            "assets": list(self.assets),
            "frequency": self.frequency,
            "source_frequency": self.source_frequency,
            "price_basis": self.price_basis,
            "lake": dict(self.lake),
            "reused": self.reused,
            "qualification_eligible": False,
        }
        return payload


# ---------------------------------------------------------------------------
# refusals that must precede every read and write
# ---------------------------------------------------------------------------


def _refuse_overrides(overrides: Mapping[str, Any] | None) -> None:
    """Refuse an agent-supplied value for anything the signed policy (or nobody) owns."""
    if not overrides:
        return
    named = sorted(str(key) for key in overrides)
    owned = [key for key in named if key in POLICY_OWNED_KEYS]
    if owned:
        raise CompileRefused(
            f"override(s) {owned} belong to the signed venue policy: costs, leverage, fences "
            "and selection_group_id are injected from contracts/policy/venue_policy.yaml, and "
            "an agent-supplied value is refused before any file is written"
        )
    raise CompileRefused(
        f"override(s) {named} are not compile knobs: a compiled artifact's mechanics are "
        "fixed by the requirement and the signed venue policy; nothing was written"
    )


def _trusted_policy(root: Path, report: ResolutionReport) -> VenuePolicy:
    """The one signed policy, or a refusal — never a default and never a caller's object."""
    try:
        policy = load_venue_policy(root)
    except VenuePolicyError as exc:
        raise CompileRefused(
            f"cannot inject standards: the signed venue policy is not trusted ({exc})"
        ) from exc
    recorded = report.to_dict()["policy"]
    if recorded.get("sha256") and recorded["sha256"] != policy.sha256:
        raise CompileRefused(
            "the resolution report was produced under a different policy digest "
            f"({recorded['sha256'][:12]}... != {policy.sha256[:12]}...); re-resolve and compile "
            "under one policy"
        )
    return policy


# ---------------------------------------------------------------------------
# window and fence arithmetic
# ---------------------------------------------------------------------------


def _interval(frequency: str) -> timedelta:
    return FREQUENCY_BAR_DURATION[frequency]


def _align_up(start: datetime, frequency: str) -> datetime:
    """Round ``start`` up to the target bar grid (a lower bound may only move later)."""
    seconds = int(_interval(frequency).total_seconds())
    epoch = int(start.timestamp())
    remainder = epoch % seconds
    return start if remainder == 0 else start + timedelta(seconds=seconds - remainder)


def _window(requirement: DataRequirement, policy: VenuePolicy) -> tuple[datetime, datetime]:
    """``(start, fence)`` with the grid and fence held, or a refusal naming the failure."""
    frequency = requirement.frequency
    fence = policy.holdout_start
    interval = _interval(frequency)
    if int(fence.timestamp()) % int(interval.total_seconds()):
        raise CompileFenceError(
            f"the holdout fence {fence.isoformat()} is not on the {frequency} bar grid "
            f"({int(interval.total_seconds())}s): the last emitted bar would straddle the fence, "
            "and a compile refuses to truncate it"
        )
    start = _align_up(requirement.min_start, frequency)
    if start >= fence:
        raise CompileFenceError(
            f"the compiled window start {start.isoformat()} is at or after the holdout fence "
            f"{fence.isoformat()}: no bar can be emitted before the fence"
        )
    if start >= policy.val_start:
        raise CompileRefused(
            f"min_start {requirement.min_start.isoformat()} compiles to {start.isoformat()}, at or "
            f"after the policy val_start {policy.val_start.isoformat()}: the artifact would hold "
            "no training bar"
        )
    return start, fence


# ---------------------------------------------------------------------------
# lake reading plan: which partitions may be opened, decided by name first
# ---------------------------------------------------------------------------


def _partition_bounds(values: Mapping[str, int]) -> tuple[datetime, datetime] | None:
    year = values.get("year")
    month = values.get("month")
    day = values.get("day")
    if year is None:
        return None
    if month is None:
        return datetime(year, 1, 1, tzinfo=UTC), datetime(year + 1, 1, 1, tzinfo=UTC)
    if day is None:
        start = datetime(year, month, 1, tzinfo=UTC)
        return start, (datetime(year + 1, 1, 1, tzinfo=UTC) if month == 12
                       else datetime(year, month + 1, 1, tzinfo=UTC))
    start = datetime(year, month, day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _leaf_partitions(
    fs: LocalRawFileSystem, base: PurePosixPath, values: dict[str, int]
) -> list[tuple[PurePosixPath, dict[str, int]]]:
    """Every leaf directory under ``base`` that holds Parquet files, with its partitions."""
    names = fs.list_dir(base)
    files = [name for name in names if name.endswith(".parquet") and not name.startswith((".", "_"))]
    children = [
        name
        for name in names
        if "=" in name and not name.startswith((".", "_")) and name.split("=", 1)[1].isdigit()
    ]
    if children and files:
        raise CompileError(
            f"{base.as_posix()!r} mixes partitions and loose Parquet files; a compiled input "
            "must be one layout so the Merkle root names exactly what was read"
        )
    if not children:
        return [(base, values)]
    found: list[tuple[PurePosixPath, dict[str, int]]] = []
    for name in children:
        key, _, value = name.partition("=")
        found.extend(_leaf_partitions(fs, base / name, {**values, key: int(value)}))
    return found


def _declared_span(lake_root: Path, dataset_key: str) -> tuple[datetime | None, datetime | None]:
    """The store's declared first/last timestamp from ``.metadata/<key>.json``, or ``None``s."""
    metadata_path = lake_root / ".metadata" / f"{dataset_key}.json"
    if not metadata_path.is_file():
        return None, None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(metadata, dict):
        return None, None
    out: list[datetime | None] = []
    for key in ("first_timestamp", "last_timestamp"):
        raw = metadata.get(key)
        if not isinstance(raw, str) or not raw.strip():
            out.append(None)
            continue
        try:
            parsed = datetime.fromisoformat(raw.strip())
        except ValueError:
            out.append(None)
            continue
        out.append(parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC))
    return out[0], out[1]


def _store_files(
    fs: LocalRawFileSystem,
    dataset_key: str,
    *,
    not_before: datetime,
    fence: datetime,
    declared_first: datetime | None,
    declared_last: datetime | None,
) -> tuple[list[tuple[PurePosixPath, datetime]], list[str]]:
    """``([(file, partition start)], excluded partitions)`` for one store.

    Selection is by *name* first: a partition that begins before ``not_before`` is skipped
    (a whole file is never sliced), one that ends after the fence is refused outright.
    """
    store = canonical_source_path(dataset_key, role="lake dataset key")
    if store.suffix == ".parquet":
        # A single flat store cannot be sliced by name: it is readable only when its own
        # declared span lies inside the window.
        if declared_last is None or declared_first is None:
            raise CompileFenceError(
                f"single-file lake store {dataset_key!r} declares no first/last timestamp; a "
                "compile cannot place it against the window and the fence"
            )
        if declared_last >= fence:
            raise CompileFenceError(
                f"single-file lake store {dataset_key!r} ends at {declared_last.isoformat()}, at or "
                f"after the holdout fence {fence.isoformat()}: the partition straddles the fence"
            )
        if declared_last < not_before:
            return [], [dataset_key]
        if declared_first < not_before:
            raise CompileFenceError(
                f"single-file lake store {dataset_key!r} starts at {declared_first.isoformat()}, "
                f"before the compiled window start {not_before.isoformat()}: a compile never "
                "slices a raw file, and reading it whole would reach before the window"
            )
        return [(store, declared_first)], []

    readable: list[tuple[PurePosixPath, datetime]] = []
    excluded: list[str] = []
    for base, values in _leaf_partitions(fs, store, {}):
        bounds = _partition_bounds(values)
        if bounds is None:
            raise CompileFenceError(
                f"lake store {dataset_key!r} has no year=/month= partition bounds at "
                f"{base.as_posix()!r}; its span cannot be placed against the fence"
            )
        start, end = bounds
        # A partition that begins before the window cannot be used at all: a compile never
        # slices a raw file, and reading it whole would reach before the declared window.
        if start < not_before or start >= fence:
            excluded.append(base.as_posix())
            continue
        if start < fence < end:
            raise CompileFenceError(
                f"lake partition {base.as_posix()!r} spans [{start.isoformat()}, {end.isoformat()}) "
                f"and straddles the holdout fence {fence.isoformat()}: reading it would read "
                "holdout bytes, and slicing it silently is the truncation this compile refuses"
            )
        names = fs.list_dir(base)
        files = sorted(
            name for name in names if name.endswith(".parquet") and not name.startswith((".", "_"))
        )
        if not files:
            excluded.append(base.as_posix())
            continue
        readable.extend((base / name, start) for name in files)
    return readable, excluded


def _catalogue_provenance(
    lake_root: Path, dataset_keys: Iterable[str]
) -> dict[str, Any]:
    """The catalogue facts a promotion must be able to re-check, per store."""
    catalog_path = lake_root / ".catalog" / "research-dataset-catalog.json"
    payload: dict[str, Any] = {
        "catalog_identity": None,
        "acceptance": {},
        "stores": [],
    }
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        payload["reason"] = f"catalogue unreadable: {exc}"
        return payload
    entries = {
        str(item.get("dataset_key")): item
        for item in catalog.get("entries") or []
        if isinstance(item, dict) and item.get("dataset_key")
    }
    payload["catalog_identity"] = catalog.get("catalog_identity")
    keys = sorted(set(dataset_keys))
    payload["stores"] = keys
    for key in keys:
        entry = entries.get(key)
        if entry is None:
            payload["acceptance"][key] = {"present": False, "reason": "not in the catalogue"}
            continue
        acceptance = entry.get("acceptance")
        if not isinstance(acceptance, Mapping):
            acceptance = entry.get("evidence", {}).get("acceptance", {})
        payload["acceptance"][key] = {
            "present": bool(acceptance.get("present")),
            "relative_path": acceptance.get("relative_path"),
            "sha256": acceptance.get("sha256"),
            "entry_identity": entry.get("entry_identity"),
            "consumer_identity": entry.get("consumer_identity"),
            "qualification": entry.get("qualification"),
            "tier": entry.get("tier"),
        }
    return payload


# ---------------------------------------------------------------------------
# the plan: everything decided before a file is created
# ---------------------------------------------------------------------------


def _bars_per_year(requirement: DataRequirement) -> int:
    """Bars per year from the declared calendar: 24/7 for perps, a 5-session week elsewhere."""
    seconds_per_year = 365 * 24 * 3600
    if requirement.asset_class != "crypto_perps":
        seconds_per_year = int(seconds_per_year * 5 / 7)
    return max(1, round(seconds_per_year / int(_interval(requirement.frequency).total_seconds())))


@dataclass(frozen=True)
class _Plan:
    requirement: DataRequirement
    report: ResolutionReport
    policy: VenuePolicy
    group: str
    dataset_id: str
    contract_id: str
    spec: Any
    #: The contract *before* its dataset pin: the pin is the digest of the bytes this compile
    #: publishes, so the bytes can only be frozen after the build (see `_pinned_contract`).
    contract_template: EvaluationContract
    parquet_path: str
    manifest_path: str
    contract_path: str
    window_start: datetime
    fence: datetime
    lake_root: Path
    dataset_keys: dict[str, str]
    input_paths: tuple[PurePosixPath, ...]
    sandbox_block: dict[str, Any]
    raw_inputs_merkle_sha256: str

    @property
    def requirement_sha256(self) -> str:
        return _digest(self.requirement.model_dump(mode="json"))


def _digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _readme_text() -> str:
    return f"""# Sandbox Contracts (`{SANDBOX_CONTRACTS_DIR}/`)

Agent-writable **exploratory** evaluation contracts and their dataset manifests
(`{SANDBOX_MANIFEST_DIR}/`), compiled by `warden data compile` from a declarative
`DataRequirement` (#117).

This namespace does **not** qualify.  Enforced by Warden:

- `warden check-worktree` does not seal `{SANDBOX_CONTRACTS_DIR}/*.yaml` or
  `{SANDBOX_MANIFEST_DIR}/*.json`, so compiling does not invalidate (or re-sign) the
  human manifest.  Compiles therefore stay cheap; promotion re-validates every digest.
- A sandbox contract runs the ordinary pipeline (`register-hypothesis`, `verify`, `screen`,
  `backtest`, `advance`) with no production signing.  Its dataset is `unknown` provenance,
  so a Stage 3 verdict is `NON_PROMOTABLE`: observed market history, no attested origin.
- Production wins a name clash: `contracts/<id>.yaml` is always resolved before
  `{SANDBOX_CONTRACTS_DIR}/<id>.yaml`.
- `warden qualify` hard-refuses any contract resolved from this namespace.  Promotion into
  `contracts/` + `contracts/datasets/`, plus the human re-sign, is a separate step.
- Each artifact is write-once: the Parquet digest, the raw-input Merkle root, the policy
  digest, the fence and the contract bytes are re-checked before a second compile reuses
  it; anything that moved is refused rather than overwritten.
"""


def _create_exclusive(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise CompileWriteOnce(f"{path.as_posix()} already exists; write-once refuses it") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _plan(
    requirement: DataRequirement,
    report: ResolutionReport,
    policy: VenuePolicy,
    root: Path,
) -> _Plan:
    payload = report.to_dict()
    group = report.selection_group_id or require_selection_group(
        policy, requirement.asset_class, requirement.frequency
    )
    lake_root_text = payload["lake"].get("root")
    if not lake_root_text:
        raise CompileRefused(
            f"the lake root is unknown ({payload['lake'].get('reason')}); a compile has no "
            "store to read"
        )
    lake_root = Path(lake_root_text)
    lake_symbols = payload["lake"]["symbols"]
    basket = tuple(sorted(payload["available_symbols"]))
    if not basket:
        raise CompileRefused("the resolver reported no basket for a compile_required decision")

    requested_start, fence = _window(requirement, policy)
    fs = LocalRawFileSystem(lake_root)

    dataset_keys: dict[str, str] = {}
    per_symbol: dict[str, list[tuple[PurePosixPath, datetime]]] = {}
    starts: dict[str, datetime] = {}
    excluded: list[str] = []
    for symbol in basket:
        entry = lake_symbols.get(symbol)
        if not entry or not entry.get("dataset_key"):
            raise CompileRefused(
                f"the lake cannot name a store for {symbol}; a compile reads stores, not symbols"
            )
        key = str(entry["dataset_key"])
        dataset_keys[symbol] = key
        declared_first, declared_last = _declared_span(lake_root, key)
        readable, skipped = _store_files(
            fs,
            key,
            not_before=requested_start,
            fence=fence,
            declared_first=declared_first,
            declared_last=declared_last,
        )
        if not readable:
            raise _no_inputs(key, requested_start, fence)
        per_symbol[symbol] = readable
        starts[symbol] = min(start for _, start in readable)
        excluded.extend(skipped)

    # Every symbol must be covered from the same instant, and a whole file is never sliced:
    # the compiled window starts at the first *whole input partition* at or after min_start
    # that every symbol's store actually carries.
    window_start = max(starts.values())
    if int(window_start.timestamp()) % int(_interval(requirement.frequency).total_seconds()):
        raise CompileFenceError(
            f"the first readable partition starts at {window_start.isoformat()}, off the "
            f"{requirement.frequency} bar grid; a compile refuses to relabel a bar"
        )
    inputs: list[PurePosixPath] = []
    for symbol, entries in per_symbol.items():
        kept = [path for path, start in entries if start >= window_start]
        if not kept:
            raise CompileFenceError(
                f"lake store {dataset_keys[symbol]!r} carries no whole partition at or after the "
                f"compiled window start {window_start.isoformat()}"
            )
        excluded.extend(path.as_posix() for path, start in entries if start < window_start)
        inputs.extend(kept)

    input_paths = tuple(sorted(set(inputs), key=lambda path: path.as_posix()))
    if not input_paths:
        raise CompileFenceError("no readable partition lies inside the compiled window")

    # Everything the artifact's identity depends on, in one digest.
    identity = {
        "schema": ARTIFACT_SCHEMA,
        "requirement": requirement.model_dump(mode="json"),
        "policy_sha256": policy.sha256,
        "selection_group_id": group,
        "fence": fence.isoformat(),
        "window_start": window_start.isoformat(),
        "basket": list(basket),
        "dataset_keys": dataset_keys,
    }
    digest = _digest(identity)
    dataset_id = f"sandbox_{requirement.asset_class}_{requirement.frequency}_{digest[:12]}_r1"
    contract_id = f"SANDBOX_{requirement.asset_class.upper()}_{requirement.frequency.upper()}_{digest[:10].upper()}"

    parquet_path = f"{PARQUET_DIR}/{dataset_id}.parquet"
    manifest_path = f"{SANDBOX_MANIFEST_DIR}/{contract_id}.json"
    contract_path = f"{SANDBOX_CONTRACTS_DIR}/{contract_id.lower()}.yaml"

    resampling = requirement.frequency in RESAMPLED_FROM_MINUTE
    source_frequency = "1m" if resampling else requirement.frequency
    minute_sub_bars = max(1, int(_interval(requirement.frequency).total_seconds()) // 60)
    # A resampled non-crypto feed carries tick counts, which is what `tick_count_proxy' says;
    # a daily artifact is a native-cadence read (no aggregation to label) and keeps the
    # store's own contract volume.
    volume_semantics = (
        "real_contracts"
        if requirement.asset_class == "crypto_perps" or requirement.frequency == "1d"
        else "tick_count_proxy"
    )

    spec_document = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "asset_class": requirement.asset_class,
        "evidence_tier": "research_proxy",
        "venue": "ml4t_lake",
        "frequency": requirement.frequency,
        "price_basis": requirement.price_basis,
        "volume_semantics": volume_semantics,
        "time_window": {
            "start_inclusive": window_start.isoformat(),
            "exploratory_end_exclusive": fence.isoformat(),
            "holdout_start_inclusive": fence.isoformat(),
            "holdout_end_exclusive": policy.holdout_end.isoformat(),
        },
        "time_contract": {
            "timezone": "UTC",
            "bar_timestamp_semantics": "open_left_closed_left",
            "session_calendar": SESSION_CALENDAR[requirement.asset_class],
            "daily_roll_anchor": None,
        },
        "source": {
            "kind": "lake_raw",
            "adapter": "ml4t_lake",
            "inputs": {
                _input_key(symbol, path): {
                    "path": path.as_posix(),
                    "format": "parquet",
                }
                for symbol, path in _inputs_by_symbol(basket, dataset_keys, input_paths)
            },
        },
        "universe": {
            "selection_mode": "explicit_symbols",
            "symbols": list(basket),
            "retain_delisted": True,
        },
        "resampling": {
            # Every intraday artifact is the product of the same left-closed aggregation,
            # including the identity 1m bin; a daily artifact is read natively.
            "enabled": resampling or requirement.frequency != "1d",
            "require_min_sub_bars": minute_sub_bars if requirement.frequency != "1d" else None,
        },
        "quality_gate": {
            "max_abs_bar_return": MAX_ABS_BAR_RETURN[requirement.frequency],
            "mad_outlier_threshold": MAD_THRESHOLD,
            "allow_zero_volume": False,
            "explicit_anomaly_filters": [],
            "min_coverage_ratio": MIN_COVERAGE.get(
                requirement.asset_class, MIN_COVERAGE["default"]
            ),
        },
        "parquet_write": {
            "compression": "zstd",
            "compression_level": 3,
            "statistics": True,
            "row_group_size": 100000,
            "sort_keys": ["timestamp", "asset"],
        },
    }
    spec = parse_dataset_spec(spec_document)

    rules = policy.rules_for(requirement.asset_class, requirement.frequency)
    contract = EvaluationContract(
        contract_id=contract_id,
        asset_class=requirement.asset_class,
        frequency=requirement.frequency,
        bars_per_year=_bars_per_year(requirement),
        exploratory_data_path=parquet_path,
        holdout_data_path=f"{HOLDOUT_DIR}/{dataset_id}.parquet",
        train_start=policy.train_start,
        val_start=policy.val_start,
        val_end=policy.val_end,
        holdout_start=policy.holdout_start,
        holdout_end=policy.holdout_end,
        n_folds=MECHANICS["n_folds"],
        label_horizon_bars=MECHANICS["label_horizon_bars"],
        embargo_bars=MECHANICS["embargo_bars"],
        execution_delay_bars=MECHANICS["execution_delay_bars"],
        taker_fee_bps=rules.taker_fee_bps,
        slippage_bps=rules.slippage_bps,
        funding_bps_per_8h=rules.funding_bps_per_8h,
        execution_mode=requirement.execution_mode,
        data_provenance="unknown",
        policy={
            "spec_version": 2,
            "evidence_tier": "research_proxy",
            "selection_group_id": group,
            "allow_leverage": rules.allow_leverage,
            "max_gross_leverage": rules.max_gross_leverage,
            "holdout_refit_mode": "purged_expanding",
            "dataset_id": dataset_id,
            "dataset_manifest_sha256": "",
            "inherits_selection_from": "",
        },
    )
    # The dataset pin is the digest of the bytes this compile publishes, so it is filled in
    # by `_pinned_contract` once the Parquet exists; the template keeps every other field.

    catalogue = _catalogue_provenance(lake_root, dataset_keys.values())
    sandbox_block = {
        "schema": ARTIFACT_SCHEMA,
        "contract_id": contract_id,
        "dataset_id": dataset_id,
        "qualification_eligible": False,
        "requirement": requirement.model_dump(mode="json"),
        "requirement_sha256": _digest(requirement.model_dump(mode="json")),
        "policy": {
            "path": "contracts/policy/venue_policy.yaml",
            "sha256": policy.sha256,
            "selection_group_id": group,
            "rules": {
                "taker_fee_bps": rules.taker_fee_bps,
                "slippage_bps": rules.slippage_bps,
                "funding_bps_per_8h": rules.funding_bps_per_8h,
                "allow_leverage": rules.allow_leverage,
                "max_gross_leverage": rules.max_gross_leverage,
            },
        },
        "content_digest": digest,
        "fence": {
            "holdout_start": policy.holdout_start.isoformat(),
            "holdout_end": policy.holdout_end.isoformat(),
            "target_frequency": requirement.frequency,
            "bar_interval_seconds": int(_interval(requirement.frequency).total_seconds()),
            "grid": "utc_left_closed_left_labelled",
            "rule": "every emitted bar closes at or before holdout_start",
            # `dataset.last_bar' + `bar_interval_seconds' is the last bar's close: a promotion
            # re-derives it instead of trusting a second copy of the same fact.
            "fence_satisfied": True,
        },
        "window": {
            "requested_min_start": requirement.min_start.isoformat(),
            "compiled_start_inclusive": window_start.isoformat(),
            "exploratory_end_exclusive": fence.isoformat(),
            "val_start": policy.val_start.isoformat(),
            "val_end": policy.val_end.isoformat(),
            # A whole raw file is never sliced, so the window begins at the first whole input
            # partition at or after `min_start' that every store carries.
            "policy": "first whole input partition at or after min_start",
        },
        "lake": {
            **catalogue,
            "root": lake_root.as_posix(),
            "dataset_keys": dict(sorted(dataset_keys.items())),
            "excluded_partitions": sorted(set(excluded)),
            "input_files": [path.as_posix() for path in input_paths],
        },
        "resampling": {
            "enabled": requirement.frequency != "1d",
            "source_frequency": source_frequency,
            "target_frequency": requirement.frequency,
            "closed": "left",
            "label": "left",
            "min_sub_bars": minute_sub_bars if requirement.frequency != "1d" else None,
        },
        "mechanics": dict(MECHANICS),
        "bars_per_year": contract.bars_per_year,
        "volume_semantics": volume_semantics,
        # No ``contract_spec_hash`` here on purpose: the contract pins the digest of these very
        # Parquet bytes, so its own digest is only knowable after the build.  A promoter loads
        # the contract and derives ``spec_hash()`` itself (one authority, no second copy).
    }

    fs_read = [(path.as_posix(), fs.read_bytes(path)) for path in input_paths]
    merkle, _ = merkle_sha256(fs_read)

    return _Plan(
        requirement=requirement,
        report=report,
        policy=policy,
        group=group,
        dataset_id=dataset_id,
        contract_id=contract_id,
        spec=spec,
        contract_template=contract,
        parquet_path=parquet_path,
        manifest_path=manifest_path,
        contract_path=contract_path,
        window_start=window_start,
        fence=fence,
        lake_root=lake_root,
        dataset_keys=dataset_keys,
        input_paths=input_paths,
        sandbox_block=sandbox_block,
        raw_inputs_merkle_sha256=merkle,
    )


def _no_inputs(dataset_key: str, window_start: datetime, fence: datetime) -> CompileFenceError:
    return CompileFenceError(
        f"lake store {dataset_key!r} carries no partition inside "
        f"[{window_start.isoformat()}, {fence.isoformat()})"
    )


def _input_key(symbol: str, path: PurePosixPath) -> str:
    slug = (
        path.as_posix()
        .replace("=", "")
        .replace("/", "_")
        .replace(".parquet", "")
        .replace("-", "_")
        .lower()
    )
    key = f"{symbol.lower()}__{slug}"
    if len(key) > 100:
        key = f"{key[:80]}_{hashlib.sha256(key.encode()).hexdigest()[:12]}"
    return key


def _inputs_by_symbol(
    basket: tuple[str, ...], dataset_keys: Mapping[str, str], input_paths: tuple[PurePosixPath, ...]
) -> list[tuple[str, PurePosixPath]]:
    """Pair each selected file with the symbol whose store it belongs to (sorted, stable)."""
    out: list[tuple[str, PurePosixPath]] = []
    for path in input_paths:
        owner = next(
            (
                symbol
                for symbol in basket
                if path.as_posix() == dataset_keys[symbol]
                or path.as_posix().startswith(f"{dataset_keys[symbol]}/")
            ),
            None,
        )
        if owner is None:
            raise CompileFenceError(
                f"selected input {path.as_posix()!r} belongs to no requested symbol's store"
            )
        out.append((owner, path))
    return sorted(out, key=lambda item: (item[0], item[1].as_posix()))


# ---------------------------------------------------------------------------
# the write path
# ---------------------------------------------------------------------------

def _parse_ts(value: Any) -> datetime:
    """Parse a Polars ``str(datetime)`` (which ends in `` UTC``) or an ISO-8601 string."""
    text = str(value).strip()
    if text.endswith(" UTC"):
        text = text[: -len(" UTC")] + "+00:00"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise CompileFenceError(f"compiled timestamp {value!r} carries no timezone")
    return parsed.astimezone(UTC)


def _pinned_contract(plan: _Plan, parquet_sha256: str) -> tuple[EvaluationContract, bytes]:
    """The contract with its dataset pin filled in, and the exact bytes to publish."""
    from dataclasses import replace

    contract = replace(plan.contract_template, data_snapshot_sha256=parquet_sha256)
    payload = yaml.safe_dump(
        contract.to_dict(), sort_keys=True, default_flow_style=False, allow_unicode=False
    )
    return contract, payload.encode("utf-8")


def _artifact_from_record(
    plan: _Plan, record: Mapping[str, Any], manifest_bytes: bytes, *, reused: bool
) -> CompiledArtifact:
    """Describe an artifact from its own manifest — never from a recomputed guess."""
    sandbox = record["sandbox"]
    interval = _interval(plan.requirement.frequency)
    last_label = _parse_ts(record["dataset"]["last_bar"])
    contract, contract_bytes = _pinned_contract(plan, str(record["sha256"]))
    return CompiledArtifact(
        contract_id=plan.contract_id,
        dataset_id=plan.dataset_id,
        requirement=plan.requirement.model_dump(mode="json"),
        requirement_sha256=plan.requirement_sha256,
        parquet_path=plan.parquet_path,
        parquet_sha256=str(record["sha256"]),
        contract_path=plan.contract_path,
        contract_sha256=hashlib.sha256(contract_bytes).hexdigest(),
        contract_spec_hash=contract.spec_hash(),
        manifest_path=plan.manifest_path,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        spec_sha256=str(record["spec_sha256"]),
        raw_inputs_merkle_sha256=str(record["raw_inputs_merkle_sha256"]),
        raw_inputs=tuple(dict(item) for item in record.get("raw_inputs") or ()),
        policy_sha256=str(sandbox["policy"]["sha256"]),
        selection_group_id=str(sandbox["policy"]["selection_group_id"]),
        fence=str(sandbox["fence"]["holdout_start"]),
        window_start=str(sandbox["window"]["compiled_start_inclusive"]),
        first_bar=str(record["dataset"]["first_bar"]),
        last_bar=str(record["dataset"]["last_bar"]),
        last_bar_close=(last_label + interval).isoformat(),
        rows=int(record["dataset"]["rows"]),
        assets=tuple(record["dataset"]["assets"]),
        frequency=str(record["dataset"]["frequency"]),
        source_frequency=str(sandbox["resampling"]["source_frequency"]),
        price_basis=str(record["dataset"]["price_basis"]),
        lake=dict(sandbox["lake"]),
        reused=reused,
    )


def _existing_artifact(root: Path, plan: _Plan) -> CompiledArtifact | None:
    """Reuse the identical artifact, or refuse write-once naming what moved."""
    parquet = root / plan.parquet_path
    manifest = root / plan.manifest_path
    contract = root / plan.contract_path
    present = {path.as_posix(): path.exists() for path in (parquet, manifest, contract)}
    if not any(present.values()):
        return None
    if not all(present.values()):
        missing = sorted(name for name, exists in present.items() if not exists)
        raise CompileWriteOnce(
            f"a half-published artifact exists for {plan.contract_id} (missing {missing}); "
            "write-once refuses to complete or overwrite it"
        )
    manifest_bytes = manifest.read_bytes()
    try:
        record = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise CompileWriteOnce(f"{plan.manifest_path} is unreadable: {exc}") from exc
    if not isinstance(record, dict) or not isinstance(record.get("sandbox"), dict):
        raise CompileWriteOnce(
            f"{plan.manifest_path} is not a {ARTIFACT_SCHEMA} artifact manifest"
        )
    sandbox = record["sandbox"]
    if sandbox.get("schema") != ARTIFACT_SCHEMA:
        raise CompileWriteOnce(
            f"{plan.manifest_path} declares sandbox schema {sandbox.get('schema')!r}, not "
            f"{ARTIFACT_SCHEMA!r}; a different artifact contract is not reused"
        )
    fs = LocalRawFileSystem(plan.lake_root)
    merkle, _ = merkle_sha256(
        [(path.as_posix(), fs.read_bytes(path)) for path in plan.input_paths]
    )
    checks = {
        "parquet_sha256": (
            hashlib.sha256(parquet.read_bytes()).hexdigest(),
            record.get("sha256"),
        ),
        "spec_sha256": (plan.spec.spec_sha256(), record.get("spec_sha256")),
        "raw_inputs_merkle_sha256": (merkle, record.get("raw_inputs_merkle_sha256")),
        "policy_sha256": (plan.policy.sha256, (sandbox.get("policy") or {}).get("sha256")),
        "selection_group_id": (
            plan.group,
            (sandbox.get("policy") or {}).get("selection_group_id"),
        ),
        "requirement_sha256": (plan.requirement_sha256, sandbox.get("requirement_sha256")),
        "fence": (plan.fence.isoformat(), (sandbox.get("fence") or {}).get("holdout_start")),
        "contract_sha256": (
            hashlib.sha256(_pinned_contract(plan, str(record.get("sha256")))[1]).hexdigest(),
            hashlib.sha256(contract.read_bytes()).hexdigest(),
        ),
    }
    mismatched = sorted(name for name, (found, recorded) in checks.items() if found != recorded)
    if mismatched:
        details = {
            name: {"found": checks[name][0], "recorded": checks[name][1]}
            for name in mismatched
        }
        raise CompileWriteOnce(
            f"artifact {plan.contract_id} exists and its evidence moved "
            f"({canonical_json(details)}); write-once refuses to overwrite it"
        )
    return _artifact_from_record(plan, record, manifest_bytes, reused=True)


def compile_data_requirement(
    requirement: DataRequirement | Mapping[str, Any] | str | Path,
    *,
    repo_root: Path | str = ".",
    parquet_reader: Any,
    lake_root: Path | str | None = None,
    policy: VenuePolicy | None = None,
    overrides: Mapping[str, Any] | None = None,
    report: ResolutionReport | None = None,
) -> CompiledArtifact:
    """Compile one resolved requirement into a write-once fenced sandbox artifact.

    Ordering is the contract: overrides are refused first, then the resolution report is
    read, then the policy is authenticated, then window/fence arithmetic is decided, then
    the readable partitions are chosen by name — and only after all of that is the first
    raw byte read or the first file created.

    Raises:
        CompileRefused: the request itself is refused (override, untrusted policy, input).
        CompileGap: the resolver answered ``fail_closed`` (nothing written).
        CompileNotRequired: the resolver answered ``reuse_signed`` (nothing to do).
        CompileFenceError: the fence cannot be held on this grid, partition or store.
        CompileWriteOnce: the artifact exists with different evidence.
    """
    root = Path(repo_root)
    req = load_requirement(requirement)
    _refuse_overrides(overrides)

    resolved = report or resolve_data_requirement(
        req, repo_root=root, lake_root=lake_root, policy=policy
    )
    if resolved.decision == DECISION_FAIL_CLOSED:
        raise CompileGap(resolved)
    if resolved.decision == DECISION_REUSE_SIGNED:
        raise CompileNotRequired(resolved)
    if resolved.decision != DECISION_COMPILE_REQUIRED:  # pragma: no cover - defensive
        raise CompileRefused(f"unknown resolver decision {resolved.decision!r}")
    if resolved.requirement != req:  # pragma: no cover - defensive
        raise CompileRefused("the resolution report describes a different requirement")

    venue = policy or _trusted_policy(root, resolved)
    plan = _plan(req, resolved, venue, root)

    existing = _existing_artifact(root, plan)
    if existing is not None:
        return existing

    readme = root / SANDBOX_README
    if not readme.exists():
        _create_exclusive(readme, _readme_text().encode("utf-8"))

    def guard(frame: Any) -> None:
        """No input row at or after the fence may influence any emitted bar."""
        if frame.is_empty():
            return
        offending = int((frame["timestamp"] >= plan.fence).sum())
        if offending:
            raise CompileFenceError(
                f"{offending} raw input rows are at or after the holdout fence "
                f"{plan.fence.isoformat()}; a compile refuses rather than truncating them"
            )

    outcome = build_dataset(
        plan.spec,
        repo_root=root,
        dataset_dir=PARQUET_DIR,
        manifest_dir=SANDBOX_MANIFEST_DIR,
        fs=LocalRawFileSystem(plan.lake_root),
        parquet_reader=parquet_reader,
        frame_guard=guard,
        manifest_extra={"sandbox": plan.sandbox_block},
        # The evaluator resolves a dataset manifest by contract id, so the artifact publishes
        # its manifest under the sandbox contract's id rather than the dataset id.
        manifest_name=plan.contract_id,
    )

    interval = _interval(req.frequency)
    last_label = _parse_ts(outcome.last_bar)
    first_bar = _parse_ts(outcome.first_bar)
    published = [root / plan.parquet_path, root / plan.manifest_path]

    def _withdraw(reason: str) -> None:
        """A post-aggregate refusal must leave no artifact behind."""
        for path in published:
            path.unlink(missing_ok=True)
        raise CompileFenceError(reason)

    if last_label + interval > plan.fence:
        _withdraw(
            f"the compiled last bar closes at {(last_label + interval).isoformat()}, after the "
            f"holdout fence {plan.fence.isoformat()}"
        )
    if first_bar < plan.window_start:
        _withdraw(
            f"the compiled first bar {first_bar.isoformat()} precedes the window start "
            f"{plan.window_start.isoformat()}"
        )
    if int(outcome.rows) <= 0:  # pragma: no cover - the gate refuses an empty panel first
        _withdraw("the compiled panel holds no bar")

    _contract, contract_bytes = _pinned_contract(plan, outcome.parquet_sha256)
    try:
        _create_exclusive(root / plan.contract_path, contract_bytes)
    except BaseException:
        for path in published:
            path.unlink(missing_ok=True)
        raise

    manifest_bytes = (root / plan.manifest_path).read_bytes()
    record = json.loads(manifest_bytes.decode("utf-8"))
    return _artifact_from_record(plan, record, manifest_bytes, reused=False)
