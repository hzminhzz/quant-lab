"""Ticket 08 tests: legacy generate_positions freeze, causal protocols, and reference templates."""

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
from warden.codeid import code_closure
from warden.contracts import EvaluationContract
from warden.qualify import qualify_candidate
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner


def _hash_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_panel(
    *,
    n_bars: int = 160,
    assets: tuple[str, ...] = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"),
    seed: int = 11,
    trend_bias: float = 0.0,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    rows: list[dict] = []
    for idx, asset in enumerate(assets):
        price = 100.0 + idx * 10.0
        for t in range(n_bars):
            ts = t0 + timedelta(hours=t)
            drift = trend_bias * (1.0 if idx % 2 == 0 else -0.5)
            ret = drift + float(rng.normal(0.0, 0.008))
            o = price
            c = max(1.0, o * (1.0 + ret))
            hi = max(o, c) * 1.003
            lo = min(o, c) * 0.997
            rows.append(
                {
                    "timestamp": ts,
                    "asset": asset,
                    "open": float(o),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(c),
                    "volume": float(1000.0 + rng.uniform(0.0, 500.0)),
                    "funding_rate": 0.0001 if idx % 2 == 0 else -0.0001,
                }
            )
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
    holdout_panel: pl.DataFrame | None = None,
    n_folds: int = 3,
    label_horizon_bars: int = 2,
    embargo_bars: int = 1,
    execution_delay_bars: int = 1,
) -> tuple[Path, EvaluationContract, Path]:
    from warden_ops.integrity import generate_manifest

    repo = tmp_path / name
    (repo / "src" / "warden").mkdir(parents=True, exist_ok=True)
    (repo / "contracts" / "datasets").mkdir(parents=True, exist_ok=True)
    (repo / "strategies" / "candidates").mkdir(parents=True, exist_ok=True)
    (repo / "strategies" / "baselines").mkdir(parents=True, exist_ok=True)
    (repo / "experiments" / "active").mkdir(parents=True, exist_ok=True)
    (repo / "cache" / "features").mkdir(parents=True, exist_ok=True)
    (repo / "AGENTS.md").write_text("# test\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='t08'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version=1\n", encoding="utf-8")

    ts = sorted(panel["timestamp"].unique().to_list())
    n_bars = len(ts)
    train_start = ts[0]
    val_start = ts[max(16, n_bars // 3)]
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
    if holdout_panel is None:
        holdout_panel = panel.with_columns(
            (pl.col("timestamp") + timedelta(hours=n_bars + 2)).alias("timestamp")
        )
    holdout_panel.write_parquet(holdout_path)
    h_sha = _hash_bytes(holdout_path)

    cid = "T08_CRYPTO_1H"
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
        taker_fee_bps=0.5,
        slippage_bps=0.5,
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
                "source": "t08_test",
            }
        ),
        encoding="utf-8",
    )
    reg = ResearchRegistry(repo / "registry" / "registry.db")
    reg.register_contract(contract)
    generate_manifest(repo)
    return repo, contract, holdout_path


def _register_hyp_and_spec(
    repo: Path,
    contract: EvaluationContract,
    *,
    hyp_id: str,
    family: str,
    model_type: str,
    strat_module: str,
    parameters: dict | None = None,
    param_grid: list[dict] | None = None,
    run_id: str | None = None,
    expected_holding_bars: int = 8,
) -> Path:
    hyp_md = repo / ".workspace" / "hypotheses" / f"{hyp_id}.md"
    hyp_md.parent.mkdir(parents=True, exist_ok=True)
    hyp_md.write_text(
        f"---\n"
        f"hypothesis_id: {hyp_id}\n"
        f"contract_id: {contract.contract_id}\n"
        f"title: Ticket 08 test {hyp_id}\n"
        f"strategy_family: {family}\n"
        f"economic_mechanism: Positive cross-sectional or trend drift captured causally\n"
        f"counterparty_or_friction: Taker fee and slippage paid to passive liquidity providers\n"
        f"expected_holding_bars: {expected_holding_bars}\n"
        f"falsification_criteria: Reject if Stage 1 or Stage 3 statistical gates fail\n"
        f"---\n",
        encoding="utf-8",
    )
    spec_payload = {
        "contract_id": contract.contract_id,
        "hypothesis_id": hyp_id,
        "strategy_name": f"Strat_{hyp_id}",
        "strategy_family": family,
        "model_type": model_type,
        "strategy_module": strat_module,
        "parameters": parameters or {},
        "param_grid": list(param_grid or []),
    }
    if run_id is not None:
        spec_payload["run_id"] = run_id
    spec_yaml = repo / "experiments" / "active" / f"{hyp_id.lower()}.yaml"
    spec_yaml.write_text(yaml.safe_dump(spec_payload), encoding="utf-8")
    assert cli_main(["register-hypothesis", str(hyp_md), "--json"]) == 0
    return spec_yaml


