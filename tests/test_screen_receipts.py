"""Stage 1 admission is a durable research attempt, not an ephemeral command result."""

from __future__ import annotations

from pathlib import Path

import pytest

from warden.contracts import HypothesisSpec, load_contract
from warden.registry import ResearchRegistry, ScreenAlreadyAttemptedError


def _registry(tmp_path: Path) -> ResearchRegistry:
    reg = ResearchRegistry(tmp_path / "registry.db")
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    reg.register_contract(contract)
    reg.register_hypothesis(HypothesisSpec(
        "HYP-SCREEN", contract.contract_id, "Screen fixture", "rules_ts",
        "A measurable price discontinuity may persist for several hours",
        "Fees and adverse selection may eliminate the apparent effect", 8,
        "Reject if the executable effect is not present in validation",
    ))
    return reg


def test_screen_reserved_before_compute_and_no_retry_after_crash(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    kwargs = ("HYP-SCREEN", "CRYPTO_PERPS_1H_V1", "strategies/flat.py", {"window": 12})
    key = registry.reserve_screen(*kwargs, data_sha256="a" * 64)
    state = registry.screen_status(*kwargs)
    assert state["state"] == "RESERVED" and state["screen_key"] == key
    assert registry.spec_status(*kwargs)["state"] == "REQUIRES_RECONCILIATION"
    # Reopening/migrating an interrupted attempt must not erase the reservation.
    reopened = ResearchRegistry(registry.db_path)
    assert reopened.screen_status(*kwargs)["state"] == "RESERVED"
    with pytest.raises(ScreenAlreadyAttemptedError):
        reopened.reserve_screen(*kwargs, data_sha256="b" * 64)
    with reopened._connect() as conn:
        assert conn.execute("SELECT count(*) FROM selection_attempts").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM screen_receipts").fetchone()[0] == 1


def test_screen_finalization_is_atomic_and_failed_record_stays_reviewable(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    kwargs = ("HYP-SCREEN", "CRYPTO_PERPS_1H_V1", "strategies/flat.py", {"window": 12})
    key = registry.reserve_screen(*kwargs)
    with pytest.raises(ScreenAlreadyAttemptedError):
        registry.record_selection_attempt(
            hypothesis_id="HYP-SCREEN", contract_id="CRYPTO_PERPS_1H_V1",
            kind="standalone_screen", stage="STAGE_1_SIGNAL", outcome="PASS", screen_key="BAD")
    assert registry.screen_status(*kwargs)["state"] == "RESERVED"
    with registry._connect() as conn:
        assert conn.execute("SELECT count(*) FROM selection_attempts").fetchone()[0] == 0

    attempt_id = registry.record_selection_attempt(
        hypothesis_id="HYP-SCREEN", contract_id="CRYPTO_PERPS_1H_V1",
        kind="standalone_screen", stage="STAGE_1_SIGNAL", outcome="REJECT",
        strategy_module="strategies/flat.py", parameters={"window": 12}, screen_key=key)
    state = registry.screen_status(*kwargs)
    assert state["state"] == "FINALIZED" and state["attempt_id"] == attempt_id
    assert state["outcome"] == "REJECT" and not state["retry_allowed"]
    assert registry.spec_status(*kwargs)["state"] == "STAGE1_REJECT"
    ResearchRegistry(registry.db_path)  # idempotent legacy backfill
    with registry._connect() as conn:
        assert conn.execute("SELECT count(*) FROM selection_attempts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM screen_receipts").fetchone()[0] == 1


def test_legacy_screen_receipt_backfilled_without_increasing_search_debt(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    kwargs = ("HYP-SCREEN", "CRYPTO_PERPS_1H_V1", "strategies/flat.py", {"window": 12})
    registry.record_selection_attempt(
        hypothesis_id="HYP-SCREEN", contract_id="CRYPTO_PERPS_1H_V1",
        kind="standalone_screen", stage="STAGE_1_SIGNAL", outcome="PASS",
        strategy_module="strategies/flat.py", parameters={"window": 12})
    migrated = ResearchRegistry(registry.db_path)
    assert migrated.screen_status(*kwargs)["state"] == "FINALIZED"
    assert migrated.spec_status(*kwargs)["state"] == "STAGE1_PASS"
    with migrated._connect() as conn:
        assert conn.execute("SELECT count(*) FROM selection_attempts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM screen_receipts").fetchone()[0] == 1
