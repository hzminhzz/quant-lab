"""Ticket 10: one EvaluationKernel, explicit session policy, per-asset ML labels.

The acceptance facts these tests pin:

* ``warden verify`` on a ``CausalRulesStrategy`` executes ``Engine.run`` at most six
  times and builds its cached position tape vectorially;
* the tape a proof compared is the tape the Engine produced (no re-derived copy);
* every execution records an explicit ``session_mode``, and a ``session_date`` that
  cannot be used is logged, never silently dropped;
* ML forward labels are computed per asset with a calendar-gap cap;
* legacy ``generate_positions``/``simulate_positions`` live only in
  :mod:`warden.legacy_replay`, and ``screen``/``backtest``/``qualify`` reach the
  Engine only through :class:`warden.evaluation.EvaluationKernel`.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ml4t.backtest as bt
import numpy as np
import polars as pl
import pytest
import yaml

from warden.cli import main as cli_main
from warden.contracts import EvaluationContract
from warden.engine_sim import (
    SessionPolicy,
    resolve_session_policy,
    run_engine_strategy,
)
from warden.evaluation import EvaluationKernel, EvaluationTape
from warden.protocols import RulesProofPipeline, compute_execution_forward_returns
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner
from warden.strategy_loader import load_module_from_path
from warden_ops.integrity import generate_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Per-asset momentum rules strategy.  It deliberately declares **no**
#: ``cross_sectional``/``portfolio_coupled`` flag, so ``verify`` also runs the
#: cross-asset independence proof — the case that needs the sixth Engine run.
RULES_MODULE = '''
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor


def required_symbols(params):
    return None


class _Momentum(Strategy):
    def __init__(self, fast=3, slow=8):
        self._fast = int(fast)
        self._slow = int(slow)
        self._hist = {}
        self._exec = TargetWeightExecutor(
            RebalanceConfig(
                allow_short=True,
                allow_fractional=True,
                max_single_weight=0.5,
                max_gross_leverage=1.0,
            )
        )

    def on_data(self, timestamp, data, context, broker):
        targets = {}
        for asset, bar in data.items():
            hist = self._hist.setdefault(asset, [])
            hist.append(float(bar["close"]))
            if len(hist) < self._slow:
                continue
            fast = sum(hist[-self._fast :]) / self._fast
            slow = sum(hist[-self._slow :]) / self._slow
            targets[asset] = 0.10 if fast > slow else -0.10
        if targets:
            self._exec.execute(targets, data, broker, timestamp=timestamp)


def configure_strategy(contract, params):
    return _Momentum(params.get("fast", 3), params.get("slow", 8))
'''


def _panel(
    n_bars: int = 60,
    assets: tuple[str, ...] = ("AAA", "BBB", "CCC"),
    *,
    seed: int = 5,
    start: datetime = datetime(2025, 1, 1, tzinfo=UTC),
    session_date: bool = False,
    step_hours: int = 1,
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for idx, asset in enumerate(assets):
        price = 100.0 + 10.0 * idx
        for step in range(n_bars):
            stamp = start + timedelta(hours=step_hours * step)
            ret = 0.0005 + float(rng.normal(0.0, 0.004))
            open_px = price
            close_px = max(1.0, open_px * (1.0 + ret))
            row = {
                "timestamp": stamp,
                "asset": asset,
                "open": float(open_px),
                "high": float(max(open_px, close_px) * 1.002),
                "low": float(min(open_px, close_px) * 0.998),
                "close": float(close_px),
                "volume": float(500.0 + rng.uniform(0.0, 100.0)),
                "funding_rate": 0.0001,
            }
            if session_date:
                row["session_date"] = stamp.date()
            rows.append(row)
            price = close_px
    frame = pl.DataFrame(rows).sort("timestamp", "asset")
    return frame.with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))


def _setup_repo(tmp_path: Path, panel: pl.DataFrame, *, n_bars: int) -> tuple[Path, EvaluationContract]:
    from warden_ops.integrity import generate_manifest

    repo = tmp_path / "repo"
    for rel in ("contracts/datasets", "strategies/candidates", "experiments/active", "cache/features"):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / "AGENTS.md").write_text("# test\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='t10'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version=1\n", encoding="utf-8")

    ts = sorted(panel["timestamp"].unique().to_list())
    train_start = ts[0]
    val_start = ts[max(12, n_bars // 3)]
    val_end = ts[-1] + timedelta(hours=1)
    holdout_start = val_end
    holdout_end = holdout_start + timedelta(hours=n_bars)

    data_rel = "data/exploratory/panel.parquet"
    data_path = repo / data_rel
    data_path.parent.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(data_path)
    sha = hashlib.sha256(data_path.read_bytes()).hexdigest()

    holdout_rel = "data/holdout/panel.parquet"
    holdout_path = repo / holdout_rel
    holdout_path.parent.mkdir(parents=True, exist_ok=True)
    panel.with_columns((pl.col("timestamp") + timedelta(hours=n_bars + 4)).alias("timestamp")).write_parquet(
        holdout_path
    )

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
        n_folds=2,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=0.5,
        slippage_bps=0.5,
        data_snapshot_sha256=sha,
        data_provenance="verified_market",
    )
    (repo / "contracts" / f"{cid.lower()}.yaml").write_text(yaml.safe_dump(contract.__dict__), encoding="utf-8")
    (repo / "contracts" / "datasets" / f"{cid}.json").write_text(
        json.dumps(
            {
                "contract_id": cid,
                "path": data_rel,
                "sha256": sha,
                "holdout_path": holdout_rel,
                "holdout_sha256": hashlib.sha256(holdout_path.read_bytes()).hexdigest(),
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


def _register(repo: Path, contract: EvaluationContract, module_rel: str, *, params: dict | None = None) -> Path:
    from warden.contracts import HypothesisSpec

    (repo / module_rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / module_rel).write_text(RULES_MODULE, encoding="utf-8")
    hyp = HypothesisSpec(
        "HYP-T10-RULES",
        contract.contract_id,
        "Ticket 10 rules",
        "rules_ts",
        "Per-asset momentum captured causally on the strategy's own bars",
        "Taker fee and slippage paid to passive liquidity providers",
        8,
        "Reject if prefix invariance or Stage 1 fails",
    )
    ResearchRegistry(repo / "registry" / "registry.db").register_hypothesis(hyp)
    spec = repo / "experiments" / "active" / "hyp_t10_rules.yaml"
    spec.write_text(
        yaml.safe_dump(
            {
                "contract_id": contract.contract_id,
                "hypothesis_id": hyp.hypothesis_id,
                "strategy_name": "Strat_HYP-T10-RULES",
                "strategy_family": "rules_ts",
                "model_type": "rules",
                "strategy_module": module_rel,
                "parameters": params or {},
            }
        ),
        encoding="utf-8",
    )
    return spec


def _count_engine_runs(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    counter = {"n": 0}
    original = bt.Engine.run

    def _counting(self, *args, **kwargs):
        counter["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(bt.Engine, "run", _counting)
    return counter


def test_causal_rules_verify_runs_the_engine_at_most_six_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One full run + two truncated + two shocked + one asset-shock = 6, not 17-18."""
    n_bars = 60
    panel = _panel(n_bars=n_bars)
    repo, contract = _setup_repo(tmp_path, panel, n_bars=n_bars)
    spec = _register(repo, contract, "strategies/candidates/rules_momentum.py")
    generate_manifest(repo)  # the strategy file is part of the worktree identity
    monkeypatch.chdir(repo)

    counter = _count_engine_runs(monkeypatch)
    assert cli_main(["verify", str(spec), "--json"]) == 0
    assert counter["n"] == 6, f"verify executed Engine.run {counter['n']} times (cap is 6)"


