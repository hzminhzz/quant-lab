"""Strict live-integration preconditions for the real data/registry checks.

These checks need the shipped market datasets and the migrated registry. They are never skipped
silently: each test fails loudly when the preconditions are absent, and
`uv run python scripts/run_integration_checks.py` reports BLOCKED (exit 3) before pytest starts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = ("data/exploratory", "registry/registry.db")
MISSING = [rel for rel in REQUIRED if not (ROOT / rel).exists()]


@pytest.fixture(autouse=True)
def _require_live_environment():
    if MISSING:
        pytest.fail(f"BLOCKED: live integration preconditions missing: {MISSING}; "
                    "run `uv run python scripts/run_integration_checks.py` (this is not a skip).")
    yield
