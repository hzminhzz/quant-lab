"""Accidental operational-ledger access guard for supported mutation entrypoints.

This is not a sandbox or a defense against an unrestricted host owner. Scratch
fixtures may use their own unlinked ledgers; appliance migrations use the
separate existing lifecycle and never this admission path.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path

from warden.lease import mutation_lease
from warden.registry import SCHEMA_VERSION, ResearchRegistry


def require_mutation_context(root: Path, db_path: Path) -> None:
    root = Path(root).resolve()
    db = Path(os.path.abspath(db_path))
    home = os.environ.get("QUANT_LAB_APPLIANCE_HOME")
    if home:
        from warden_ops.appliance import Appliance

        app = Appliance(home)
        release = app.active_release()
        if (release is None or root != app.runtime(release).resolve()
                or db.resolve() != app.db.resolve() or (app.home / "halted.json").exists()
                or Path(sys.executable).absolute() != app.python.absolute()
                or Path(__file__).resolve().parents[1] != root / "src"):
            raise ValueError("mutation requires the active, non-halted installed appliance")
        observed = app.observe()
        if not observed["healthy"] or observed["release_id"] != release:
            raise ValueError("mutation requires the same healthy installed appliance")
    # A source checkout is for engineering. A linked ledger is not a scratch
    # fixture, even if it happens to have a compatible schema. Also refuse an
    # explicit path into a real source checkout, regardless of the caller root.
    else:
        owners = (root, db.parent.parent)
        copied_source = any((owner / "src/warden/registry.py").is_file() for owner in owners)
        fixture_root = os.environ.get("WARDEN_TEST_FIXTURE_ROOT")
        isolated_fixture = (fixture_root is not None and Path(fixture_root).resolve() == root
                            and db.is_relative_to(root)
                            and not any((root / name).is_symlink()
                                        for name in ("registry", "data", "cache", ".workspace")))
        if (db.resolve() != db
                or any((owner / ".git").exists() or (owner / ".git").is_symlink() for owner in owners)
                or (copied_source and not isolated_fixture)):
            raise ValueError("operational mutations require the installed appliance; use an isolated scratch fixture")
    if db.is_file():
        schema = ResearchRegistry(db, readonly=True).schema_version
        if schema != SCHEMA_VERSION:
            raise ValueError(f"separate ledger migration required: {schema} -> {SCHEMA_VERSION}")


@contextmanager
def operational_admission(root: Path, db_path: Path):
    """Serialize short job admissions with the existing appliance lifecycle lock."""
    home = os.environ.get("QUANT_LAB_APPLIANCE_HOME")
    lease = mutation_lease(Path(home) / "operations.db") if home else nullcontext()
    with lease:
        require_mutation_context(root, db_path)
        yield