def test_cached_rules_tape_is_the_engine_tape(tmp_path: Path) -> None:
    """The feature cache stores the execution the proof compared, built column-wise."""
    n_bars = 60
    panel = _panel(n_bars=n_bars)
    repo, contract = _setup_repo(tmp_path, panel, n_bars=n_bars)
    module_rel = "strategies/candidates/rules_momentum.py"
    _register(repo, contract, module_rel)

    norm = panel.sort("timestamp", "asset")
    ts = sorted(norm["timestamp"].unique().to_list())
    assets = sorted(norm["asset"].unique().to_list())
    kernel = EvaluationKernel()
    tape = kernel.run_rules(
        load_module_from_path(repo / module_rel),
        norm,
        ts,
        assets,
        contract,
        slice(0, len(ts)),
        {},
    )
    assert isinstance(tape, EvaluationTape)

    # Direct Engine execution must agree with the tape (execution alignment).
    direct = run_engine_strategy(
        norm,
        load_module_from_path(repo / module_rel).configure_strategy(contract, {}),
        ts,
        assets,
        contract,
        slice(0, len(ts)),
    )
    assert np.allclose(tape.positions, direct.positions, rtol=1e-12, atol=1e-12)
    assert np.allclose(tape.net_returns, direct.net_returns, rtol=1e-12, atol=1e-12)

    # The same kernel session serves the pipeline: memo hit, byte-identical tape.
    pipeline = RulesProofPipeline(
        repo_root=str(repo),
        strategy_module=module_rel,
        code_sha256="0" * 64,
        params={},
        kernel=kernel,
    )
    feats = pipeline.compute(norm, contract)
    assert feats.columns == ["timestamp", "asset", "position_weight"]
    assert feats.height == norm.height
    wide = feats.pivot(index="timestamp", on="asset", values="position_weight").sort("timestamp")
    rebuilt = np.column_stack([wide[asset].to_numpy() for asset in assets])
    assert np.allclose(rebuilt, tape.positions, rtol=1e-12, atol=1e-12)
    assert kernel.engine_runs == 1  # the pipeline did not execute the Engine again


