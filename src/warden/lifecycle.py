"""External, read-only contract-lifecycle registry (issue #106).

Why a file rather than a table: the SQLite ledger is *evidence*.  Trials, attempts and
hypothesis rows are append-only and a later ticket must never be able to re-interpret
them.  Lifecycle facts ("this contract is deprecated and unexecutable", "this one keeps a
survivorship-biased universe", "this v2 replacement inherits that search problem") are
*decisions about the future*, so they live beside the contracts as one reviewed document
that a human signs with the rest of the protected inventory.

Read-only is a property of the code, not a promise: this module has no writer.  There is
no ``save``/``update``/``append`` path and no ``open(..., "w")`` anywhere in it, so an
agent can consult the registry but cannot re-status a contract.  A malformed or
unreadable registry is refused outright -- a lifecycle decision that cannot be parsed is
never treated as "no decision", because that is exactly how a deprecated contract would
become executable again.

Blast radius by design:

* a **blocking** entry refuses a *new* hypothesis (``register-hypothesis`` and the
  ``advance`` auto-registration path).  It never touches historical evidence:
  ``status``, ``verify-causality`` and ``audit-check`` do not import this module, so a
  trial recorded under a deprecated contract still reads back exactly as recorded, with
  its v1 ``spec_hash`` unchanged (``lifecycle`` is not part of ``spec_hash_payload()``);
* an **ACTIVE_V2** entry only records which replacement owns the successor role and which
  legacy selection group its DSR floor must inherit.  The floor itself is still computed
  by :meth:`warden.registry.ResearchRegistry.prior_search_floor` from recorded trials --
  this module supplies the *scope key*, never a number, so it cannot lower a floor.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Reviewed lifecycle document.  It sits in its own directory so the ``contracts/*.yaml``
#: glob that enumerates contract specs never matches it, and it is added to the protected
#: inventory explicitly (``warden_ops.integrity._PROTECTED_FILES``) so deleting or editing
#: it invalidates ``warden check-worktree`` -- the gate every agent runs before registering.
DEFAULT_REGISTRY_PATH = Path("contracts") / "lifecycle" / "contract_lifecycle.json"
ENV_REGISTRY_PATH = "WARDEN_LIFECYCLE_REGISTRY"
SCHEMA_VERSION = 1

DEPRECATED_UNEXECUTABLE = "DEPRECATED_UNEXECUTABLE"
LEGACY_SURVIVORSHIP_BIASED = "LEGACY_SURVIVORSHIP_BIASED"
ACTIVE_V2 = "ACTIVE_V2"

#: Statuses that forbid a new hypothesis on the contract.
BLOCKING_STATUSES = frozenset({DEPRECATED_UNEXECUTABLE, LEGACY_SURVIVORSHIP_BIASED})
KNOWN_STATUSES = BLOCKING_STATUSES | {ACTIVE_V2}
_TOP_LEVEL_KEYS = frozenset({"schema_version", "generated_at", "note", "contracts", "legacy_scripts"})
_ENTRY_KEYS = frozenset(
    {"contract_id", "status", "reason", "replacement_contract_id", "selection_group_id", "evidence"}
)
_SCRIPT_KEYS = frozenset({"path", "replaces", "archive_after", "status"})
_ID = re.compile(r"[A-Z][A-Z0-9_]*")
_GROUP = re.compile(r"[A-Za-z0-9_-]+")
_SCRIPT_STATUSES = frozenset({"pending_replacement", "archived"})


class ContractLifecycleError(ValueError):
    """The lifecycle document is unusable. Fails closed; never read as "no entries"."""


class ContractLifecycleBlocked(ContractLifecycleError):
    """A new hypothesis was declared on a contract the lifecycle document closes."""

    def __init__(self, entry: LifecycleEntry) -> None:
        self.entry = entry
        successor = (
            f"; replacement is {entry.replacement_contract_id}"
            if entry.replacement_contract_id
            else "; no replacement is declared"
        )
        super().__init__(
            f"contract {entry.contract_id} is {entry.status}: {entry.reason}{successor}"
        )


@dataclass(frozen=True)
class LifecycleEntry:
    """One contract's reviewed lifecycle decision."""

    contract_id: str
    status: str
    reason: str
    replacement_contract_id: str = ""
    selection_group_id: str = ""
    evidence: str = ""

    @property
    def blocking(self) -> bool:
        return self.status in BLOCKING_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "status": self.status,
            "reason": self.reason,
            "replacement_contract_id": self.replacement_contract_id,
            "selection_group_id": self.selection_group_id,
            "evidence": self.evidence,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class LifecycleRegistry:
    """A parsed lifecycle document. Immutable; consult-only."""

    path: str
    entries: Mapping[str, LifecycleEntry]
    legacy_scripts: tuple[dict[str, Any], ...] = ()
    #: ``"file"`` when a document was read, ``"absent"`` when none exists at ``path``.
    source: str = "file"

    # -- lookups ---------------------------------------------------------------
    def entry(self, contract_id: str) -> LifecycleEntry | None:
        return self.entries.get(str(contract_id))

    def blocking_reason(self, contract_id: str) -> str | None:
        item = self.entry(contract_id)
        if item is None or not item.blocking:
            return None
        return str(item.reason)

    def assert_open(self, contract_id: str) -> None:
        """Refuse a new hypothesis on a blocked contract."""
        item = self.entry(contract_id)
        if item is not None and item.blocking:
            raise ContractLifecycleBlocked(item)

    def dsr_inheritance(self, contract_id: str) -> dict[str, str]:
        """The governance scope a v2 replacement must declare to inherit this contract.

        Returns ``{}`` for an unknown contract and for a contract the document does not
        place in a selection group.  A replacement contract declares both keys verbatim;
        the floor number itself is never stored here (see module docstring).
        """
        item = self.entry(contract_id)
        if item is None or not item.selection_group_id:
            return {}
        return {
            "selection_group_id": item.selection_group_id,
            "inherits_selection_from": item.contract_id,
        }

    def replacements(self) -> dict[str, LifecycleEntry]:
        return {cid: e for cid, e in self.entries.items() if e.status == ACTIVE_V2}

    def blocking_entries(self) -> dict[str, LifecycleEntry]:
        return {cid: e for cid, e in self.entries.items() if e.blocking}

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "source": self.source,
            "entries": {cid: e.to_dict() for cid, e in sorted(self.entries.items())},
            "legacy_scripts": list(self.legacy_scripts),
        }


