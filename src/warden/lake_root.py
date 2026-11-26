"""Locate the external canonical ML4T Hive store, not a Warden dataset copy."""

import os
from pathlib import Path

LAKE_STORE_ENV = "ML4T_DATA_STORE"


def resolve_lake_root(repo_root: Path | str = Path(".")) -> Path:
    """Use ML4T_DATA_STORE when set, else the sibling ml4t-data store."""
    from warden_evidence.dataset_builder.containment import assert_never_holdout

    configured = os.environ.get(LAKE_STORE_ENV)
    if configured is not None and not configured.strip():
        raise FileNotFoundError(f"{LAKE_STORE_ENV} is empty; set it to an existing store directory")
    candidate = (
        Path(configured).expanduser()
        if configured is not None
        else Path(repo_root).resolve().parent / "ml4t-data" / ".data" / "store"
    )
    assert_never_holdout(str(candidate), role=LAKE_STORE_ENV)
    root = candidate.resolve()
    assert_never_holdout(str(root), role=LAKE_STORE_ENV)
    if not root.is_dir():
        if configured is not None:
            raise FileNotFoundError(f"{LAKE_STORE_ENV} is not a directory: {root}")
        raise FileNotFoundError(
            f"Default ML4T lake store is not a directory: {root}; set {LAKE_STORE_ENV}"
        )
    return root