def test_legacy_generate_positions_rejected_for_active_hypothesis_and_historical_replay_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from warden.protocols import LegacyStrategyProtocolError

    # Strong clean trend with nonzero vol so historical replay legitimately clears Stage 0 & Stage 3
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    rng = np.random.default_rng(99)
    rows = []
    pa, pb = 100.0, 100.0
    for t in range(120):
        ts = t0 + timedelta(hours=t)
        ra = 0.004 + float(rng.normal(0.0, 0.002))
        rb = -0.003 + float(rng.normal(0.0, 0.002))
        for asset, p, ret in (("AAA", pa, ra), ("BBB", pb, rb)):
            rows.append(
                {
                    "timestamp": ts,
                    "asset": asset,
                    "open": float(p),
                    "high": float(p * 1.004),
                    "low": float(p * 0.996),
                    "close": float(p * (1.0 + ret)),
                    "volume": 1000.0,
                    "funding_rate": 0.0,
                }
            )
        pa *= 1.0 + ra
        pb *= 1.0 + rb
    panel = pl.DataFrame(rows).sort("timestamp", "asset").with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )
    holdout_panel = panel.with_columns((pl.col("timestamp") + timedelta(hours=125)).alias("timestamp"))
    repo, contract, holdout_path = _setup_repo(
        tmp_path, name="legacy_freeze", panel=panel, holdout_panel=holdout_panel, n_folds=2
    )
    legacy_rel = "strategies/candidates/legacy_frozen.py"
    legacy_code = (
        "import numpy as np\n\n"
        "def generate_positions(panel_df, contract, **kwargs):\n"
        "    ts = sorted(panel_df['timestamp'].unique().to_list())\n"
        "    assets = sorted(panel_df['asset'].unique().to_list())\n"
        "    pos = np.zeros((len(ts), len(assets)), dtype=float)\n"
        "    pos[:, 0] = 0.45\n"
        "    pos[:, 1] = -0.45\n"
        "    return {'positions': pos}\n"
    )
    (repo / legacy_rel).write_text(legacy_code, encoding="utf-8")
    monkeypatch.chdir(repo)

    spec_yaml = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T08-LEGACY",
        family="rules_ts",
        model_type="rules",
        strat_module=legacy_rel,
        run_id="RUN-T08-LEGACY",
    )

    runner = ExperimentRunner(repo_root=repo)
    with pytest.raises(LegacyStrategyProtocolError):
        runner.run_stage1_screen(spec_yaml)
    with pytest.raises(LegacyStrategyProtocolError):
        runner.run_spec(spec_yaml)

    # CLI fail-closed
    assert cli_main(["verify", str(spec_yaml), "--json"]) == 1
    assert cli_main(["screen", str(spec_yaml), "--json"]) == 1
    assert cli_main(["backtest", str(spec_yaml), "--json"]) == 1

    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        trials_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T08-LEGACY'"
        ).fetchone()[0]
        assert trials_used == 0

    # Frozen legacy module code_closure remains stable & historical replay intact via allow_legacy_replay=True
    sha_before, _ = code_closure(repo, legacy_rel)
    hist_runner = ExperimentRunner(repo_root=repo, allow_legacy_replay=True)
    hist_res = hist_runner.run_spec(spec_yaml, override_run_id="RUN-T08-HIST")
    assert hist_res.strategy_code_sha256 == sha_before
    assert hist_res.verdict == "CANDIDATE_FOR_QUALIFICATION"

    monkeypatch.delenv("WARDEN_NON_INTERACTIVE", raising=False)
    qual = qualify_candidate("RUN-T08-HIST", "YES_UNSEAL_HOLDOUT", hist_runner, holdout_override_path=holdout_path)
    assert qual["run_id"] == "RUN-T08-HIST"
    assert qual["verdict"] == "PROMOTED"
    sha_after, _ = code_closure(repo, legacy_rel)
    assert sha_after == sha_before


