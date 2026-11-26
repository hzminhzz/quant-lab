"""Frozen governance records."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    import polars as pl
    from ml4t.specs import FeedSpec

MARKET = "verified_market"
DEFAULT_SIMULATION_EPOCH = "legacy_matrix"
ENGINE_SIMULATION_EPOCH = "ml4t_backtest_engine_v1"
FREQUENCY_BAR_DURATION: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


class ContractValidationError(ValueError):
    """An experiment specification violates the evaluation boundary."""


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, default=str, allow_nan=False)


def utc(value) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ContractValidationError("timestamps must include an explicit timezone")
    return result.astimezone(UTC)


def _normalize_optional_utc(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC).isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed = date.fromisoformat(text)
            return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC).isoformat()
        return utc(text).isoformat()
    raise ContractValidationError("source_published_at must be an ISO-8601 date or timezone-aware datetime")


def valid_id(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ContractValidationError("IDs must contain only letters, digits, '_' or '-'")


# --- Contract-owned governance metadata (#94, unified by #113) ---------------
# ``policy`` is an explicit, declared field holding the contract's governance
# block as a JSON-safe mapping (``None`` for a legacy v1 contract).  #94 had to
# hide the block in an ``InitVar`` because ``asdict()`` fed both the ``contracts``
# DDL and the digest; #113 retires that workaround:
#
# * a real field survives ``dataclasses.replace()``, ``copy.deepcopy`` and pickling
#   natively, so ``warden verify``'s leakage copy can no longer silently downgrade
#   a v2 contract to a v1 one (the failure #94's ``InitVar`` was written to avoid);
# * it is persisted in its own additive ``contracts.policy`` TEXT column, so the
#   ledger records the declared group instead of re-deriving it from YAML;
# * it is *excluded* from ``spec_hash_payload()`` and ``to_dict()`` explicitly, so
#   every historical v1 digest and every v2 digest computed before this change is
#   byte-identical.  ``asdict()`` does carry the carrier now — deliberately:
#   ``EvaluationContract(**asdict(c))`` is policy-preserving instead of collapsing
#   to a v1 projection (that collapse was the DSR-reset loophole #109 names).
GOVERNANCE_ATTR = "policy"
#: Historical spelling: a dumped ``__dict__`` (or a pre-#113 document) carries the
#: policy under the field name itself, so ``from_dict`` accepts it either way.
GOVERNANCE_INITVAR = GOVERNANCE_ATTR
#: SQLite storage hint for the explicit carrier column (canonical JSON text).
POLICY_FIELD_METADATA = {"sqlite_type": "TEXT"}
CURRENT_SPEC_VERSION = 2
LEGACY_SPEC_VERSION = 1

EVIDENCE_TIERS = ("native_venue", "research_proxy", "unverified_roll", "synthetic")
#: Tiers whose numeric holdout pass may never be promoted to a market claim.
PROMOTION_CAPPED_EVIDENCE_TIERS = frozenset({"research_proxy", "unverified_roll", "synthetic"})
HOLDOUT_REFIT_MODES = ("purged_expanding", "frozen_pre_holdout")

PROXY_HOLDOUT_PASS = "PROXY_HOLDOUT_PASS"
PROMOTED = "PROMOTED"
REJECTED_ON_HOLDOUT = "REJECTED_ON_HOLDOUT"

#: Keys a contract YAML may declare only when it also declares
#: ``spec_version: 2``.  A legacy v1 contract reads its tier from the dataset
#: manifest instead, so its ``spec_hash()`` never moves.
GOVERNANCE_KEYS = (
    "spec_version",
    "evidence_tier",
    "selection_group_id",
    "allow_leverage",
    "max_gross_leverage",
    "holdout_refit_mode",
    "dataset_id",
    "dataset_manifest_sha256",
    "inherits_selection_from",
)


@dataclass(frozen=True)
class ContractPolicy:
    """Contract-owned execution and evidence policy.

    ``spec_version`` is 1 for every legacy contract (undeclared) and 2 for a
    contract that owns its governance block in YAML.  A v1 policy may carry a
    manifest-declared tier; it never contributes to ``spec_hash()``.
    """

    spec_version: int = LEGACY_SPEC_VERSION
    evidence_tier: str = "native_venue"
    selection_group_id: str = ""
    allow_leverage: bool = False
    max_gross_leverage: float = 1.0
    holdout_refit_mode: str = "purged_expanding"
    dataset_id: str = ""
    dataset_manifest_sha256: str = ""
    inherits_selection_from: str = ""

    def __post_init__(self):
        if type(self.spec_version) is not int or self.spec_version not in {LEGACY_SPEC_VERSION, CURRENT_SPEC_VERSION}:
            raise ContractValidationError(
                f"unsupported spec_version {self.spec_version!r}; only {LEGACY_SPEC_VERSION} and {CURRENT_SPEC_VERSION} exist")
        if not isinstance(self.evidence_tier, str) or self.evidence_tier not in EVIDENCE_TIERS:
            raise ContractValidationError(f"evidence_tier must be one of {list(EVIDENCE_TIERS)}")
        if not isinstance(self.holdout_refit_mode, str) or self.holdout_refit_mode not in HOLDOUT_REFIT_MODES:
            raise ContractValidationError(f"holdout_refit_mode must be one of {list(HOLDOUT_REFIT_MODES)}")
        if type(self.allow_leverage) is not bool:
            raise ContractValidationError("allow_leverage must be a bool")
        if not isinstance(self.max_gross_leverage, (int, float)) or type(self.max_gross_leverage) is bool:
            raise ContractValidationError("max_gross_leverage must be a finite number")
        if not math.isfinite(float(self.max_gross_leverage)) or float(self.max_gross_leverage) <= 0.0:
            raise ContractValidationError("max_gross_leverage must be finite positive")
        if not self.allow_leverage and float(self.max_gross_leverage) > 1.0:
            raise ContractValidationError(
                "max_gross_leverage > 1.0 requires allow_leverage=True; leverage is never granted implicitly")
        for name in ("selection_group_id", "inherits_selection_from"):
            value = getattr(self, name)
            if value and (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value)):
                raise ContractValidationError(f"{name} must contain only letters, digits, '_' or '-'")
        for name in ("dataset_id", "dataset_manifest_sha256"):
            value = getattr(self, name)
            if value and not isinstance(value, str):
                raise ContractValidationError(f"{name} must be a string")
        if self.dataset_manifest_sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.dataset_manifest_sha256):
            raise ContractValidationError("dataset_manifest_sha256 must be 64 lowercase hex characters")
        if self.spec_version >= CURRENT_SPEC_VERSION and not self.selection_group_id:
            raise ContractValidationError("spec_version 2 requires an explicit selection_group_id")

    @property
    def declares_governance(self) -> bool:
        """True when the contract itself owns the policy (and its hash covers it)."""
        return self.spec_version >= CURRENT_SPEC_VERSION

    @property
    def is_promotion_capped(self) -> bool:
        return self.evidence_tier in PROMOTION_CAPPED_EVIDENCE_TIERS

    def promotion_cap_reason(self) -> str:
        if not self.is_promotion_capped:
            return ""
        return (f"evidence_tier '{self.evidence_tier}' caps holdout outcome at {PROXY_HOLDOUT_PASS}; "
                "market promotion requires native_venue evidence")

    def hash_payload(self) -> dict:
        """Only the declared governance keys, in a stable order, for the v2 digest."""
        return {key: getattr(self, key) for key in GOVERNANCE_KEYS}

    def to_dict(self) -> dict:
        return {
            "spec_version": self.spec_version,
            "evidence_tier": self.evidence_tier,
            "selection_group_id": self.selection_group_id,
            "allow_leverage": self.allow_leverage,
            "max_gross_leverage": float(self.max_gross_leverage),
            "holdout_refit_mode": self.holdout_refit_mode,
            "dataset_id": self.dataset_id,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "inherits_selection_from": self.inherits_selection_from,
        }

    @classmethod
    def from_mapping(cls, raw: dict | None) -> ContractPolicy:
        """Build from an untrusted mapping; unknown keys are refused, not ignored."""
        if not raw:
            return cls()
        unknown = sorted(set(raw) - set(GOVERNANCE_KEYS))
        if unknown:
            raise ContractValidationError(f"unknown governance metadata keys: {unknown}")
        values = dict(raw)
        if "max_gross_leverage" in values and isinstance(values["max_gross_leverage"], (int, float)):
            values["max_gross_leverage"] = float(values["max_gross_leverage"])
        if "spec_version" in values and isinstance(values["spec_version"], float):
            raise ContractValidationError("spec_version must be an integer")
        try:
            return cls(**values)
        except TypeError as exc:
            raise ContractValidationError(f"invalid governance metadata: {exc}") from exc


def coerce_contract_policy(value) -> ContractPolicy:
    """Accept a validated policy, a mapping, or ``None`` (legacy default)."""
    if value is None:
        return ContractPolicy()
    if isinstance(value, ContractPolicy):
        return value
    if isinstance(value, dict):
        return ContractPolicy.from_mapping(value)
    raise ContractValidationError(
        f"contract policy must be a ContractPolicy or mapping, got {type(value).__name__}")


def attach_contract_policy(contract: EvaluationContract, policy) -> EvaluationContract:
    """Attach a validated policy to a frozen contract's explicit carrier field.

    The stored value is the JSON-safe ``to_dict()`` mapping, so ``asdict()``,
    YAML dumping and the ``contracts.policy`` JSON column all see the same
    document.  A policy equal to the legacy default clears the carrier, which
    keeps a legacy contract's row and document byte-identical to a pre-#94 one.
    """
    resolved = coerce_contract_policy(policy)
    object.__setattr__(contract, GOVERNANCE_ATTR,
                       None if resolved == ContractPolicy() else resolved.to_dict())
    return contract


@dataclass(frozen=True)
class EvaluationContract:
    contract_id: str
    asset_class: str
    frequency: str
    bars_per_year: int
    exploratory_data_path: str
    holdout_data_path: str
    train_start: datetime
    val_start: datetime
    val_end: datetime
    holdout_start: datetime
    holdout_end: datetime
    n_folds: int
    label_horizon_bars: int
    embargo_bars: int
    execution_delay_bars: int
    taker_fee_bps: float
    slippage_bps: float
    funding_bps_per_8h: float = 0.0
    execution_mode: str = "NEXT_BAR"
    min_expected_move_to_cost_ratio: float = 2.5
    data_snapshot_sha256: str = ""
    # Declared origin, independent of the digest. Only "verified_market" may reach
    # market qualification; "unknown" fails closed.
    data_provenance: str = "unknown"
    #: Contract-owned governance block (#94, explicit field since #113): a validated
    #: ``ContractPolicy`` or its JSON-safe mapping, normalised to the mapping.
    #: ``None`` for a legacy v1 contract.  Held off the digest by
    #: ``spec_hash_payload()`` rather than by hiding the field, and stored in the
    #: additive ``contracts.policy`` column.  ``compare=False`` keeps equality,
    #: hashing and frozen-dataclass copying independent of the (unhashable) block.
    policy: ContractPolicy | dict | None = field(default=None, compare=False, repr=False,
                                                 metadata=POLICY_FIELD_METADATA)

    def __post_init__(self):
        valid_id(self.contract_id)
        # The explicit carrier is stored in its canonical JSON-safe form; the
        # legacy default is stored as ``None`` so a v1 contract's row and digest
        # stay byte-identical to a pre-#94 contract.
        if self.policy is not None:
            resolved = coerce_contract_policy(self.policy)
            object.__setattr__(self, GOVERNANCE_ATTR,
                               None if resolved == ContractPolicy() else resolved.to_dict())
        for n in ("train_start", "val_start", "val_end", "holdout_start", "holdout_end"):
            object.__setattr__(self, n, utc(getattr(self, n)))
        d = [self.train_start, self.val_start, self.val_end, self.holdout_start, self.holdout_end]
        if not (d[0] < d[1] < d[2] <= d[3] < d[4]):
            raise ContractValidationError("chronological boundaries overlap")
        if self.asset_class not in {"crypto_perps", "fx", "indices", "metals", "commodities", "multi_asset"} or self.frequency not in FREQUENCY_BAR_DURATION or self.execution_mode not in {"NEXT_BAR", "QUOTE_SIDE"}:
            raise ContractValidationError("invalid asset_class, frequency, or execution_mode")
        for n in ("bars_per_year", "n_folds", "label_horizon_bars", "embargo_bars", "execution_delay_bars"):
            if type(getattr(self, n)) is not int or getattr(self, n) < 1:
                raise ContractValidationError(f"{n} must be positive int")
        for n in ("taker_fee_bps", "slippage_bps", "min_expected_move_to_cost_ratio"):
            v = getattr(self, n)
            if not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                raise ContractValidationError(f"{n} must be finite positive")
        if not math.isfinite(self.funding_bps_per_8h) or self.funding_bps_per_8h < 0 or (self.data_snapshot_sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.data_snapshot_sha256)) or not self.exploratory_data_path or self.exploratory_data_path == self.holdout_data_path:
            raise ContractValidationError("invalid funding, sha256 or data paths")
        if self.data_provenance not in {MARKET, "synthetic", "unknown"}:
            raise ContractValidationError("data_provenance must be verified_market, synthetic or unknown")
        if self.data_provenance != "unknown" and not re.fullmatch(r"[0-9a-f]{64}", self.data_snapshot_sha256 or ""):
            raise ContractValidationError("declared data_provenance requires a data_snapshot_sha256 pin")

    def total_one_way_cost_decimal(self) -> float:
        return (self.taker_fee_bps + self.slippage_bps) * 1e-4

    def bar_duration(self) -> timedelta:
        return FREQUENCY_BAR_DURATION[self.frequency]

    @property
    def governance(self) -> ContractPolicy:
        """Contract-owned policy (v2 YAML, else the legacy default).

        The carrier is an ordinary field, so ``dataclasses.replace()``,
        ``copy``/``deepcopy`` and pickling carry it without a special case; a copy
        never silently reverts to the legacy default.
        """
        raw = self.policy
        return ContractPolicy() if raw is None else coerce_contract_policy(raw)

    @property
    def selection_group_id(self) -> str:
        """The contract's *own* declared selection group (``""`` for a v1 contract).

        Policy-derived membership is a registry fact (``contract_governance``), not
        a contract field: a contract may only ever declare its own group, and the
        registry is the only authority that can add a policy-mapped one.
        """
        return self.governance.selection_group_id

    def spec_hash_payload(self) -> dict:
        """The exact mapping a digest is computed over, per spec version.

        v1 is the historical ``asdict()`` projection without the policy carrier,
        unchanged since the first registered contract.  v2 adds the declared
        governance block, so a policy edit is a different contract rather than a
        silent re-interpretation.  The carrier itself is never hashed: it is the
        *transport* for that block, and popping it keeps every pre-#113 digest
        (v1 and v2 alike) byte-identical.
        """
        payload = asdict(self)
        payload.pop(GOVERNANCE_ATTR, None)
        policy = self.governance
        if policy.declares_governance:
            payload["governance"] = policy.hash_payload()
        return payload

    def to_dict(self) -> dict:
        """Policy-preserving serialisation in contract-YAML document shape.

        A declared governance block is emitted as top-level keys, so
        ``EvaluationContract.from_dict(c.to_dict())`` reproduces ``c``, including
        its ``spec_hash()``.  The carrier field itself is not emitted: the block
        it transports *is* the top-level keys, and a document never carries both.
        """
        payload = asdict(self)
        payload.pop(GOVERNANCE_ATTR, None)
        policy = self.governance
        if policy.declares_governance:
            payload.update(policy.hash_payload())
        return payload

    @classmethod
    def from_dict(cls, document: dict, *, source: str = "<mapping>") -> EvaluationContract:
        """Official deserialiser; mirrors the contract-YAML document rules.

        A governance block is only accepted together with ``spec_version: 2``
        (a legacy v1 contract declares its tier in the dataset manifest).  Any
        other key is refused, so a mistyped governance key can never leave a
        proxy contract quietly promotable.  A ``policy`` carrier (a dumped
        ``__dict__`` or a pre-#113 document) is accepted as the same declaration.
        """
        if not isinstance(document, dict):
            raise ContractValidationError(f"{source}: contract must be a mapping")
        valid_keys = {f.name for f in fields(cls)}
        # A contract's own ``__dict__`` carries the policy under the field name, so
        # that spelling is accepted as the same declaration (a dumped contract
        # reloads with its governance instead of failing as an unknown key).
        accepted = valid_keys | set(GOVERNANCE_KEYS)
        unknown = sorted(set(document) - accepted)
        if unknown:
            raise ContractValidationError(
                f"{source}: unknown contract key(s) {unknown}; a governance key must match "
                f"{list(GOVERNANCE_KEYS)} exactly")
        governance = {key: document[key] for key in GOVERNANCE_KEYS if key in document}
        carried = document.get(GOVERNANCE_INITVAR)
        if carried is not None:
            if not isinstance(carried, dict):
                raise ContractValidationError(f"{source}: policy must be a mapping")
            clash = sorted(set(carried) & set(governance))
            if clash:
                raise ContractValidationError(f"{source}: policy redeclares {clash} outside the policy block")
            governance = {**governance, **carried}
        if governance and governance.get("spec_version") != CURRENT_SPEC_VERSION:
            raise ContractValidationError(
                f"{source}: governance keys {sorted(governance)} require spec_version: {CURRENT_SPEC_VERSION}; "
                "a legacy v1 contract declares its evidence_tier in the dataset manifest instead")
        kwargs = {key: value for key, value in document.items() if key in valid_keys}
        if governance:
            kwargs[GOVERNANCE_ATTR] = ContractPolicy.from_mapping(governance)
        return cls(**kwargs)

    def spec_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.spec_hash_payload()).encode()).hexdigest()[:16]

    def to_feed_spec(self) -> FeedSpec:
        from ml4t.specs import FeedSpec

        return FeedSpec(
            timestamp_col="timestamp",
            entity_col="asset",
            open_col="open",
            high_col="high",
            low_col="low",
            close_col="close",
            volume_col="volume",
            bid_col="bid" if self.execution_mode == "QUOTE_SIDE" else None,
            ask_col="ask" if self.execution_mode == "QUOTE_SIDE" else None,
            calendar=None,
            timezone="UTC",
            data_frequency=self.frequency,
            time_contract_version=2,
        )


def contract_row(contract: EvaluationContract) -> dict:
    """The ``contracts``-table row for a contract (#113).

    ``asdict()`` carries the explicit ``policy`` field, but SQLite cannot bind a
    mapping, so the row stores the canonical JSON text (``""`` for a legacy
    contract).  Everything else is the historical field projection, and
    ``spec_hash`` is the contract's own digest — the row therefore records the
    same bytes as before #113 for every pre-existing contract.
    """
    row = asdict(contract)
    policy = row.pop(GOVERNANCE_ATTR, None)
    row[GOVERNANCE_ATTR] = canonical_json(policy) if policy else ""
    row["spec_hash"] = contract.spec_hash()
    return row


def validate_panel_feed_spec(panel: pl.DataFrame, contract: EvaluationContract) -> FeedSpec:
    import polars as pl

    spec = contract.to_feed_spec()
    if panel.is_empty():
        raise ContractValidationError(f"{contract.contract_id}: panel is empty")
    try:
        resolved = spec.resolve(panel.columns, ("asset",))
    except ValueError as exc:
        raise ContractValidationError(f"{contract.contract_id}: {exc}") from exc

    ts_col = resolved.timestamp_col
    ts_dtype = panel.schema[ts_col]
    if not isinstance(ts_dtype, pl.Datetime) or ts_dtype.time_zone not in {"UTC", "Etc/UTC"}:
        raise ContractValidationError(
            f"{contract.contract_id}: '{ts_col}' must be UTC-aware Datetime, got {ts_dtype}"
        )
    if panel.select(pl.col(ts_col).is_null().any()).item():
        raise ContractValidationError(f"{contract.contract_id}: null timestamps are not allowed")
    latest = panel.select(pl.col(ts_col).max()).item()
    if latest is not None and utc(latest) >= contract.holdout_start:
        raise ContractValidationError(
            f"{contract.contract_id}: '{ts_col}' reaches {latest} at or past holdout_start "
            f"{contract.holdout_start.isoformat()}; exploratory panels may not contain holdout bars"
        )
    entity_col = str(resolved.entity_col)
    if panel.select(
        (pl.col(entity_col).is_null() | (pl.col(entity_col).cast(pl.String).str.len_chars() == 0)).any()
    ).item():
        raise ContractValidationError(f"{contract.contract_id}: null or empty '{entity_col}' values")

    ohlcv_cols = [resolved.open_col, resolved.high_col, resolved.low_col, resolved.close_col, resolved.volume_col]
    missing_ohlcv = [c for c in ohlcv_cols if c not in panel.columns]
    if missing_ohlcv:
        raise ContractValidationError(f"{contract.contract_id}: missing OHLCV columns {missing_ohlcv}")
    for col in ohlcv_cols:
        if not panel.schema[col].is_numeric():
            raise ContractValidationError(f"{contract.contract_id}: '{col}' must be numeric, got {panel.schema[col]}")
        if panel.select((pl.col(col).is_null() | ~pl.col(col).is_finite()).any()).item():
            raise ContractValidationError(f"{contract.contract_id}: null or non-finite values in '{col}'")
    for col in ohlcv_cols[:4]:
        if panel.select((pl.col(col) <= 0).any()).item():
            raise ContractValidationError(f"{contract.contract_id}: non-positive prices in '{col}'")
    if panel.select((pl.col(resolved.volume_col) < 0).any()).item():
        raise ContractValidationError(f"{contract.contract_id}: negative volume in '{resolved.volume_col}'")

    if contract.execution_mode == "QUOTE_SIDE":
        for col in (resolved.bid_col, resolved.ask_col):
            if col not in panel.columns:
                raise ContractValidationError(f"{contract.contract_id}: QUOTE_SIDE requires '{col}'")
            if not panel.schema[col].is_numeric():
                raise ContractValidationError(f"{contract.contract_id}: '{col}' must be numeric")
            if panel.select((pl.col(col).is_null() | ~pl.col(col).is_finite() | (pl.col(col) <= 0)).any()).item():
                raise ContractValidationError(f"{contract.contract_id}: '{col}' must be non-null finite positive")
        if panel.select((pl.col(resolved.ask_col) < pl.col(resolved.bid_col)).any()).item():
            raise ContractValidationError(f"{contract.contract_id}: QUOTE_SIDE requires ask >= bid")

    if contract.asset_class == "crypto_perps":
        if "funding_rate" not in panel.columns:
            raise ContractValidationError(f"{contract.contract_id}: crypto_perps requires 'funding_rate'")
        if not panel.schema["funding_rate"].is_numeric():
            raise ContractValidationError(f"{contract.contract_id}: 'funding_rate' must be numeric")
        if panel.select((pl.col("funding_rate").is_null() | ~pl.col("funding_rate").is_finite()).any()).item():
            raise ContractValidationError(f"{contract.contract_id}: crypto_perps requires non-null finite 'funding_rate'")
    elif "funding_rate" in panel.columns:
        f_dtype = panel.schema["funding_rate"]
        if f_dtype != pl.Null and not f_dtype.is_numeric():
            raise ContractValidationError(f"{contract.contract_id}: 'funding_rate' must be numeric or null")
        if f_dtype != pl.Null and panel.select(
            (pl.col("funding_rate").is_not_null() & ~pl.col("funding_rate").is_finite()).any()
        ).item():
            raise ContractValidationError(f"{contract.contract_id}: non-finite values in 'funding_rate'")

    return resolved


@dataclass(frozen=True)
class HypothesisSpec:
    hypothesis_id: str
    contract_id: str
    title: str
    strategy_family: str
    economic_mechanism: str
    counterparty_or_friction: str
    expected_holding_bars: int
    falsification_criteria: str
    parent_hypothesis_id: str | None = None
    source_provenance: str = "internal_research"
    llm_model_id: str = "unspecified"
    strategy_module: str = ""
    source_published_at: str | None = None

    def __post_init__(self):
        valid_id(self.hypothesis_id)
        valid_id(self.contract_id)
        if self.parent_hypothesis_id:
            valid_id(self.parent_hypothesis_id)
        if self.parent_hypothesis_id == self.hypothesis_id or self.strategy_family not in {"rules_ts", "cross_sectional_ml", "carry_rv", "meta_overlay"}:
            raise ContractValidationError("invalid parent or strategy_family")
        if self.strategy_family == "meta_overlay" and not self.parent_hypothesis_id:
            raise ContractValidationError("meta_overlay requires parent_hypothesis_id")
        for n in ("economic_mechanism", "counterparty_or_friction"):
            if not isinstance(getattr(self, n), str) or len(getattr(self, n).strip()) < 20:
                raise ContractValidationError(f"{n} must explain at least 20 chars")
        if type(self.expected_holding_bars) is not int or self.expected_holding_bars < 1 or not self.title.strip() or not self.falsification_criteria.strip():
            raise ContractValidationError("invalid holding bars, title, or criteria")
        object.__setattr__(self, "source_published_at", _normalize_optional_utc(self.source_published_at))

    def source_published_utc(self) -> datetime | None:
        return utc(self.source_published_at) if self.source_published_at else None

    def is_retrospective_for(self, contract: EvaluationContract) -> bool:
        pub = self.source_published_utc()
        return pub is not None and pub >= contract.val_start

    @classmethod
    def from_yaml(cls, path: Path):
        return _load(path, cls)

    @classmethod
    def from_markdown(cls, path: Path):
        return _load(path, cls, markdown=True)


@dataclass(frozen=True)
class Stage1Report:
    strategy_family: str
    passed: bool
    metrics: dict[str, float | None] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ExperimentResult:
    run_id: str
    hypothesis_id: str
    contract_id: str
    strategy_name: str
    strategy_family: str
    config_hash: str
    stage_reached: str
    verdict: str
    gross_sharpe: float
    net_sharpe: float
    deflated_sharpe_prob: float
    expected_max_sharpe_noise: float
    total_trials_n: int
    effective_trials_keff: float
    mean_ic: float | None
    ic_hac_tstat: float | None
    hit_rate: float
    profit_factor: float
    max_drawdown_pct: float
    annualized_turnover: float
    breakeven_cost_bps: float
    stress_2x_cost_sharpe: float
    param_neighbor_stability: float | None
    notes: str
    delay_plus1_sharpe: float = 0.0
    cvar_99_pct: float = 0.0
    synthetic: int = 0
    data_snapshot_sha256: str = ""
    strategy_module: str = ""
    model_type: str = ""
    returns_sha256: str = ""
    data_provenance: str = "unknown"
    diagnostics_json: str = "{}"
    stress_1p5_cost_sharpe: float | None = None
    strategy_code_sha256: str = ""
    simulation_epoch: str = DEFAULT_SIMULATION_EPOCH

    def to_dict(self):
        return asdict(self)


def _load(path, cls, markdown=False):
    try:
        text = Path(path).read_text()
        if markdown:
            m = re.match(r"\A---\s*\n(.*?)\n---(?:\n|$)", text, re.DOTALL)
            if not m:
                raise ContractValidationError("missing Markdown frontmatter")
            text = m.group(1)
        v = yaml.safe_load(text)
        if not isinstance(v, dict):
            raise ContractValidationError("spec must be YAML mapping")
        if cls is EvaluationContract:
            # One deserialisation rule for YAML and for ``from_dict``: a
            # misspelled governance key must not silently leave a proxy
            # contract promotable, and an undeclared block is refused rather
            # than dropped onto a hash that does not cover it.
            return EvaluationContract.from_dict(v, source=str(path))
        valid_keys = {f.name for f in fields(cls)}
        return cls(**{k: val for k, val in v.items() if k in valid_keys})
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        if isinstance(exc, ContractValidationError):
            raise
        raise ContractValidationError(f"{path}: {exc}") from exc


def load_contract(path: Path) -> EvaluationContract:
    return _load(path, EvaluationContract)


def load_hypothesis(path: Path) -> HypothesisSpec:
    return _load(path, HypothesisSpec, markdown=Path(path).suffix == ".md")
