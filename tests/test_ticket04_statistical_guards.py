"""Ticket 04 statistical guards: trimmed_sharpe, frequency-aware fold purging,
sign-safe neighbor stability, and structured REVISE attribution."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
from pathlib import Path

import numpy as np
import pytest

from warden.cli import cmd_verify
from warden.cli import main as cli_main
from warden.codeid import code_closure
from warden.contracts import ExperimentResult, canonical_json
from warden.gates import annualised_sharpe
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner

CAUSAL_MODULE = """
import numpy as np
import polars as pl


class CausalPipeline:
    def compute(self, bars_df, contract):
        return bars_df.sort("timestamp", "asset").with_columns(
            pl.col("close").shift(1).over("asset").alias("past_close")
        )


pipeline = CausalPipeline()


def generate_positions(panel_df, contract, **kwargs):
    times = len(panel_df["timestamp"].unique())
    assets = len(panel_df["asset"].unique())
    pos = np.zeros((times, assets))
    pos[::4] = 0.1
    return {"positions": pos}
"""


def _setup_audit_trial(
    tiny_repo,
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_id: str,
    returns: np.ndarray,
    net_sharpe: float,
):
    fixture = tiny_repo(module_source=CAUSAL_MODULE, module_name="causal_t04")
    reg = ResearchRegistry(fixture.repo / "registry" / "registry.db")
    reg.register_contract(fixture.contract)
    with fixture.registry._connect() as old_conn:
        h = old_conn.execute(
            "SELECT hypothesis_id, contract_id, title, strategy_family, economic_mechanism, "
            "counterparty_or_friction, expected_holding_bars, falsification_criteria FROM hypotheses"
        ).fetchone()
    from warden.contracts import HypothesisSpec

    reg.register_hypothesis(
        HypothesisSpec(
            h[0],
            h[1],
            h[2],
            h[3],
            h[4],
            h[5],
            int(h[6]),
            h[7],
        )
    )
    monkeypatch.setattr("warden.cli.check_worktree", lambda: True)
    runner = ExperimentRunner(registry=reg, repo_root=fixture.repo)
    assert cmd_verify(argparse.Namespace(spec_yaml=str(fixture.spec), json=True), runner) == 0

    strat_module = "strategies/causal_t04.py"
    code_sha, _ = code_closure(fixture.repo, strat_module)
    cfg_hash = hashlib.sha256(canonical_json({}).encode()).hexdigest()[:12]
    res = ExperimentResult(
        run_id=run_id,
        hypothesis_id=h[0],
        contract_id=fixture.contract.contract_id,
        strategy_name="Tiny",
        strategy_family="rules_ts",
        config_hash=cfg_hash,
        stage_reached="STAGE_3_STRESS",
        verdict="CANDIDATE_FOR_QUALIFICATION",
        gross_sharpe=net_sharpe + 0.2,
        net_sharpe=net_sharpe,
        deflated_sharpe_prob=0.92,
        expected_max_sharpe_noise=0.25,
        total_trials_n=1,
        effective_trials_keff=1.0,
        mean_ic=None,
        ic_hac_tstat=None,
        hit_rate=0.52,
        profit_factor=1.35,
        max_drawdown_pct=4.0,
        annualized_turnover=12.0,
        breakeven_cost_bps=15.0,
        stress_2x_cost_sharpe=0.75,
        param_neighbor_stability=0.85,
        notes="Ticket 04 audit fixture",
        delay_plus1_sharpe=0.30,
        synthetic=0,
        data_snapshot_sha256=fixture.sha,
        strategy_module=strat_module,
        model_type="rules",
        data_provenance="verified_market",
        strategy_code_sha256=code_sha,
    )
    reg.record_trial(res, {}, returns)
    return fixture.repo, reg


def test_audit_check_rejects_hyp_2026_125_outlier_trap(tiny_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    n_bars = 200
    outlier_returns = np.array([-0.00018 if i % 2 == 0 else -0.00022 for i in range(n_bars)], dtype=float)
    outlier_returns[-10:] = 0.0050
    p95 = float(np.percentile(outlier_returns, 95.0))
    assert float(np.mean(outlier_returns[outlier_returns <= p95])) > -0.001
    headline_sr = annualised_sharpe(outlier_returns, 8760)
    assert headline_sr > 1.0

    repo, _ = _setup_audit_trial(
        tiny_repo,
        monkeypatch,
        run_id="RUN-OUTLIER-125",
        returns=outlier_returns,
        net_sharpe=headline_sr,
    )
    monkeypatch.chdir(repo)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli_main(["audit-check", "--run-id", "RUN-OUTLIER-125", "--json"])

    assert rc == 1
    data = json.loads(buf.getvalue())
    assert data["verdict"] == "REJECTED"
    assert data["checks"]["1_target_rebalance_alignment"]["passed"] is True
    assert data["checks"]["2_outlier_sensitivity"]["passed"] is False
    assert data["checks"]["3_point_in_time_causality"]["passed"] is True
    assert data["checks"]["4_fold_local_fitting"]["passed"] is True
    assert data["checks"]["5_cost_and_latency_survival"]["passed"] is True
    assert data["checks"]["6_selection_bias_accounting"]["passed"] is True
    assert data["trimmed_sharpe"] < 0.0
    assert data["checks"]["2_outlier_sensitivity"]["trimmed_sharpe"] == pytest.approx(data["trimmed_sharpe"])

    md = (Path(".workspace") / "hypotheses" / "AUDIT_RUN-OUTLIER-125.md").read_text(encoding="utf-8")
    assert "Trimmed Sharpe (ex top-5%):" in md
    assert f"{data['trimmed_sharpe']:.4f}" in md
    assert "- [FAIL] 2_outlier_sensitivity" in md


def test_audit_check_passes_steady_positive_control(tiny_repo, monkeypatch: pytest.MonkeyPatch) -> None:
    steady_returns = np.array([0.0006 if i % 2 == 0 else 0.0004 for i in range(200)], dtype=float)
    headline_sr = annualised_sharpe(steady_returns, 8760)
    assert headline_sr > 1.0

    repo, _ = _setup_audit_trial(
        tiny_repo,
        monkeypatch,
        run_id="RUN-STEADY-OK",
        returns=steady_returns,
        net_sharpe=headline_sr,
    )
    monkeypatch.chdir(repo)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli_main(["audit-check", "--run-id", "RUN-STEADY-OK", "--json"])

    assert rc == 0
    data = json.loads(buf.getvalue())
    assert data["verdict"] == "APPROVED"
    assert data["checks"]["2_outlier_sensitivity"]["passed"] is True
    assert data["trimmed_sharpe"] > 0.0
    assert data["checks"]["2_outlier_sensitivity"]["trimmed_sharpe"] == pytest.approx(data["trimmed_sharpe"])

    md = (Path(".workspace") / "hypotheses" / "AUDIT_RUN-STEADY-OK.md").read_text(encoding="utf-8")
    assert f"Trimmed Sharpe (ex top-5%): {data['trimmed_sharpe']:.4f}" in md
    assert "- [PASS] 2_outlier_sensitivity" in md


def test_trimmed_sharpe_frequency_and_scale_invariance() -> None:
    from warden.gates import TRIMMED_SHARPE_FLOOR, compute_trimmed_sharpe

    assert compute_trimmed_sharpe([], 8760) is None
    assert compute_trimmed_sharpe([0.01], 8760) is None
    assert compute_trimmed_sharpe([0.01, np.nan], 8760) is None
    assert compute_trimmed_sharpe([0.01, 0.02], 0) is None

    outlier = np.array([-0.0002 + (1e-4 if i % 2 == 0 else -1e-4) for i in range(200)], dtype=float)
    outlier[-10:] = 0.0100
    steady = np.array([0.0005 + (1e-4 if i % 2 == 0 else -1e-4) for i in range(200)], dtype=float)

    bars_list = (525600, 8760, 252, 365)
    for vec, expected_pass in ((outlier, False), (steady, True)):
        per_bar_vals = []
        for bpy in bars_list:
            tsr = compute_trimmed_sharpe(vec, bpy)
            assert tsr is not None
            assert (tsr > TRIMMED_SHARPE_FLOOR) is expected_pass
            per_bar_vals.append(tsr / math.sqrt(bpy))
        for val in per_bar_vals[1:]:
            assert val == pytest.approx(per_bar_vals[0], rel=1e-12, abs=1e-12)

    base_steady = compute_trimmed_sharpe(steady, 8760)
    base_outlier = compute_trimmed_sharpe(outlier, 8760)
    assert base_steady is not None and base_outlier is not None
    for k in (0.05, 3.5, 100.0):
        assert compute_trimmed_sharpe(k * steady, 8760) == pytest.approx(base_steady, rel=1e-6)
        assert compute_trimmed_sharpe(k * outlier, 8760) == pytest.approx(base_outlier, rel=1e-6)

    rng = np.random.default_rng(17)
    steady_brownian_srs = []
    for bpy in (525600, 8760, 365):
        dt = 1.0 / bpy
        r_steady = 1.5 * dt + 0.005 * math.sqrt(dt) * rng.standard_normal(4000)
        tsr_steady = compute_trimmed_sharpe(r_steady, bpy)
        assert tsr_steady is not None and tsr_steady > TRIMMED_SHARPE_FLOOR
        steady_brownian_srs.append(tsr_steady)

        r_trap = (-0.05 * dt) + 0.01 * dt * rng.standard_normal(400)
        r_trap[-20:] = 2.5 * dt
        tsr_trap = compute_trimmed_sharpe(r_trap, bpy)
        assert tsr_trap is not None and tsr_trap <= TRIMMED_SHARPE_FLOOR
    assert max(steady_brownian_srs) / min(steady_brownian_srs) < 2.0


class _StubFoldModel:
    def fit_predict_fold(self, train_df, val_features_df, feature_cols, label_col, seed: int = 42):
        import polars as pl

        assert label_col not in val_features_df.columns
        return val_features_df.select("timestamp", "asset").with_columns(prediction_value=pl.lit(1.0))


def _make_contract(frequency: str, *, horizon: int = 2, embargo: int = 1):
    from datetime import UTC, datetime, timedelta

    from warden.contracts import EvaluationContract

    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    bpy = {"1m": 525600, "5m": 105120, "15m": 35040, "30m": 17520, "1h": 8760, "4h": 2190, "1d": 365}[frequency]
    return EvaluationContract(
        contract_id=f"TEST_{frequency.upper()}_V1",
        asset_class="crypto_perps",
        frequency=frequency,
        bars_per_year=bpy,
        exploratory_data_path="data/exploratory/dummy.parquet",
        holdout_data_path="data/holdout/dummy.parquet",
        train_start=t0,
        val_start=t0 + timedelta(days=10),
        val_end=t0 + timedelta(days=20),
        holdout_start=t0 + timedelta(days=20),
        holdout_end=t0 + timedelta(days=30),
        n_folds=2,
        label_horizon_bars=horizon,
        embargo_bars=embargo,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )


def _make_split_frames(start, step, n_train: int, gap_delta, n_val: int = 3):
    import polars as pl

    train_ts = [start + step * i for i in range(n_train)]
    val_start = train_ts[-1] + gap_delta
    val_ts = [val_start + step * i for i in range(n_val)]
    train_df = pl.DataFrame(
        {
            "timestamp": train_ts,
            "asset": ["AAA"] * len(train_ts),
            "feat": [float(i) for i in range(len(train_ts))],
            "target": [0.01] * len(train_ts),
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    val_df = pl.DataFrame(
        {
            "timestamp": val_ts,
            "asset": ["AAA"] * len(val_ts),
            "feat": [float(i) for i in range(len(val_ts))],
            "target": [0.02] * len(val_ts),
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    return train_df, val_df


def test_fold_isolation_1d_and_5m_contracts() -> None:
    from datetime import UTC, datetime, timedelta

    from warden.leakage import LookaheadLeakageError, verify_fold_isolation

    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    c_1d = _make_contract("1d", horizon=5, embargo=1)
    assert c_1d.bar_duration() == timedelta(days=1)

    tr_bad_1d, va_bad_1d = _make_split_frames(t0, timedelta(days=1), 5, timedelta(days=1))
    with pytest.raises(LookaheadLeakageError, match="Fold purge/embargo violation"):
        verify_fold_isolation(_StubFoldModel(), tr_bad_1d, va_bad_1d, ["feat"], "target", contract=c_1d)
    with pytest.raises(LookaheadLeakageError, match="Fold purge/embargo violation"):
        verify_fold_isolation(
            _StubFoldModel(), tr_bad_1d, va_bad_1d, ["feat"], "target", label_horizon_bars=5, embargo_bars=1
        )

    tr_ok_1d, va_ok_1d = _make_split_frames(t0, timedelta(days=1), 5, timedelta(days=6))
    preds_1d = verify_fold_isolation(_StubFoldModel(), tr_ok_1d, va_ok_1d, ["feat"], "target", contract=c_1d)
    assert "prediction_value" in preds_1d.columns

    c_5m = _make_contract("5m", horizon=48, embargo=12)
    assert c_5m.bar_duration() == timedelta(minutes=5)

    tr_ok_5m, va_ok_5m = _make_split_frames(t0, timedelta(minutes=5), 5, timedelta(hours=5))
    preds_5m = verify_fold_isolation(_StubFoldModel(), tr_ok_5m, va_ok_5m, ["feat"], "target", contract=c_5m)
    assert "prediction_value" in preds_5m.columns

    tr_bad_5m, va_bad_5m = _make_split_frames(t0, timedelta(minutes=5), 5, timedelta(hours=4, minutes=55))
    with pytest.raises(LookaheadLeakageError, match="Fold purge/embargo violation"):
        verify_fold_isolation(_StubFoldModel(), tr_bad_5m, va_bad_5m, ["feat"], "target", contract=c_5m)


@pytest.mark.parametrize("frequency", ["1m", "5m", "15m", "30m", "1h", "4h", "1d"])
def test_fold_isolation_all_contract_frequencies(frequency: str) -> None:
    from datetime import UTC, datetime

    from warden.contracts import FREQUENCY_BAR_DURATION
    from warden.leakage import LookaheadLeakageError, verify_fold_isolation

    step = FREQUENCY_BAR_DURATION[frequency]
    c = _make_contract(frequency, horizon=4, embargo=2)
    assert c.bar_duration() == step
    t0 = datetime(2025, 1, 1, tzinfo=UTC)

    tr_ok, va_ok = _make_split_frames(t0, step, 4, step * 6)
    preds = verify_fold_isolation(_StubFoldModel(), tr_ok, va_ok, ["feat"], "target", contract=c)
    assert len(preds) == len(va_ok)

    tr_bad, va_bad = _make_split_frames(t0, step, 4, step * 5)
    with pytest.raises(LookaheadLeakageError, match="Fold purge/embargo violation"):
        verify_fold_isolation(_StubFoldModel(), tr_bad, va_bad, ["feat"], "target", contract=c)


def test_fold_isolation_walk_forward_cv_panel_timestamps_and_session_col() -> None:
    from datetime import UTC, datetime, timedelta

    import polars as pl
    from ml4t.diagnostic.splitters import WalkForwardCV

    from warden.leakage import LookaheadLeakageError, verify_fold_isolation

    dates = [
        datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i)
        for i in range(45)
        if (datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i)).weekday() < 5
    ]
    df_1d = pl.DataFrame(
        {
            "timestamp": dates,
            "asset": ["AAA"] * len(dates),
            "session_date": [d.date() for d in dates],
            "feat": [float(i) for i in range(len(dates))],
            "target": [0.01] * len(dates),
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))

    # Friday (index 7: 2025-01-10) -> Monday (index 8: 2025-01-13) is 3 calendar days, 1 bar gap
    fri_idx = next(i for i, d in enumerate(dates) if d.weekday() == 4)
    assert dates[fri_idx + 1] - dates[fri_idx] == timedelta(days=3)
    with pytest.raises(LookaheadLeakageError, match="bar gap 1 < 2"):
        verify_fold_isolation(
            _StubFoldModel(),
            df_1d[: fri_idx + 1],
            df_1d[fri_idx + 1 :],
            ["feat"],
            "target",
            label_horizon_bars=1,
            embargo_bars=1,
            panel_timestamps=df_1d["timestamp"],
        )

    cv_1d = WalkForwardCV(n_splits=3, label_horizon=2, embargo_size=1, calendar=None, timestamp_col="timestamp")
    for tr_idx, va_idx in cv_1d.split(df_1d):
        preds = verify_fold_isolation(
            _StubFoldModel(),
            df_1d[tr_idx],
            df_1d[va_idx],
            ["feat"],
            "target",
            label_horizon_bars=2,
            embargo_bars=1,
            panel_timestamps=df_1d["timestamp"],
            session_col="session_date",
        )
        assert "prediction_value" in preds.columns

    t15 = [datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * i) for i in range(160)]
    df_15m = pl.DataFrame(
        {
            "timestamp": t15,
            "asset": ["AAA"] * len(t15),
            "session_date": [d.date() for d in t15],
            "feat": [float(i) for i in range(len(t15))],
            "target": [0.01] * len(t15),
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    cv_15m_underpurged = WalkForwardCV(
        n_splits=3, label_horizon=4, embargo_size=2, calendar=None, timestamp_col="timestamp"
    )
    tr_u, va_u = next(cv_15m_underpurged.split(df_15m))
    with pytest.raises(LookaheadLeakageError, match="Fold purge/embargo violation"):
        verify_fold_isolation(
            _StubFoldModel(),
            df_15m[tr_u],
            df_15m[va_u],
            ["feat"],
            "target",
            frequency="15m",
            label_horizon_bars=4,
            embargo_bars=2,
            panel_timestamps=df_15m["timestamp"],
        )

    cv_15m = WalkForwardCV(n_splits=3, label_horizon=6, calendar=None, timestamp_col="timestamp")
    for tr_idx, va_idx in cv_15m.split(df_15m):
        preds = verify_fold_isolation(
            _StubFoldModel(),
            df_15m[tr_idx],
            df_15m[va_idx],
            ["feat"],
            "target",
            frequency="15m",
            label_horizon_bars=4,
            embargo_bars=2,
            panel_timestamps=df_15m["timestamp"],
        )
        assert "prediction_value" in preds.columns

    # Same-day intraday splits with valid timestamp gap still fail on overlapping session_col
    with pytest.raises(LookaheadLeakageError, match="Session overlap"):
        verify_fold_isolation(
            _StubFoldModel(),
            df_15m[:10],
            df_15m[16:30],
            ["feat"],
            "target",
            frequency="15m",
            label_horizon_bars=4,
            embargo_bars=2,
            session_col="session_date",
        )

    # Single-timestamp splits without contract/frequency/bar_duration fail closed when required_gap > 0
    with pytest.raises(ValueError, match="single-timestamp|bar duration"):
        verify_fold_isolation(
            _StubFoldModel(),
            df_15m[:1],
            df_15m[10:11],
            ["feat"],
            "target",
            label_horizon_bars=2,
            embargo_bars=1,
        )


def _backtest_fixture(
    tmp_path: Path,
    name: str,
    *,
    panel_prices: list[float] | None = None,
    module_source: str,
    family: str = "rules_ts",
    model_type: str = "rules",
    parameters: dict | None = None,
    param_grid: list[dict] | None = None,
    taker_fee_bps: float = 0.5,
    slippage_bps: float = 0.5,
):
    from datetime import UTC, datetime, timedelta

    import polars as pl
    import yaml

    from warden.contracts import EvaluationContract, HypothesisSpec

    repo = tmp_path / name
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "strategies").mkdir(parents=True, exist_ok=True)

    n_bars = len(panel_prices) if panel_prices is not None else 100
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    if panel_prices is None:
        prices = [100.0 * (1.003 ** i) for i in range(n_bars)]
    else:
        prices = panel_prices

    rows = []
    for asset, mult in (("AAA", 1.0), ("BBB", 0.5)):
        for i, px in enumerate(prices):
            p = px * mult
            p_next = (prices[min(i + 1, n_bars - 1)] * mult)
            rows.append(
                {
                    "timestamp": t0 + timedelta(hours=i),
                    "asset": asset,
                    "open": p,
                    "high": max(p, p_next) * 1.001,
                    "low": min(p, p_next) * 0.999,
                    "close": p_next,
                    "volume": 100.0,
                    "funding_rate": 0.0,
                }
            )
    panel = (
        pl.DataFrame(rows)
        .sort("timestamp", "asset")
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    )
    data_path = repo / "data" / "exploratory" / "bt.parquet"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(data_path)
    sha = hashlib.sha256(data_path.read_bytes()).hexdigest()

    val_idx = 20
    contract = EvaluationContract(
        contract_id=f"BT_{name.upper()}_V1",
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="data/exploratory/bt.parquet",
        holdout_data_path="data/holdout/bt.parquet",
        train_start=t0,
        val_start=t0 + timedelta(hours=val_idx),
        val_end=t0 + timedelta(hours=n_bars),
        holdout_start=t0 + timedelta(hours=n_bars),
        holdout_end=t0 + timedelta(hours=n_bars + 24),
        n_folds=2,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=taker_fee_bps,
        slippage_bps=slippage_bps,
        data_snapshot_sha256=sha,
        data_provenance="verified_market",
    )
    (repo / "contracts" / f"{contract.contract_id.lower()}.yaml").write_text(
        yaml.safe_dump(contract.__dict__), encoding="utf-8"
    )
    (repo / "contracts" / "datasets" / f"{contract.contract_id}.json").write_text(
        json.dumps(
            {
                "contract_id": contract.contract_id,
                "path": contract.exploratory_data_path,
                "sha256": sha,
                "provenance": "verified_market",
                "source": "fixture",
            }
        ),
        encoding="utf-8",
    )
    (repo / "strategies" / f"{name}.py").write_text(module_source, encoding="utf-8")
    reg = ResearchRegistry(repo / "registry" / "registry.db")
    reg.register_contract(contract)
    hyp_id = f"HYP-{name.upper()}"
    reg.register_hypothesis(
        HypothesisSpec(
            hyp_id,
            contract.contract_id,
            f"Test {name}",
            family,
            "Deterministic test mechanism twenty chars",
            "Deterministic test friction twenty chars",
            4,
            "Reject failed gates",
        )
    )
    spec_doc: dict = {
        "contract_id": contract.contract_id,
        "hypothesis_id": hyp_id,
        "strategy_name": f"Strat_{name}",
        "strategy_family": family,
        "model_type": model_type,
        "strategy_module": f"strategies/{name}.py",
        "parameters": parameters or {},
    }
    if param_grid is not None:
        spec_doc["param_grid"] = param_grid
    spec_path = repo / "spec.yaml"
    spec_path.write_text(yaml.safe_dump(spec_doc), encoding="utf-8")
    return repo, reg, spec_path


def test_evaluate_neighbor_stability_sign_safe_and_boundaries() -> None:
    from warden.gates import evaluate_neighbor_stability

    empty = evaluate_neighbor_stability([], 1.2)
    assert empty["neighbor_tested"] is False
    assert empty["passed"] is None
    assert empty["stability"] is None

    # HYP-2026-003B all-negative neighbor trap: must never divide negative/negative into +1.19
    neg = evaluate_neighbor_stability([-1.15, -0.96], -0.94)
    assert neg["neighbor_tested"] is True
    assert neg["passed"] is False
    assert neg["stability"] == 0.0

    neg_vs_pos = evaluate_neighbor_stability([-1.15, -0.96], 1.1)
    assert neg_vs_pos["neighbor_tested"] is True
    assert neg_vs_pos["passed"] is False
    assert neg_vs_pos["stability"] == 0.0

    weak = evaluate_neighbor_stability([0.50, 1.00], 1.20)
    assert weak["neighbor_tested"] is True
    assert weak["passed"] is False
    assert weak["stability"] == pytest.approx(0.50 / 1.20)

    mixed = evaluate_neighbor_stability([-0.20, 1.40], 1.20)
    assert mixed["neighbor_tested"] is True
    assert mixed["passed"] is False
    assert mixed["stability"] == pytest.approx(-0.20 / 1.20)

    strong = evaluate_neighbor_stability([0.85, 1.10], 1.20)
    assert strong["neighbor_tested"] is True
    assert strong["passed"] is True
    assert strong["stability"] == pytest.approx(0.85 / 1.20)


def test_backtest_negative_neighbor_grid_and_empty_grid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("warden.cli.check_worktree", lambda: True)

    neg_strat = """