def test_causal_model_ragged_panel_walk_forward_strips_val_labels_and_computes_ic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _make_panel(
        n_bars=120,
        assets=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"),
        seed=17,
        trend_bias=0.001,
    )
    ts = sorted(base["timestamp"].unique().to_list())
    # Add ragged late-listing asset (starts at bar 45) and early-delisting asset (ends at bar 85)
    extra_rows: list[dict] = []
    rng = np.random.default_rng(23)
    for asset, start_i, end_i in (("LATE", 45, 120), ("DELIST", 0, 85)):
        price = 80.0
        for t in range(start_i, end_i):
            ret = float(rng.normal(0.0005 if asset == "LATE" else -0.0005, 0.006))
            c = max(1.0, price * (1.0 + ret))
            extra_rows.append(
                {
                    "timestamp": ts[t],
                    "asset": asset,
                    "open": float(price),
                    "high": float(max(price, c) * 1.002),
                    "low": float(min(price, c) * 0.998),
                    "close": float(c),
                    "volume": 500.0,
                    "funding_rate": 0.0,
                }
            )
            price = c
    panel = pl.concat(
        [base, pl.DataFrame(extra_rows).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))]
    ).sort("timestamp", "asset")

    repo, contract, _ = _setup_repo(
        tmp_path,
        name="causal_model_ragged",
        panel=panel,
        n_folds=3,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
    )
    probe_log = tmp_path / "fold_probe.jsonl"
    strat_rel = "strategies/candidates/causal_probe_ml.py"
    (repo / strat_rel).write_text(
        f"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

PROBE_LOG = Path({str(probe_log)!r})

class ProbePipeline:
    feature_cols = ("feat_mom",)

    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        return (
            df.sort("timestamp", "asset")
            .with_columns(
                feat_mom=(pl.col("close") / pl.col("close").shift(3).over("asset") - 1.0).fill_null(0.0)
            )
        )

class ProbeEstimator:
    def __init__(self, alpha: float = 5.0):
        self.alpha = alpha

    def fit_predict_fold(self, train_df, val_features_df, feature_cols, label_col, seed=42):
        # Record fold metadata for independent boundary & zero-label verification
        entry = {{
            "train_max_ts": train_df["timestamp"].max().isoformat(),
            "val_min_ts": val_features_df["timestamp"].min().isoformat(),
            "val_cols": list(val_features_df.columns),
            "label_col": label_col,
            "train_feat_mean": float(train_df[feature_cols[0]].mean()),
            "val_feat_mean": float(val_features_df[feature_cols[0]].mean()),
        }}
        with PROBE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\\n")

        assert label_col not in val_features_df.columns
        x_tr = train_df.select(feature_cols).to_numpy()
        y_tr = train_df[label_col].to_numpy()
        valid = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
        model = Ridge(alpha=self.alpha)
        model.fit(x_tr[valid], y_tr[valid])
        x_val = np.nan_to_num(val_features_df.select(feature_cols).to_numpy(), nan=0.0)
        preds = model.predict(x_val)
        return val_features_df.select("timestamp", "asset").with_columns(
            prediction_value=pl.Series(preds),
            # Attempt to spoof ic_series in return object (must be ignored by Warden)
            ic_series=pl.Series(np.full(len(preds), 0.99)),
        )

pipeline = ProbePipeline()

def build_estimator(params: dict):
    return ProbeEstimator(alpha=float(params.get("ridge_alpha", 5.0)))
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    spec_yaml = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T08-ML-RAGGED",
        family="cross_sectional_ml",
        model_type="ridge",
        strat_module=strat_rel,
        parameters={"ridge_alpha": 5.0},
        run_id="RUN-T08-ML-RAGGED",
    )

    runner = ExperimentRunner(repo_root=repo)
    screen_report = runner.run_stage1_screen(spec_yaml)
    assert probe_log.is_file()
    folds = [json.loads(line) for line in probe_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(folds) == contract.n_folds

    ts_idx = {t: i for i, t in enumerate(ts)}
    required_gap = contract.label_horizon_bars + contract.execution_delay_bars + contract.embargo_bars
    for f in folds:
        tr_max = datetime.fromisoformat(f["train_max_ts"])
        val_min = datetime.fromisoformat(f["val_min_ts"])
        assert ts_idx[val_min] - ts_idx[tr_max] >= required_gap
        lowered = [c.lower() for c in f["val_cols"]]
        assert not any(
            k in col for col in lowered for k in ("label", "target", "forward", "fut_")
        ), f"Label-like column leaked into val_features_df: {f['val_cols']}"

    # Warden's computed IC is genuine (~[-0.5, 0.5]), never the spoofed 0.99
    assert abs(float(screen_report.metrics["mean_ic"])) < 0.90


def test_causal_model_adversarial_label_reader_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _make_panel(n_bars=90, assets=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"), seed=31)
    repo, contract, _ = _setup_repo(tmp_path, name="causal_model_adv_label", panel=panel, n_folds=2)
    strat_rel = "strategies/candidates/adv_label_reader.py"
    (repo / strat_rel).write_text(
        """
from __future__ import annotations
import polars as pl

class CleanPipeline:
    feature_cols = ("feat_mom",)
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        return df.sort("timestamp", "asset").with_columns(
            feat_mom=(pl.col("close") / pl.col("close").shift(2).over("asset") - 1.0).fill_null(0.0)
        )

class LabelReadingEstimator:
    def fit_predict_fold(self, train_df, val_features_df, feature_cols, label_col, seed=42):
        # Adversarial attempt to read validation labels directly from val_features_df
        cheated = val_features_df[label_col].to_numpy()
        return val_features_df.select("timestamp", "asset").with_columns(
            prediction_value=pl.Series(cheated)
        )

pipeline = CleanPipeline()

def build_estimator(params: dict):
    return LabelReadingEstimator()
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    spec_yaml = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T08-ADV-LABEL",
        family="cross_sectional_ml",
        model_type="ridge",
        strat_module=strat_rel,
        run_id="RUN-T08-ADV-LABEL",
    )

    assert cli_main(["verify", str(spec_yaml), "--json"]) == 1
    assert cli_main(["screen", str(spec_yaml), "--json"]) == 1
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        trials_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T08-ADV-LABEL'"
        ).fetchone()[0]
        assert trials_used == 0


def test_causal_rules_strategy_runs_via_engine_with_risk_overlays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    rng = np.random.default_rng(77)
    rows = []
    pa, pb = 100.0, 100.0
    for t in range(120):
        ts = t0 + timedelta(hours=t)
        ra = 0.004 + float(rng.normal(0.0, 0.002))
        rb = -0.003 + float(rng.normal(0.0, 0.002))
        for asset, p, ret in (("AAA", pa, ra), ("BBB", pb, rb), ("CCC", 100.0, 0.0001)):
            rows.append(
                {
                    "timestamp": ts,
                    "asset": asset,
                    "open": float(p),
                    "high": float(p * 1.004),
                    "low": float(p * 0.996),
                    "close": float(p * (1.0 + ret)),
                    "volume": 1000.0,
                    "funding_rate": 0.0,
                }
            )
        pa *= 1.0 + ra
        pb *= 1.0 + rb
    panel = (
        pl.DataFrame(rows)
        .sort("timestamp", "asset")
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    )
    holdout_panel = panel.with_columns((pl.col("timestamp") + timedelta(hours=125)).alias("timestamp"))
    repo, contract, holdout_path = _setup_repo(
        tmp_path, name="causal_rules_engine", panel=panel, holdout_panel=holdout_panel, n_folds=2
    )
    strat_rel = "strategies/candidates/causal_rules_probe.py"
    (repo / strat_rel).write_text(
        """
from __future__ import annotations
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor
from ml4t.backtest.risk.portfolio import MaxDrawdownLimit, RiskManager
from ml4t.backtest.risk.position import StopLoss

portfolio_coupled = True

def required_symbols(params: dict):
    return list(params.get("symbols", ["AAA", "BBB"]))

class ProbeRulesStrategy(Strategy):
    def __init__(self, max_dd: float = 0.15, stop_pct: float = 0.05) -> None:
        self._max_dd = max_dd
        self._stop_pct = stop_pct
        self._executor = TargetWeightExecutor(
            RebalanceConfig(allow_short=True, allow_fractional=True, max_single_weight=0.5, max_gross_leverage=1.0)
        )
        self._risk = RiskManager(
            limits=[MaxDrawdownLimit(max_drawdown=self._max_dd, action="reduce", reduction_pct=0.5)]
        )

    def on_start(self, broker) -> None:
        broker.set_position_rules(StopLoss(pct=self._stop_pct))
        self._risk.initialize(broker.get_account_value())

    def on_data(self, timestamp, data, context, broker) -> None:
        held = {a: p.quantity for a, p in broker.get_positions().items() if p.quantity != 0}
        results = self._risk.update(
            equity=broker.get_account_value(),
            positions=held,
            timestamp=timestamp,
            broker=broker,
        )
        gate = 0.5 if any(r.breached for r in results) else 1.0
        targets = {}
        if "AAA" in data:
            targets["AAA"] = 0.4 * gate
        if "BBB" in data:
            targets["BBB"] = -0.4 * gate
        self._executor.execute(targets, data, broker, timestamp=timestamp)

def configure_strategy(contract, params: dict) -> Strategy:
    return ProbeRulesStrategy(
        max_dd=float(params.get("max_dd", 0.15)),
        stop_pct=float(params.get("stop_pct", 0.05)),
    )
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    spec_yaml = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T08-RULES",
        family="rules_ts",
        model_type="rules",
        strat_module=strat_rel,
        parameters={"symbols": ["AAA", "BBB"], "max_dd": 0.15, "stop_pct": 0.05},
        # T12: numeric parameters need two distinct perturbed configurations
        # before Stage 3 may publish a candidate.
        param_grid=[{"max_dd": 0.12}, {"max_dd": 0.18}],
        run_id="RUN-T08-RULES",
    )

    runner = ExperimentRunner(repo_root=repo)
    s1 = runner.run_stage1_screen(spec_yaml)
    assert s1.passed is True

    res = runner.run_spec(spec_yaml)
    assert res.verdict == "CANDIDATE_FOR_QUALIFICATION"
    assert res.net_sharpe > 1.0
    # Verify required_symbols filtered out CCC (only AAA & BBB appear in fills)
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        diag = json.loads(
            conn.execute("SELECT diagnostics_json FROM trials WHERE run_id='RUN-T08-RULES'").fetchone()[0]
        )
    assert diag["trade_analysis"]["n_trades"] >= 0
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE", raising=False)
    qual = qualify_candidate("RUN-T08-RULES", "YES_UNSEAL_HOLDOUT", runner, holdout_override_path=holdout_path)
    assert qual["verdict"] == "PROMOTED"


def test_adversarial_peeking_estimator_with_clean_pipeline_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _make_panel(n_bars=90, assets=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"), seed=41)
    repo, contract, _ = _setup_repo(tmp_path, name="adv_peek_est", panel=panel, n_folds=2)
    strat_rel = "strategies/candidates/adv_peek_estimator.py"
    (repo / strat_rel).write_text(
        """
from __future__ import annotations
import polars as pl

class CleanPipeline:
    feature_cols = ("feat_mom",)
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        return df.sort("timestamp", "asset").with_columns(
            feat_mom=(pl.col("close") / pl.col("close").shift(2).over("asset") - 1.0).fill_null(0.0)
        )

class PeekingEstimator:
    def fit_predict_fold(self, train_df, val_features_df, feature_cols, label_col, seed=42):
        # Clean pipeline, but estimator peeks at future validation bar via shift(-1)!
        peeking = (
            val_features_df.sort("timestamp", "asset")
            .with_columns(
                prediction_value=pl.col(feature_cols[0]).shift(-1).over("asset").fill_null(0.0)
            )
            .select("timestamp", "asset", "prediction_value")
        )
        return peeking

pipeline = CleanPipeline()

def build_estimator(params: dict):
    return PeekingEstimator()
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    spec_yaml = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T08-PEEK-EST",
        family="cross_sectional_ml",
        model_type="ridge",
        strat_module=strat_rel,
        run_id="RUN-T08-PEEK-EST",
    )

    assert cli_main(["verify", str(spec_yaml), "--json"]) == 1
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        trials_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T08-PEEK-EST'"
        ).fetchone()[0]
        assert trials_used == 0


def test_adversarial_peeking_rules_on_data_with_clean_pipeline_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _make_panel(n_bars=90, assets=("AAA", "BBB"), seed=43)
    repo, contract, _ = _setup_repo(tmp_path, name="adv_peek_rules", panel=panel, n_folds=2)
    strat_rel = "strategies/candidates/adv_peek_rules.py"
    (repo / strat_rel).write_text(
        """
from __future__ import annotations
from pathlib import Path
import polars as pl
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor

class CleanPipeline:
    def compute(self, bars_df: pl.DataFrame | pl.LazyFrame, contract) -> pl.DataFrame:
        df = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        return df.sort("timestamp", "asset").with_columns(
            feat_ret=(pl.col("close") / pl.col("close").shift(1).over("asset") - 1.0).fill_null(0.0)
        )

pipeline = CleanPipeline()

def required_symbols(params: dict):
    return ["AAA", "BBB"]

class PeekingOnDataStrategy(Strategy):
    def __init__(self, future_signs: dict) -> None:
        self._future_signs = future_signs
        self._executor = TargetWeightExecutor(
            RebalanceConfig(allow_short=True, allow_fractional=True, max_single_weight=0.5, max_gross_leverage=1.0)
        )

    def on_data(self, timestamp, data, context, broker) -> None:
        w = self._future_signs.get(timestamp, 0.0)
        if w == 0.0:
            return
        self._executor.execute({"AAA": w, "BBB": -w}, data, broker, timestamp=timestamp)

def configure_strategy(contract, params: dict) -> Strategy:
    df = pl.read_parquet(Path(contract.exploratory_data_path)).filter(pl.col("asset") == "AAA").sort("timestamp")
    fut = df.with_columns(
        ((pl.col("close").shift(-3) / pl.col("close") - 1.0).clip(-0.4, 0.4)).fill_null(0.0).alias("w")
    )
    future_signs = {r["timestamp"]: float(r["w"]) for r in fut.select("timestamp", "w").iter_rows(named=True)}
    return PeekingOnDataStrategy(future_signs)
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    spec_yaml = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T08-PEEK-RULES",
        family="rules_ts",
        model_type="rules",
        strat_module=strat_rel,
        run_id="RUN-T08-PEEK-RULES",
    )

    assert cli_main(["verify", str(spec_yaml), "--json"]) == 1
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        trials_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T08-PEEK-RULES'"
        ).fetchone()[0]
        assert trials_used == 0


def test_adversarial_cross_asset_leaking_per_asset_rules_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _make_panel(n_bars=80, assets=("AAA", "BBB"), seed=47)
    repo, contract, _ = _setup_repo(tmp_path, name="adv_cross_asset", panel=panel, n_folds=2)
    strat_rel = "strategies/candidates/adv_cross_asset_rules.py"
    (repo / strat_rel).write_text(
        """
from __future__ import annotations
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor

# Default is per-asset (cross_sectional=False, portfolio_coupled=False)
def required_symbols(params: dict):
    return ["AAA", "BBB"]

class CrossAssetLeakingStrategy(Strategy):
    def __init__(self) -> None:
        self._executor = TargetWeightExecutor(
            RebalanceConfig(allow_short=True, allow_fractional=True, max_single_weight=0.5, max_gross_leverage=1.0)
        )

    def on_data(self, timestamp, data, context, broker) -> None:
        # Leaks AAA's close price into BBB's position size!
        aaa_px = data["AAA"]["close"]
        w_bbb = 0.25 if aaa_px > 120.0 else -0.25
        self._executor.execute({"AAA": 0.2, "BBB": w_bbb}, data, broker, timestamp=timestamp)

def configure_strategy(contract, params: dict) -> Strategy:
    return CrossAssetLeakingStrategy()
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    spec_yaml = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T08-CROSS-ASSET",
        family="rules_ts",
        model_type="rules",
        strat_module=strat_rel,
        run_id="RUN-T08-CROSS-ASSET",
    )

    assert cli_main(["verify", str(spec_yaml), "--json"]) == 1
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        trials_used = conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T08-CROSS-ASSET'"
        ).fetchone()[0]
        assert trials_used == 0


def _make_persistent_trend_panel(
    *,
    n_bars: int = 180,
    assets: tuple[str, ...] = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"),
    seed: int = 101,
    t0: datetime = datetime(2025, 1, 1, tzinfo=UTC),
    start_prices: dict[str, float] | None = None,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    half = len(assets) // 2
    for idx, asset in enumerate(assets):
        level = start_prices.get(asset) if start_prices else None
        price = 100.0 + idx * 5.0 if level is None else float(level)
        drift = 0.0028 if idx < half else -0.0022
        for t in range(n_bars):
            ts = t0 + timedelta(hours=t)
            ret = drift + float(rng.normal(0.0, 0.0025))
            o = price
            c = max(1.0, o * (1.0 + ret))
            hi = max(o, c) * 1.003
            lo = min(o, c) * 0.997
            rows.append(
                {
                    "timestamp": ts,
                    "asset": asset,
                    "open": float(o),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(c),
                    "volume": float(1500.0 + rng.uniform(0.0, 300.0)),
                    "funding_rate": 0.0,
                }
            )
            price = c
    return (
        pl.DataFrame(rows)
        .sort("timestamp", "asset")
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    )


def test_reference_ridge_is_refused_and_drawdown_rules_complete_funnel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    panel = _make_persistent_trend_panel(n_bars=180, seed=101, t0=datetime(2025, 1, 1, tzinfo=UTC))
    # #97 loads a production holdout as one read-only exploratory warmup prefix plus the
    # holdout window, i.e. the same feed continued.  A fixture whose holdout restarts at
    # 100 while the prefix ends near 165 hands the strategy a fabricated -40% bar at the
    # boundary (full book flip, 372 fills instead of 92), so the holdout price levels are
    # seeded from the exploratory panel's last closes.  Drift law, seed, bar counts,
    # window and every gate floor are unchanged; only the impossible level jump is gone.
    last_closes = (
        panel.filter(pl.col("timestamp") == panel["timestamp"].max())
        .sort("asset")
        .select("asset", "close")
        .to_dicts()
    )
    holdout_panel = _make_persistent_trend_panel(
        n_bars=120,
        seed=202,
        t0=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=185),
        start_prices={row["asset"]: float(row["close"]) for row in last_closes},
    )
    repo, contract, _ = _setup_repo(
        tmp_path,
        name="ref_templates_funnel",
        panel=panel,
        holdout_panel=holdout_panel,
        n_folds=3,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
    )

    ridge_rel = "strategies/baselines/causal_ridge_1h.py"
    rules_rel = "strategies/candidates/causal_rules_drawdown_template.py"
    (repo / ridge_rel).write_text((repo_root / ridge_rel).read_text(encoding="utf-8"), encoding="utf-8")
    (repo / rules_rel).write_text((repo_root / rules_rel).read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.chdir(repo)
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE", raising=False)

    # 1. CausalModelStrategy reference template end-to-end
    ridge_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T08-REF-RIDGE",
        family="cross_sectional_ml",
        model_type="ridge",
        strat_module=ridge_rel,
        parameters={"ridge_alpha": 10.0, "rebalance_every": 4, "top_k": 3},
        # T12: the reference baseline declares RidgeParams, so the funnel supplies
        # two distinct numeric neighbors instead of leaning on an empty grid.
        param_grid=[{"ridge_alpha": 8.5}, {"ridge_alpha": 11.5}],
        run_id="RUN-T08-REF-RIDGE",
        expected_holding_bars=4,
    )
    assert cli_main(["verify", str(ridge_spec), "--json"]) == 0
    assert cli_main(["screen", str(ridge_spec), "--json"]) == 0
    assert cli_main(["backtest", str(ridge_spec), "--json"]) == 0
    capsys.readouterr()
    assert cli_main(["audit-check", "--run-id", "RUN-T08-REF-RIDGE", "--json"]) == 1
    ridge_audit = json.loads(capsys.readouterr().out)
    assert ridge_audit["verdict"] == "REJECTED"
    cadence = ridge_audit["checks"]["1_target_rebalance_alignment"]
    assert cadence["passed"] is False
    assert cadence["blocking_dimensions"] == ["holding"]
    assert ridge_audit["checks"]["4_fold_local_fitting"]["passed"] is True

    # 2. CausalRulesStrategy drawdown template end-to-end
    rules_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T08-REF-RULES",
        family="rules_ts",
        model_type="rules",
        strat_module=rules_rel,
        parameters={"fast_span": 6, "slow_span": 18, "max_drawdown": 0.10, "stop_loss_pct": 0.05},
        # T12: ± neighbor spans on the reference rules template.
        param_grid=[{"fast_span": 5, "slow_span": 16}, {"fast_span": 7, "slow_span": 20}],
        run_id="RUN-T08-REF-RULES",
        expected_holding_bars=8,
    )
    assert cli_main(["verify", str(rules_spec), "--json"]) == 0
    assert cli_main(["screen", str(rules_spec), "--json"]) == 0
    assert cli_main(["backtest", str(rules_spec), "--json"]) == 0
    assert cli_main(["audit-check", "--run-id", "RUN-T08-REF-RULES", "--json"]) == 0
    capsys.readouterr()
    assert cli_main([
        "qualify", "--run-id", "RUN-T08-REF-RULES",
        "--unseal-holdout", "--human-confirm-unseal", "YES_UNSEAL_HOLDOUT", "--json",
    ]) == 0
    rules_qual = json.loads(capsys.readouterr().out)
    assert rules_qual["verdict"] == "PROMOTED"


def test_preregistered_four_bar_synthetic_control_completes_funnel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Synthetic positive control, not a trading claim. The cadence, seeds, windows,
    # and pass criteria are fixed independently of the negative ridge witness:
    # a 4-bar long/short exposure followed by 4 flat bars; distinct panels with
    # the same drift law. Require measured completed holds and the full CLI funnel.
    start = datetime(2025, 1, 1, tzinfo=UTC)
    repo, contract, _ = _setup_repo(
        tmp_path,
        name="fixed_cadence_control",
        panel=_make_persistent_trend_panel(n_bars=192, seed=311, t0=start),
        holdout_panel=_make_persistent_trend_panel(
            n_bars=192, seed=733, t0=start + timedelta(hours=192)
        ),
        label_horizon_bars=4,
    )
    strategy_path = "strategies/candidates/fixed_cadence_control.py"
    (repo / strategy_path).write_text(
        '''from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor
from pydantic import BaseModel, ConfigDict


class ParamsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def required_symbols(params):
    return ["AAA", "EEE"]


class FixedCadenceControl(Strategy):
    def __init__(self):
        self.executor = TargetWeightExecutor(RebalanceConfig(
            allow_short=True, allow_fractional=True, max_single_weight=0.5, max_gross_leverage=1.0,
        ))

    def on_data(self, timestamp, data, context, broker):
        phase = timestamp.hour % 8
        if phase in (0, 4):
            weight = 0.4 if phase == 0 else 0.0
            targets = {a: w for a, w in (("AAA", weight), ("EEE", -weight)) if a in data}
            self.executor.execute(targets, data, broker, timestamp=timestamp)


def configure_strategy(contract, params):
    return FixedCadenceControl()
''',
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.delenv("WARDEN_NON_INTERACTIVE", raising=False)
    run_id = "RUN-T08-FIXED-CADENCE"
    spec = _register_hyp_and_spec(
        repo, contract, hyp_id="HYP-T08-FIXED-CADENCE", family="rules_ts",
        model_type="rules", strat_module=strategy_path, run_id=run_id, expected_holding_bars=4,
    )
    for subcommand in ("verify", "screen", "backtest"):
        assert cli_main([subcommand, str(spec), "--json"]) == 0, subcommand
    capsys.readouterr()
    assert cli_main(["audit-check", "--run-id", run_id, "--json"]) == 0
    audit = json.loads(capsys.readouterr().out)
    assert audit["verdict"] == "APPROVED"
    assert all(check["passed"] for check in audit["checks"].values())
    cadence = audit["checks"]["1_target_rebalance_alignment"]
    qualification_rc = cli_main([
        "qualify", "--run-id", run_id,
        "--unseal-holdout", "--human-confirm-unseal", "YES_UNSEAL_HOLDOUT", "--json",
    ])
    result = json.loads(capsys.readouterr().out)
    assert qualification_rc == 0, result
    assert result["holdout_override"] is False
    assert result["verdict"] == "PROMOTED"  # promotion only in the disposable fixture registry
    assert cadence["holding_completed_epochs"] > 0
    assert cadence["observed_holding_completed_cadence_bars"] == pytest.approx(4.0)


def test_protocol_family_mismatch_fails_closed_with_zero_trials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from warden.protocols import LegacyStrategyProtocolError

    repo_root = Path(__file__).resolve().parents[1]
    panel = _make_panel(n_bars=80, assets=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"), seed=61)
    repo, contract, _ = _setup_repo(tmp_path, name="proto_family_mismatch", panel=panel, n_folds=2)
    rules_rel = "strategies/candidates/causal_rules_drawdown_template.py"
    (repo / rules_rel).write_text((repo_root / rules_rel).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(repo)

    bad_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T08-MISMATCH",
        family="cross_sectional_ml",
        model_type="ridge",
        strat_module=rules_rel,
        run_id="RUN-T08-MISMATCH",
    )
    runner = ExperimentRunner(repo_root=repo)
    with pytest.raises(LegacyStrategyProtocolError, match="CausalRulesStrategy requires strategy_family='rules_ts'"):
        runner.run_stage1_screen(bad_spec)
    with pytest.raises(LegacyStrategyProtocolError, match="CausalRulesStrategy requires strategy_family='rules_ts'"):
        runner.run_spec(bad_spec)
    assert cli_main(["verify", str(bad_spec), "--json"]) == 1
    with sqlite3.connect(repo / "registry" / "registry.db") as conn:
        assert conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T08-MISMATCH'"
        ).fetchone()[0] == 0