def _fail(message: str) -> ContractLifecycleError:
    return ContractLifecycleError(message)


def _text(value: Any, field: str, *, required: bool) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{field} must be a non-empty string")
    return value.strip()


def _parse_entry(raw: Any, index: int) -> LifecycleEntry:
    if not isinstance(raw, dict):
        raise _fail(f"contracts[{index}] must be an object")
    unknown = sorted(set(raw) - _ENTRY_KEYS)
    if unknown:
        raise _fail(f"contracts[{index}] has unknown keys {unknown}; allowed {sorted(_ENTRY_KEYS)}")
    contract_id = _text(raw.get("contract_id"), f"contracts[{index}].contract_id", required=True)
    if not _ID.fullmatch(contract_id):
        raise _fail(f"contracts[{index}].contract_id {contract_id!r} is not an upper-case contract id")
    status = _text(raw.get("status"), f"{contract_id}.status", required=True)
    if status not in KNOWN_STATUSES:
        raise _fail(
            f"{contract_id}.status {status!r} is unknown; known statuses are {sorted(KNOWN_STATUSES)}"
        )
    reason = _text(raw.get("reason"), f"{contract_id}.reason", required=True)
    replacement = _text(raw.get("replacement_contract_id"), f"{contract_id}.replacement_contract_id", required=False)
    if replacement:
        if not _ID.fullmatch(replacement):
            raise _fail(f"{contract_id}.replacement_contract_id {replacement!r} is not a contract id")
        if replacement == contract_id:
            raise _fail(f"{contract_id} declares itself as its own replacement")
    group = _text(raw.get("selection_group_id"), f"{contract_id}.selection_group_id", required=False)
    if group and not _GROUP.fullmatch(group):
        raise _fail(
            f"{contract_id}.selection_group_id {group!r} must contain only letters, digits, '_' or '-'"
        )
    if status == ACTIVE_V2 and not group:
        raise _fail(f"{contract_id} is {ACTIVE_V2} but declares no selection_group_id to inherit")
    evidence = _text(raw.get("evidence"), f"{contract_id}.evidence", required=False)
    return LifecycleEntry(contract_id, status, reason, replacement, group, evidence)