import numpy as np

def generate_positions(panel_df, contract, scale=1.0, **kwargs):
    n_ts = len(panel_df["timestamp"].unique())
    n_assets = len(panel_df["asset"].unique())
    pos = np.full((n_ts, n_assets), -float(scale), dtype=float)
    return {"positions": pos}
"""
    # Steadily rising panel with tiny jitter -> short position (-scale) has negative Sharpe across all grid points
    rng = np.random.default_rng(11)
    rising = [100.0]
    for _ in range(99):
        rising.append(rising[-1] * (1.002 + rng.normal(0.0, 0.0005)))

    repo_neg, reg_neg, spec_neg = _backtest_fixture(
        tmp_path,
        "neg_grid",
        panel_prices=rising,
        module_source=neg_strat,
        parameters={"scale": 0.3},
        param_grid=[{"scale": 0.2}, {"scale": 0.4}],
    )
    monkeypatch.chdir(repo_neg)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli_main(["backtest", str(spec_neg), "--json"])
    assert rc == 0
    payload_line = buf.getvalue().strip().splitlines()[0]
    res_json = json.loads(payload_line)
    assert res_json["verdict"] == "REVISE"
    assert res_json["param_neighbor_stability"] == 0.0
    diag = json.loads(res_json["diagnostics_json"])
    assert diag["neighbor_tested"] is True
    assert diag["neighbor_stability"]["passed"] is False
    assert "neighbor_stability" in diag["failed_gates"]
    with reg_neg._connect() as conn:
        assert conn.execute("SELECT param_neighbor_stability FROM trials").fetchone()[0] == 0.0

    CONST_SCALE_STRAT = """