def test_rules_tape_construction_has_no_per_cell_loop() -> None:
    """``_positions_tape`` unpivots a matrix; it never builds a dict per (bar, asset)."""
    from warden.protocols import _positions_tape

    tree = ast.parse((REPO_ROOT / "src" / "warden" / "protocols.py").read_text(encoding="utf-8"))
    target = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_positions_tape"
    )
    source = ast.get_source_segment(
        (REPO_ROOT / "src" / "warden" / "protocols.py").read_text(encoding="utf-8"), target
    )
    assert source is not None
    assert "unpivot" in source
    assert "enumerate(ts)" not in source
    assert "iter_rows" not in source
    tape = _positions_tape(
        [datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(3)],
        ["AAA", "BBB"],
        np.array([[0.1, -0.1], [0.2, -0.2], [0.3, -0.3]]),
    )
    assert tape.height == 6
    assert tape["position_weight"].to_list()[0] == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("kwargs", "expected_mode", "expected_col"),
    [
        ({"session_date": True, "step_hours": 24}, "single_venue_daily_session", "session_date"),
        ({}, "venue_calendar", None),
    ],
)
def test_session_policy_is_explicit_for_a_synchronised_panel(
    kwargs: dict, expected_mode: str, expected_col: str | None
) -> None:
    panel = _panel(n_bars=24, **kwargs)
    policy = resolve_session_policy(panel)
    assert isinstance(policy, SessionPolicy)
    assert policy.mode == expected_mode
    assert policy.session_col == expected_col
    assert policy.reason


def test_async_multi_venue_panel_is_named_not_assumed() -> None:
    es = _panel(n_bars=30, assets=("ES",), seed=3)
    dax = _panel(n_bars=30, assets=("DAX",), seed=4).filter(
        (pl.col("timestamp").dt.hour() >= 8) & (pl.col("timestamp").dt.hour() <= 16)
    )
    panel = pl.concat([es, dax]).sort("timestamp", "asset")
    policy = resolve_session_policy(panel)
    assert policy.mode == "multi_venue_intraday"
    assert policy.session_col is None
    assert "session_date" in policy.reason


