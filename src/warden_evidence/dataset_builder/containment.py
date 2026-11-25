"""Containment before I/O: a blocked path is refused before any filesystem call.

The builder reads raw vendor files, so the dangerous operation is *touching* a
path, not interpreting a frame.  ``canonical_source_path`` is therefore a pure
string check performed first; only a path that passes it is ever handed to a
:class:`RawFileSystem`, which re-checks containment after resolution as defence
in depth.  Tests assert the ordering with a recording filesystem: a refused
input produces zero read calls, so the program cannot have opened, statted or
hashed it.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, runtime_checkable

#: A component or basename containing this token is never a source of exploratory data.
FORBIDDEN_TOKENS: tuple[str, ...] = ("holdout",)


class SourceContainmentError(ValueError):
    """A declared or target path may reach protected data; nothing was opened."""


class AdapterError(ValueError):
    """A source adapter could not produce a raw bundle."""


def canonical_source_path(raw: Any, *, role: str = "source path") -> PurePosixPath:
    """Validate a repository-relative path as a POSIX path, with no filesystem access."""
    if not isinstance(raw, str):
        raise SourceContainmentError(f"{role} must be a string, got {type(raw).__name__}")
    text = raw.strip()
    if not text:
        raise SourceContainmentError(f"{role} must not be empty")
    if "\x00" in text:
        raise SourceContainmentError(f"{role} contains a NUL byte")
    if "\\" in text:
        raise SourceContainmentError(f"{role} {text!r} uses a backslash; declare a POSIX path")
    if text.startswith("/") or Path(text).is_absolute():
        raise SourceContainmentError(f"{role} {text!r} must be relative to the declared root")
    if len(text) > 1 and text[1] == ":":
        raise SourceContainmentError(f"{role} {text!r} must not name a drive")
    parts = PurePosixPath(text).parts
    for part in parts:
        if part in {"", "."}:
            raise SourceContainmentError(f"{role} {text!r} has an empty or '.' component")
        if part == "..":
            raise SourceContainmentError(f"{role} {text!r} escapes the declared root with '..'")
        folded = part.casefold()
        if any(token in folded for token in FORBIDDEN_TOKENS):
            raise SourceContainmentError(
                f"{role} {text!r} names a protected path component; refused before any read"
            )
    folded_whole = text.casefold()
    if any(token in folded_whole for token in FORBIDDEN_TOKENS):
        raise SourceContainmentError(
            f"{role} {text!r} contains a protected token; refused before any read"
        )
    return PurePosixPath(*parts)


def contained_join(root: Path, relative: PurePosixPath, *, role: str) -> Path:
    """Join an already-guarded path onto a root and refuse a resolved escape."""
    base = Path(root).resolve()
    candidate = (base / relative).resolve()
    if not candidate.is_relative_to(base):
        raise SourceContainmentError(
            f"{role} {relative.as_posix()!r} resolves outside {base} (symlink escape)"
        )
    return candidate


def assert_directory_contained(root: Path, relative: PurePosixPath, *, role: str) -> Path:
    """Resolve an output directory under ``root`` without creating it."""
    return contained_join(root, relative, role=role)


def assert_never_holdout(value: str, *, role: str) -> str:
    """Belt-and-braces check for free-form strings that become a path later."""
    if any(token in value.casefold() for token in FORBIDDEN_TOKENS):
        raise SourceContainmentError(f"{role} {value!r} names protected data")
    return value


@runtime_checkable
class RawFileSystem(Protocol):
    """The only way a source adapter may reach raw bytes.

    Implementations must never be called with a path that has not passed
    :func:`canonical_source_path`; the adapter guarantees that ordering.
    """

    def read_bytes(self, relative: PurePosixPath) -> bytes:  # pragma: no cover - protocol
        ...


@runtime_checkable
class ListingRawFileSystem(RawFileSystem, Protocol):
    """A raw filesystem that can also enumerate one directory's child names.

    A partition-per-symbol lake cannot be described by a fixed file list, so an adapter
    has to *discover* partitions.  Discovery is name-only: it never opens, stats a file's
    content or follows an entry outside the declared root, and every name it returns has
    already passed :func:`canonical_source_path`.
    """

    def list_dir(
        self, relative: PurePosixPath, *, missing_ok: bool = False
    ) -> tuple[str, ...]:  # pragma: no cover
        ...


class LocalRawFileSystem:
    """Local raw-input reader confined to one source root."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    def read_bytes(self, relative: PurePosixPath) -> bytes:
        candidate = contained_join(self.root, relative, role="raw input")
        if not candidate.is_file():
            raise AdapterError(f"raw input {relative.as_posix()!r} is not a regular file")
        try:
            return candidate.read_bytes()
        except OSError as exc:
            raise AdapterError(f"cannot read raw input {relative.as_posix()!r}: {exc}") from exc

    def list_dir(self, relative: PurePosixPath, *, missing_ok: bool = False) -> tuple[str, ...]:
        """Child names of one directory beneath the root, sorted, never followed out.

        Each returned name is re-checked with :func:`canonical_source_path` so a lake
        cannot smuggle a protected or escaping component into a later read.  With
        ``missing_ok`` an absent directory is an empty listing: a *missing* funding
        directory is a data gap the missing-settlement policy must judge, not an I/O error.
        """
        directory = contained_join(self.root, relative, role="raw directory")
        if not directory.is_dir():
            if missing_ok and not directory.exists():
                return ()
            raise AdapterError(f"raw directory {relative.as_posix()!r} is not a directory")
        try:
            names = sorted(entry.name for entry in os.scandir(directory))
        except OSError as exc:
            raise AdapterError(
                f"cannot list raw directory {relative.as_posix()!r}: {exc}"
            ) from exc
        for name in names:
            canonical_source_path(f"{relative.as_posix()}/{name}", role="discovered entry")
        return tuple(names)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"LocalRawFileSystem({os.fspath(self.root)!r})"