def _parse_scripts(raw: Any) -> tuple[dict[str, Any], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _fail("legacy_scripts must be a list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise _fail(f"legacy_scripts[{index}] must be an object")
        unknown = sorted(set(item) - _SCRIPT_KEYS)
        if unknown:
            raise _fail(f"legacy_scripts[{index}] has unknown keys {unknown}")
        path = _text(item.get("path"), f"legacy_scripts[{index}].path", required=True)
        if path in seen:
            raise _fail(f"legacy_scripts declares {path!r} twice")
        seen.add(path)
        status = _text(item.get("status"), f"{path}.status", required=True)
        if status not in _SCRIPT_STATUSES:
            raise _fail(f"{path}.status {status!r} is unknown; known {sorted(_SCRIPT_STATUSES)}")
        out.append(
            {
                "path": path,
                "replaces": _text(item.get("replaces"), f"{path}.replaces", required=False),
                "archive_after": _text(item.get("archive_after"), f"{path}.archive_after", required=False),
                "status": status,
            }
        )
    return tuple(out)


def resolve_registry_path(repo_root: Path | str = ".", path: Path | str | None = None) -> Path:
    """``explicit`` > ``$WARDEN_LIFECYCLE_REGISTRY`` > ``<repo_root>/contracts/lifecycle/...``."""
    if path is not None:
        return Path(path)
    env = os.environ.get(ENV_REGISTRY_PATH, "").strip()
    if env:
        return Path(env)
    return Path(repo_root) / DEFAULT_REGISTRY_PATH


def load_lifecycle_registry(
    repo_root: Path | str = ".",
    path: Path | str | None = None,
    *,
    strict: bool = False,
) -> LifecycleRegistry:
    """Parse the lifecycle document, fail closed on anything unusable.

    ``strict=True`` refuses an absent document.  The default (``strict=False``) reports an
    absent document as an empty registry: every repository that predates #106 — including
    each test's isolated ``tmp_path`` repo — keeps working, and deleting the document on a
    signed worktree is still caught by ``warden check-worktree`` because it is part of the
    protected inventory.  A document that *exists* is always validated, so corruption can
    never be mistaken for absence.
    """
    target = resolve_registry_path(repo_root, path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        if strict:
            raise _fail(f"lifecycle registry {target.as_posix()!r} is missing") from None
        return LifecycleRegistry(path=target.as_posix(), entries={}, source="absent")
    except (OSError, UnicodeDecodeError) as exc:
        raise _fail(f"lifecycle registry {target.as_posix()!r} is unreadable: {exc}") from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise _fail(f"lifecycle registry {target.as_posix()!r} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise _fail(f"lifecycle registry {target.as_posix()!r} must be a JSON object")
    unknown = sorted(set(document) - _TOP_LEVEL_KEYS)
    if unknown:
        raise _fail(f"lifecycle registry has unknown keys {unknown}; allowed {sorted(_TOP_LEVEL_KEYS)}")
    version = document.get("schema_version")
    if version != SCHEMA_VERSION or type(version) is not int:
        raise _fail(f"lifecycle registry schema_version must be {SCHEMA_VERSION}, got {version!r}")
    raw_entries = document.get("contracts")
    if not isinstance(raw_entries, list):
        raise _fail("lifecycle registry must declare a 'contracts' list")
    entries: dict[str, LifecycleEntry] = {}
    for index, raw in enumerate(raw_entries):
        entry = _parse_entry(raw, index)
        if entry.contract_id in entries:
            raise _fail(f"lifecycle registry declares {entry.contract_id} twice")
        entries[entry.contract_id] = entry
    for entry in entries.values():
        if entry.replacement_contract_id and entry.replacement_contract_id not in entries:
            raise _fail(
                f"{entry.contract_id} names replacement {entry.replacement_contract_id}, "
                "which the lifecycle registry does not declare"
            )
        if entry.blocking and entry.replacement_contract_id:
            successor = entries[entry.replacement_contract_id]
            if successor.status != ACTIVE_V2:
                raise _fail(
                    f"{entry.contract_id} names replacement {successor.contract_id} "
                    f"which is {successor.status}, not {ACTIVE_V2}"
                )
    return LifecycleRegistry(
        path=target.as_posix(),
        entries=entries,
        legacy_scripts=_parse_scripts(document.get("legacy_scripts")),
        source="file",
    )
