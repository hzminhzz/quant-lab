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
    elif (db.resolve() != db or (root / "src/warden/registry.py").is_file()
          or (db.parent.parent / "src/warden/registry.py").is_file()):
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