import numpy as np

def generate_positions(panel_df, contract, scale=0.4, **kwargs):
    n_ts = len(panel_df["timestamp"].unique())
    n_assets = len(panel_df["asset"].unique())
    pos = np.full((n_ts, n_assets), float(scale), dtype=float)
    return {"positions": pos}
"""
    repo_empty, reg_empty, spec_empty = _backtest_fixture(
        tmp_path,
        "empty_grid",
        panel_prices=rising,
        module_source=CONST_SCALE_STRAT,
        parameters={"scale": 0.4},
        param_grid=None,
    )
    monkeypatch.chdir(repo_empty)
    buf_empty = io.StringIO()
    with contextlib.redirect_stdout(buf_empty):
        rc_empty = cli_main(["backtest", str(spec_empty), "--json"])
    assert rc_empty == 0
    res_empty = json.loads(buf_empty.getvalue().strip().splitlines()[0])
    assert res_empty["param_neighbor_stability"] is None
    diag_empty = json.loads(res_empty["diagnostics_json"])
    assert diag_empty["neighbor_tested"] is False
    with reg_empty._connect() as conn:
        db_val = conn.execute("SELECT param_neighbor_stability FROM trials").fetchone()[0]
    assert db_val is None

    # Explicit YAML `param_grid: null` also records neighbor_tested=False / None
    repo_null, _, spec_null = _backtest_fixture(
        tmp_path,
        "null_grid",
        panel_prices=rising,
        module_source=CONST_SCALE_STRAT,
        parameters={"scale": 0.4},
        param_grid=None,
    )
    spec_null.write_text(spec_null.read_text(encoding="utf-8") + "param_grid: null\n", encoding="utf-8")
    res_null = ExperimentRunner(repo_root=repo_null).run_spec(spec_null)
    assert res_null.param_neighbor_stability is None
    assert json.loads(res_null.diagnostics_json)["neighbor_tested"] is False


def test_backtest_failed_gates_covers_all_eight_keys_and_candidate_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("warden.cli.check_worktree", lambda: True)
    rng = np.random.default_rng(23)
    rising = [100.0]
    for _ in range(119):
        rising.append(rising[-1] * (1.003 + rng.normal(0.0, 0.0004)))

    good_strat = """
