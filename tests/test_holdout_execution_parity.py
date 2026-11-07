"""Fixture holdout must share Warden validation's exact fill economics."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.contracts import ExperimentResult
from warden.qualify import HoldoutAlreadyUnsealedError, qualify_candidate
from warden.runner import (
    ExecutionDataError,
    ExperimentRunner,
    _aligned_matrix,
    _funding_settlement_mask,
    calculate_metrics_from_returns,
    execution_returns,
    simulate_positions,
)


def _candidate(fixture) -> ExperimentResult:
    return ExperimentResult(
        run_id="RUN-FIX-QUAL", hypothesis_id="HYP-TINY", contract_id="TINY_V1",
        strategy_name="Fixture", strategy_family="rules_ts", config_hash="abc",
        stage_reached="STAGE_3_STRESS", verdict="CANDIDATE_FOR_QUALIFICATION",
        gross_sharpe=1.5, net_sharpe=1.2, deflated_sharpe_prob=0.96,
        expected_max_sharpe_noise=0.1, total_trials_n=1, effective_trials_keff=1.0,
        mean_ic=None, ic_hac_tstat=None, hit_rate=0.5, profit_factor=1.2,
        max_drawdown_pct=2.0, annualized_turnover=10.0,
        breakeven_cost_bps=8.0, stress_2x_cost_sharpe=0.8,
        param_neighbor_stability=0.8, notes="Synthetic fixture only",
        synthetic=0, data_snapshot_sha256=fixture.sha, data_provenance="verified_market",
        strategy_module="strategies/flat.py", model_type="rules",
    )


@pytest.mark.parametrize("execution_mode", ["NEXT_BAR", "QUOTE_SIDE"])
def test_holdout_returns_are_identical_to_validation_simulator(tiny_repo, tmp_path: Path,
                                                                 execution_mode: str) -> None:
    f = tiny_repo(contract_overrides={"execution_mode": execution_mode})
    f.registry.record_trial(_candidate(f), {"parameters": {}}, np.linspace(-0.002, 0.003, 40))
    # Synthetic market panel in an explicitly fixture-only holdout override.
    panel = f.panel
    if execution_mode == "QUOTE_SIDE":
        panel = panel.with_columns(bid=pl.col("open") - 0.025, ask=pl.col("open") + 0.025)
    hpath = tmp_path / f"{execution_mode}.parquet"
    panel.write_parquet(hpath)
    runner = ExperimentRunner(registry=f.registry, repo_root=f.repo)
    report = qualify_candidate("RUN-FIX-QUAL", "YES_UNSEAL_HOLDOUT", runner,
                               holdout_override_path=hpath)

    window = panel.filter((pl.col("timestamp") >= f.contract.holdout_start)
                          & (pl.col("timestamp") <= f.contract.holdout_end))
    panel = window if window.height >= 20 else panel
    assets = sorted(panel["asset"].unique().to_list())
    ts = sorted(panel["timestamp"].unique().to_list())
    positions = np.zeros((len(ts), len(assets)))
    positions[::4] = 0.1
    prices = execution_returns(panel, f.contract, assets)
    funding = _aligned_matrix(panel, "funding_rate", assets)
    expected = simulate_positions(positions, prices["long"], funding, f.contract,
                                  slice(0, len(ts)), quote_returns=prices,
                                  funding_mask=_funding_settlement_mask(ts, f.contract))
    metrics = calculate_metrics_from_returns(expected["gross_returns"], expected["net_returns"],
                                              expected["turnover"], f.contract.bars_per_year)
    assert report["holdout_net_sharpe"] == pytest.approx(metrics["net_sharpe"], abs=1e-12)
    assert report["holdout_override"] is True
    with f.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0] == 1
        assert conn.execute("SELECT state FROM holdout_claims").fetchone()[0] in {
            "PROMOTED", "REJECTED_ON_HOLDOUT"}


def test_production_holdout_requires_audit_and_matching_causal_proof_before_claim(tiny_repo) -> None:
    f = tiny_repo()
    f.registry.record_trial(_candidate(f), {"parameters": {}}, np.linspace(-0.002, 0.003, 40))
    runner = ExperimentRunner(registry=f.registry, repo_root=f.repo)
    # No holdout file is provisioned. The audit must deny *before* checking or
    # opening the protected file, and no one-shot claim may be consumed.
    with pytest.raises(PermissionError, match="APPROVED six-check audit"):
        qualify_candidate("RUN-FIX-QUAL", "YES_UNSEAL_HOLDOUT", runner)
    f.registry.record_audit("RUN-FIX-QUAL", "TINY_V1", "APPROVED", {"fixture": True})
    with pytest.raises(PermissionError, match="run-bound causal proof"):
        qualify_candidate("RUN-FIX-QUAL", "YES_UNSEAL_HOLDOUT", runner)
    with f.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM holdout_claims").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0] == 0


def test_missing_frozen_strategy_never_burns_one_shot_holdout(tiny_repo, tmp_path: Path) -> None:
    f = tiny_repo()
    unavailable = replace(_candidate(f), strategy_module="strategies/candidates/never_registered.py")
    f.registry.record_trial(unavailable, {"parameters": {}}, np.linspace(-0.002, 0.003, 40))
    hpath = tmp_path / "fixture.parquet"
    f.panel.write_parquet(hpath)
    with pytest.raises(FileNotFoundError, match="Frozen strategy"):
        qualify_candidate("RUN-FIX-QUAL", "YES_UNSEAL_HOLDOUT",
                          ExperimentRunner(registry=f.registry, repo_root=f.repo),
                          holdout_override_path=hpath)
    with f.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM holdout_claims").fetchone()[0] == 0


def test_missing_bid_or_ask_cannot_be_replaced_by_close(tiny_repo, tmp_path: Path, monkeypatch) -> None:
    f = tiny_repo(contract_overrides={"execution_mode": "QUOTE_SIDE"})
    f.registry.record_trial(_candidate(f), {}, np.linspace(-0.002, 0.003, 40))
    panel = f.panel.with_columns(bid=pl.col("open") - 0.01)
    hpath = tmp_path / "incomplete_quote.parquet"
    panel.write_parquet(hpath)
    runner = ExperimentRunner(registry=f.registry, repo_root=f.repo)
    reads = []
    real_read = pl.read_parquet

    def counted_read(path, *args, **kwargs):
        reads.append(str(path))
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", counted_read)
    with pytest.raises(ExecutionDataError, match="requires"):
        qualify_candidate("RUN-FIX-QUAL", "YES_UNSEAL_HOLDOUT", runner,
                          holdout_override_path=hpath)
    with pytest.raises(HoldoutAlreadyUnsealedError):
        qualify_candidate("RUN-FIX-QUAL", "YES_UNSEAL_HOLDOUT", runner,
                          holdout_override_path=hpath)
    assert reads == [str(hpath)]  # duplicate request made no second protected read
    with f.registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM qualifications").fetchone()[0] == 0
        assert conn.execute("SELECT state FROM holdout_claims").fetchone()[0] == "READ_STARTED"