def test_unusable_session_date_is_logged_not_dropped_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    panel = _panel(n_bars=6, session_date=True, step_hours=24)
    # Break the completeness invariant: one asset misses a whole session.
    broken = panel.filter(
        ~(
            (pl.col("asset") == "AAA")
            & (pl.col("timestamp") == pl.lit(datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=2)))
        )
    )
    with caplog.at_level(logging.WARNING, logger="warden.engine_sim"):
        policy = resolve_session_policy(broken)
        for warning in policy.warnings:  # the Engine call site logs exactly these
            logging.getLogger("warden.engine_sim").warning("session semantics degraded: %s", warning)
    assert policy.session_col is None
    assert policy.mode != "single_venue_daily_session"
    assert policy.reason.startswith("session_date dropped:")
    assert any("session semantics degraded" in record.message for record in caplog.records)


def _async_panel(*, drop_hours: tuple[int, ...] = ()) -> pl.DataFrame:
    """24h ES and 08:00-16:00 DAX on one hourly contract clock."""
    es = _panel(n_bars=72, assets=("ES",), seed=11)
    dax = _panel(n_bars=72, assets=("DAX",), seed=12).filter(
        (pl.col("timestamp").dt.hour() >= 8) & (pl.col("timestamp").dt.hour() <= 16)
    )
    if drop_hours:
        dax = dax.filter(~pl.col("timestamp").dt.hour().is_in(list(drop_hours)))
    return pl.concat([es, dax]).sort("timestamp", "asset")


def test_ml_forward_labels_are_per_asset_on_an_async_panel() -> None:
    panel = _async_panel(drop_hours=(12,))
    contract = EvaluationContract(
        contract_id="T10_ASYNC",
        asset_class="indices",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="data/exploratory/panel.parquet",
        holdout_data_path="data/holdout/panel.parquet",
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        val_start=datetime(2025, 1, 2, tzinfo=UTC),
        val_end=datetime(2025, 1, 4, tzinfo=UTC),
        holdout_start=datetime(2025, 1, 4, tzinfo=UTC),
        holdout_end=datetime(2025, 1, 5, tzinfo=UTC),
        n_folds=2,
        label_horizon_bars=1,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )
    labels = compute_execution_forward_returns(panel, contract).filter(pl.col("asset") == "DAX")
    by_hour: dict[int, list[float | None]] = {}
    for stamp, value in labels.select("timestamp", "forward_return").iter_rows():
        by_hour.setdefault(stamp.hour, []).append(value)

    # 10:00 labels enter at 11:00 and exit at (missing) 12:00 -> 13:00.  On a union
    # timestamp grid 11:00 is an ES-only bar, so the pre-#96 code nulled it.
    assert any(value is not None for value in by_hour[10])
    assert all(value is not None for value in by_hour[14])
    # Across the whole panel DAX carries labels only where its own next bars exist.
    assert labels["forward_return"].null_count() < labels.height


def test_ml_forward_labels_null_across_a_genuine_calendar_gap() -> None:
    panel = _async_panel(drop_hours=(11, 12, 13))
    contract = EvaluationContract(
        contract_id="T10_ASYNC_GAP",
        asset_class="indices",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="data/exploratory/panel.parquet",
        holdout_data_path="data/holdout/panel.parquet",
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        val_start=datetime(2025, 1, 2, tzinfo=UTC),
        val_end=datetime(2025, 1, 4, tzinfo=UTC),
        holdout_start=datetime(2025, 1, 4, tzinfo=UTC),
        holdout_end=datetime(2025, 1, 5, tzinfo=UTC),
        n_folds=2,
        label_horizon_bars=1,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )
    labels = compute_execution_forward_returns(panel, contract).filter(pl.col("asset") == "DAX")
    # 09:00 -> entry 10:00, exit 14:00: a 4-bar hole at horizon=1, beyond the cap.
    nine = labels.filter(pl.col("timestamp").dt.hour() == 9)["forward_return"].to_list()
    assert nine and all(value is None for value in nine)
    # 14:00 (just after the hole) still labels against its own adjacent bars.
    fourteen = labels.filter(pl.col("timestamp").dt.hour() == 14)["forward_return"].to_list()
    assert fourteen and all(value is not None for value in fourteen)


