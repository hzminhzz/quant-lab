"""Exploratory-storage fence (issue #106).

The exploratory store is the *unsealed* side of the fence: every byte there is readable by
an agent, so it must never contain a bar from the holdout window.  A digest pin alone does
not prove that: a file can be pinned and still end after ``holdout_start``, and a file can
sit in ``data/exploratory/`` with no manifest at all, which means no contract owns it and
no gate has ever looked at its calendar.

This module answers exactly one question, per file, and refuses everything it cannot
answer:

* does a dataset manifest own this exact path (exactly one, digest equal)? and
* is the file's last bar strictly before its owner's ``holdout_start``?

No Parquet reader is imported here.  ``pl.read_parquet`` is confined to
``warden.market_data`` by ``warden_ops.integrity.check_ast_boundaries``; the caller injects
the reader, exactly as ``warden data build`` does.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from warden.contracts import ContractValidationError, load_contract

DEFAULT_DATASET_DIR = "data/exploratory"
DEFAULT_MANIFEST_DIR = "contracts/datasets"
DEFAULT_CONTRACTS_DIR = "contracts"
#: Read-only archive namespace (#114): archived contracts and their dataset manifests live
#: here and must still be visible to the fence, or every archived file becomes UNOWNED.
DEFAULT_ARCHIVE_DIR = "contracts/archive"
#: Agent-writable sandbox namespace (#117): compiled artifacts are exploratory bytes like
#: any other, so their contract and manifest must be visible here or a lawful compile would
#: be reported UNOWNED/NO_CONTRACT the moment it landed.
DEFAULT_SANDBOX_DIR = "contracts/sandbox"
HOLDOUT_ROOT = "data/holdout"

#: ``state`` values. Only ``fenced`` passes; everything else is a refusal with a reason.
FENCED = "fenced"
UNOWNED = "unowned"
AMBIGUOUS = "ambiguous_owner"
DIGEST_MISMATCH = "digest_mismatch"
NO_CONTRACT = "owner_contract_missing"
BEYOND_HOLDOUT = "bar_at_or_after_holdout"
UNREADABLE = "unreadable"


class ExploratoryFenceError(ValueError):
    """The fence could not be evaluated (bad directory, non-pinned manifest, ...)."""


@dataclass(frozen=True)
class FenceFile:
    path: str
    state: str
    reason: str
    contract_id: str = ""
    manifest: str = ""
    sha256: str = ""
    rows: int = 0
    max_timestamp: str = ""
    holdout_start: str = ""

    @property
    def ok(self) -> bool:
        return self.state == FENCED

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "state": self.state,
            "reason": self.reason,
            "contract_id": self.contract_id,
            "manifest": self.manifest,
            "sha256": self.sha256,
            "rows": self.rows,
            "max_timestamp": self.max_timestamp,
            "holdout_start": self.holdout_start,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class FenceReport:
    dataset_dir: str
    files: tuple[FenceFile, ...] = ()
    #: Manifests whose declared exploratory path has no file. Reported, never fatal:
    #: the criterion is "every file is owned", not "every manifest has bytes".
    orphan_manifests: tuple[str, ...] = ()
    reasons: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return bool(self.files) and all(item.ok for item in self.files) and not self.reasons

    @property
    def failures(self) -> tuple[FenceFile, ...]:
        return tuple(item for item in self.files if not item.ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_dir": self.dataset_dir,
            "ok": self.ok,
            "files_checked": len(self.files),
            "files": [item.to_dict() for item in self.files],
            "failures": [item.to_dict() for item in self.failures],
            "orphan_manifests": list(self.orphan_manifests),
            "reasons": list(self.reasons),
        }


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _load_manifests(
    manifest_dir: Path,
    archive_dir: Path | None = None,
    sandbox_dir: Path | None = None,
) -> list[tuple[str, dict[str, Any], str]]:
    """``[(file name, record, sha256 of the manifest bytes)]`` across every namespace.

    The bytes digest is what lets the owner lookup tell a *promoted copy* (#118) from a
    genuine conflict: a promotion copies the dataset manifest with identical bytes, so one
    exploratory file can legitimately be declared twice by one artifact.
    """
    out: list[tuple[str, dict[str, Any], str]] = []
    # An archived contract's dataset manifest is archived with it (#114).  Reading it here
    # is what keeps its exploratory file *owned* instead of silently becoming UNOWNED.
    dirs = [manifest_dir]
    if archive_dir is not None:
        dirs.append(archive_dir / manifest_dir.name)
    if sandbox_dir is not None:
        dirs.append(sandbox_dir / manifest_dir.name)
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if not path.is_file():
                continue
            try:
                raw = path.read_bytes()
                record = json.loads(raw.decode("utf-8"))
            except (OSError, ValueError) as exc:
                raise ExploratoryFenceError(f"{path.name}: manifest is not readable JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise ExploratoryFenceError(f"{path.name}: manifest must be a JSON object")
            out.append((path.name, record, hashlib.sha256(raw).hexdigest()))
    return out


def _collapsed_owner(
    candidates: list[tuple[str, dict[str, Any], str]],
) -> tuple[str, dict[str, Any], str] | None:
    """One owner for several byte-identical declarations of the same artifact.

    A promotion (#118) copies a compiled sandbox dataset manifest into ``contracts/datasets``
    with identical bytes, so the same exploratory file is declared by the sandbox record and
    its production copy.  That is one owner, not an ambiguity: the records agree on the bytes,
    the pin and the contract.  Anything else -- a different pin, a different contract id or a
    different manifest document -- stays ``AMBIGUOUS``, which is the refusal that matters.
    """
    first = candidates[0]
    for name, record, digest in candidates[1:]:
        same_document = digest == first[2]
        same_contract = str(record.get("contract_id") or "") == str(first[1].get("contract_id") or "")
        same_pin = str(record.get("exploratory_sha256") or record.get("sha256") or "") == str(
            first[1].get("exploratory_sha256") or first[1].get("sha256") or ""
        )
        if not (same_document and same_contract and same_pin):
            return None
    return first


def _load_contracts(
    contracts_dir: Path,
    archive_dir: Path | None = None,
    sandbox_dir: Path | None = None,
) -> dict[str, Any]:
    contracts: dict[str, Any] = {}
    # Archived contracts are loaded too (#114): a manifest may still name an archived
    # contract, and "no contract document" is a fence failure, not a silent unownership.
    dirs = [archive_dir, contracts_dir] if archive_dir is not None else [contracts_dir]
    if sandbox_dir is not None:
        dirs.append(sandbox_dir)
    for directory in dirs:
        if not directory.is_dir():
            continue
        for pattern in ("*.yaml", "*.yml"):
            for path in sorted(directory.glob(pattern)):
                if not path.is_file():
                    continue
                try:
                    contract = load_contract(path)
                except (ContractValidationError, OSError, ValueError):
                    # A contract document that does not parse is not silently skipped when it
                    # owns a file: the owning lookup reports ``owner_contract_missing``.
                    continue
                contracts[str(contract.contract_id)] = contract
    return contracts


def _as_utc(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise ExploratoryFenceError(f"max timestamp {value!r} is not a datetime")
    if value.tzinfo is None:
        raise ExploratoryFenceError(
            "max timestamp is timezone-naive; a fence cannot compare a naive clock to holdout_start"
        )
    return value


def audit_exploratory_storage(
    repo_root: Path | str = ".",
    *,
    dataset_dir: str = DEFAULT_DATASET_DIR,
    manifest_dir: str = DEFAULT_MANIFEST_DIR,
    contracts_dir: str = DEFAULT_CONTRACTS_DIR,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
    sandbox_dir: str = DEFAULT_SANDBOX_DIR,
    parquet_reader: Callable[[bytes], Any],
) -> FenceReport:
    """Audit every Parquet file under ``dataset_dir`` against its manifest and the fence.

    The dataset directory is refused unless it lives under ``data/`` and outside
    ``data/holdout``: pointing the fence at the protected root would turn a read-only check
    into a holdout read.
    """
    root = Path(repo_root).resolve()
    dataset_rel = Path(dataset_dir).as_posix().rstrip("/")
    if dataset_rel != "data" and not dataset_rel.startswith("data/"):
        raise ExploratoryFenceError(f"dataset dir {dataset_dir!r} must live under 'data/'")
    if dataset_rel == HOLDOUT_ROOT or dataset_rel.startswith(f"{HOLDOUT_ROOT}/"):
        raise ExploratoryFenceError(
            f"dataset dir {dataset_dir!r} is inside the sealed holdout root; the fence never reads it"
        )
    base = (root / dataset_rel).resolve()
    if not base.is_relative_to(root):
        raise ExploratoryFenceError(f"dataset dir {dataset_dir!r} escapes the repository")
    if not base.is_dir():
        raise ExploratoryFenceError(f"dataset dir {dataset_rel!r} does not exist")

    manifests = _load_manifests(root / manifest_dir, root / archive_dir, root / sandbox_dir)
    contracts = _load_contracts(root / contracts_dir, root / archive_dir, root / sandbox_dir)
    owners: dict[str, list[tuple[str, dict[str, Any], str]]] = {}
    for name, record, digest in manifests:
        declared = record.get("exploratory_path") or record.get("path")
        if isinstance(declared, str) and declared.strip():
            owners.setdefault(declared.strip(), []).append((name, record, digest))

    files: list[FenceFile] = []
    seen: set[str] = set()
    for path in sorted(base.rglob("*.parquet")):
        if not path.is_file():
            continue
        relative = _relative(path, root)
        seen.add(relative)
        candidates = owners.get(relative, [])
        if not candidates:
            files.append(FenceFile(relative, UNOWNED, "no dataset manifest declares this exploratory file"))
            continue
        mirrors = 0
        if len(candidates) > 1:
            collapsed = _collapsed_owner(candidates)
            if collapsed is None:
                files.append(
                    FenceFile(
                        relative,
                        AMBIGUOUS,
                        f"{len(candidates)} manifests declare this path: "
                        f"{sorted(name for name, _, _ in candidates)}",
                    )
                )
                continue
            # One artifact, several namespaces: the sandbox record and its promoted copy.
            mirrors = len(candidates) - 1
            candidates = [collapsed]
        name, record, _digest = candidates[0]
        contract_id = str(record.get("contract_id") or "")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            files.append(FenceFile(relative, UNREADABLE, f"cannot read file: {exc}", contract_id, name))
            continue
        digest = hashlib.sha256(payload).hexdigest()
        pinned = record.get("exploratory_sha256") or record.get("sha256")
        if not isinstance(pinned, str) or pinned != digest:
            files.append(
                FenceFile(
                    relative,
                    DIGEST_MISMATCH,
                    f"file digest {digest[:12]}... != manifest pin {str(pinned)[:12]}...",
                    contract_id,
                    name,
                    digest,
                )
            )
            continue
        contract = contracts.get(contract_id)
        if contract is None:
            files.append(
                FenceFile(
                    relative,
                    NO_CONTRACT,
                    f"manifest owner {contract_id!r} has no contract document under {contracts_dir}/ "
                    f"or {archive_dir}/",
                    contract_id,
                    name,
                    digest,
                )
            )
            continue
        try:
            frame = parquet_reader(payload)
        except Exception as exc:  # noqa: BLE001 - an injected reader may raise anything; a
            # refusal is the only safe outcome, and the reason is reported verbatim.
            files.append(
                FenceFile(relative, UNREADABLE, f"parquet reader refused the file: {exc}", contract_id, name, digest)
            )
            continue
        if "timestamp" not in getattr(frame, "columns", []):
            files.append(
                FenceFile(relative, UNREADABLE, "file declares no 'timestamp' column", contract_id, name, digest)
            )
            continue
        rows = int(frame.height)
        stamps = frame["timestamp"].drop_nulls().max()
        if rows == 0 or stamps is None:
            files.append(
                FenceFile(relative, UNREADABLE, "file has no bars to fence", contract_id, name, digest, rows)
            )
            continue
        try:
            last = _as_utc(stamps)
        except ExploratoryFenceError as exc:
            files.append(FenceFile(relative, UNREADABLE, str(exc), contract_id, name, digest, rows))
            continue
        holdout_start = contract.holdout_start
        state, reason = FENCED, "last bar precedes holdout_start and the owner manifest pins the bytes"
        if mirrors:
            reason += (
                f"; declared by {mirrors + 1} byte-identical manifests for {contract_id} "
                "(a promoted copy owns the same bytes as its sandbox original)"
            )
        if last >= holdout_start:
            state = BEYOND_HOLDOUT
            reason = (
                f"last bar {last.isoformat()} is at or after {contract.contract_id} "
                f"holdout_start {holdout_start.isoformat()}"
            )
        files.append(
            FenceFile(
                relative,
                state,
                reason,
                contract_id,
                name,
                digest,
                rows,
                last.isoformat(),
                holdout_start.isoformat(),
            )
        )

    orphans = tuple(sorted(set(owners) - seen))
    reasons: list[str] = []
    if not files:
        # An empty store proves nothing about ownership: 100% of zero files is not evidence.
        reasons.append(f"no Parquet files under {dataset_rel!r}; an empty store cannot be fenced")
    return FenceReport(dataset_dir=dataset_rel, files=tuple(files), orphan_manifests=orphans, reasons=tuple(reasons))
