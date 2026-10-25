import itertools
import sqlite3
from dataclasses import replace
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from warden.contracts import ExperimentResult, HypothesisSpec, load_contract
from warden.registry import (
    HypothesisBudgetExhaustedError,
    ResearchRegistry,
    compute_effective_trials_and_dsr,
)


def result(run_id="RUN-1", hypothesis_id="HYP-1"):
    return ExperimentResult(
        run_id=run_id, hypothesis_id=hypothesis_id, contract_id="CRYPTO_PERPS_1H_V1",
        strategy_name="test", strategy_family="rules_ts", config_hash="abc",
        stage_reached="STAGE_3_STRESS", verdict="REVISE", gross_sharpe=1., net_sharpe=.5,
        deflated_sharpe_prob=.6, expected_max_sharpe_noise=0., total_trials_n=1,
        effective_trials_keff=1., mean_ic=None, ic_hac_tstat=None, hit_rate=.5,
        profit_factor=1.1, max_drawdown_pct=2., annualized_turnover=30.,
        breakeven_cost_bps=8., stress_2x_cost_sharpe=-.1, param_neighbor_stability=.8,
        notes="Isolated registry behavioral fixture",
    )


@pytest.fixture
def registry(tmp_path):
    registry = ResearchRegistry(tmp_path / "registry.db")
    contract = load_contract(Path("contracts/crypto_perps_1h_v1.yaml"))
    registry.register_contract(contract)
    hyp = HypothesisSpec("HYP-1", contract.contract_id, "Breakout", "rules_ts",
                         "Forced liquidations create trend continuation",
                         "Leveraged traders incur forced execution costs", 8, "Reject net Sharpe < 1")
    registry.register_hypothesis(hyp)
    return registry, contract, hyp


def test_append_only_budget_and_exact_parquet_roundtrip(registry):
    registry, contract, hyp = registry
    returns = np.random.default_rng(7).normal(0, .01, 100)
    for index in range(3):
        registry.record_trial(result(f"RUN-{index}"), {"threshold": 2 + index}, returns)
    registry.register_hypothesis(hyp)
    registry.register_contract(contract)
    with pytest.raises(HypothesisBudgetExhaustedError):
        registry.register_trial_start(hyp.hypothesis_id, "RUN-4")
    for sql in ["DELETE FROM trials", "UPDATE trials SET net_sharpe=99"]:
        with registry._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="Append-only"):
            conn.execute(sql)
    with registry._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 3
        assert conn.execute("SELECT trials_used,status FROM hypotheses").fetchone()[:] == (3, "REJECTED")
        path = conn.execute("SELECT returns_parquet_path FROM trials ORDER BY run_id").fetchone()[0]
    np.testing.assert_array_equal(pl.read_parquet(path)["net_return"].to_numpy(), returns)
    np.testing.assert_array_equal(registry.load_prior_trial_returns(contract.contract_id, 100)[0], returns)
    with pytest.raises(ValueError, match="immutable"):
        registry.register_contract(replace(contract, taker_fee_bps=5))
    with pytest.raises(ValueError, match="immutable"):
        registry.register_hypothesis(replace(hyp, title="Changed mechanism"))


def test_trial_reservation_is_durable_and_ids_cannot_overwrite(registry):
    registry, _, hyp = registry
    assert registry.register_trial_start(hyp.hypothesis_id, "RUN-1") == 1
    reopened = ResearchRegistry(registry.db_path)
    with pytest.raises(ValueError, match="Duplicate"):
        reopened.register_trial_start(hyp.hypothesis_id, "RUN-1")
    reopened.record_trial(result(), {}, np.linspace(-.01, .011, 100), trial_number=1)
    with pytest.raises(FileExistsError):
        reopened.record_trial(result(), {}, np.ones(100), trial_number=1)
    with registry._connect() as conn:
        assert conn.execute("SELECT trials_used FROM hypotheses").fetchone()[0] == 1
    with pytest.raises(ValueError, match="aligned"):
        reopened.load_prior_trial_returns("CRYPTO_PERPS_1H_V1", 99)


def test_effective_rank_collinear_orthogonal_and_candidate_specific_dsr():
    rng = np.random.default_rng(42)
    x = rng.normal(size=2000)
    duplicate = compute_effective_trials_and_dsr(x, [x, x], 8760)
    assert duplicate[2] == 3
    assert duplicate[3] == pytest.approx(1., abs=1e-8)
    independent = rng.normal(size=(2000, 4))
    ranks = [compute_effective_trials_and_dsr(independent[:, n],
             [independent[:, j] for j in range(n)], 8760) for n in range(4)]
    assert [row[2] for row in ranks] == [1, 2, 3, 4]
    assert all(a[3] < b[3] and a[1] < b[1] for a, b in itertools.pairwise(ranks))
    assert ranks[-1][3] > 3.9
    candidate = compute_effective_trials_and_dsr(x - .1, [x + .5], 8760)
    assert candidate[0] < .01  # Must not inherit the historical leader's positive DSR.
    with pytest.raises(ValueError, match="aligned"):
        compute_effective_trials_and_dsr(x, [x[:-1]], 8760)
    with pytest.raises(ValueError, match="finite"):
        compute_effective_trials_and_dsr(np.full(100, np.nan), [], 8760)