def test_gap_cap_is_configurable_and_monotonic() -> None:
    panel = _async_panel(drop_hours=(12,))
    contract = EvaluationContract(
        contract_id="T10_CAP",
        asset_class="indices",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="e.parquet",
        holdout_data_path="h.parquet",
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        val_start=datetime(2025, 1, 2, tzinfo=UTC),
        val_end=datetime(2025, 1, 4, tzinfo=UTC),
        holdout_start=datetime(2025, 1, 4, tzinfo=UTC),
        holdout_end=datetime(2025, 1, 5, tzinfo=UTC),
        n_folds=2,
        label_horizon_bars=1,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )
    tight = compute_execution_forward_returns(panel, contract, max_gap_bars=0)
    loose = compute_execution_forward_returns(panel, contract, max_gap_bars=2)
    tight_nulls = tight["forward_return"].null_count()
    loose_nulls = loose["forward_return"].null_count()
    assert tight_nulls >= loose_nulls
    assert loose_nulls < tight_nulls  # the one-bar hole is tolerated only by the wider cap


def test_legacy_replay_is_the_only_legacy_execution_module() -> None:
    from warden import legacy_replay, runner

    # The runner re-exports the legacy arithmetic instead of owning it.
    assert legacy_replay.simulate_positions.__module__ == "warden.legacy_replay"
    positions = np.array([[0.5], [-0.5], [0.0]])
    returns = np.array([[0.01], [0.02], [-0.01]])
    contract = _minimal_contract()
    via_runner = runner.simulate_positions(positions, returns, None, contract, slice(0, 3))
    via_module = legacy_replay.simulate_positions(positions, returns, None, contract, slice(0, 3))
    assert np.array_equal(via_runner["net_returns"], via_module["net_returns"])

    for rel in ("src/warden/evaluation.py", "src/warden/qualify.py", "src/warden/cli.py"):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        called = {
            getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
        }
        assert "generate_positions" not in called, f"{rel} executes legacy generate_positions directly"
        assert "simulate_positions" not in called, f"{rel} owns legacy PnL arithmetic"

    legacy_source = (REPO_ROOT / "src" / "warden" / "legacy_replay.py").read_text(encoding="utf-8")
    assert "def run_generate_positions_isolated(" in legacy_source
    assert "def simulate_positions(" in legacy_source


def test_legacy_replay_refuses_without_the_explicit_flag(tmp_path: Path) -> None:
    from warden.legacy_replay import LegacyReplayRefusedError, run_generate_positions_in_process

    module = repo_module(tmp_path)
    contract = _minimal_contract()
    with pytest.raises(LegacyReplayRefusedError):
        run_generate_positions_in_process(module, _panel(n_bars=4), contract, {}, allow_legacy_replay=False)


def test_engine_entry_points_are_only_called_from_the_kernel() -> None:
    """``screen``/``backtest``/``qualify`` and their helpers own no Engine call."""
    for rel in (
        "src/warden/runner.py",
        "src/warden/qualify.py",
        "src/warden/cli.py",
        "src/warden/leakage.py",
        "src/warden/protocols.py",
    ):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "run_engine_strategy(" not in source, rel
        assert "run_engine_simulation(" not in source, rel
    kernel_source = (REPO_ROOT / "src" / "warden" / "evaluation.py").read_text(encoding="utf-8")
    assert "engine_sim.run_engine_strategy(" in kernel_source
    assert "engine_sim.run_engine_simulation(" in kernel_source


