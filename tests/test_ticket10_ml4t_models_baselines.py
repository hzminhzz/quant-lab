"""Ticket 10 tests: ml4t-models linear/IPCA baselines and deep-model guardrails."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from warden.cli import main as cli_main
from warden.contracts import EvaluationContract
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner, LinearBaselineRequiredError


def _hash_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_ragged_cross_sectional_panel(
    *,
    n_bars: int = 180,
    seed: int = 42,
    signal_strength: float = 0.012,
    noise_scale: float = 0.004,
) -> pl.DataFrame:
    """Create a synthetic ragged crypto panel with mid-window listings and delistings.

    Asset returns follow a causal short-horizon momentum/mean-reversion structure driven
    by lagged bar moves plus mild idiosyncratic noise so linear/IPCA models achieve
    positive Stage-1 IC while exercising ragged universe changes.
    """
    rng = np.random.default_rng(seed)
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    # 10 assets: 6 full-span, 2 late listings (bars 55..n_bars), 2 early delistings (bars 0..125)
    windows: list[tuple[str, int, int, float]] = [
        ("BTC", 0, n_bars, 1.0),
        ("ETH", 0, n_bars, -1.0),
        ("SOL", 0, n_bars, 0.8),
        ("AVAX", 0, n_bars, -0.8),
        ("LINK", 0, n_bars, 0.6),
        ("UNI", 0, n_bars, -0.6),
        ("LATE_A", 55, n_bars, 0.9),
        ("LATE_B", 70, n_bars, -0.9),
        ("DELIST_A", 0, 125, 0.7),
        ("DELIST_B", 0, 110, -0.7),
    ]
    rows: list[dict] = []
    # Pre-generate persistent latent factor + regime sign per bar so causal roc_2/roc_6 predicts future return
    factor_wave = np.sin(np.linspace(0.0, 10.0 * np.pi, n_bars + 4))
    for asset_idx, (asset, start_i, end_i, beta) in enumerate(windows):
        price = 100.0 + asset_idx * 5.0
        prev_ret = 0.0
        for t in range(start_i, end_i):
            ts = t0 + timedelta(hours=t)
            # Autocorrelated causal return component -> past ROC predicts next-bar open-to-open return
            causal_drift = signal_strength * beta * factor_wave[t // 3] + 0.35 * prev_ret
            ret = float(causal_drift + rng.normal(0.0, noise_scale))
            o = price
            c = max(1.0, o * (1.0 + ret))
            hi = max(o, c) * 1.002
            lo = min(o, c) * 0.998
            rows.append(
                {
                    "timestamp": ts,
                    "asset": asset,
                    "open": float(o),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(c),
                    "volume": float(1000.0 + rng.uniform(0.0, 400.0)),
                    "funding_rate": 0.0001 if beta > 0 else -0.0001,
                }
            )
            prev_ret = ret
            price = c
    return (
        pl.DataFrame(rows)
        .sort("timestamp", "asset")
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    )


def _setup_repo(
    tmp_path: Path,
    *,
    name: str,
    panel: pl.DataFrame,
    n_folds: int = 3,
    label_horizon_bars: int = 2,
    embargo_bars: int = 1,
    execution_delay_bars: int = 1,
    taker_fee_bps: float = 0.5,
    slippage_bps: float = 0.5,
) -> tuple[Path, EvaluationContract]:
    from warden_ops.integrity import generate_manifest

    repo = tmp_path / name
    (repo / "src" / "warden").mkdir(parents=True, exist_ok=True)
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "strategies" / "candidates").mkdir(parents=True, exist_ok=True)
    (repo / "strategies" / "baselines").mkdir(parents=True, exist_ok=True)
    (repo / "experiments" / "active").mkdir(parents=True, exist_ok=True)
    (repo / "cache" / "features").mkdir(parents=True, exist_ok=True)
    (repo / "AGENTS.md").write_text("# test\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='t10'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version=1\n", encoding="utf-8")

    ts = sorted(panel["timestamp"].unique().to_list())
    n_bars = len(ts)
    train_start = ts[0]
    val_start = ts[max(24, n_bars // 3)]
    val_end = ts[-1] + timedelta(hours=1)
    holdout_start = val_end
    holdout_end = holdout_start + timedelta(hours=max(40, n_bars))

    data_rel = "data/exploratory/panel.parquet"
    data_path = repo / data_rel
    data_path.parent.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(data_path)
    sha = _hash_bytes(data_path)

    holdout_rel = "data/holdout/panel_holdout.parquet"
    holdout_path = repo / holdout_rel
    holdout_path.parent.mkdir(parents=True, exist_ok=True)
    holdout_panel = panel.with_columns(
        (pl.col("timestamp") + timedelta(hours=n_bars + 2)).alias("timestamp")
    )
    holdout_panel.write_parquet(holdout_path)
    h_sha = _hash_bytes(holdout_path)

    cid = "T10_CRYPTO_1H"
    contract = EvaluationContract(
        contract_id=cid,
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path=data_rel,
        holdout_data_path=holdout_rel,
        train_start=train_start,
        val_start=val_start,
        val_end=val_end,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        n_folds=n_folds,
        label_horizon_bars=label_horizon_bars,
        embargo_bars=embargo_bars,
        execution_delay_bars=execution_delay_bars,
        taker_fee_bps=taker_fee_bps,
        slippage_bps=slippage_bps,
        data_snapshot_sha256=sha,
        data_provenance="verified_market",
    )
    (repo / "contracts" / f"{cid.lower()}.yaml").write_text(
        yaml.safe_dump(contract.__dict__), encoding="utf-8"
    )
    (repo / "contracts" / "datasets" / f"{cid}.json").write_text(
        json.dumps(
            {
                "contract_id": cid,
                "path": data_rel,
                "sha256": sha,
                "holdout_path": holdout_rel,
                "holdout_sha256": h_sha,
                "provenance": "verified_market",
                "source": "t10_test",
            }
        ),
        encoding="utf-8",
    )
    reg = ResearchRegistry(repo / "registry" / "registry.db")
    reg.register_contract(contract)
    generate_manifest(repo)
    return repo, contract


def _register_hyp_and_spec(
    repo: Path,
    contract: EvaluationContract,
    *,
    hyp_id: str,
    strat_name: str,
    model_type: str,
    strat_module: str,
    parameters: dict | None = None,
    run_id: str | None = None,
    model_class: str | None = None,
    expected_holding_bars: int = 8,
    register_hyp: bool = True,
) -> Path:
    if register_hyp:
        hyp_md = repo / ".workspace" / "hypotheses" / f"{hyp_id}.md"
        hyp_md.parent.mkdir(parents=True, exist_ok=True)
        hyp_md.write_text(
            f"---\n"
            f"hypothesis_id: {hyp_id}\n"
            f"contract_id: {contract.contract_id}\n"
            f"title: Ticket 10 test {hyp_id}\n"
            f"strategy_family: cross_sectional_ml\n"
            f"economic_mechanism: Causal cross-sectional return prediction with turnover control\n"
            f"counterparty_or_friction: Taker fee and slippage paid to passive liquidity providers\n"
            f"expected_holding_bars: {expected_holding_bars}\n"
            f"falsification_criteria: Reject if Stage 1 IC or Stage 3 net Sharpe fails\n"
            f"---\n",
            encoding="utf-8",
        )
        assert cli_main(["register-hypothesis", str(hyp_md), "--json"]) == 0
    spec_payload: dict = {
        "contract_id": contract.contract_id,
        "hypothesis_id": hyp_id,
        "strategy_name": strat_name,
        "strategy_family": "cross_sectional_ml",
        "model_type": model_type,
        "strategy_module": strat_module,
        "parameters": parameters or {},
    }
    if model_class is not None:
        spec_payload["model_class"] = model_class
    if run_id is not None:
        spec_payload["run_id"] = run_id
    spec_yaml = repo / "experiments" / "active" / f"{strat_name.lower()}.yaml"
    spec_yaml.write_text(yaml.safe_dump(spec_payload), encoding="utf-8")
    return spec_yaml


def _copy_repo_module(
    repo: Path,
    src_rel: str,
    dest_rel: str | None = None,
    *,
    include_ridge_dep: bool = True,
) -> None:
    repo_src = Path(__file__).resolve().parents[1]
    copies: list[tuple[str, str]] = [(src_rel, dest_rel or src_rel)]
    if include_ridge_dep:
        copies.append(("strategies/baselines/causal_ridge_1h.py", "strategies/baselines/causal_ridge_1h.py"))
    for src_p, dst_p in copies:
        src = repo_src / src_p
        dest = repo / dst_p
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def test_ml4t_models_pinned_without_pytorch() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject_text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    lock_text = (repo_root / "uv.lock").read_text(encoding="utf-8")
    assert "ml4t-models" in pyproject_text
    assert "[deep]" not in pyproject_text
    assert 'name = "torch"' not in lock_text


@pytest.mark.parametrize(
    ("model_type", "model_class"),
    [
        ("CAEModel", None),
        ("SAEModel", "SAEModel"),
        ("StochasticDiscountFactorModel", None),
        ("DeepPortfolioModel", "DeepPortfolioModel"),
    ],
)
def test_deep_model_rejected_before_linear_baseline_and_budget_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_type: str,
    model_class: str | None,
) -> None:
    panel = _make_ragged_cross_sectional_panel(n_bars=140, seed=7)
    repo, contract = _setup_repo(tmp_path, name=f"deep_block_{model_type.lower()}", panel=panel)
    # Deliberately name module without "baseline" or "ridge"
    strat_rel = "strategies/candidates/neural_alpha.py"
    _copy_repo_module(repo, "strategies/baselines/causal_ridge_1h.py", strat_rel, include_ridge_dep=False)
    monkeypatch.chdir(repo)

    spec_yaml = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T10-DEEP-1",
        strat_name=f"Attempt_{model_type}",
        model_type=model_type,
        model_class=model_class,
        strat_module=strat_rel,
    )

    runner = ExperimentRunner(repo_root=repo)
    with pytest.raises(LinearBaselineRequiredError, match="Linear baseline required"):
        runner.run_spec(spec_yaml)

    # CLI backtest also fails closed with non-zero exit code
    assert cli_main(["backtest", str(spec_yaml), "--json"]) == 1

    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        trials_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T10-DEEP-1'"
        ).fetchone()[0]
        assert trials_used == 0


def test_deep_model_rejected_when_linear_baseline_fails_cost_breakeven_hurdle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Weak signal + high fee/slippage ensures linear baseline passes IC/exec or logs trial
    # with breakeven_cost_bps <= contract cost_bps
    panel = _make_ragged_cross_sectional_panel(
        n_bars=150, seed=13, signal_strength=0.0005, noise_scale=0.008
    )
    repo, contract = _setup_repo(
        tmp_path,
        name="deep_cost_hurdle",
        panel=panel,
        taker_fee_bps=25.0,
        slippage_bps=25.0,
    )
    # Use a causal linear module that emits valid IC, or insert a logged linear baseline trial
    # whose breakeven_cost_bps is below contract cost_bps (50 bps).
    strat_rel = "strategies/candidates/linear_alpha_entry.py"
    _copy_repo_module(repo, "strategies/baselines/causal_ridge_1h.py", strat_rel, include_ridge_dep=False)
    monkeypatch.chdir(repo)

    # Register hypothesis and run a legitimate linear trial first, or verify directly if breakeven <= cost
    lin_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T10-HURDLE",
        strat_name="LinearPortfolioEntry",
        model_type="LinearFeaturePortfolioModel",
        strat_module=strat_rel,
        run_id="RUN-T10-LIN-FAILCOST",
    )
    runner = ExperimentRunner(repo_root=repo)
    lin_res = runner.run_spec(lin_spec)
    cost_bps = contract.total_one_way_cost_decimal() * 1e4
    assert lin_res.breakeven_cost_bps <= cost_bps

    deep_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T10-HURDLE",
        strat_name="DeepCAEEntry",
        model_type="CAEModel",
        strat_module=strat_rel,
        register_hyp=False,
    )
    with pytest.raises(LinearBaselineRequiredError, match="cost-breakeven"):
        runner.run_spec(deep_spec)

    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        trials_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T10-HURDLE'"
        ).fetchone()[0]
        assert trials_used == 1


def test_linear_first_gate_accepts_ml4t_linear_or_ipca_without_ridge_in_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _make_ragged_cross_sectional_panel(
        n_bars=160, seed=21, signal_strength=0.015, noise_scale=0.002
    )
    repo, contract = _setup_repo(
        tmp_path,
        name="linear_meta_unlock",
        panel=panel,
        taker_fee_bps=0.5,
        slippage_bps=0.5,
    )
    # Neither strategy_name nor strategy_module contains 'Ridge' or 'baseline'!
    lin_rel = "strategies/candidates/custom_portfolio_entry.py"
    _copy_repo_module(repo, "strategies/baselines/causal_ridge_1h.py", lin_rel, include_ridge_dep=False)
    monkeypatch.chdir(repo)

    lin_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T10-UNLOCK",
        strat_name="PortfolioFactorEntry",
        model_type="IPCAModel",
        strat_module=lin_rel,
        run_id="RUN-T10-IPCA-UNLOCK",
    )
    runner = ExperimentRunner(repo_root=repo)
    lin_res = runner.run_spec(lin_spec)
    assert lin_res.verdict != "REJECTED_EARLY"
    assert lin_res.breakeven_cost_bps > contract.total_one_way_cost_decimal() * 1e4

    # Check structured model_metadata was logged in diagnostics_json
    diag = json.loads(lin_res.diagnostics_json)
    assert diag["model_metadata"]["model_tier"] == "linear"
    assert diag["model_metadata"]["model_type"] == "IPCAModel"

    deep_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T10-UNLOCK",
        strat_name="DeepFollowUp",
        model_type="DeepPortfolioModel",
        strat_module=lin_rel,
        run_id="RUN-T10-DEEP-OK",
        register_hyp=False,
    )
    deep_res = runner.run_spec(deep_spec)
    assert deep_res.run_id == "RUN-T10-DEEP-OK"


def test_linear_feature_portfolio_baseline_ragged_panel_and_turnover_penalty_reduces_churn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _make_ragged_cross_sectional_panel(
        n_bars=180, seed=31, signal_strength=0.014, noise_scale=0.004
    )
    repo, contract = _setup_repo(
        tmp_path,
        name="linear_portfolio_ragged",
        panel=panel,
        n_folds=3,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=0.5,
        slippage_bps=0.5,
    )
    strat_rel = "strategies/baselines/causal_linear_portfolio_1h.py"
    _copy_repo_module(repo, strat_rel)
    monkeypatch.chdir(repo)

    spec_unpen = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T10-LP-0",
        strat_name="LinearPortfolioUnpenalized",
        model_type="LinearFeaturePortfolioModel",
        strat_module=strat_rel,
        parameters={"ridge_alpha": 10.0, "turnover_penalty": 0.0, "rebalance_every": 1},
        run_id="RUN-T10-LP-0",
    )
    assert cli_main(["verify", str(spec_unpen), "--json"]) == 0
    assert cli_main(["screen", str(spec_unpen), "--json"]) == 0
    assert cli_main(["backtest", str(spec_unpen), "--json"]) == 0

    spec_pen = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T10-LP-PEN",
        strat_name="LinearPortfolioPenalized",
        model_type="LinearFeaturePortfolioModel",
        strat_module=strat_rel,
        parameters={"ridge_alpha": 10.0, "turnover_penalty": 4.0, "rebalance_every": 1},
        run_id="RUN-T10-LP-PEN",
    )
    assert cli_main(["verify", str(spec_pen), "--json"]) == 0
    assert cli_main(["screen", str(spec_pen), "--json"]) == 0
    assert cli_main(["backtest", str(spec_pen), "--json"]) == 0

    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        rows = {
            r[0]: (r[1], float(r[2]))
            for r in conn.execute(
                "SELECT run_id, verdict, annualized_turnover FROM trials "
                "WHERE run_id IN ('RUN-T10-LP-0', 'RUN-T10-LP-PEN')"
            ).fetchall()
        }
    assert set(rows) == {"RUN-T10-LP-0", "RUN-T10-LP-PEN"}
    assert rows["RUN-T10-LP-0"][0] != "REJECTED_EARLY"
    assert rows["RUN-T10-LP-PEN"][0] != "REJECTED_EARLY"
    assert rows["RUN-T10-LP-PEN"][1] < rows["RUN-T10-LP-0"][1]


def test_ipca_baseline_ragged_panel_verify_screen_backtest_and_causal_factor_premia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _make_ragged_cross_sectional_panel(
        n_bars=180, seed=41, signal_strength=0.015, noise_scale=0.003
    )
    repo, contract = _setup_repo(
        tmp_path,
        name="ipca_ragged",
        panel=panel,
        n_folds=3,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=0.5,
        slippage_bps=0.5,
    )
    strat_rel = "strategies/baselines/causal_ipca_1h.py"
    _copy_repo_module(repo, strat_rel)
    monkeypatch.chdir(repo)

    ipca_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T10-IPCA",
        strat_name="ConditionalIPCAEntry",
        model_type="IPCAModel",
        strat_module=strat_rel,
        parameters={"n_factors": 2, "max_iter": 150, "rebalance_every": 2, "top_k": 3},
        run_id="RUN-T10-IPCA-1",
    )
    assert cli_main(["verify", str(ipca_spec), "--json"]) == 0
    assert cli_main(["screen", str(ipca_spec), "--json"]) == 0
    assert cli_main(["backtest", str(ipca_spec), "--json"]) == 0

    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        row = conn.execute(
            "SELECT verdict, breakeven_cost_bps, diagnostics_json FROM trials WHERE run_id='RUN-T10-IPCA-1'"
        ).fetchone()
    assert row is not None
    assert row[0] != "REJECTED_EARLY"
    diag = json.loads(row[2])
    assert diag["model_metadata"]["model_class"] == "IPCAModel"
    assert diag["model_metadata"]["model_tier"] == "linear"

    # Empirical Asset Pricing causality check: ExpandingMeanFactorForecaster receives strictly
    # training-fold factor returns (val_batch has returns=None), and val predictions are invariant
    # to adding/changing future validation timestamps.
    from warden.protocols import iter_causal_model_folds, prepare_causal_model_dataset
    from warden.strategy_loader import load_module_from_path

    mod = load_module_from_path(repo / strat_rel)
    ts = sorted(panel["timestamp"].unique().to_list())
    v_start = ts.index(contract.val_start)
    _, model_df, _, feature_cols, valid_train_mask = prepare_causal_model_dataset(
        mod, panel, contract, ts
    )
    est = mod.build_estimator({"n_factors": 2, "max_iter": 150})
    train_df, val_feat = next(
        iter_causal_model_folds(
            model_df, feature_cols, valid_train_mask, ts, contract, v_start, len(ts)
        )
    )
    assert "forward_label" not in val_feat.columns
    val_ts = sorted(val_feat["timestamp"].unique().to_list())
    mid_ts = val_ts[len(val_ts) // 2]
    full_pred = est.fit_predict_fold(train_df, val_feat, feature_cols, "forward_label", seed=42)
    trunc_pred = est.fit_predict_fold(
        train_df, val_feat.filter(pl.col("timestamp") <= mid_ts), feature_cols, "forward_label", seed=42
    )
    full_pref = full_pred.filter(pl.col("timestamp") <= mid_ts).sort("timestamp", "asset")
    trunc_sorted = trunc_pred.sort("timestamp", "asset")
    assert np.allclose(
        full_pref["prediction_value"].to_numpy(),
        trunc_sorted["prediction_value"].to_numpy(),
        rtol=1e-10,
        atol=1e-10,
    )

    # Confirm logged IPCAModel trial clears cost-breakeven and unlocks Linear-First Gate for a deep model
    assert float(row[1]) > contract.total_one_way_cost_decimal() * 1e4
    deep_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T10-IPCA",
        strat_name="DeepAfterIPCA",
        model_type="SAEModel",
        strat_module=strat_rel,
        run_id="RUN-T10-DEEP-AFTER-IPCA",
        register_hyp=False,
    )
    assert cli_main(["backtest", str(deep_spec), "--json"]) == 0
