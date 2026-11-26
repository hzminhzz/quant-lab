"""One authority for *where* a contract document lives: active tree or read-only archive.

Issue #114 archived the contracts the reviewed lifecycle document closes.  Two lookups
therefore have to stay distinguishable and must never blur into each other:

``active``
    New work — registering a hypothesis, resolving a dataset, screening, backtesting,
    qualifying.  The live ``contracts/`` directory is the only place a document may come
    from.  An archived id is **refused** with an error naming its archive location; the
    lookup never falls back to the live directory, even if a stale copy is sitting there.

``recorded``
    Historical replay — a trial that was recorded against a contract which has since been
    archived.  The same bytes still resolve: live first, then the archive, with the
    mapping's recorded digest checked before the document is handed out.

The archive is a namespace plus one reviewed mapping document
(``contracts/archive/archive_map.json``) recording original path -> archive path for the
contract YAML *and* its ``contracts/datasets/*.json`` counterpart.  The move is
byte-preserving (``git`` records it as a rename) and mechanically reversible through
:meth:`ContractArchive.reverse_plan`.

Fail-closed rules, all of them deliberate:

* an unparseable, duplicated or wrongly-keyed mapping is refused -- corruption is never
  read as "no archived contracts";
* an entry whose archive path escapes ``contracts/archive/`` is refused, so the mapping
  cannot point a resolver at an arbitrary file;
* a mapping entry for a contract the lifecycle document keeps ``ACTIVE_V2`` is refused:
  the archive holds closed contracts only;
* a resolved archived document whose bytes do not match the recorded ``sha256`` is
  refused (this is what makes "same digest as before the move" a checked property rather
  than a claim).

This module has no writer for the mapping: regenerating it is a reviewed, human-signed
edit like the lifecycle document.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from warden.contracts import EvaluationContract, load_contract

#: Read-only archive namespace.  A subdirectory of ``contracts/`` on purpose: the
#: ``contracts/*.yaml`` glob used for active enumeration never reaches it.
ARCHIVE_DIR = Path("contracts") / "archive"
#: Reviewed mapping document (original path -> archive path, digests, provenance commit).
ARCHIVE_MAP_NAME = "archive_map.json"
DATASET_DIR = Path("contracts") / "datasets"
#: Agent-writable exploratory namespace (#117).  A compiled *sandbox* contract is runnable
#: and never reusable: it is outside the active lookup's active set, outside the archive,
#: and outside the protected inventory, so compiling one neither re-signs the human
#: manifest nor lets a later requirement resolve to it as signed evidence.
SANDBOX_DIR = Path("contracts") / "sandbox"
SANDBOX_MANIFEST_DIR = SANDBOX_DIR / "datasets"
SANDBOX_README_NAME = "README.md"
ACTIVE = "active"
ARCHIVE = "archive"
SANDBOX = "sandbox"

_SCHEMA_VERSION = 1
_TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "generated_at", "note", "moved_from_commit", "contracts"}
)
_ENTRY_KEYS = frozenset(
    {
        "contract_id",
        "status",
        "original_path",
        "archive_path",
        "dataset_original_path",
        "dataset_archive_path",
        "spec_hash",
        "sha256",
        "dataset_sha256",
    }
)
_CONTRACT_ID = re.compile(r"[A-Z][A-Z0-9_]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ContractSourceError(ValueError):
    """A contract lookup could not be answered under the requested authority."""


class ContractArchiveError(ContractSourceError):
    """The archive namespace or its mapping is unusable. Fails closed."""


class ContractNotFound(ContractSourceError, FileNotFoundError):
    """No contract document for this id in the namespaces the caller allowed."""

    def __init__(self, contract_id: str, *, searched: str = "the active contracts directory") -> None:
        self.contract_id = str(contract_id)
        super().__init__(f"Contract not found: '{contract_id}' (searched {searched})")


class ArchivedContractRefused(ContractSourceError, FileNotFoundError):
    """An *active* lookup hit an archived contract. Never substituted, never fallen back."""

    def __init__(self, entry: ArchivedContract) -> None:
        self.entry = entry
        status = f", {entry.status}" if entry.status else ""
        super().__init__(
            f"contract {entry.contract_id} is archived read-only at {entry.archive_path} "
            f"(original {entry.original_path}{status}); an active lookup never resolves an "
            "archived contract, and no live copy is substituted for it"
        )


@dataclass(frozen=True)
class ArchivedContract:
    """One mapping entry: where the bytes were and where they are now."""

    contract_id: str
    original_path: str
    archive_path: str
    status: str = ""
    dataset_original_path: str = ""
    dataset_archive_path: str = ""
    spec_hash: str = ""
    sha256: str = ""
    dataset_sha256: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "contract_id": self.contract_id,
            "status": self.status,
            "original_path": self.original_path,
            "archive_path": self.archive_path,
            "dataset_original_path": self.dataset_original_path,
            "dataset_archive_path": self.dataset_archive_path,
            "spec_hash": self.spec_hash,
            "sha256": self.sha256,
            "dataset_sha256": self.dataset_sha256,
        }


@dataclass(frozen=True)
class ContractSource:
    """Where a resolved contract document actually came from."""

    contract_id: str
    path: Path
    location: str

    @property
    def archived(self) -> bool:
        return self.location == ARCHIVE


@dataclass(frozen=True)
class ContractArchive:
    """A parsed archive mapping. Immutable; consult-only."""

    path: str
    entries: Mapping[str, ArchivedContract]
    moved_from_commit: str = ""
    #: ``"file"`` when a mapping was read, ``"absent"`` when no archive exists at ``path``.
    source: str = "file"

    def entry(self, contract_id: str) -> ArchivedContract | None:
        return self.entries.get(str(contract_id))

    def is_archived(self, contract_id: str) -> bool:
        return str(contract_id) in self.entries

    def dataset_manifest(self, contract_id: str) -> str:
        item = self.entry(contract_id)
        return item.dataset_archive_path if item is not None else ""

    def reverse_plan(self) -> list[tuple[str, str]]:
        """``(current path, original path)`` pairs for a mechanical, reverse-order move."""
        plan: list[tuple[str, str]] = []
        for entry in sorted(self.entries.values(), key=lambda e: e.contract_id):
            plan.append((entry.archive_path, entry.original_path))
            if entry.dataset_archive_path and entry.dataset_original_path:
                plan.append((entry.dataset_archive_path, entry.dataset_original_path))
        return plan

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "source": self.source,
            "moved_from_commit": self.moved_from_commit,
            "entries": {cid: e.to_dict() for cid, e in sorted(self.entries.items())},
        }


def _fail(message: str) -> ContractArchiveError:
    return ContractArchiveError(message)


def _text(raw: dict, key: str, *, required: bool, where: str) -> str:
    value = raw.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise _fail(f"{where}.{key} must be a non-empty string")
        return ""
    if not isinstance(value, str):
        raise _fail(f"{where}.{key} must be a string")
    return value.strip()


def _under(path_text: str, directory: Path, where: str) -> str:
    """A mapping path must be relative, normalised and inside ``directory``."""
    path = Path(path_text)
    if path.is_absolute() or ".." in path.parts:
        raise _fail(f"{where} {path_text!r} must be a normalised path relative to the repository")
    try:
        path.relative_to(directory)
    except ValueError:
        raise _fail(f"{where} {path_text!r} is outside {directory.as_posix()}/") from None
    return path.as_posix()


def _parse_entry(raw: object, index: int) -> ArchivedContract:
    if not isinstance(raw, dict):
        raise _fail(f"contracts[{index}] must be an object")
    where = f"contracts[{index}]"
    unknown = sorted(set(raw) - _ENTRY_KEYS)
    if unknown:
        raise _fail(f"{where} has unknown keys {unknown}; allowed {sorted(_ENTRY_KEYS)}")
    contract_id = _text(raw, "contract_id", required=True, where=where)
    if not _CONTRACT_ID.fullmatch(contract_id):
        raise _fail(f"{where}.contract_id {contract_id!r} is not an upper-case contract id")
    where = contract_id
    status = _text(raw, "status", required=False, where=where)
    if status:
        from warden.lifecycle import ACTIVE_V2, KNOWN_STATUSES  # local: no import cycle

        if status not in KNOWN_STATUSES:
            raise _fail(f"{where}.status {status!r} is unknown; known statuses are {sorted(KNOWN_STATUSES)}")
        if status == ACTIVE_V2:
            raise _fail(
                f"{where} is {ACTIVE_V2}: the archive holds closed contracts only, "
                "so an active contract can never be archived"
            )
    original = _under(_text(raw, "original_path", required=True, where=where), Path("contracts"), f"{where}.original_path")
    if original == ARCHIVE_DIR.as_posix() or original.startswith(f"{ARCHIVE_DIR.as_posix()}/"):
        raise _fail(f"{where}.original_path {original!r} is already inside the archive namespace")
    archive_path = _under(_text(raw, "archive_path", required=True, where=where), ARCHIVE_DIR, f"{where}.archive_path")
    expected_stem = contract_id.lower()
    if Path(archive_path).stem != expected_stem:
        raise _fail(
            f"{where}.archive_path {archive_path!r} does not name the contract "
            f"({expected_stem}.yaml|.yml expected)"
        )
    dataset_original = _text(raw, "dataset_original_path", required=False, where=where)
    dataset_archive = _text(raw, "dataset_archive_path", required=False, where=where)
    if dataset_original:
        dataset_original = _under(dataset_original, DATASET_DIR, f"{where}.dataset_original_path")
    if dataset_archive:
        if not dataset_original:
            raise _fail(f"{where}.dataset_archive_path is declared without dataset_original_path")
        dataset_archive = _under(
            dataset_archive, ARCHIVE_DIR / DATASET_DIR.name, f"{where}.dataset_archive_path"
        )
        if Path(dataset_archive).name != f"{contract_id}.json":
            raise _fail(f"{where}.dataset_archive_path {dataset_archive!r} must be named {contract_id}.json")
    spec_hash = _text(raw, "spec_hash", required=False, where=where)
    sha256 = _text(raw, "sha256", required=False, where=where)
    dataset_sha256 = _text(raw, "dataset_sha256", required=False, where=where)
    if spec_hash and not re.fullmatch(r"[0-9a-f]{16}", spec_hash):
        raise _fail(f"{where}.spec_hash must be the 16-character contract digest")
    for key, value in (("sha256", sha256), ("dataset_sha256", dataset_sha256)):
        if value and not _SHA256.fullmatch(value):
            raise _fail(f"{where}.{key} must be a 64-character lowercase hex digest")
    return ArchivedContract(
        contract_id=contract_id,
        original_path=original,
        archive_path=archive_path,
        status=status,
        dataset_original_path=dataset_original,
        dataset_archive_path=dataset_archive,
        spec_hash=spec_hash,
        sha256=sha256,
        dataset_sha256=dataset_sha256,
    )


def load_contract_archive(repo_root: Path | str = ".") -> ContractArchive:
    """Parse the archive mapping, fail closed on anything unusable.

    An absent mapping is an empty namespace: every repository (and every test's isolated
    ``tmp_path`` repo) that predates #114 keeps working, and deleting the mapping on a
    signed worktree is still caught by ``warden check-worktree`` because the archive is
    part of the protected inventory.
    """
    root = Path(repo_root)
    target = root / ARCHIVE_DIR / ARCHIVE_MAP_NAME
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ContractArchive(path=target.as_posix(), entries={}, source="absent")
    except (OSError, UnicodeDecodeError) as exc:
        raise _fail(f"archive mapping {target.as_posix()!r} is unreadable: {exc}") from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise _fail(f"archive mapping {target.as_posix()!r} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise _fail(f"archive mapping {target.as_posix()!r} must be a JSON object")
    unknown = sorted(set(document) - _TOP_LEVEL_KEYS)
    if unknown:
        raise _fail(f"archive mapping has unknown keys {unknown}; allowed {sorted(_TOP_LEVEL_KEYS)}")
    version = document.get("schema_version")
    if version != _SCHEMA_VERSION or type(version) is not int:
        raise _fail(f"archive mapping schema_version must be {_SCHEMA_VERSION}, got {version!r}")
    raw_entries = document.get("contracts")
    if not isinstance(raw_entries, list):
        raise _fail("archive mapping must declare a 'contracts' list")
    entries: dict[str, ArchivedContract] = {}
    for index, raw in enumerate(raw_entries):
        entry = _parse_entry(raw, index)
        if entry.contract_id in entries:
            raise _fail(f"archive mapping declares {entry.contract_id} twice")
        entries[entry.contract_id] = entry
    moved_from = _text(document, "moved_from_commit", required=False, where="archive mapping")
    if moved_from and not re.fullmatch(r"[0-9a-f]{7,40}", moved_from):
        raise _fail("archive mapping moved_from_commit must be a git object name")
    return ContractArchive(
        path=target.as_posix(),
        entries=entries,
        moved_from_commit=moved_from,
        source="file",
    )


def archived_contract(repo_root: Path | str, contract_id: str) -> ArchivedContract | None:
    """The mapping entry for ``contract_id``, or ``None`` when it is not archived."""
    return load_contract_archive(repo_root).entry(str(contract_id))


def _verify_archived_bytes(root: Path, entry: ArchivedContract) -> None:
    """The recorded digest is checked before the document is handed out."""
    if entry.sha256:
        path = root / entry.archive_path
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise _fail(f"archived contract {entry.contract_id} at {entry.archive_path} is unreadable: {exc}") from exc
        if digest != entry.sha256:
            raise _fail(
                f"archived contract {entry.contract_id} at {entry.archive_path} does not match its "
                f"recorded digest (recorded {entry.sha256[:12]}..., found {digest[:12]}...); "
                "archived bytes are immutable"
            )


def _active_path(root: Path, contract_id: str) -> Path | None:
    stem = str(contract_id).lower()
    for suffix in (".yaml", ".yml"):
        candidate = root / "contracts" / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def sandbox_contract_path(repo_root: Path | str, contract_id: str) -> Path | None:
    """The compiled sandbox contract document for ``contract_id``, or ``None``."""
    stem = str(contract_id).lower()
    for suffix in (".yaml", ".yml"):
        candidate = Path(repo_root) / SANDBOX_DIR / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def sandbox_contract_paths(repo_root: Path | str = ".") -> list[Path]:
    """Every compiled sandbox contract, in stable order.  Never the live or archived set."""
    root = Path(repo_root)
    out: list[Path] = []
    for pattern in (f"{SANDBOX_DIR.as_posix()}/*.yaml", f"{SANDBOX_DIR.as_posix()}/*.yml"):
        out.extend(sorted(root.glob(pattern)))
    return out


def is_sandbox_contract(repo_root: Path | str, contract_id: str) -> bool:
    """True when ``contract_id`` resolves to the sandbox namespace.

    Production always wins a name clash: a live ``contracts/<id>.yaml`` makes the id a
    production contract even if a sandbox copy also exists.
    """
    if _active_path(Path(repo_root), str(contract_id)) is not None:
        return False
    return sandbox_contract_path(repo_root, contract_id) is not None


def resolve_contract_source(
    repo_root: Path | str, contract_id: str, *, allow_archive: bool, allow_sandbox: bool = False
) -> ContractSource:
    """Resolve one contract id under an explicit authority.

    ``allow_archive=False`` is the *active* lookup: the live directory only, and an
    archived id raises :class:`ArchivedContractRefused` naming the archive location
    (never a fallback to a live copy).  ``allow_archive=True`` is the *recorded* lookup
    used by historical replay: live first, then the archive, digest-checked.

    ``allow_sandbox=True`` additionally resolves a compiled sandbox contract (#117) — the
    authority an *execution* path needs, because a sandbox artifact may run the ordinary
    pipeline.  It is never combined with reuse: a caller that must not run un-promoted
    evidence (qualification) checks the returned ``location`` and refuses.
    """
    root = Path(repo_root)
    cid = str(contract_id)
    if not cid or cid != cid.strip():
        raise ContractNotFound(contract_id)
    archive = load_contract_archive(root)
    entry = archive.entry(cid)
    if not allow_archive:
        if entry is not None:
            raise ArchivedContractRefused(entry)
        active = _active_path(root, cid)
        if active is not None:
            return ContractSource(cid, active, ACTIVE)
        if allow_sandbox:
            sandbox = sandbox_contract_path(root, cid)
            if sandbox is not None:
                return ContractSource(cid, sandbox, SANDBOX)
        raise ContractNotFound(cid)
    active = _active_path(root, cid)
    if active is not None:
        return ContractSource(cid, active, ACTIVE)
    if entry is not None:
        _verify_archived_bytes(root, entry)
        return ContractSource(cid, root / entry.archive_path, ARCHIVE)
    if allow_sandbox:
        sandbox = sandbox_contract_path(root, cid)
        if sandbox is not None:
            return ContractSource(cid, sandbox, SANDBOX)
    raise ContractNotFound(
        cid,
        searched="the active contracts directory or the contract archive",
    )


def load_active_contract(
    repo_root: Path | str, contract_id: str, *, allow_sandbox: bool = False
) -> EvaluationContract:
    """Load a contract for *new work*; refuses an archived id with its archive location."""
    return load_contract(
        resolve_contract_source(
            repo_root, contract_id, allow_archive=False, allow_sandbox=allow_sandbox
        ).path
    )


def load_recorded_contract(
    repo_root: Path | str, contract_id: str, *, allow_sandbox: bool = False
) -> EvaluationContract:
    """Load a contract for *historical replay*; archived bytes (and optionally sandbox) resolve."""
    return load_contract(
        resolve_contract_source(
            repo_root, contract_id, allow_archive=True, allow_sandbox=allow_sandbox
        ).path
    )


def recorded_contract_path(
    repo_root: Path | str, contract_id: str, *, allow_sandbox: bool = False
) -> Path:
    """The path a *recorded* lookup resolves: live, then archived, then sandbox."""
    return resolve_contract_source(
        repo_root, contract_id, allow_archive=True, allow_sandbox=allow_sandbox
    ).path


def active_contract_paths(repo_root: Path | str = ".") -> list[Path]:
    """Live contract documents, in stable order. Never the archive."""
    root = Path(repo_root)
    out: list[Path] = []
    for pattern in ("contracts/*.yaml", "contracts/*.yml"):
        out.extend(sorted(root.glob(pattern)))
    return out


def archived_contract_paths(repo_root: Path | str = ".") -> list[Path]:
    root = Path(repo_root)
    out: list[Path] = []
    for pattern in (f"{ARCHIVE_DIR.as_posix()}/*.yaml", f"{ARCHIVE_DIR.as_posix()}/*.yml"):
        out.extend(sorted(root.glob(pattern)))
    return out


def all_contract_paths(repo_root: Path | str = ".") -> list[Path]:
    """Every contract document in the tree: active first, then archived."""
    return active_contract_paths(repo_root) + archived_contract_paths(repo_root)


def dataset_manifest_path(
    repo_root: Path | str, contract_id: str, *, allow_archive: bool = True
) -> Path | None:
    """The ``contracts/datasets`` manifest for a contract, live or archived.

    The archive fallback exists so a recorded contract's evidence stays addressable; an
    *active* data read is refused earlier, in :func:`warden.provenance.resolve_dataset`.
    """
    root = Path(repo_root)
    live = root / DATASET_DIR / f"{contract_id}.json"
    if live.is_file():
        return live
    if not allow_archive:
        return None
    entry = archived_contract(root, contract_id)
    if entry is None or not entry.dataset_archive_path:
        return None
    archived_manifest = root / entry.dataset_archive_path
    return archived_manifest if archived_manifest.is_file() else None