def test_screen_and_backtest_execute_through_the_kernel(
    tiny_repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tiny_repo()
    monkeypatch.chdir(fixture.repo)
    calls: list[str] = []
    original = EvaluationKernel.run

    def _spy(self, mod, *args, **kwargs):
        calls.append("run")
        return original(self, mod, *args, **kwargs)

    monkeypatch.setattr(EvaluationKernel, "run", _spy)
    runner = ExperimentRunner(registry=fixture.registry, repo_root=fixture.repo)
    report = runner.run_stage1_screen(fixture.spec)
    assert report.strategy_family == "rules_ts"
    assert calls, "screen did not execute through EvaluationKernel"
    calls.clear()
    res = runner.run_spec(fixture.spec, override_run_id="RUN-T10-KERNEL")
    assert res.run_id == "RUN-T10-KERNEL"
    assert calls, "backtest did not execute through EvaluationKernel"


def repo_module(tmp_path: Path):
    """A trivially importable legacy module for the replay-refusal test."""
    import types

    module = types.ModuleType("legacy_fixture")
    module.__file__ = str(tmp_path / "legacy_fixture.py")

    def generate_positions(panel_df, contract, **kwargs):
        return {"positions": np.zeros((panel_df["timestamp"].n_unique(), 1))}

    module.generate_positions = generate_positions
    return module


def _minimal_contract() -> EvaluationContract:
    return EvaluationContract(
        contract_id="T10_MIN",
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path="e.parquet",
        holdout_data_path="h.parquet",
        train_start=datetime(2025, 1, 1, tzinfo=UTC),
        val_start=datetime(2025, 1, 2, tzinfo=UTC),
        val_end=datetime(2025, 1, 3, tzinfo=UTC),
        holdout_start=datetime(2025, 1, 3, tzinfo=UTC),
        holdout_end=datetime(2025, 1, 4, tzinfo=UTC),
        n_folds=2,
        label_horizon_bars=2,
        embargo_bars=1,
        execution_delay_bars=1,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )


# ------------------------------------------- proof reuse is evaluator-owned (F0)


class _LeakyFuturePeekPipeline:
    """A feature that peeks at the panel's final bar; the gate must always refuse it."""

    def __init__(self) -> None:
        self.calls = 0

    def compute(self, bars_df, contract):
        frame = bars_df.collect() if isinstance(bars_df, pl.LazyFrame) else bars_df
        frame = frame.sort("timestamp", "asset")
        self.calls += 1
        return frame.select("timestamp", "asset").with_columns(
            pl.lit(float(frame["close"][-1])).alias("peek")
        )


def _leaky_panel() -> tuple[pl.DataFrame, EvaluationContract]:
    panel = _panel(24).filter(pl.col("asset") == "AAA")
    contract = _minimal_contract()
    return panel, contract


def test_strategy_supplied_prefix_proof_cannot_short_circuit_the_leakage_gate() -> None:
    """A claim on the object under test is not a proof.

    Three shapes of strategy-supplied ``prefix_proof`` are attached to a pipeline that
    genuinely peeks at the future: a duck-typed object whose ``covers`` always says yes,
    a real-looking :class:`PrefixInvarianceProof` that the evaluator never issued, and
    that same unissued proof on the evaluator's own pipeline class.  All three must be
    ignored, with the full sampled check refusing the leak.
    """
    from types import SimpleNamespace

    from warden.evaluation import (
        PrefixInvarianceProof,
        canonical_json,
        normalise_panel,
        panel_digest,
    )
    from warden.leakage import LookaheadLeakageError, verify_prefix_invariance

    panel, contract = _leaky_panel()

    def _unissued_proof() -> PrefixInvarianceProof:
        return PrefixInvarianceProof(
            protocol="causal_rules",
            panel_sha256=panel_digest(normalise_panel(panel)),
            contract_spec_hash=contract.spec_hash(),
            params_json=canonical_json({}),
            policy_json="{}",
            checkpoints=("a", "b"),
            evidence=(("a", "0" * 64), ("b", "1" * 64)),
            engine_runs=0,
            strategy_sha256="2" * 64,
        )

    duck = _LeakyFuturePeekPipeline()
    duck.prefix_proof = SimpleNamespace(covers=lambda *args, **kwargs: True)
    with pytest.raises(LookaheadLeakageError):
        verify_prefix_invariance(duck, panel, contract)
    assert duck.calls > 0, "the sampled check must actually have run"

    lookalike = _LeakyFuturePeekPipeline()
    lookalike.prefix_proof = _unissued_proof()
    with pytest.raises(LookaheadLeakageError):
        verify_prefix_invariance(lookalike, panel, contract)

    # The evaluator's own pipeline class is no substitute for an issued proof either.
    disguised = RulesProofPipeline(
        repo_root=str(REPO_ROOT),
        strategy_module="strategies/candidates/rules_momentum.py",
        code_sha256="2" * 64,
        params={},
        prefix_proof=_unissued_proof(),
    )
    disguised.compute = _LeakyFuturePeekPipeline().compute
    with pytest.raises(LookaheadLeakageError):
        verify_prefix_invariance(disguised, panel, contract)


def test_only_an_issued_proof_covering_this_execution_satisfies_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fast path still exists — and only for an issued, covering, same-code proof."""
    from warden.evaluation import (
        PrefixInvarianceProof,
        canonical_json,
        normalise_panel,
        panel_digest,
        register_prefix_proof,
        trusted_prefix_proof,
    )
    from warden.leakage import verify_prefix_invariance

    panel, contract = _leaky_panel()
    code_sha = "3" * 64
    proof = PrefixInvarianceProof(
        protocol="causal_rules",
        panel_sha256=panel_digest(normalise_panel(panel)),
        contract_spec_hash=contract.spec_hash(),
        params_json=canonical_json({}),
        policy_json="{}",
        checkpoints=("a", "b"),
        evidence=(("a", "0" * 64), ("b", "1" * 64)),
        engine_runs=0,
        strategy_sha256=code_sha,
    )
    pipeline = RulesProofPipeline(
        repo_root=str(REPO_ROOT),
        strategy_module="strategies/candidates/rules_momentum.py",
        code_sha256=code_sha,
        params={},
        prefix_proof=proof,
    )
    # Unissued: the identical object is still not evidence.
    assert trusted_prefix_proof(pipeline, panel, contract) is None

    register_prefix_proof(proof)
    assert trusted_prefix_proof(pipeline, panel, contract) is proof

    # Issued for another strategy code closure: refused, because the panel identity says
    # nothing about which module was executed on it.
    other_module = RulesProofPipeline(
        repo_root=str(REPO_ROOT),
        strategy_module="strategies/candidates/rules_momentum.py",
        code_sha256="4" * 64,
        params={},
        prefix_proof=proof,
    )
    assert trusted_prefix_proof(other_module, panel, contract) is None

    # Issued and covering: the gate returns without re-running the pipeline at all.
    def _explode(*args, **kwargs):  # pragma: no cover - asserted by the raise
        raise AssertionError("the gate re-executed a proven prefix-invariant pipeline")

    monkeypatch.setattr(RulesProofPipeline, "compute", _explode)
    verify_prefix_invariance(pipeline, panel, contract)


def test_a_causal_model_screen_has_no_second_execution_path() -> None:
    """#96: an active protocol screens through the kernel, never the frozen arithmetic."""
    source = (REPO_ROOT / "src" / "warden" / "runner.py").read_text(encoding="utf-8")
    assert "run_causal_model_strategy(" not in source
    # The frozen replay arithmetic is still reachable — but only on the legacy branch.
    assert source.count("sim1 = simulate_positions(") == 1
    assert "out = _run_strategy_isolated(" in source
