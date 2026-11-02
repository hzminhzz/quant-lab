"""Regression: CFD OHLCV is a valid separate NEXT_BAR research basis, not quote fills."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.contracts import load_contract
from warden.runner import (
    ExecutionDataError,
    ExperimentRunner,
    execution_returns,
    require_execution_columns,
    simulate_positions,
)

INDEX = load_contract(Path("contracts/indices_1h_v2.yaml"))
FX_QUOTE = load_contract(Path("contracts/fx_majors_15m_v1.yaml"))


def _cfd_bars() -> pl.DataFrame:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return pl.DataFrame({
        "timestamp": [start + timedelta(hours=i) for i in range(4)],
        "asset": ["ES"] * 4,
        "open": [100.0, 105.0, 110.0, 108.0],
        "high": [101.0, 107.0, 111.0, 109.0],
        "low": [99.0, 104.0, 107.0, 106.0],
        "close": [100.5, 106.0, 108.5, 107.0],
        "volume": [150.0, 150.0, 150.0, 150.0],
    })


def test_cfd_ohlcv_can_screen_with_next_bar_and_nonzero_costs() -> None:
    """No futures quotes are needed for a causally delayed proxy signal screen."""
    contract = replace(
        INDEX,
        contract_id="INDICES_CFD_PROXY_UNIT_TEST",
        slippage_bps=2.0,  # includes modeled spread + slippage
        data_provenance="unknown", data_snapshot_sha256="",  # in-memory unit fixture only
    )
    panel = _cfd_bars()
    assert "bid" not in panel.columns and "ask" not in panel.columns
    returns = execution_returns(panel, contract, ["ES"])
    positions = np.ones((4, 1), dtype=float)
    simulated = simulate_positions(
        positions, returns["long"], None, contract, slice(0, 4),
        quote_returns=returns,
    )
    assert simulated["net_returns"][0] == 0.0  # no same-bar fill
    expected = 110.0 / 105.0 - 1.0 - contract.total_one_way_cost_decimal()
    np.testing.assert_allclose(simulated["net_returns"][1], expected)
    assert simulated["net_returns"][1] < simulated["gross_returns"][1]
    assert contract.spec_hash() != INDEX.spec_hash()


def test_proxy_does_not_silently_override_quote_side_contract() -> None:
    """Changing execution basis requires a separately identified contract."""
    cfd = _cfd_bars()
    with pytest.raises(ExecutionDataError, match="requires \\['bid', 'ask'\\]"):
        require_execution_columns(cfd, FX_QUOTE)
    proxy = replace(
        FX_QUOTE, contract_id="FX_CFD_PROXY_UNIT_TEST", execution_mode="NEXT_BAR",
        data_provenance="unknown", data_snapshot_sha256="",
    )
    assert proxy.contract_id != FX_QUOTE.contract_id
    assert proxy.spec_hash() != FX_QUOTE.spec_hash()
    require_execution_columns(cfd, proxy)  # explicit new lineage, no fake quotes


def test_full_stage1_cfd_proxy_screen_records_pinned_evidence(tiny_repo) -> None:
    """A proxy-only FX screen uses Warden's registered Stage 1 path, not a private simulator."""
    fixture = tiny_repo(contract_overrides={"asset_class": "fx", "execution_mode": "NEXT_BAR"})
    assert "bid" not in fixture.panel.columns and "ask" not in fixture.panel.columns
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    report = runner.run_stage1_screen(fixture.spec)

    with fixture.registry._connect() as conn:
        attempts = conn.execute(
            "SELECT stage, outcome, data_provenance, returns_observed, diagnostics_json "
            "FROM selection_attempts"
        ).fetchall()
        assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 0
        assert conn.execute("SELECT trials_used FROM hypotheses").fetchone()[0] == 0

    assert len(attempts) == 1
    stage, outcome, provenance, observed, diagnostics_json = attempts[0]
    assert stage == "STAGE_1_SIGNAL"
    assert outcome == ("PASS" if report.passed else "REJECT")
    assert provenance == "verified_market" and observed == 1
    dataset = json.loads(diagnostics_json)["dataset"]
    assert dataset["sha256"] == fixture.sha
    assert dataset["eligible_market"] is True
    assert dataset["contract_id"] == fixture.contract.contract_id


def test_full_stage1_refuses_quotes_on_ohlcv_without_converting_contract(tiny_repo) -> None:
    """A separate quote-based experiment remains blocked when source quotes are absent."""
    fixture = tiny_repo(contract_overrides={"asset_class": "fx", "execution_mode": "QUOTE_SIDE"})
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    with pytest.raises(ExecutionDataError, match="requires \\['bid', 'ask'\\]"):
        runner.run_stage1_screen(fixture.spec)
    assert runner.resolve_contract(fixture.contract.contract_id).execution_mode == "QUOTE_SIDE"
    with fixture.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 0
