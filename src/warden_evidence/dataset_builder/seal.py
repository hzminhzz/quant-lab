"""``warden data seal-holdout``: the human-only transition from a build to a sealed window.

This is the one code path allowed to write under ``data/holdout``.  It is deliberately
narrow and deliberately ordered:

1. refuse immediately under ``WARDEN_NON_INTERACTIVE=1`` (an agent, a cron job or an MCP
   client can never seal);
2. refuse unless the caller passed the confirmation token a human types;
3. refuse any target that is not exactly ``data/holdout/<dataset_id>.parquet``;
4. refuse without a v2 manifest for *this* spec — a seal extends a verified build, it
   never creates the exploratory side;
5. refuse a manifest whose exploratory Parquet no longer matches its pinned digest.

Steps 1–5 touch no raw source, so a refused seal provably read nothing: the tests assert
zero filesystem read calls with a recording filesystem, which is the only observable that
distinguishes "refused" from "read first, refused later".

Only after all of that does it load the declared source, run **the same** ``quality_gate``
over ``[holdout_start, holdout_end)`` — one gate implementation, parameterised by window
role — and publish the bytes straight to the sealed path, atomically pinning
``holdout.sha256``/``holdout.sealed`` in the v2 manifest.  There is no staging directory.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from warden_evidence.dataset_builder.adapters import (
    ParquetReader,
    SourceAdapter,
    get_source_adapter,
)
from warden_evidence.dataset_builder.containment import (
    LocalRawFileSystem,
    RawFileSystem,
    SourceContainmentError,
)
from warden_evidence.dataset_builder.publish import (
    MANIFEST_SCHEMA_VERSION,
    Manifest,
    WriteOnceError,
    builder_code_closure_sha256,
    holdout_pin,
    publish_sealed,
    serialise_parquet,
    with_holdout,
)
from warden_evidence.dataset_builder.quality import GateOutcome, QualityGateError, run_quality_gate
from warden_evidence.dataset_builder.spec import DATASET_ID_PATTERN, DatasetSpec

#: The only directory a sealed holdout may be written to.  Not configurable: a "holdout"
#: somewhere else is an unsealed file with a misleading name.
HOLDOUT_DIR = "data/holdout"
HOLDOUT_SUFFIX = ".parquet"
#: What a human must type.  A flag alone is not a decision, which is why the token exists.
CONFIRMATION_TOKEN = "YES_SEAL_HOLDOUT"
#: The environment marker every non-interactive caller (MCP, cron, agent harness) sets.
NON_INTERACTIVE_ENV = "WARDEN_NON_INTERACTIVE"


class SealRefused(PermissionError):
    """The seal was refused before any source byte was read."""


class SealError(ValueError):
    """The seal could not run: the tree, the manifest or the window is not sealable."""


@dataclass(frozen=True)
class SealOutcome:
    dataset_id: str
    holdout_path: str
    holdout_sha256: str
    manifest_path: str
    manifest_sha256: str
    spec_sha256: str
    rows: int
    assets: tuple[str, ...]
    first_bar: str
    last_bar: str
    quality_gate_report: dict[str, Any] = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe summary for ``warden data seal-holdout --json``."""
        return {
            "dataset_id": self.dataset_id,
            "holdout_path": self.holdout_path,
            "holdout_sha256": self.holdout_sha256,
            "manifest_path": self.manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "spec_sha256": self.spec_sha256,
            "rows": self.rows,
            "assets": list(self.assets),
            "first_bar": self.first_bar,
            "last_bar": self.last_bar,
            "quality_gate_report": self.quality_gate_report,
        }


def sealed_holdout_target(
    repo_root: Path | str, dataset_id: str, *, holdout_dir: str = HOLDOUT_DIR
) -> tuple[PurePosixPath, Path]:
    """Resolve ``data/holdout/<dataset_id>.parquet`` or refuse, with no I/O on the target.

    ``canonical_source_path`` deliberately rejects anything naming ``holdout``; that is the
    *builder's* containment rule.  This function is its mirror image: it accepts exactly one
    spelling of the protected root and nothing else, so "sealed holdout" cannot be widened
    to a sibling directory by an argument.
    """
    if not isinstance(dataset_id, str) or not DATASET_ID_PATTERN.fullmatch(dataset_id):
        raise SealError(f"dataset_id {dataset_id!r} is not a valid dataset identifier")
    declared = Path(str(holdout_dir))
    if declared.is_absolute() or ".." in declared.parts or "\\" in str(holdout_dir):
        raise SourceContainmentError(
            f"--holdout-dir {holdout_dir!r} must be a relative POSIX path"
        )
    if declared.as_posix().rstrip("/") != HOLDOUT_DIR:
        raise SourceContainmentError(
            f"--holdout-dir {holdout_dir!r} is not the sealed holdout root {HOLDOUT_DIR!r}; "
            "a holdout written anywhere else is an unsealed file with a misleading name"
        )
    root = Path(repo_root).resolve()
    directory = (root / HOLDOUT_DIR).resolve()
    if not directory.is_relative_to(root):
        raise SourceContainmentError(
            f"{HOLDOUT_DIR!r} resolves outside the repository root (symlink escape)"
        )
    relative = PurePosixPath(HOLDOUT_DIR) / f"{dataset_id}{HOLDOUT_SUFFIX}"
    return relative, directory / f"{dataset_id}{HOLDOUT_SUFFIX}"


