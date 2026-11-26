"""Human-only promotion of a compiled sandbox artifact into the production contract set (#118).

A ``warden data compile`` artifact (#117) is exploratory evidence: it lives under
``contracts/sandbox/`` and ``data/exploratory/sandbox/``, it is never holdout-eligible, and
``qualify`` refuses it by namespace.  Promotion is the one **human-gated** step that turns it
into a production *candidate*:

1. it re-validates the compiled-artifact contract, in the order
   :data:`warden.compile.ARTIFACT_CONTRACT` states — paths, bytes, raw-input Merkle root,
   spec/contract identity, policy, fence, lake provenance, namespace — and refuses **before**
   it copies anything;
2. every refusal is named (:class:`PromotionRefused` carries a stable ``code``), non-zero and
   atomic: nothing is copied and no registry is opened;
3. on success it copies the contract and the dataset manifest with **identical bytes**, writes
   a promotion receipt (who/what/when, every digest, the sandbox source paths), and tells the
   operator to re-sign the manifest — it never signs it, never generates it, never writes an
   admission attestation and never opens a holdout byte.

Three things this module deliberately does *not* do, because they are the boundary the ticket
draws:

* **no eligibility.**  The copied manifest still declares ``provenance: unknown`` and the
  contract carries no signed-origin admission, so the promoted contract is ``STAGED``: a
  Stage 3 verdict is ``NON_PROMOTABLE`` and ``qualify`` keeps refusing it for provenance.
  Promotion moves bytes into the production namespace; it does not make them market data.
* **no signing.**  ``contracts/datasets/<CONTRACT_ID>.json`` is protected and now stale; the
  human re-signs (``warden generate-manifest --human-approve-manifest``).  The receipt records
  ``manifest_signed: false`` so a later reader cannot mistake the copy for an approval.
* **no second validator.**  Every fact is produced by the module that owns it —
  ``warden.compile`` for the artifact schema and the catalogue provenance,
  ``warden_evidence.dataset_builder.merkle`` for the raw-input root, ``warden_ops.venue_policy``
  for the signed policy, ``warden.lifecycle`` for a retired id, ``warden.contract_source`` for
  the namespaces.  Promotion compares, it never re-derives.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from warden.compile import (
    ARTIFACT_SCHEMA,
    PARQUET_DIR,
    SANDBOX_MANIFEST_DIR,
    catalogue_provenance,
)
from warden.contract_source import is_sandbox_contract, sandbox_contract_path
from warden.contracts import FREQUENCY_BAR_DURATION, ContractValidationError, load_contract
from warden.lifecycle import (
    ContractLifecycleBlocked,
    ContractLifecycleError,
    load_lifecycle_registry,
)
from warden_evidence.dataset_builder.merkle import merkle_sha256
from warden_ops.venue_policy import (
    SelectionGroupResolutionError,
    VenuePolicyError,
    load_venue_policy,
    require_selection_group,
)

__all__ = [
    "CONFIRMATION_TOKEN",
    "CONTRACTS_DIR",
    "DATASETS_DIR",
    "NON_INTERACTIVE_ENV",
    "PROMOTION_SCHEMA",
    "RECEIPTS_DIR",
    "ContractPromotion",
    "PromotionRefused",
    "promote_contract",
]

#: Receipt schema.  A promotion receipt is written once, beside the production copy.
PROMOTION_SCHEMA = "spec109.contract-promotion.v1"
#: The token a human types.  A flag is intent; this literal is the decision.
CONFIRMATION_TOKEN = "YES_PROMOTE"
NON_INTERACTIVE_ENV = "WARDEN_NON_INTERACTIVE"

CONTRACTS_DIR = "contracts"
DATASETS_DIR = "contracts/datasets"
RECEIPTS_DIR = "contracts/promotions"

#: Refusals of the *request*: exit 2, like every other "the request was refused".
REQUEST_CODES = frozenset(
    {
        "not_authorized",
        "unknown_contract",
        "invalid_contract",
        "already_promoted",
        "half_promoted",
        "retired_contract",
        "wrong_namespace",
    }
)
#: Refusals of the *evidence*: exit 1, like ``data compile``'s fence/write-once refusals.
#: The artifact exists, the request is legitimate, and the bytes no longer support it.
EVIDENCE_CODES = frozenset(
    {
        "missing_manifest",
        "invalid_manifest",
        "mismatched_identity",
        "missing_dataset",
        "unpinned_dataset",
        "tampered_parquet",
        "mismatched_pin",
        "mismatched_merkle",
        "stale_policy",
        "stale_fence",
        "fence_violated",
        "lake_provenance",
        "lake_inputs",
    }
)
#: The copy itself failed; the tree was rolled back.
FAILURE_CODES = frozenset({"promotion_failed"})

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class PromotionRefused(ValueError):
    """A promotion was refused for a named reason, with nothing copied.

    ``code`` is the machine-readable reason (stable across releases); ``detail`` carries the
    field-level facts an operator needs (found/recorded digests, the failing field name).
    """

    def __init__(self, code: str, message: str, *, detail: dict[str, Any] | None = None) -> None:
        self.code = str(code)
        self.detail = dict(detail or {})
        super().__init__(f"{self.code}: {message}")

    @property
    def exit_code(self) -> int:
        """``2`` for a refused request, ``1`` for refused evidence or a failed copy.

        The vocabulary is ``docs/EXIT-CODES.md``: a request refusal is the caller's problem
        (2); refused evidence (``EVIDENCE_CODES``) or a failed copy (``FAILURE_CODES``) is
        the artifact's (1).  An unrecognised code lands on 1 as well, so a new refusal can
        never look like a success or a request error.
        """
        if self.code in REQUEST_CODES:
            return 2
        return 1


@dataclass(frozen=True)
class _Artifact:
    """One compiled sandbox artifact, as its own files describe it."""

    contract_id: str
    dataset_id: str
    contract_path: Path
    contract_bytes: bytes
    contract: Any
    manifest_path: Path
    manifest_bytes: bytes
    manifest: dict[str, Any]
    sandbox: dict[str, Any]
    dataset: dict[str, Any]
    parquet_path: Path
    parquet_sha256: str
    #: The repo-relative Parquet path the manifest declares (the refusal names it).
    dataset_path_rel: str


@dataclass(frozen=True)
class ContractPromotion:
    """The receipt a successful promotion returns (and writes)."""

    contract_id: str
    dataset_id: str
    promoted_at: str
    operator: str
    source: dict[str, str]
    target: dict[str, str]
    receipt_path: str
    digests: dict[str, str]
    selection_group_id: str
    asset_class: str
    frequency: str
    first_bar: str
    last_bar: str
    last_bar_close: str
    holdout_start: str
    holdout_end: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PROMOTION_SCHEMA,
            "contract_id": self.contract_id,
            "dataset_id": self.dataset_id,
            "action": "promote_compiled_sandbox_artifact",
            "promoted_at": self.promoted_at,
            "operator": self.operator,
            "human_confirmed": True,
            "source": dict(self.source),
            "target": dict(self.target),
            "receipt_path": self.receipt_path,
            "digests": dict(self.digests),
            "selection_group_id": self.selection_group_id,
            "asset_class": self.asset_class,
            "frequency": self.frequency,
            "first_bar": self.first_bar,
            "last_bar": self.last_bar,
            "last_bar_close": self.last_bar_close,
            "holdout_start": self.holdout_start,
            "holdout_end": self.holdout_end,
            # Never eligibility: the bytes are unchanged and the origin is still unattested.
            "qualification_eligible": False,
            "holdout_opened": False,
            "manifest_signed": False,
            "manifest_generated": False,
            "requires_human_resign": [self.target["manifest"]],
            "next_step": (
                "a human re-signs the protected inventory: "
                "`uv run warden generate-manifest --human-approve-manifest`"
            ),
            **dict(self.extra),
        }


# ---------------------------------------------------------------------------
# the human gate
# ---------------------------------------------------------------------------


def _operator_name(stated: str | None) -> str:
    if stated and str(stated).strip():
        return str(stated).strip()
    env = os.environ.get("WARDEN_OPERATOR", "").strip()
    if env:
        return env
    try:
        return getpass.getuser()
    except (KeyError, OSError):  # no passwd entry / no controlling terminal
        return "unknown-operator"


def _authorize(confirmation_token: str | None) -> None:
    """Refuse a non-human caller before a path is read.

    Two independent barriers, in the order ``warden qualify`` and ``data seal-holdout`` use
    them: a non-interactive caller (agent, cron, MCP client) can never promote, whatever it
    passes; and a flag is not a decision — the token a human types is.
    """
    if os.environ.get(NON_INTERACTIVE_ENV) == "1":
        raise PromotionRefused(
            "not_authorized",
            "warden promote-contract is an interactive human-only gate; it is refused under "
            f"{NON_INTERACTIVE_ENV}=1",
        )
    if str(confirmation_token or "") != CONFIRMATION_TOKEN:
        raise PromotionRefused(
            "not_authorized",
            f"promoting a compiled contract requires the operator token {CONFIRMATION_TOKEN!r}; "
            "nothing was copied",
        )


# ---------------------------------------------------------------------------
# step 1. paths: which artifact, and is it the one the manifest describes
# ---------------------------------------------------------------------------


def _refuse(code: str, message: str, **detail: Any) -> PromotionRefused:
    return PromotionRefused(code, message, detail=detail)


def _locate(root: Path, contract_id: str) -> _Artifact:
    """Resolve the sandbox contract + dataset manifest + Parquet, or refuse (step 1)."""
    stated = str(contract_id or "").strip()
    if not stated:
        raise _refuse("unknown_contract", "no contract id was stated")
    contract_path = sandbox_contract_path(root, stated)
    if contract_path is None:
        raise _refuse(
            "unknown_contract",
            f"no compiled sandbox contract for {stated!r} under {SANDBOX_MANIFEST_DIR.rsplit('/', 1)[0]}/: "
            "promotion starts from a `warden data compile` artifact, never from a live contract "
            "or a hand-written document",
            contract_id=stated,
        )
    try:
        contract = load_contract(contract_path)
    except (ContractValidationError, OSError, ValueError) as exc:
        raise _refuse(
            "invalid_contract",
            f"{contract_path.relative_to(root).as_posix()} does not load as an evaluation "
            f"contract: {exc}",
            contract=contract_path.relative_to(root).as_posix(),
        ) from exc

    cid = str(contract.contract_id)
    if cid.upper() != stated.upper():
        raise _refuse(
            "mismatched_identity",
            f"{contract_path.name} declares contract_id {cid!r}, not the requested {stated!r}",
            requested=stated,
            declared=cid,
        )
    manifest_path = root / SANDBOX_MANIFEST_DIR / f"{cid}.json"
    if not manifest_path.is_file():
        raise _refuse(
            "missing_manifest",
            f"the dataset manifest {SANDBOX_MANIFEST_DIR}/{cid}.json is missing: a promotion "
            "re-validates the compiled artifact, so a half-published artifact is refused",
            manifest=f"{SANDBOX_MANIFEST_DIR}/{cid}.json",
        )
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise _refuse(
            "invalid_manifest", f"{SANDBOX_MANIFEST_DIR}/{cid}.json is unreadable JSON: {exc}"
        ) from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("sandbox"), dict):
        raise _refuse(
            "invalid_manifest",
            f"{SANDBOX_MANIFEST_DIR}/{cid}.json carries no sandbox block; it is not a "
            f"{ARTIFACT_SCHEMA} artifact manifest",
        )
    sandbox = manifest["sandbox"]
    dataset = manifest.get("dataset")
    if not isinstance(dataset, dict):
        raise _refuse(
            "invalid_manifest", f"{SANDBOX_MANIFEST_DIR}/{cid}.json declares no dataset block"
        )

    if str(manifest.get("contract_id") or "") != cid:
        raise _refuse(
            "mismatched_identity",
            f"{SANDBOX_MANIFEST_DIR}/{cid}.json is published for contract "
            f"{manifest.get('contract_id')!r}, not {cid!r}",
            declared=manifest.get("contract_id"),
            contract_id=cid,
        )
    dataset_id = str(sandbox.get("dataset_id") or "")
    declared_ids = {str(manifest.get("dataset_id") or ""), str(dataset.get("dataset_id") or "")}
    if not dataset_id or declared_ids != {dataset_id}:
        raise _refuse(
            "mismatched_identity",
            f"{SANDBOX_MANIFEST_DIR}/{cid}.json declares dataset ids {sorted(declared_ids)} "
            f"but its sandbox block names {dataset_id!r}",
            sandbox_dataset_id=dataset_id,
            declared=sorted(declared_ids),
        )

    expected_parquet = f"{PARQUET_DIR}/{dataset_id}.parquet"
    declared_parquet = Path(str(contract.exploratory_data_path)).as_posix()
    if declared_parquet != expected_parquet:
        raise _refuse(
            "mismatched_identity",
            f"contract {cid} reads {declared_parquet!r}, not the sandbox dataset path "
            f"{expected_parquet!r}: a promoted contract must point at the artifact it was "
            "compiled from",
            contract_path_field=declared_parquet,
            expected=expected_parquet,
        )
    parquet_path = root / expected_parquet
    if not parquet_path.is_file():
        raise _refuse(
            "missing_dataset",
            f"the compiled dataset {expected_parquet} is missing; there is nothing to promote",
            parquet=expected_parquet,
        )
    return _Artifact(
        contract_id=cid,
        dataset_id=dataset_id,
        contract_path=contract_path,
        contract_bytes=contract_path.read_bytes(),
        contract=contract,
        manifest_path=manifest_path,
        manifest_bytes=manifest_bytes,
        manifest=manifest,
        sandbox=sandbox,
        dataset=dataset,
        parquet_path=parquet_path,
        parquet_sha256=hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
        dataset_path_rel=expected_parquet,
    )


# ---------------------------------------------------------------------------
# step 2. bytes: the Parquet, the manifest pin and the contract pin are one digest
# ---------------------------------------------------------------------------


def _check_bytes(artifact: _Artifact) -> None:
    actual = artifact.parquet_sha256
    recorded = str(artifact.manifest.get("sha256") or "")
    pinned = str(getattr(artifact.contract, "data_snapshot_sha256", "") or "")
    if not _SHA256_HEX.fullmatch(pinned):
        raise _refuse(
            "unpinned_dataset",
            f"contract {artifact.contract_id} carries no data_snapshot_sha256; the compiled "
            "artifact's contract must pin the bytes it was compiled from",
            contract=artifact.contract_path.name,
        )
    if recorded != actual:
        raise _refuse(
            "tampered_parquet",
            f"{artifact.dataset_path_rel} digest {actual} does not match the manifest pin "
            f"{recorded or '<missing>'}: the promoted bytes would not be the compiled bytes",
            actual=actual,
            recorded=recorded,
        )
    if pinned != actual:
        raise _refuse(
            "mismatched_pin",
            f"contract {artifact.contract_id} pins {pinned} but the dataset is {actual}: the "
            "contract was edited after the compile",
            contract_pin=pinned,
            actual=actual,
        )


# ---------------------------------------------------------------------------
# step 3. raw inputs: the Merkle root over the exact files read still holds
# ---------------------------------------------------------------------------


def _check_raw_inputs(artifact: _Artifact) -> None:
    lake = artifact.sandbox.get("lake") or {}
    root_text = str(lake.get("root") or "")
    if not root_text:
        raise _refuse(
            "lake_provenance",
            f"{artifact.contract_id}'s manifest records no lake root; the raw inputs it was "
            "compiled from cannot be re-read",
        )
    lake_root = Path(root_text)
    if not lake_root.is_dir():
        raise _refuse(
            "lake_provenance",
            f"the lake root {root_text!r} recorded at compile time is not readable",
            lake_root=root_text,
        )
    records = artifact.manifest.get("raw_inputs")
    if not isinstance(records, list) or not records:
        raise _refuse(
            "mismatched_merkle",
            f"{artifact.contract_id}'s manifest records no raw_inputs; the artifact has no "
            "raw-input provenance to re-prove",
        )
    pairs: list[tuple[str, bytes]] = []
    for record in records:
        if not isinstance(record, dict) or not str(record.get("path") or "").strip():
            raise _refuse(
                "invalid_manifest", "a raw_inputs record carries no path; the Merkle tree is unbuildable"
            )
        relative = str(record["path"])
        target = lake_root / relative
        if not target.is_file():
            raise _refuse(
                "mismatched_merkle",
                f"raw input {relative!r} is missing from the lake; the compiled bytes can no "
                "longer be attributed to the bytes read",
                path=relative,
            )
        payload = target.read_bytes()
        if record.get("bytes") is not None and int(record["bytes"]) != len(payload):
            raise _refuse(
                "mismatched_merkle",
                f"raw input {relative!r} is {len(payload)} bytes, the manifest records "
                f"{record['bytes']}",
                path=relative,
                found=len(payload),
                recorded=record["bytes"],
            )
        if str(record.get("sha256") or "") != hashlib.sha256(payload).hexdigest():
            raise _refuse(
                "mismatched_merkle",
                f"raw input {relative!r} changed since the compile: a changed lake breaks the "
                "Merkle root promotion re-proves",
                path=relative,
            )
        pairs.append((relative, payload))

    root_hex, _records = merkle_sha256(pairs)
    recorded_root = str(artifact.manifest.get("raw_inputs_merkle_sha256") or "")
    if root_hex != recorded_root:
        raise _refuse(
            "mismatched_merkle",
            f"the recomputed raw-input Merkle root {root_hex} != the recorded root "
            f"{recorded_root or '<missing>'}: the lake this artifact was compiled from moved",
            found=root_hex,
            recorded=recorded_root,
        )


# ---------------------------------------------------------------------------
# step 4. spec/contract identity: which compiled spec, and which contract digest
# ---------------------------------------------------------------------------


def _check_spec_identity(artifact: _Artifact) -> str:
    spec_sha = str(artifact.manifest.get("spec_sha256") or "")
    if not _SHA256_HEX.fullmatch(spec_sha):
        raise _refuse(
            "invalid_manifest",
            f"{artifact.contract_id}'s manifest carries no spec_sha256; the compiled spec is "
            "not identifiable",
        )
    gate = artifact.manifest.get("quality_gate_report")
    if isinstance(gate, dict):
        recorded_spec = str(gate.get("spec_sha256") or "")
        if recorded_spec and recorded_spec != spec_sha:
            raise _refuse(
                "mismatched_identity",
                f"the manifest records two different spec digests ({spec_sha} vs {recorded_spec})",
                manifest=spec_sha,
                quality_gate_report=recorded_spec,
            )
    spec_hash = str(artifact.contract.spec_hash() or "")
    if not spec_hash:
        raise _refuse(
            "invalid_contract",
            f"contract {artifact.contract_id} has no spec_hash(); promotion records it as the "
            "contract identity",
        )
    return spec_hash


# ---------------------------------------------------------------------------
# step 5. policy: the artifact was frozen under *this* signed policy
# ---------------------------------------------------------------------------

#: The five floors a compiled contract freezes from the policy.  The first three are
#: ``EvaluationContract`` fields (the execution floors); leverage is governance (#113).
_RULE_FIELDS = (
    "taker_fee_bps",
    "slippage_bps",
    "funding_bps_per_8h",
    "allow_leverage",
    "max_gross_leverage",
)
#: Where each frozen floor lives on the contract, so the check names the real owner.
_RULE_OWNERS = {
    "taker_fee_bps": ("contract", "taker_fee_bps"),
    "slippage_bps": ("contract", "slippage_bps"),
    "funding_bps_per_8h": ("contract", "funding_bps_per_8h"),
    "allow_leverage": ("governance", "allow_leverage"),
    "max_gross_leverage": ("governance", "max_gross_leverage"),
}


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) is bool(right)
    try:
        return abs(float(left) - float(right)) <= 1e-9
    except (TypeError, ValueError):
        return left == right


def _check_policy(root: Path, artifact: _Artifact) -> Any:
    contract = artifact.contract
    try:
        policy = load_venue_policy(root)
    except VenuePolicyError as exc:
        raise _refuse(
            "stale_policy",
            f"the signed venue policy cannot be trusted, so no current policy can be matched "
            f"({exc})",
            policy="untrusted",
        ) from exc
    block = artifact.sandbox.get("policy")
    if not isinstance(block, dict):
        raise _refuse(
            "invalid_manifest",
            f"{artifact.contract_id}'s manifest records no sandbox policy block",
        )
    recorded_digest = str(block.get("sha256") or "")
    if recorded_digest != policy.sha256:
        raise _refuse(
            "stale_policy",
            f"the artifact was frozen under policy {recorded_digest or '<missing>'} but the "
            f"signed policy is now {policy.sha256}: recompile, do not re-date the artifact",
            recorded=recorded_digest,
            current=policy.sha256,
        )
    try:
        group = require_selection_group(policy, contract.asset_class, contract.frequency)
    except SelectionGroupResolutionError as exc:
        raise _refuse(
            "stale_policy",
            f"the signed policy no longer maps this market domain: {exc}",
            domain=f"{contract.asset_class}/{contract.frequency}",
        ) from exc
    if str(block.get("selection_group_id") or "") != group:
        raise _refuse(
            "stale_policy",
            f"the artifact's selection_group_id {block.get('selection_group_id')!r} != the "
            f"policy's {group!r}: search debt would be charged to the wrong group",
            field="selection_group_id",
            recorded=block.get("selection_group_id"),
            current=group,
        )
    try:
        rules = policy.rules_for(contract.asset_class, contract.frequency)
    except VenuePolicyError as exc:
        raise _refuse(
            "stale_policy", f"the signed policy carries no rules for this domain: {exc}"
        ) from exc
    recorded_rules = block.get("rules")
    if not isinstance(recorded_rules, dict):
        raise _refuse(
            "invalid_manifest", f"{artifact.contract_id}'s manifest records no policy rules"
        )
    for name in _RULE_FIELDS:
        current = getattr(rules, name)
        if not _same(recorded_rules.get(name), current):
            raise _refuse(
                "stale_policy",
                f"the artifact froze {name}={recorded_rules.get(name)!r} but the signed policy "
                f"says {current!r}",
                field=name,
                recorded=recorded_rules.get(name),
                current=current,
            )
        owner, attribute = _RULE_OWNERS[name]
        frozen = getattr(contract if owner == "contract" else contract.governance, attribute)
        if not _same(frozen, current):
            raise _refuse(
                "stale_policy",
                f"contract {artifact.contract_id} froze {name}={frozen!r} but the signed policy "
                f"says {current!r}",
                field=name,
                contract=frozen,
                current=current,
            )
    governance = contract.governance
    if str(governance.selection_group_id or "") != group:
        raise _refuse(
            "stale_policy",
            f"contract {artifact.contract_id} declares selection_group_id "
            f"{governance.selection_group_id!r} but the signed policy maps this domain to "
            f"{group!r}",
            field="contract.selection_group_id",
            contract=governance.selection_group_id,
            current=group,
        )
    if str(governance.dataset_id or "") != artifact.dataset_id:
        raise _refuse(
            "mismatched_identity",
            f"contract {artifact.contract_id} declares dataset_id {governance.dataset_id!r} but "
            f"the manifest's dataset is {artifact.dataset_id!r}",
            field="contract.dataset_id",
            contract=governance.dataset_id,
            manifest=artifact.dataset_id,
        )
    return policy


# ---------------------------------------------------------------------------
# step 6. fence: the same fence, still before the holdout, on a live contract
# ---------------------------------------------------------------------------


def _check_fence(root: Path, artifact: _Artifact, policy: Any) -> datetime:
    contract = artifact.contract
    block = artifact.sandbox.get("fence")
    if not isinstance(block, dict):
        raise _refuse(
            "invalid_manifest", f"{artifact.contract_id}'s manifest records no sandbox fence"
        )
    recorded_fence = _timestamp(block.get("holdout_start"))
    if not (
        recorded_fence == contract.holdout_start == policy.holdout_start
    ):
        raise _refuse(
            "stale_fence",
            "the compiled fence, the contract's holdout_start and the signed policy's "
            f"holdout_start disagree ({recorded_fence.isoformat()}, "
            f"{contract.holdout_start.isoformat()}, {policy.holdout_start.isoformat()})",
            artifact=recorded_fence.isoformat(),
            contract=contract.holdout_start.isoformat(),
            policy=policy.holdout_start.isoformat(),
        )
    if str(block.get("target_frequency") or "") != contract.frequency:
        raise _refuse(
            "stale_fence",
            f"the artifact's fence grid is {block.get('target_frequency')!r} but the contract "
            f"is {contract.frequency!r}",
            field="target_frequency",
        )
    interval = FREQUENCY_BAR_DURATION[contract.frequency]
    if int(block.get("bar_interval_seconds") or 0) != int(interval.total_seconds()):
        raise _refuse(
            "stale_fence",
            f"the artifact's bar interval {block.get('bar_interval_seconds')!r}s is not the "
            f"{contract.frequency} interval {int(interval.total_seconds())}s",
            field="bar_interval_seconds",
        )
    last_bar = _timestamp(artifact.dataset.get("last_bar"))
    last_close = last_bar + interval
    if last_close > contract.holdout_start:
        raise _refuse(
            "fence_violated",
            f"the compiled last bar closes at {last_close.isoformat()}, after the holdout fence "
            f"{contract.holdout_start.isoformat()}; the manifest's own "
            "fence_satisfied flag is not trusted over the arithmetic",
            last_bar=last_bar.isoformat(),
            last_bar_close=last_close.isoformat(),
            holdout_start=contract.holdout_start.isoformat(),
        )
    if not contract.holdout_start < contract.holdout_end:
        raise _refuse(
            "retired_contract",
            f"contract {artifact.contract_id} declares an elapsed holdout window "
            f"({contract.holdout_start.isoformat()} .. {contract.holdout_end.isoformat()})",
            holdout_start=contract.holdout_start.isoformat(),
            holdout_end=contract.holdout_end.isoformat(),
        )
    try:
        load_lifecycle_registry(root).assert_open(contract.contract_id)
    except ContractLifecycleBlocked as exc:
        raise _refuse(
            "retired_contract",
            f"the reviewed lifecycle document does not keep {artifact.contract_id} open: {exc}",
            contract_id=artifact.contract_id,
        ) from exc
    except ContractLifecycleError as exc:
        raise _refuse(
            "retired_contract",
            f"the lifecycle document is unusable, so openness cannot be established: {exc}",
            contract_id=artifact.contract_id,
        ) from exc
    return last_close


# ---------------------------------------------------------------------------
# step 7. lake provenance: the catalogue still says what it said at compile time
# ---------------------------------------------------------------------------


def _check_lake(artifact: _Artifact) -> None:
    lake = artifact.sandbox.get("lake")
    if not isinstance(lake, dict):
        raise _refuse(
            "lake_provenance", f"{artifact.contract_id}'s manifest records no lake block"
        )
    lake_root_text = str(lake.get("root") or "")
    if not lake_root_text:
        raise _refuse("lake_provenance", "the artifact records no lake root")
    lake_root = Path(lake_root_text)
    if not lake_root.is_dir():
        raise _refuse(
            "lake_provenance",
            f"the lake root {lake_root_text!r} is not readable, so its catalogue cannot be "
            "re-resolved",
            lake_root=lake_root_text,
        )
    keys = lake.get("dataset_keys")
    if not isinstance(keys, dict) or not keys:
        raise _refuse(
            "lake_provenance", f"{artifact.contract_id}'s manifest maps no symbol to a store"
        )
    recorded_acceptance = lake.get("acceptance")
    if not isinstance(recorded_acceptance, dict):
        raise _refuse(
            "lake_provenance",
            f"{artifact.contract_id}'s manifest records no per-store acceptance identity",
        )
    current = catalogue_provenance(lake_root, [str(key) for key in keys.values()])
    if str(current.get("catalog_identity") or "") != str(lake.get("catalog_identity") or ""):
        raise _refuse(
            "lake_provenance",
            "the lake catalogue identity moved since the compile "
            f"({current.get('catalog_identity')!r} != {lake.get('catalog_identity')!r})",
            current=current.get("catalog_identity"),
            recorded=lake.get("catalog_identity"),
        )
    for key in sorted({str(key) for key in keys.values()}):
        now_entry = (current.get("acceptance") or {}).get(key) or {}
        recorded = recorded_acceptance.get(key) or {}
        if not now_entry.get("present"):
            raise _refuse(
                "lake_provenance",
                f"the catalogue no longer accepts store {key!r}: {now_entry.get('reason', 'not present')}",
                store=key,
            )
        for field_name in ("relative_path", "sha256", "entry_identity"):
            if str(now_entry.get(field_name) or "") != str(recorded.get(field_name) or ""):
                raise _refuse(
                    "lake_provenance",
                    f"store {key!r} acceptance {field_name} moved "
                    f"({now_entry.get(field_name)!r} != {recorded.get(field_name)!r})",
                    store=key,
                    field=field_name,
                    current=now_entry.get(field_name),
                    recorded=recorded.get(field_name),
                )

    input_files = [str(item) for item in lake.get("input_files") or []]
    if not input_files:
        raise _refuse(
            "lake_inputs", f"{artifact.contract_id}'s manifest names no input file"
        )
    absent = sorted(item for item in input_files if not (lake_root / item).is_file())
    if absent:
        raise _refuse(
            "lake_inputs",
            f"recorded input file(s) {absent} are missing from the lake",
            missing=absent,
        )
    excluded = [str(item) for item in lake.get("excluded_partitions") or []]
    overlap = sorted(set(excluded) & set(input_files))
    if overlap:
        raise _refuse(
            "lake_inputs",
            f"partition(s) {overlap} are recorded as both excluded and read: the fence "
            "bookkeeping moved",
            overlap=overlap,
        )
    for item in excluded:
        prefix = item.rstrip("/") + "/"
        nested = sorted(entry for entry in input_files if entry.startswith(prefix))
        if nested:
            raise _refuse(
                "lake_inputs",
                f"excluded partition {item!r} now holds input file(s) {nested}: the recorded "
                "exclusion no longer describes what was read",
                partition=item,
                inputs=nested,
            )


# ---------------------------------------------------------------------------
# step 8. namespace: still a sandbox artifact, not already promoted
# ---------------------------------------------------------------------------


def _check_namespace(root: Path, artifact: _Artifact) -> None:
    if str(artifact.sandbox.get("schema") or "") != ARTIFACT_SCHEMA:
        raise _refuse(
            "wrong_namespace",
            f"{artifact.contract_id} declares sandbox schema "
            f"{artifact.sandbox.get('schema')!r}, not {ARTIFACT_SCHEMA!r}",
            schema=artifact.sandbox.get("schema"),
        )
    if artifact.sandbox.get("qualification_eligible") is not False:
        raise _refuse(
            "wrong_namespace",
            f"{artifact.contract_id} does not declare itself non-qualifying; a promotion never "
            "inherits an eligibility claim",
        )
    live = root / CONTRACTS_DIR / f"{artifact.contract_id.lower()}.yaml"
    if live.is_file():
        raise _refuse(
            "already_promoted",
            f"{live.relative_to(root).as_posix()} already exists: promotion is once per contract "
            "id, and a production copy is never overwritten",
            path=live.relative_to(root).as_posix(),
        )
    receipt = root / RECEIPTS_DIR / f"{artifact.contract_id}.json"
    if receipt.is_file():
        raise _refuse(
            "already_promoted",
            f"{receipt.relative_to(root).as_posix()} records an earlier promotion of "
            f"{artifact.contract_id}",
            path=receipt.relative_to(root).as_posix(),
        )
    target_manifest = root / DATASETS_DIR / f"{artifact.contract_id}.json"
    if target_manifest.is_file():
        raise _refuse(
            "half_promoted",
            f"{target_manifest.relative_to(root).as_posix()} exists without its contract or "
            "receipt: a stranded copy is refused, never completed",
            path=target_manifest.relative_to(root).as_posix(),
        )
    if not is_sandbox_contract(root, artifact.contract_id):
        raise _refuse(
            "wrong_namespace",
            f"{artifact.contract_id} no longer resolves to the sandbox namespace; only a compiled "
            "sandbox artifact is promotable",
        )


# ---------------------------------------------------------------------------
# the promotion
# ---------------------------------------------------------------------------


def _copy_exclusive(path: Path, payload: bytes) -> None:
    """Create ``path`` with ``payload``, refusing to overwrite anything."""
    with open(path, "xb") as handle:
        handle.write(payload)


def _publish(root: Path, artifact: _Artifact, receipt: ContractPromotion) -> ContractPromotion:
    """Copy the two documents and the receipt, or leave the tree as it was."""
    targets = {
        "manifest": (root / DATASETS_DIR / f"{artifact.contract_id}.json", artifact.manifest_bytes),
        "contract": (root / CONTRACTS_DIR / f"{artifact.contract_id.lower()}.yaml", artifact.contract_bytes),
        "receipt": (
            root / RECEIPTS_DIR / f"{artifact.contract_id}.json",
            (json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n").encode("utf-8"),
        ),
    }
    for directory in (root / CONTRACTS_DIR, root / DATASETS_DIR, root / RECEIPTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        # The manifest first, the contract second: the contract's presence is what makes the
        # id resolve.  A reader that sees the contract therefore always sees its manifest.
        for name in ("manifest", "contract", "receipt"):
            path, payload = targets[name]
            _copy_exclusive(path, payload)
            written.append(path)
    except BaseException as exc:
        for path in reversed(written):
            path.unlink(missing_ok=True)
        if isinstance(exc, FileExistsError):
            raise _refuse(
                "already_promoted",
                f"{exc.filename} already exists; a promotion is write-once and was rolled back",
            ) from exc
        raise _refuse("promotion_failed", f"the copy failed and was rolled back: {exc}") from exc
    return receipt


def promote_contract(
    contract_id: str,
    *,
    repo_root: Path | str = ".",
    confirmation_token: str = "",
    operator: str | None = None,
    now: datetime | None = None,
) -> ContractPromotion:
    """Promote one compiled sandbox contract into the production candidate set.

    The order is the contract: the human gate, then every validation step of
    :data:`warden.compile.ARTIFACT_CONTRACT` in the order it states, and only then the copy.
    Nothing is copied, no registry is opened and no holdout byte is read on any path.

    Raises:
        PromotionRefused: for a named, atomic refusal (``code`` says which).
    """
    _authorize(confirmation_token)
    root = Path(repo_root)
    artifact = _locate(root, str(contract_id))
    _check_bytes(artifact)
    _check_raw_inputs(artifact)
    spec_hash = _check_spec_identity(artifact)
    policy = _check_policy(root, artifact)
    last_close = _check_fence(root, artifact, policy)
    _check_lake(artifact)
    _check_namespace(root, artifact)

    contract = artifact.contract
    moment = now or datetime.now(UTC)
    receipt_path = f"{RECEIPTS_DIR}/{artifact.contract_id}.json"
    return _publish(
        root,
        artifact,
        ContractPromotion(
            contract_id=artifact.contract_id,
            dataset_id=artifact.dataset_id,
            promoted_at=moment.astimezone(UTC).isoformat(),
            operator=_operator_name(operator),
            source={
                "contract": artifact.contract_path.relative_to(root).as_posix(),
                "manifest": artifact.manifest_path.relative_to(root).as_posix(),
                "parquet": Path(str(contract.exploratory_data_path)).as_posix(),
            },
            target={
                "contract": f"{CONTRACTS_DIR}/{artifact.contract_id.lower()}.yaml",
                "manifest": f"{DATASETS_DIR}/{artifact.contract_id}.json",
            },
            receipt_path=receipt_path,
            digests={
                "parquet_sha256": artifact.parquet_sha256,
                "manifest_sha256": hashlib.sha256(artifact.manifest_bytes).hexdigest(),
                "contract_sha256": hashlib.sha256(artifact.contract_bytes).hexdigest(),
                "spec_sha256": str(artifact.manifest.get("spec_sha256") or ""),
                "contract_spec_hash": spec_hash,
                "raw_inputs_merkle_sha256": str(artifact.manifest.get("raw_inputs_merkle_sha256") or ""),
                "policy_sha256": str((artifact.sandbox.get("policy") or {}).get("sha256") or ""),
            },
            selection_group_id=str((artifact.sandbox.get("policy") or {}).get("selection_group_id") or ""),
            asset_class=str(contract.asset_class),
            frequency=str(contract.frequency),
            first_bar=str(artifact.dataset.get("first_bar") or ""),
            last_bar=str(artifact.dataset.get("last_bar") or ""),
            last_bar_close=last_close.isoformat(),
            holdout_start=contract.holdout_start.isoformat(),
            holdout_end=contract.holdout_end.isoformat(),
        ),
    )


def _timestamp(value: Any) -> datetime:
    """Parse a manifest timestamp (Polars spelling ``… UTC`` or ISO-8601), refusing a naive one."""
    text = str(value).strip()
    if text.endswith(" UTC"):
        text = text[: -len(" UTC")] + "+00:00"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise _refuse("invalid_manifest", f"manifest timestamp {value!r} carries no timezone")
    return parsed.astimezone(UTC)
