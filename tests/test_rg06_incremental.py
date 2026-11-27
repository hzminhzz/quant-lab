"""RG-06 public runner and governed comparison witnesses (scratch data only)."""
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from tests.test_rg05_research_intent import approve, review
from warden.contracts import utc
from warden.qualify import qualify_candidate


def test_incremental_trial_persists_comparison_and_refuses_short_sample(tiny_repo):
    f = tiny_repo()
    runner, _, _ = approve(f, incremental=True)
    review(runner, f)
    result = runner.run_spec(f.spec)
    diag = json.loads(result.diagnostics_json)
    paired = diag["incremental_comparison"]
    assert paired["status"] == "INCONCLUSIVE"
    assert paired["policy"]["min_observations"] == 120
    assert "incremental_comparison" in diag["failed_gates"]
    assert result.verdict != "CANDIDATE_FOR_QUALIFICATION"
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1
    assert {r["scenario_key"] for r in f.registry.robustness_status(result.run_id)} >= {"benchmark"}
    assert f.registry.validation_attempt_count(f.contract.contract_id) == 5
    assert f.registry.selection_attempt_count(f.contract.contract_id) == 1
    for key in ("candidate_path", "benchmark_path"):
        assert Path(paired[key]).is_file()


def _control_panel():
    rng = np.random.default_rng(628)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    for asset, mean, std in (("AAA", 0.006, 0.006), ("BBB", 0.014, 0.004)):
        price = 100.0
        for day in range(180):
            close = price * (1.0 + rng.normal(mean, std))
            rows.append({
                "timestamp": start + timedelta(days=day), "asset": asset,
                "open": price, "close": close, "high": max(price, close) * 1.001,
                "low": min(price, close) * 0.999, "volume": 1000000.0,
                "funding_rate": 0.0,
            })
            price = close
    return pl.DataFrame(rows).sort("timestamp", "asset").with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")))


def _long_module(symbol):
    return f"""
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor

def required_symbols(params):
    return None

class LongStrategy(Strategy):
    def __init__(self):
        self.executor = TargetWeightExecutor(RebalanceConfig(
            allow_short=False, allow_fractional=True,
            max_single_weight=0.5, max_gross_leverage=1.0))

    def on_data(self, timestamp, data, context, broker):
        self.executor.execute({{a: 0.4 if a == {symbol!r} else 0.0 for a in data}},
                              data, broker, timestamp=timestamp)

def configure_strategy(contract, params):
    return LongStrategy()
"""