def _file_sha256(path: Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_manifest(manifest_path: Path, spec: DatasetSpec) -> tuple[Manifest, bytes]:
    """Load the v2 manifest this seal extends, refusing anything but this spec's build."""
    if not manifest_path.is_file():
        raise SealError(
            f"no v2 dataset manifest at {manifest_path}: seal-holdout extends a verified build "
            "and never creates the exploratory side"
        )
    raw = manifest_path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SealError(f"{manifest_path} is not a readable JSON manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise SealError(f"{manifest_path} must be a JSON object")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise SealError(
            f"{manifest_path} declares schema_version {payload.get('schema_version')!r}, "
            f"not the v2 manifest a seal pins"
        )
    if payload.get("dataset_id") != spec.dataset_id:
        raise SealError(
            f"{manifest_path} describes dataset {payload.get('dataset_id')!r}, not "
            f"{spec.dataset_id!r}"
        )
    if payload.get("spec_sha256") != spec.spec_sha256():
        raise SealError(
            f"{manifest_path} was produced by spec {payload.get('spec_sha256')}, not by this "
            "spec; a seal must pin the window the build declared"
        )
    holdout = payload.get("holdout")
    if isinstance(holdout, dict) and holdout.get("sealed") is True:
        raise WriteOnceError(
            f"dataset {spec.dataset_id!r} already has a sealed holdout "
            f"({holdout.get('path')} {holdout.get('sha256')}); sealing is write-once"
        )
    return Manifest(payload=payload, sha256=hashlib.sha256(raw).hexdigest()), raw


def _assert_exploratory_integrity(repo_root: Path, manifest: Manifest) -> None:
    """The exploratory side must still be the bytes the manifest pinned."""
    declared = manifest.payload.get("sha256")
    relative = manifest.payload.get("path")
    if not isinstance(declared, str) or not isinstance(relative, str) or not relative:
        raise SealError("manifest declares no exploratory path/digest pair to verify")
    target = (repo_root / PurePosixPath(relative)).resolve()
    if not target.is_relative_to(repo_root):
        raise SourceContainmentError(f"manifest path {relative!r} escapes the repository root")
    if not target.is_file():
        raise SealError(f"exploratory dataset {relative!r} is missing; nothing to extend")
    actual = _file_sha256(target)
    if actual != declared:
        raise SealError(
            f"exploratory dataset {relative!r} digest {actual} does not match the manifest pin "
            f"{declared}; refusing to seal a holdout beside unverified exploratory bytes"
        )


def seal_holdout(
    spec: DatasetSpec,
    *,
    repo_root: Path | str,
    confirmation: str,
    dataset_dir: str = "data/exploratory",
    holdout_dir: str = HOLDOUT_DIR,
    manifest_dir: str = "contracts/datasets",
    window_role: str = "holdout",
    adapter: SourceAdapter | None = None,
    fs: RawFileSystem | None = None,
    parquet_reader: ParquetReader | None = None,
) -> SealOutcome:
    """Seal the declared holdout window for an already-built dataset.

    Refusals 1–5 above happen before ``adapter.load``: the raw source is read only after the
    caller has proven human intent and the tree has proven it holds the build those bytes
    belong to.
    """
    # 1. Non-interactive callers cannot seal, whatever flags they pass.
    if os.environ.get(NON_INTERACTIVE_ENV) == "1":
        raise SealRefused(
            "warden data seal-holdout is an interactive human-only gate; it is refused under "
            f"{NON_INTERACTIVE_ENV}=1"
        )
    # 2. A flag is not a decision: the token is what the human types.
    if confirmation != CONFIRMATION_TOKEN:
        raise SealRefused(
            f"sealing a holdout requires the confirmation token {CONFIRMATION_TOKEN!r}"
        )
    if window_role != "holdout":
        raise SealError(
            f"seal-holdout only seals the holdout window, got window_role {window_role!r}"
        )

    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise SealError(f"repo_root {root} is not a directory")

    # 3. The only writable protected path, resolved without touching it.
    holdout_relative, holdout_path = sealed_holdout_target(root, spec.dataset_id, holdout_dir=holdout_dir)

    manifest_path = (root / manifest_dir / f"{spec.dataset_id}.json").resolve()
    if not manifest_path.is_relative_to((root / manifest_dir).resolve()):
        raise SourceContainmentError(f"manifest path for {spec.dataset_id!r} escapes {manifest_dir!r}")

    # 4./5. Extend this spec's verified build only.
    manifest_before, _ = _read_manifest(manifest_path, spec)
    _assert_exploratory_integrity(root, manifest_before)

    # Not read yet — the first raw byte is read below, after every refusal above.
    if holdout_path.exists():
        raise WriteOnceError(
            f"{holdout_relative.as_posix()} already exists; a sealed holdout is write-once"
        )

    reader = fs
    declared = spec.seal_source or spec.source
    if adapter is None:
        if reader is None:
            reader = LocalRawFileSystem(root)
        adapter = get_source_adapter(declared.adapter, fs=reader, parquet_reader=parquet_reader)
    if getattr(adapter, "name", None) != declared.adapter:
        raise SealError(
            f"adapter {getattr(adapter, 'name', None)!r} does not match declared "
            f"source.adapter {declared.adapter!r}"
        )

    if spec.seal_source is None:
        bundle = adapter.load(spec)
    else:
        try:
            bundle = adapter.load(spec, source=declared)
        except TypeError as exc:
            raise SealError(
                f"adapter {declared.adapter!r} cannot read a separately declared holdout source: "
                f"{exc}"
            ) from exc
    # The same gate, a different declared window: no second implementation to drift.
    outcome: GateOutcome = run_quality_gate(bundle.frame, spec, window_role="holdout")
    frame = outcome.frame
    if frame.is_empty():  # pragma: no cover - the gate already refuses emptiness
        raise QualityGateError("gate produced an empty holdout panel")

    parquet_bytes = serialise_parquet(frame, spec)
    parquet_sha256 = hashlib.sha256(parquet_bytes).hexdigest()
    assets = sorted({str(value) for value in frame["asset"].unique().to_list()})
    pin = holdout_pin(
        {
            "path": holdout_relative.as_posix(),
            "sha256": parquet_sha256,
            "rows": frame.height,
            "assets": assets,
            "first_bar": str(frame["timestamp"].min()),
            "last_bar": str(frame["timestamp"].max()),
            "window": {
                "start_inclusive": spec.time_window.holdout_start_inclusive.isoformat(),
                "end_exclusive": spec.time_window.holdout_end_exclusive.isoformat(),
                "window_role": "holdout",
            },
            "quality_gate_report": outcome.report,
        }
    )
    pin["evidence_tier"] = spec.evidence_tier
    pin["builder_code_closure_sha256"] = builder_code_closure_sha256()
    pin["raw_inputs_merkle_sha256"] = bundle.merkle_sha256
    pin["source_summary"] = bundle.summary
    manifest_after = with_holdout(manifest_before, holdout=pin)
    sealed = publish_sealed(
        holdout_path=holdout_path,
        parquet_bytes=parquet_bytes,
        manifest_path=manifest_path,
        manifest_before=manifest_before,
        manifest_after=manifest_after,
    )
    return SealOutcome(
        dataset_id=spec.dataset_id,
        holdout_path=holdout_relative.as_posix(),
        holdout_sha256=parquet_sha256,
        manifest_path=manifest_path.relative_to(root).as_posix(),
        manifest_sha256=sealed.manifest.sha256,
        spec_sha256=spec.spec_sha256(),
        rows=frame.height,
        assets=tuple(assets),
        first_bar=str(frame["timestamp"].min()),
        last_bar=str(frame["timestamp"].max()),
        quality_gate_report=outcome.report,
    )


__all__ = [
    "CONFIRMATION_TOKEN",
    "HOLDOUT_DIR",
    "NON_INTERACTIVE_ENV",
    "SealError",
    "SealOutcome",
    "SealRefused",
    "seal_holdout",
    "sealed_holdout_target",
]
