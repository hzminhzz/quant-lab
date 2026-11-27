"""Ticket 09 tests: carry_rv and meta_overlay Stage-1 and Stage-3 gates."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

from warden.cli import main as cli_main
from warden.contracts import ContractValidationError, EvaluationContract, HypothesisSpec
from warden.gates import apply_causal_meta_overlay, evaluate_stage1_meta_overlay
from warden.registry import HypothesisBudgetExhaustedError, ResearchRegistry


def _forbid_engine_execution(
    monkeypatch: pytest.MonkeyPatch, *, message: str, raise_on_call: bool = True
) -> dict[str, int]:
    """Guard the real execution seams, wherever the refactor moved them.

    ``warden.runner`` no longer owns engine helpers: after the evaluation-kernel
    refactor every in-process execution funnels through
    :meth:`warden.evaluation.EvaluationKernel.run` and finally through
    ``ml4t.backtest.Engine.run``.  Patching the class methods (not a module
    attribute that can be renamed or re-exported) keeps the assertion meaningful:
    a stale symbol would make the hook vacuous, which is exactly how the previous
    ``runner.run_engine_strategy`` / ``runner.run_engine_simulation`` hooks broke.
    The kernel seam is included so a memoised tape cannot satisfy the guard with
    zero Engine runs.

    Returns a counter; with ``raise_on_call`` the first execution raises.
    """
    import ml4t.backtest as bt

    from warden.evaluation import EvaluationKernel

    calls = {"n": 0}
    original_engine_run = bt.Engine.run
    original_kernel_run = EvaluationKernel.run

    def _counted_engine_run(self, *args, **kwargs):
        calls["n"] += 1
        if raise_on_call:
            raise AssertionError(message)
        return original_engine_run(self, *args, **kwargs)

    def _counted_kernel_run(self, *args, **kwargs):
        calls["n"] += 1
        if raise_on_call:
            raise AssertionError(message)
        return original_kernel_run(self, *args, **kwargs)

    monkeypatch.setattr(bt.Engine, "run", _counted_engine_run)
    monkeypatch.setattr(EvaluationKernel, "run", _counted_kernel_run)
    # A stale symbol cannot leave the guard installed-but-inert: assert the patch
    # is on the very objects the production path resolves.
    assert bt.Engine.run is _counted_engine_run
    assert EvaluationKernel.run is _counted_kernel_run
    return calls


def _hash_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_carry_panel(
    *,
    stationary: bool,
    positive_carry: bool,
    n_bars: int = 240,
    seed: int = 42,
) -> pl.DataFrame:
    """Deterministic 2-asset panel for carry_rv tests.

    Asset A and B track a common factor plus a spread. When ``stationary`` is
    True, the log-price spread follows a strongly mean-reverting AR(1) process;
    when False, it follows a unit-root random walk.
    """
    rng = np.random.default_rng(seed)
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    common = np.cumsum(rng.normal(0.0, 0.001, n_bars))
    spread = np.zeros(n_bars, dtype=float)
    for t in range(1, n_bars):
        eps = float(rng.normal(0.0, 0.008))
        if stationary:
            spread[t] = 0.40 * spread[t - 1] + eps
        else:
            spread[t] = spread[t - 1] + 0.02 * eps

    pa = 100.0 * np.exp(common + 0.5 * spread)
    pb = 100.0 * np.exp(common - 0.5 * spread)
    # In crypto_perps, funding_rate > 0 means longs pay shorts.
    # Going short A / long B captures (funding_A - funding_B) when A has higher funding.
    f_a = 0.0015 if positive_carry else -0.0015
    f_b = -0.0015 if positive_carry else 0.0015

    rows: list[dict] = []
    for t in range(n_bars):
        ts = t0 + timedelta(hours=t)
        for asset, px, f_rate in (("AAA", pa, f_a), ("BBB", pb, f_b)):
            o = float(px[t])
            c = float(px[min(t + 1, n_bars - 1)]) if t + 1 < n_bars else o
            hi = max(o, c) * 1.002
            lo = min(o, c) * 0.998
            rows.append(
                {
                    "timestamp": ts,
                    "asset": asset,
                    "open": o,
                    "high": hi,
                    "low": lo,
                    "close": c,
                    "volume": 5000.0,
                    "funding_rate": float(f_rate),
                }
            )
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
    (repo / "pyproject.toml").write_text("[project]\nname='t09'\n", encoding="utf-8")
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

    cid = "T09_CRYPTO_1H"
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
                "source": "t09_test",
            }
        ),
        encoding="utf-8",
    )
    reg = ResearchRegistry(repo / "registry" / "registry.db")
    reg.register_contract(contract)
    generate_manifest(repo)
    return repo, contract


def _write_carry_strategy(repo: Path, rel_path: str = "strategies/candidates/carry_pair.py") -> str:
    code = '''"""Causal carry/relative-value pair strategy for Ticket 09 tests."""

from __future__ import annotations

import math
from typing import Any

from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor

from warden.contracts import EvaluationContract

portfolio_coupled = True


def required_symbols(params: dict[str, Any]) -> list[str] | None:
    syms = params.get("target_symbols")
    return [str(s) for s in syms] if syms else ["AAA", "BBB"]


class CausalCarryPairStrategy(Strategy):
    def __init__(self, *, weight: float = 0.40, z_entry: float = 0.5) -> None:
        self._weight = float(weight)
        self._z_entry = float(z_entry)
        self._executor = TargetWeightExecutor(
            RebalanceConfig(
                allow_short=True,
                allow_fractional=True,
                max_single_weight=0.45,
                max_gross_leverage=0.95,
            )
        )
        self._spreads: list[float] = []

    def on_data(
        self,
        timestamp: Any,
        data: dict[str, dict[str, Any]],
        context: dict[str, Any],
        broker: Any,
    ) -> None:
        _ = context
        if "AAA" not in data or "BBB" not in data:
            return
        pa = float(data["AAA"]["close"])
        pb = float(data["BBB"]["close"])
        if pa <= 0.0 or pb <= 0.0:
            return
        sp = math.log(pa) - math.log(pb)
        self._spreads.append(sp)
        # Base carry tilt: short higher-funding AAA, long BBB, modulated by causal spread z-score.
        w_a = -self._weight
        w_b = self._weight
        if len(self._spreads) >= 12:
            win = self._spreads[-12:]
            mu = sum(win) / len(win)
            var = sum((x - mu) ** 2 for x in win) / max(1, len(win) - 1)
            sd = math.sqrt(var) if var > 1e-12 else 1.0
            z = (sp - mu) / sd
            if z < -self._z_entry:
                # Spread unusually cheap, scale down short A slightly to reduce excursion risk
                w_a *= 0.8
                w_b *= 0.8
        self._executor.execute({"AAA": w_a, "BBB": w_b}, data, broker, timestamp=timestamp)


def configure_strategy(contract: EvaluationContract, params: dict[str, Any]) -> Strategy:
    _ = contract
    return CausalCarryPairStrategy(
        weight=float(params.get("weight", 0.40)),
        z_entry=float(params.get("z_entry", 0.5)),
    )


# The strategy's own cost assumption: it rebalances the pair and expects to hold the hedge
# for the horizon the hypothesis declares (``expected_holding_bars``), so its round-trip cost
# is amortised over that many bars.
HOLDING_BARS = 24


def causal_carry_outputs(*, panel_df, contract, val_slice, timestamps, assets, params):
    """The pair's own causal spread and the net carry its book actually collects.

    This is the mechanism the strategy trades (short AAA / long BBB on the funding tilt),
    not a generic panel stand-in: the spread is the log ratio of the two legs it holds, and
    the net carry is the funding its direction receives minus its own cost per bar.
    """
    import numpy as np
    import polars as pl

    _ = assets
    wide = (
        panel_df.filter(pl.col("asset").is_in(["AAA", "BBB"]))
        .pivot(index="timestamp", on="asset", values="close")
        .sort("timestamp")
    )
    wide = wide.join(
        panel_df.filter(pl.col("asset") == "AAA").select("timestamp", "funding_rate"),
        on="timestamp",
        how="left",
    ).rename({"funding_rate": "fund_a"})
    wide = wide.join(
        panel_df.filter(pl.col("asset") == "BBB").select("timestamp", "funding_rate"),
        on="timestamp",
        how="left",
    ).rename({"funding_rate": "fund_b"})
    a = wide["AAA"].to_numpy().astype(float)
    b = wide["BBB"].to_numpy().astype(float)
    f_a = np.nan_to_num(wide["fund_a"].to_numpy().astype(float))
    f_b = np.nan_to_num(wide["fund_b"].to_numpy().astype(float))
    spread = np.log(np.maximum(a, 1e-12)) - np.log(np.maximum(b, 1e-12))

    start = int(val_slice.start or 0)
    stop = int(val_slice.stop if val_slice.stop is not None else len(spread))
    direction = params.get("carry_direction")
    if direction is not None:
        w0, w1 = float(direction[0]), float(direction[1])
    else:
        # Same pre-validation-only direction estimate the strategy's funding tilt expresses.
        train_end = max(1, start)
        mean_diff = float(np.mean(f_a[:train_end] - f_b[:train_end]))
        w0, w1 = (-1.0, 1.0) if mean_diff >= 0.0 else (1.0, -1.0)
    cost_per_bar = (2.0 * contract.total_one_way_cost_decimal()) / float(HOLDING_BARS)
    net_carry = -(w0 * f_a + w1 * f_b) - cost_per_bar
    return {"spread": spread[start:stop], "net_carry": net_carry[start:stop]}
'''
    (repo / rel_path).write_text(code, encoding="utf-8")
    return rel_path


def _register_hyp_and_spec(
    repo: Path,
    contract: EvaluationContract,
    *,
    hyp_id: str,
    family: str,
    strat_module: str,
    parent_hypothesis_id: str | None = None,
    parameters: dict | None = None,
    run_id: str | None = None,
    expected_holding_bars: int = 24,
) -> Path:
    hyp_md = repo / ".workspace" / "hypotheses" / f"{hyp_id}.md"
    hyp_md.parent.mkdir(parents=True, exist_ok=True)
    parent_line = f"parent_hypothesis_id: {parent_hypothesis_id}\n" if parent_hypothesis_id else ""
    hyp_md.write_text(
        f"---\n"
        f"hypothesis_id: {hyp_id}\n"
        f"contract_id: {contract.contract_id}\n"
        f"title: Ticket 09 test {hyp_id}\n"
        f"strategy_family: {family}\n"
        f"{parent_line}"
        f"economic_mechanism: Positive funding carry with mean-reverting pair spread capture\n"
        f"counterparty_or_friction: Taker fee and slippage paid when rebalancing pair hedge\n"
        f"expected_holding_bars: {expected_holding_bars}\n"
        f"falsification_criteria: Reject at Stage 1 if carry <= 0 or spread non-stationary\n"
        f"---\n",
        encoding="utf-8",
    )
    spec_payload: dict = {
        "contract_id": contract.contract_id,
        "hypothesis_id": hyp_id,
        "strategy_name": f"Strat_{hyp_id}",
        "strategy_family": family,
        "model_type": "rules",
        "strategy_module": strat_module,
        "parameters": parameters or {},
    }
    if run_id is not None:
        spec_payload["run_id"] = run_id
    spec_yaml = repo / "experiments" / "active" / f"{hyp_id.lower()}.yaml"
    spec_yaml.write_text(yaml.safe_dump(spec_payload), encoding="utf-8")
    assert cli_main(["register-hypothesis", str(hyp_md), "--json"]) == 0
    return spec_yaml


def test_carry_rv_rejects_nonstationary_random_walk_at_stage1_without_engine(
    pre_rg05_policy,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _make_carry_panel(stationary=False, positive_carry=True, n_bars=240, seed=17)
    repo, contract = _setup_repo(tmp_path, name="carry_nonstat", panel=panel)
    strat_rel = _write_carry_strategy(repo)
    monkeypatch.chdir(repo)

    spec_yaml = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T09-CARRY-RW",
        family="carry_rv",
        strat_module=strat_rel,
        run_id="RUN-T09-CARRY-RW",
    )
    capsys.readouterr()

    engine_calls = _forbid_engine_execution(
        monkeypatch,
        message="ml4t.backtest.Engine must not run when carry_rv fails Stage 1",
    )

    rc_screen = cli_main(["screen", str(spec_yaml), "--json"])
    out_screen = json.loads(capsys.readouterr().out)
    assert rc_screen == 1
    assert out_screen["strategy_family"] == "carry_rv"
    assert out_screen["passed"] is False
    assert out_screen["metrics"]["adf_p_value"] >= 0.05
    # The gate scores the strategy's own published series, not the panel proxy (#99).
    assert out_screen["metrics"]["carry_series_source"] == "strategy_causal_outputs"
    assert out_screen["metrics"]["carry_mechanism_policy"] == "active_own_outputs_required"
    assert engine_calls["n"] == 0

    rc_bt = cli_main(["backtest", str(spec_yaml), "--json"])
    captured = capsys.readouterr()
    assert rc_bt == 1
    assert "screen outcome REJECT" in captured.err
    assert "WARDEN_RUN_COMPLETE" not in captured.out
    assert engine_calls["n"] == 0


def test_carry_rv_rejects_negative_net_carry_at_stage1_without_engine(
    pre_rg05_policy,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _make_carry_panel(stationary=True, positive_carry=False, n_bars=240, seed=19)
    repo, contract = _setup_repo(tmp_path, name="carry_neg", panel=panel)
    strat_rel = _write_carry_strategy(repo)
    monkeypatch.chdir(repo)

    spec_yaml = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T09-CARRY-NEG",
        family="carry_rv",
        strat_module=strat_rel,
        parameters={"carry_direction": [-1.0, 1.0]},
        run_id="RUN-T09-CARRY-NEG",
    )
    capsys.readouterr()

    engine_calls = _forbid_engine_execution(
        monkeypatch, message="Engine must not run on negative net carry"
    )

    rc_screen = cli_main(["screen", str(spec_yaml), "--json"])
    out_screen = json.loads(capsys.readouterr().out)
    assert rc_screen == 1
    assert out_screen["strategy_family"] == "carry_rv"
    assert out_screen["passed"] is False
    assert out_screen["metrics"]["mean_net_carry"] <= 0.0
    assert out_screen["metrics"]["carry_series_source"] == "strategy_causal_outputs"
    assert engine_calls["n"] == 0


def test_carry_rv_stationary_positive_carry_passes_stage1_through_stage3(
    pre_rg05_policy,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _make_carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=23)
    repo, contract = _setup_repo(tmp_path, name="carry_pass", panel=panel)
    strat_rel = _write_carry_strategy(repo)
    monkeypatch.chdir(repo)

    spec_yaml = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T09-CARRY-OK",
        family="carry_rv",
        strat_module=strat_rel,
        run_id="RUN-T09-CARRY-OK",
    )
    capsys.readouterr()

    # Positive control for the guard used by the two "must not run the Engine" tests:
    # the same seams do observe real executions when the gate passes, so a zero count
    # there is meaningful and not a vacuous hook.
    engine_calls = _forbid_engine_execution(
        monkeypatch,
        message="this carry_rv run is expected to reach the Engine",
        raise_on_call=False,
    )

    assert cli_main(["verify", str(spec_yaml), "--json"]) == 0
    verify_out = json.loads(capsys.readouterr().out)
    assert verify_out["status"] == "PASS"
    assert engine_calls["n"] >= 1

    assert cli_main(["screen", str(spec_yaml), "--json"]) == 0
    screen_out = json.loads(capsys.readouterr().out)
    assert screen_out["strategy_family"] == "carry_rv"
    assert screen_out["passed"] is True
    assert screen_out["metrics"]["adf_p_value"] < 0.05
    assert screen_out["metrics"]["mean_net_carry"] > 0.0
    assert screen_out["metrics"]["carry_series_source"] == "strategy_causal_outputs"
    assert screen_out["metrics"]["carry_mechanism_policy"] == "active_own_outputs_required"
    assert "max_abs_excursion_z" in screen_out["metrics"]
    assert "p99_abs_excursion_z" in screen_out["metrics"]
    assert "max_spread_drawdown" in screen_out["metrics"]

    assert cli_main(["backtest", str(spec_yaml), "--json"]) == 0
    bt_lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert bt_lines[-1] == "WARDEN_RUN_COMPLETE"
    bt_payload = json.loads(bt_lines[0])
    assert bt_payload["strategy_family"] == "carry_rv"
    assert bt_payload["stage_reached"] == "STAGE_3_STRESS"
    assert bt_payload["net_sharpe"] > 1.0
    assert bt_payload.get("funding_events", True)
    diag = json.loads(bt_payload["diagnostics_json"])
    assert "stage1_carry_rv" in diag
    assert diag["stage1_carry_rv"]["metrics"]["adf_p_value"] < 0.05


def _make_meta_panel(n_bars: int = 240, seed: int = 31) -> pl.DataFrame:
    """Deterministic single-asset panel with trend regime + noisy chop regime."""
    rng = np.random.default_rng(seed)
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    rows: list[dict] = []
    px = 100.0
    for t in range(n_bars):
        ts = t0 + timedelta(hours=t)
        if (t // 24) % 2 == 0:
            # Clean trend regime: steady upward drift, low bar-to-bar noise
            ret = 0.0025 + float(rng.normal(0.0, 0.0012))
        else:
            # High-vol choppy whipsaw regime: zero drift, alternating reversals
            ret = (0.008 if t % 2 == 0 else -0.008) + float(rng.normal(0.0, 0.003))
        o = px
        c = max(1.0, o * (1.0 + ret))
        hi = max(o, c) * 1.002
        lo = min(o, c) * 0.998
        rows.append(
            {
                "timestamp": ts,
                "asset": "AAA",
                "open": float(o),
                "high": float(hi),
                "low": float(lo),
                "close": float(c),
                "volume": 5000.0,
                "funding_rate": 0.0,
            }
        )
        px = c
    return (
        pl.DataFrame(rows)
        .sort("timestamp", "asset")
        .with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    )


def _write_meta_parent_strategy(
    repo: Path, rel_path: str = "strategies/candidates/meta_parent_rules.py"
) -> str:
    code = '''"""Noisy primary CausalRulesStrategy for meta_overlay tests."""

from __future__ import annotations

from typing import Any

from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor

from warden.contracts import EvaluationContract


def required_symbols(params: dict[str, Any]) -> list[str] | None:
    syms = params.get("target_symbols")
    return [str(s) for s in syms] if syms else ["AAA"]


class NoisyPrimaryStrategy(Strategy):
    def __init__(self, *, weight: float = 0.60) -> None:
        self._weight = float(weight)
        self._prev_close: float | None = None
        self._executor = TargetWeightExecutor(
            RebalanceConfig(
                allow_short=True,
                allow_fractional=True,
                max_single_weight=0.80,
                max_gross_leverage=0.95,
            )
        )

    def on_data(
        self,
        timestamp: Any,
        data: dict[str, dict[str, Any]],
        context: dict[str, Any],
        broker: Any,
    ) -> None:
        _ = context
        bar = data.get("AAA")
        if bar is None:
            return
        px = float(bar["close"])
        if self._prev_close is None:
            self._prev_close = px
            return
        ret = (px - self._prev_close) / self._prev_close
        self._prev_close = px
        # Reacts to every 1-bar return -> high turnover & whipsaws in choppy regime.
        sig = 1.0 if ret >= 0.0 else -1.0
        self._executor.execute({"AAA": sig * self._weight}, data, broker, timestamp=timestamp)


def configure_strategy(contract: EvaluationContract, params: dict[str, Any]) -> Strategy:
    _ = contract
    return NoisyPrimaryStrategy(weight=float(params.get("weight", 0.60)))
'''
    (repo / rel_path).write_text(code, encoding="utf-8")
    return rel_path


def _write_meta_child_strategy(
    repo: Path, rel_path: str = "strategies/candidates/meta_child_overlay.py"
) -> str:
    code = '''"""Causal meta-labeling overlay using ml4t.engineer.labeling."""

from __future__ import annotations

from collections import deque
from typing import Any

import polars as pl
from ml4t.backtest import Strategy
from ml4t.backtest.execution.rebalancer import RebalanceConfig, TargetWeightExecutor
from ml4t.engineer.labeling import apply_meta_model, compute_bet_size, meta_labels

from warden.contracts import EvaluationContract


def required_symbols(params: dict[str, Any]) -> list[str] | None:
    syms = params.get("target_symbols")
    return [str(s) for s in syms] if syms else ["AAA"]


class CausalMetaOverlayStrategy(Strategy):
    def __init__(
        self,
        *,
        weight: float = 0.60,
        vol_cutoff: float = 0.0045,
        filter_enabled: bool = True,
    ) -> None:
        self._weight = float(weight)
        self._vol_cutoff = float(vol_cutoff)
        self._filter_enabled = bool(filter_enabled)
        self._prev_close: float | None = None
        self._history_sig: deque[float] = deque(maxlen=24)
        self._history_ret: deque[float] = deque(maxlen=24)
        self._executor = TargetWeightExecutor(
            RebalanceConfig(
                allow_short=True,
                allow_fractional=True,
                max_single_weight=0.80,
                max_gross_leverage=0.95,
            )
        )

    def on_data(
        self,
        timestamp: Any,
        data: dict[str, dict[str, Any]],
        context: dict[str, Any],
        broker: Any,
    ) -> None:
        _ = context
        bar = data.get("AAA")
        if bar is None:
            return
        px = float(bar["close"])
        if self._prev_close is None:
            self._prev_close = px
            return
        ret = (px - self._prev_close) / self._prev_close
        self._prev_close = px

        primary_sig = 1.0 if ret >= 0.0 else -1.0
        if self._history_sig:
            # Record realized return for previous bar's signal causally
            self._history_ret.append(ret)

        if not self._filter_enabled:
            self._history_sig.append(primary_sig)
            self._executor.execute({"AAA": primary_sig * self._weight}, data, broker, timestamp=timestamp)
            return

        # Causal meta-probability from trailing realized signal hit-rate and low-chop vol regime
        n_hist = min(len(self._history_sig), len(self._history_ret))
        if n_hist >= 4:
            hist_df = pl.DataFrame(
                {
                    "primary_signal": list(self._history_sig)[:n_hist],
                    "realized_ret": list(self._history_ret)[:n_hist],
                }
            )
            labeled = meta_labels(
                hist_df, signal_col="primary_signal", return_col="realized_ret", threshold=0.0
            ).drop_nulls("meta_label")
            hit_prob = float(labeled["meta_label"].mean()) if labeled.height > 0 else 0.5
            recent_abs = sum(abs(r) for r in list(self._history_ret)[-4:]) / 4.0
            meta_prob = hit_prob if recent_abs <= self._vol_cutoff else 0.20
        else:
            meta_prob = 0.75

        cur_df = pl.DataFrame(
            {"primary_signal": [1.0 if primary_sig > 0 else primary_sig], "meta_prob": [float(meta_prob)]}
        )
        cur_df = cur_df.with_columns(
            compute_bet_size("meta_prob", method="discrete", threshold=0.55).alias("bet_size")
        )
        sized_df = apply_meta_model(
            cur_df,
            primary_signal_col="primary_signal",
            meta_probability_col="meta_prob",
            bet_size_method="discrete",
            threshold=0.55,
            output_col="sized_signal",
        )
        sized_sig = float(sized_df["sized_signal"][0])
        self._history_sig.append(primary_sig)
        self._executor.execute({"AAA": sized_sig * self._weight}, data, broker, timestamp=timestamp)


def configure_strategy(contract: EvaluationContract, params: dict[str, Any]) -> Strategy:
    _ = contract
    return CausalMetaOverlayStrategy(
        weight=float(params.get("weight", 0.60)),
        vol_cutoff=float(params.get("vol_cutoff", 0.0045)),
        filter_enabled=bool(params.get("filter_enabled", True)),
    )
'''
    (repo / rel_path).write_text(code, encoding="utf-8")
    return rel_path


def test_meta_overlay_requires_parent_hypothesis_id() -> None:
    with pytest.raises(ContractValidationError, match="parent_hypothesis_id"):
        HypothesisSpec(
            hypothesis_id="HYP-T09-META-NOPARENT",
            contract_id="T09_CRYPTO_1H",
            title="Meta overlay without parent",
            strategy_family="meta_overlay",
            economic_mechanism="Filter noisy trades using meta-labeling probability threshold",
            counterparty_or_friction="Taker fee and slippage saved by abstaining in choppy regimes",
            expected_holding_bars=4,
            falsification_criteria="Reject if turnover reduction < 20% or Sharpe drops > 0.05",
            parent_hypothesis_id=None,
        )


def test_meta_overlay_rejects_missing_parent_stream_at_stage1(
    pre_rg05_policy,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _make_meta_panel(n_bars=240, seed=41)
    repo, contract = _setup_repo(tmp_path, name="meta_missing_parent", panel=panel)
    parent_rel = _write_meta_parent_strategy(repo)
    child_rel = _write_meta_child_strategy(repo)
    monkeypatch.chdir(repo)

    _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T09-PARENT-EMPTY",
        family="rules_ts",
        strat_module=parent_rel,
        expected_holding_bars=4,
    )
    child_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T09-META-ORPHAN",
        family="meta_overlay",
        strat_module=child_rel,
        parent_hypothesis_id="HYP-T09-PARENT-EMPTY",
        expected_holding_bars=4,
    )
    capsys.readouterr()

    rc = cli_main(["screen", str(child_spec), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["strategy_family"] == "meta_overlay"
    assert out["passed"] is False
    assert "parent" in out["notes"].lower()


def test_meta_overlay_rejects_unimproved_child_at_stage1(
    pre_rg05_policy,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _make_meta_panel(n_bars=240, seed=43)
    repo, contract = _setup_repo(tmp_path, name="meta_unimproved", panel=panel)
    parent_rel = _write_meta_parent_strategy(repo)
    child_rel = _write_meta_child_strategy(repo)
    monkeypatch.chdir(repo)

    parent_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T09-PARENT-1",
        family="rules_ts",
        strat_module=parent_rel,
        run_id="RUN-T09-PARENT-1",
        expected_holding_bars=4,
    )
    assert cli_main(["backtest", str(parent_spec), "--json"]) == 0
    capsys.readouterr()

    child_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T09-META-NOOP",
        family="meta_overlay",
        strat_module=child_rel,
        parent_hypothesis_id="HYP-T09-PARENT-1",
        parameters={"filter_enabled": False},
        run_id="RUN-T09-META-NOOP",
        expected_holding_bars=4,
    )
    capsys.readouterr()

    rc_screen = cli_main(["screen", str(child_spec), "--json"])
    screen_out = json.loads(capsys.readouterr().out)
    assert rc_screen == 1
    assert screen_out["strategy_family"] == "meta_overlay"
    assert screen_out["passed"] is False
    assert screen_out["metrics"]["turnover_reduction"] < 0.20

    rc_bt = cli_main(["backtest", str(child_spec), "--json"])
    captured = capsys.readouterr()
    assert rc_bt == 1
    assert "screen outcome REJECT" in captured.err
    assert "WARDEN_RUN_COMPLETE" not in captured.out


def test_meta_overlay_ml4t_engineer_filter_passes_stage1_through_stage3(
    pre_rg05_policy,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _make_meta_panel(n_bars=240, seed=47)
    repo, contract = _setup_repo(
        tmp_path,
        name="meta_pass",
        panel=panel,
        taker_fee_bps=1.0,
        slippage_bps=1.0,
    )
    parent_rel = _write_meta_parent_strategy(repo)
    child_rel = _write_meta_child_strategy(repo)
    monkeypatch.chdir(repo)

    parent_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T09-PARENT-2",
        family="rules_ts",
        strat_module=parent_rel,
        run_id="RUN-T09-PARENT-2",
        expected_holding_bars=4,
    )
    assert cli_main(["backtest", str(parent_spec), "--json"]) == 0
    capsys.readouterr()

    child_spec = _register_hyp_and_spec(
        repo,
        contract,
        hyp_id="HYP-T09-META-OK",
        family="meta_overlay",
        strat_module=child_rel,
        parent_hypothesis_id="HYP-T09-PARENT-2",
        parameters={"filter_enabled": True, "vol_cutoff": 0.0045},
        run_id="RUN-T09-META-OK",
        expected_holding_bars=4,
    )
    capsys.readouterr()

    assert cli_main(["verify", str(child_spec), "--json"]) == 0
    verify_out = json.loads(capsys.readouterr().out)
    assert verify_out["status"] == "PASS"

    assert cli_main(["screen", str(child_spec), "--json"]) == 0
    screen_out = json.loads(capsys.readouterr().out)
    assert screen_out["strategy_family"] == "meta_overlay"
    assert screen_out["passed"] is True
    assert screen_out["metrics"]["turnover_reduction"] > 0.25
    assert screen_out["metrics"]["sharpe_delta"] >= -0.05

    assert cli_main(["backtest", str(child_spec), "--json"]) == 0
    bt_lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert bt_lines[-1] == "WARDEN_RUN_COMPLETE"
    bt_payload = json.loads(bt_lines[0])
    assert bt_payload["strategy_family"] == "meta_overlay"
    assert bt_payload["stage_reached"] == "STAGE_3_STRESS"
    diag = json.loads(bt_payload["diagnostics_json"])
    assert diag["stage1_meta_overlay"]["parent_run_id"] == "RUN-T09-PARENT-2"
    assert diag["stage1_meta_overlay"]["metrics"]["turnover_reduction"] > 0.25


def test_meta_overlay_alignment_and_shared_lineage_budget(tmp_path: Path) -> None:
    p = np.linspace(0.001, 0.002, 50)
    c = np.linspace(0.001, 0.002, 49)
    with pytest.raises(ValueError, match="aligned"):
        evaluate_stage1_meta_overlay(p, c, 100.0, 60.0, bars_per_year=8760)

    panel = _make_meta_panel(n_bars=120, seed=53)
    repo, contract = _setup_repo(tmp_path, name="meta_lineage", panel=panel)
    reg = ResearchRegistry(repo / "registry" / "registry.db")

    parent_hyp = HypothesisSpec(
        hypothesis_id="HYP-T09-LIN-PARENT",
        contract_id=contract.contract_id,
        title="Lineage parent",
        strategy_family="rules_ts",
        economic_mechanism="Primary trend signal before meta-labeling filter",
        counterparty_or_friction="Taker fee and slippage paid on entries",
        expected_holding_bars=4,
        falsification_criteria="Reject if Sharpe < 1.0",
    )
    reg.register_hypothesis(parent_hyp)
    for i in range(3):
        reg.register_trial_start(parent_hyp.hypothesis_id, f"RUN-LIN-P-{i}")

    child_hyp = HypothesisSpec(
        hypothesis_id="HYP-T09-LIN-CHILD",
        contract_id=contract.contract_id,
        title="Lineage child",
        strategy_family="meta_overlay",
        parent_hypothesis_id=parent_hyp.hypothesis_id,
        economic_mechanism="Meta-labeling filter over parent hypothesis stream",
        counterparty_or_friction="Taker fee and slippage saved by abstention",
        expected_holding_bars=4,
        falsification_criteria="Reject if turnover reduction < 20%",
    )
    reg.register_hypothesis(child_hyp)
    for i in range(3):
        reg.register_trial_start(child_hyp.hypothesis_id, f"RUN-LIN-C-{i}")

    sibling_hyp = HypothesisSpec(
        hypothesis_id="HYP-T09-LIN-SIB",
        contract_id=contract.contract_id,
        title="Lineage sibling",
        strategy_family="meta_overlay",
        parent_hypothesis_id=parent_hyp.hypothesis_id,
        economic_mechanism="Second meta-labeling variant sharing parent lineage budget",
        counterparty_or_friction="Taker fee and slippage saved by abstention",
        expected_holding_bars=4,
        falsification_criteria="Reject if turnover reduction < 20%",
    )
    reg.register_hypothesis(sibling_hyp)
    with pytest.raises(HypothesisBudgetExhaustedError, match="lineage"):
        reg.register_trial_start(sibling_hyp.hypothesis_id, "RUN-LIN-S-0")


def test_meta_overlay_helper_cvar_reduction_and_sharpe_degradation_guard() -> None:
    # Exercise apply_causal_meta_overlay directly with ml4t-engineer labeling primitives
    overlay_out = apply_causal_meta_overlay(
        primary_signal=[1.0, 1.0, -1.0, 0.0, 1.0],
        realized_returns=[0.02, -0.01, -0.03, 0.01, 0.015],
        meta_probability=[0.85, 0.25, 0.80, 0.50, 0.90],
        method="discrete",
        threshold=0.55,
    )
    assert overlay_out["meta_label"].tolist() == [1.0, 0.0, 1.0, 0.0, 1.0]
    assert overlay_out["sized_signal"].tolist() == [1.0, 0.0, -1.0, 0.0, 1.0]

    # Statistically meaningful tail-risk (CVaR_99 / MaxDD) reduction passes even with < 20% turnover reduction
    rng = np.random.default_rng(77)
    parent_rets = rng.normal(0.0008, 0.002, 200)
    parent_rets[::20] = -0.045  # recurring tail crashes
    child_rets = np.where(parent_rets < -0.02, -0.001, parent_rets)
    tail_report = evaluate_stage1_meta_overlay(
        parent_rets,
        child_rets,
        parent_annualized_turnover=100.0,
        child_annualized_turnover=95.0,
        bars_per_year=8760,
    )
    assert tail_report.passed is True
    assert tail_report.metrics["cvar_99_reduction"] > 0.10
    assert tail_report.metrics["cvar_reduction_p_value"] < 0.05

    # Turnover reduction > 25% still fails if Sharpe degrades by more than 0.05
    degraded_rets = parent_rets - 0.0015
    degraded_report = evaluate_stage1_meta_overlay(
        parent_rets,
        degraded_rets,
        parent_annualized_turnover=100.0,
        child_annualized_turnover=50.0,
        bars_per_year=8760,
    )
    assert degraded_report.passed is False
    assert degraded_report.metrics["sharpe_delta"] < -0.05