import numpy as np

def generate_positions(panel_df, contract, scale=0.3, **kwargs):
    n_ts = len(panel_df["timestamp"].unique())
    n_assets = len(panel_df["asset"].unique())
    pos = np.full((n_ts, n_assets), float(scale), dtype=float)
    return {"positions": pos}
"""
    repo_ok, _, spec_ok = _backtest_fixture(
        tmp_path,
        "cand_ok",
        panel_prices=rising,
        module_source=good_strat,
        parameters={"scale": 0.3},
        param_grid=[{"scale": 0.2}, {"scale": 0.4}],
    )
    res_ok = ExperimentRunner(repo_root=repo_ok).run_spec(spec_ok)
    assert res_ok.verdict == "CANDIDATE_FOR_QUALIFICATION"
    diag_ok = json.loads(res_ok.diagnostics_json)
    assert diag_ok["failed_gates"] == []
    assert diag_ok["neighbor_tested"] is True
    assert diag_ok["neighbor_stability"]["passed"] is True

    # 2. Weak/losing neighbor + cost/latency sensitive strategy triggers cost_2x, latency_plus1, neighbor_stability
    churn_strat = """
import numpy as np

def generate_positions(panel_df, contract, mode="ok", **kwargs):
    n_ts = len(panel_df["timestamp"].unique())
    n_assets = len(panel_df["asset"].unique())
    pos = np.zeros((n_ts, n_assets), dtype=float)
    if mode == "ok":
        pos[::2] = 0.4
        pos[1::2] = -0.25
    else:
        pos[:] = -0.4
    return {"positions": pos}