@pytest.mark.parametrize("symbol,comparator,expected", [
    ("BBB", "AAA", "PASS"),
    ("AAA", "BBB", "REJECT"),
    ("BBB", "BBB", "REJECT"),
    ("BBB", "NONE", "INCONCLUSIVE"),
])
def test_actual_kernel_pair_can_pass_or_reject_independently_of_absolute_gates(
    tiny_repo, symbol, comparator, expected
):
    end = utc("2025-06-30T00:00:00Z")
    f = tiny_repo(
        module_source=_long_module(symbol), panel_override=_control_panel(),
        contract_overrides={
            "frequency": "1d", "bars_per_year": 365,
            "val_start": utc("2025-01-21T00:00:00Z"),
            "val_end": end, "holdout_start": end,
            "holdout_end": utc("2025-12-01T00:00:00Z"),
        },
    )
    runner, _, _ = approve(f, incremental=True, benchmark_source=_long_module(comparator))
    review(runner, f)
    result = runner.run_spec(f.spec)
    diag = json.loads(result.diagnostics_json)
    pair = diag["incremental_comparison"]
    assert pair["status"] == expected, (pair, diag["failed_gates"])
    assert pair["n_observations"] >= 120
    assert pair["bootstrap_samples_completed"] == (0 if comparator == "NONE" else 20000)
    assert pair["adjusted_alpha"] == pytest.approx(0.05 / 6)
    assert pair["benchmark_causality"]["prefix_checks"] == "PASS"
    if symbol == comparator:
        assert pair["delta_sharpe"] == pytest.approx(0.0)
        assert pair["delta_sharpe_ci"] == [0.0, 0.0]
    assert set(diag["failed_gates"]) == (set() if expected == "PASS" else {"incremental_comparison"})
    assert result.verdict == ("CANDIDATE_FOR_QUALIFICATION" if expected == "PASS" else "REVISE")
    assert f.registry.reconcile_attempt(result.run_id)["state"] == "FINALIZED"
    with pytest.raises(ValueError, match="frozen paired batch|candidate-for-qualification|CANDIDATE_FOR_QUALIFICATION"):
        qualify_candidate(result.run_id, "YES_UNSEAL_HOLDOUT", runner,
                          holdout_override_path=f.repo / "never-read.parquet")
    with sqlite3.connect(f.registry.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM holdout_claims").fetchone()[0] == 0
    Path(pair["benchmark_path"]).write_bytes(b"tampered")
    assert f.registry.reconcile_attempt(result.run_id)["state"] == "EVIDENCE_DRIFT"


def test_frozen_comparative_policy_and_benchmark_plan_cannot_be_changed(tiny_repo):
    f = tiny_repo()
    runner, plan, sha = approve(f, incremental=True)
    review(runner, f)
    with pytest.raises(ValueError, match="immutable"):
        runner.approve_research_intent("HYP-TINY", {
            **plan, "comparative_block_length": 10,
            "comparative_block_reason": "Longer autocorrelation horizon",
        }, "principal")
    spec, candidate, robustness = runner.describe_review_candidate(f.spec)
    robustness.pop("benchmark")
    with pytest.raises(ValueError, match="benchmark operation differs"):
        f.registry.reserve_screen(
            candidate.hypothesis_id, candidate.contract_id, candidate.strategy_module,
            spec.parameters, candidate.dataset_sha256, candidate=candidate,
            robustness_plan=robustness, intent_sha=sha)
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 0


def test_longer_bootstrap_block_requires_preregistered_justification(tiny_repo):
    f = tiny_repo()
    with pytest.raises(ValueError, match="preregistered justification"):
        approve(f, incremental=True, comparative_block_length=10)


def test_incomparable_benchmark_universe_is_refused_before_admission(tiny_repo):
    f = tiny_repo()
    source = _long_module("AAA").replace(
        "def required_symbols(params):\n    return None",
        "def required_symbols(params):\n    return ['AAA']",
    )
    runner, _, _ = approve(f, incremental=True, benchmark_source=source)
    with pytest.raises(ValueError, match="identical approved universe"):
        review(runner, f)
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 0


def test_interrupted_benchmark_retains_receipt_and_cannot_expose_success(tiny_repo, monkeypatch):
    from warden import incremental

    f = tiny_repo()
    runner, _, _ = approve(f, incremental=True)
    review(runner, f)

    def fail(*args, **kwargs):
        raise RuntimeError("Injected benchmark failure after reservation")

    monkeypatch.setattr(incremental, "execute_benchmark", fail)
    with pytest.raises(RuntimeError, match="Injected benchmark failure"):
        runner.run_spec(f.spec)
    with sqlite3.connect(f.registry.db_path) as conn:
        run, verdict = conn.execute("SELECT run_id,verdict FROM trials").fetchone()
        assert verdict == "FAILED"
    assert f.registry.validation_attempt_count(f.contract.contract_id) == 2
    receipt = f.registry.reconcile_attempt(run, confirm_interrupted=True)
    assert receipt["robustness"][0]["scenario_key"] == "benchmark"
    assert receipt["robustness"][0]["state"] == "INTERRUPTED_REVIEW"
    with pytest.raises(ValueError):
        runner.run_spec(f.spec)
    assert f.registry.candidate_budget("HYP-TINY")["used"] == 1
