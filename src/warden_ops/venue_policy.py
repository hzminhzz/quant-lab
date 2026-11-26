"""Read-only, manifest-authenticated research standards for new contracts."""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal

import yaml
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from warden_ops.integrity import VENUE_POLICY_FILE, read_signed_manifest_entry

AssetClass = Literal["crypto_perps", "fx", "indices", "metals", "commodities", "multi_asset"]
CadenceBucket = Literal["intraday", "daily"]
SelectionGroupID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+$")]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class VenuePolicyError(ValueError):
    """The venue policy is unavailable, is not trusted, or cannot govern this market."""


class _PolicyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise yaml.YAMLError("expected a policy mapping")
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise yaml.YAMLError("policy mapping keys must be strings")
            if key in mapping:
                raise ValueError(f"duplicate policy key: {key!r}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


# Let the policy's UTC validator interpret both quoted and unquoted timestamps.
_PolicyLoader.add_constructor("tag:yaml.org,2002:timestamp", _PolicyLoader.construct_scalar)


def _utc_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("policy timestamps must be ISO-8601 datetimes with an explicit timezone")
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError("policy timestamp is outside the UTC datetime range") from exc


PolicyTimestamp = Annotated[AwareDatetime, BeforeValidator(_utc_timestamp)]


def cadence_bucket(frequency: str) -> CadenceBucket:
    """Return the selection-debt cadence for a canonical contract frequency."""
    if frequency == "1d":
        return "daily"
    if frequency in ("1m", "5m", "15m", "30m", "1h", "4h"):
        return "intraday"
    raise VenuePolicyError(f"unsupported venue policy frequency: {frequency!r}")


class _PolicyFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class _AssetRules(_PolicyFields):
    taker_fee_bps: Positive
    slippage_bps: Positive
    funding_bps_per_8h: NonNegative
    allow_leverage: bool
    max_gross_leverage: Positive
    selection_groups: Annotated[dict[CadenceBucket, SelectionGroupID], Field(min_length=1)]


class _Document(_PolicyFields):
    version: int
    train_start: PolicyTimestamp
    val_start: PolicyTimestamp
    val_end: PolicyTimestamp
    holdout_start: PolicyTimestamp
    holdout_end: PolicyTimestamp
    asset_classes: Annotated[dict[AssetClass, _AssetRules], Field(min_length=1)]


@dataclass(frozen=True, slots=True)
class VenueRules:
    """Policy floors, leverage ceiling, and debt identity for one market domain."""

    taker_fee_bps: float
    slippage_bps: float
    funding_bps_per_8h: float
    allow_leverage: bool
    max_gross_leverage: float
    selection_group_id: str


@dataclass(frozen=True, slots=True)
class VenuePolicy:
    """Validated policy. Load through `load_venue_policy` to authenticate its origin."""

    train_start: datetime
    val_start: datetime
    val_end: datetime
    holdout_start: datetime
    holdout_end: datetime
    sha256: str
    _rules: Mapping[tuple[str, CadenceBucket], VenueRules] = field(repr=False)

    def rules_for(self, asset_class: str, frequency: str) -> VenueRules:
        bucket = cadence_bucket(frequency)
        try:
            return self._rules[asset_class, bucket]
        except KeyError:
            raise VenuePolicyError(
                f"no venue policy rules for ({asset_class!r}, {bucket!r})"
            ) from None

    def selection_group_id(self, asset_class: str, frequency: str) -> str:
        return self.rules_for(asset_class, frequency).selection_group_id


def load_venue_policy(repo_root: Path = Path(".")) -> VenuePolicy:
    """Read and validate the repository's one signed policy. No fallback or writes."""
    try:
        data = read_signed_manifest_entry(repo_root, VENUE_POLICY_FILE)
    except (OSError, ValueError, RuntimeError) as exc:
        raise VenuePolicyError(f"cannot trust venue policy: {exc}") from exc
    try:
        document = _Document.model_validate(yaml.load(data.decode("utf-8"), Loader=_PolicyLoader))
    except (ValueError, RecursionError, yaml.YAMLError) as exc:
        raise VenuePolicyError(f"malformed venue policy: {exc}") from exc
    if document.version != 1:
        raise VenuePolicyError("malformed venue policy: version must be 1")
    if not (
        document.train_start
        < document.val_start
        < document.val_end
        <= document.holdout_start
        < document.holdout_end
    ):
        raise VenuePolicyError(
            "malformed venue policy: temporal boundaries overlap or are out of order"
        )

    rules = {}
    for asset_class, row in document.asset_classes.items():
        if not row.allow_leverage and row.max_gross_leverage > 1:
            raise VenuePolicyError(
                f"malformed venue policy: {asset_class} leverage exceeds cash cap"
            )
        for bucket, group in row.selection_groups.items():
            rules[asset_class, bucket] = VenueRules(
                row.taker_fee_bps,
                row.slippage_bps,
                row.funding_bps_per_8h,
                row.allow_leverage,
                row.max_gross_leverage,
                group,
            )
    return VenuePolicy(
        train_start=document.train_start,
        val_start=document.val_start,
        val_end=document.val_end,
        holdout_start=document.holdout_start,
        holdout_end=document.holdout_end,
        sha256=hashlib.sha256(data).hexdigest(),
        _rules=MappingProxyType(rules),
    )