"""
    cost_prices = [100.0]
    for _ in range(119):
        cost_prices.append(cost_prices[-1] * (1.0005 + rng.normal(0.0, 0.004)))
    repo_cost, _, spec_cost = _backtest_fixture(
        tmp_path,
        "fail_cost_nb",
        panel_prices=cost_prices,
        module_source=churn_strat,
        parameters={"mode": "ok"},
        param_grid=[{"mode": "bad"}],
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )
    res_cost = ExperimentRunner(repo_root=repo_cost).run_spec(spec_cost)
    assert res_cost.verdict == "REVISE"
    fg_cost = set(json.loads(res_cost.diagnostics_json)["failed_gates"])
    assert {"net_sharpe", "dsr", "cost_2x", "latency_plus1", "neighbor_stability", "stage1_rules"} <= fg_cost

    # 3. Outlier-dominated strategy: headline net_sharpe > 1.0 while trimmed_sharpe < 0.0
    outlier_prices = [100.0]
    for i in range(1, 120):
        # Slight steady bleed most bars, with 5 sharp positive spikes in validation window (bars 20..120)
        if i in (30, 50, 70, 90, 110):
            outlier_prices.append(outlier_prices[-1] * 1.015)
        else:
            step = -0.00025 if i % 2 == 0 else -0.00015
            outlier_prices.append(outlier_prices[-1] * (1.0 + step))

    repo_trim, _, spec_trim = _backtest_fixture(
        tmp_path,
        "fail_trim",
        panel_prices=outlier_prices,
        module_source=good_strat,
        parameters={"scale": 0.3},
        taker_fee_bps=0.01,
        slippage_bps=0.01,
    )
    res_trim = ExperimentRunner(repo_root=repo_trim).run_spec(spec_trim)
    diag_trim = json.loads(res_trim.diagnostics_json)
    assert res_trim.net_sharpe > 1.0
    assert diag_trim["trimmed_sharpe"] < 0.0
    assert "trimmed_sharpe" in diag_trim["failed_gates"]
    assert res_trim.verdict == "REVISE"

    # 4. cross_sectional_ml early rejection triggers ic_hac and neighbor_tested=False
    ml_fail_strat = """
import numpy as np

def generate_positions(panel_df, contract, **kwargs):
    n_ts = len(panel_df["timestamp"].unique())
    n_assets = len(panel_df["asset"].unique())
    return {"positions": np.zeros((n_ts, n_assets)), "ic_series": np.zeros(max(n_ts - 4, 0))}
"""
    repo_ml, _, spec_ml = _backtest_fixture(
        tmp_path,
        "fail_ic",
        panel_prices=rising,
        module_source=ml_fail_strat,
        family="cross_sectional_ml",
        model_type="ridge",
    )
    res_ml = ExperimentRunner(repo_root=repo_ml).run_spec(spec_ml)
    assert res_ml.verdict == "REJECTED_EARLY"
    assert res_ml.param_neighbor_stability is None
    diag_ml = json.loads(res_ml.diagnostics_json)
    assert diag_ml["neighbor_tested"] is False
    assert diag_ml["failed_gates"] == ["ic_hac"]


def test_check_revision_prereg_uses_failed_gates_and_refuses_untested_neighbor(tmp_path: Path) -> None:
    import runpy
    import sqlite3

    import yaml

    gate = runpy.run_path(str(Path("scripts") / "check_revision_prereg.py"))
    axis_fails = gate["axis_fails"]
    check = gate["check"]
    admit = gate["admit"]

    # Direct axis_fails reads diagnostics_json["failed_gates"] without parsing prose notes
    fails_cost, _ = axis_fails(
        "2x_cost_survival",
        {"diagnostics_json": json.dumps({"failed_gates": ["cost_2x"]}), "notes": "prose ignored"},
        [],
    )
    assert fails_cost is True

    passes_cost, _ = axis_fails(
        "2x_cost_survival",
        {"diagnostics_json": json.dumps({"failed_gates": ["dsr"]}), "notes": "2x cost failed in prose"},
        [],
    )
    assert passes_cost is False

    fails_lat, _ = axis_fails(
        "latency_survival",
        {"diagnostics_json": json.dumps({"failed_gates": ["latency_plus1"]}), "notes": ""},
        [],
    )
    assert fails_lat is True

    fails_nb, _ = axis_fails(
        "param_neighbour_stability",
        {
            "diagnostics_json": json.dumps({"neighbor_tested": True, "failed_gates": ["neighbor_stability"]}),
            "param_neighbor_stability": 0.0,
            "notes": "",
        },
        [],
    )
    assert fails_nb is True

    # Untested neighbor grid (neighbor_tested=False, param_neighbor_stability=None) must be refused cleanly
    untested_nb, detail = axis_fails(
        "param_neighbour_stability",
        {
            "diagnostics_json": json.dumps({"neighbor_tested": False, "failed_gates": ["dsr"]}),
            "param_neighbor_stability": None,
            "notes": "",
        },
        [],
    )
    assert untested_nb is False
    assert "neighbor_tested=False" in detail

    # Legacy fallback preserved when failed_gates is absent
    legacy_cost, _ = axis_fails(
        "2x_cost_survival",
        {"diagnostics_json": "{}", "stress_2x_cost_sharpe": 0.30},
        [],
    )
    assert legacy_cost is True

    # End-to-end check() & admit() against a SQLite registry
    prereg = tmp_path / "prereg.md"
    prereg_doc = {
        "hypothesis_id": "HYP-2026-901",
        "contract_id": "MULTI_ASSET_1D_V1",
        "revisions": [
            {
                "run_id": "RUN-HYP901-002",
                "axis": "param_neighbour_stability",
                "parameters": {"fast": 32},
                "rationale": "address neighbor stability",
            },
            {
                "run_id": "RUN-HYP901-003",
                "axis": "2x_cost_survival",
                "parameters": {"fast": 48},
                "rationale": "address 2x cost",
            },
        ],
    }
    prereg.write_text("# prereg\n\n```yaml\n" + yaml.safe_dump(prereg_doc) + "```\n", encoding="utf-8")
    spec = tmp_path / "rev2.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "hypothesis_id": "HYP-2026-901",
                "contract_id": "MULTI_ASSET_1D_V1",
                "run_id": "RUN-HYP901-002",
                "parameters": {"fast": 32},
            }
        ),
        encoding="utf-8",
    )

    def _db(folder: str, *, diag: dict, stability: float | None) -> Path:
        db = tmp_path / folder / "registry.db"
        ResearchRegistry(db)
        con = sqlite3.connect(db)
        con.execute(
            "INSERT INTO hypotheses (hypothesis_id,contract_id,title,strategy_family,economic_mechanism,"
            "counterparty_or_friction,expected_holding_bars,falsification_criteria,trials_used) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("HYP-2026-901", "MULTI_ASSET_1D_V1", "Prereg test", "rules_ts", "mechanism text twenty",
             "friction text twenty", 10, "criteria", 1),
        )
        con.execute(
            "INSERT INTO trials (run_id,hypothesis_id,contract_id,trial_number_in_hyp,stage_reached,"
            "verdict,net_sharpe,stress_2x_cost_sharpe,delay_plus1_sharpe,breakeven_cost_bps,"
            "param_neighbor_stability,diagnostics_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("RUN-HYP901-001", "HYP-2026-901", "MULTI_ASSET_1D_V1", 1, "STAGE_3_STRESS", "REVISE",
             0.8, 0.7, 0.2, 10.0, stability, json.dumps(diag), 1.0),
        )
        con.commit()
        con.close()
        return db

    db_untested = _db("untested", diag={"neighbor_tested": False, "failed_gates": ["net_sharpe"]}, stability=None)
    assert any("does not fail it" in e for e in check(prereg, spec, db_untested))
    assert any("does not fail it" in e for e in admit(prereg, "RUN-HYP901-002", db_untested))

    db_failed_nb = _db(
        "failed_nb",
        diag={"neighbor_tested": True, "failed_gates": ["neighbor_stability"]},
        stability=0.0,
    )
    assert check(prereg, spec, db_failed_nb) == []
    assert admit(prereg, "RUN-HYP901-002", db_failed_nb) == []
