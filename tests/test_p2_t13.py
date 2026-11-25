"""T13 (#99): strategy-owned carry Stage 1, linear-first screen, and audit evidence.

Covers three integrity gaps:

1. ``carry_rv`` Stage 1 scores the strategy's *own* published causal spread / net-carry
   series (prefix-checked by the evaluator) instead of the equal-weight panel proxy, so a
   non-stationary or non-positive-carry mechanism fails even when the panel pair spread
   would pass.
2. ``warden screen`` enforces the linear-first rule (including the deep-model
   cost-breakeven hurdle) before reserving a screen slot, so a refusal writes no
   ``selection_attempts`` row.
3. Audit Check 1 reads the cadence measured on the executed tape, and Check 4 reads the
   folds that actually ran; both refuse recorded divergence / missing ML fold metadata
   instead of trusting static contract integers or a strategy's own claim.

Every gate here is exercised through evaluator-produced evidence: the cadence numbers come
from ``EvaluationTape.positions`` and the fold record from ``run_causal_model_strategy``,
never from a value the strategy hands in.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
import yaml

from warden.cli import main as cli_main
from warden.contracts import EvaluationContract, ExperimentResult
from warden.gates import (
    evaluate_cadence_alignment,
    evaluate_fold_isolation_evidence,
    measure_trading_cadence,
)
from warden.protocols import CARRY_MECHANISM_LEGACY, CarryMechanismRequiredError
from warden.registry import ResearchRegistry
from warden.runner import ExperimentRunner, LinearBaselineRequiredError

CARRY_STRATEGY_BODY = '''"""Causal carry/relative-value pair strategy for T13 tests."""

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

    def on_data(self, timestamp, data, context, broker) -> None:
        _ = context
        if "AAA" not in data or "BBB" not in data:
            return
        pa = float(data["AAA"]["close"])
        pb = float(data["BBB"]["close"])
        if pa <= 0.0 or pb <= 0.0:
            return
        sp = math.log(pa) - math.log(pb)
        self._spreads.append(sp)
        w_a, w_b = -self._weight, self._weight
        if len(self._spreads) >= 12:
            win = self._spreads[-12:]
            mu = sum(win) / len(win)
            var = sum((x - mu) ** 2 for x in win) / max(1, len(win) - 1)
            sd = math.sqrt(var) if var > 1e-12 else 1.0
            z = (sp - mu) / sd
            if z < -self._z_entry:
                w_a *= 0.8
                w_b *= 0.8
        self._executor.execute({"AAA": w_a, "BBB": w_b}, data, broker, timestamp=timestamp)


def configure_strategy(contract: EvaluationContract, params: dict[str, Any]) -> Strategy:
    _ = contract
    return CausalCarryPairStrategy(
        weight=float(params.get("weight", 0.40)),
        z_entry=float(params.get("z_entry", 0.5)),
    )
'''

CARRY_HOOK = '''

def causal_carry_outputs(*, panel_df, contract, val_slice, timestamps, assets, params):
    """Strategy-owned causal spread and net carry for the validation slice.

    ``carry_outputs_mode`` selects the mechanism under test: the plain log-price spread
    (stationary on a mean-reverting panel), an I(1) cumulation of it (non-stationary),
    a series that peeks at the last bar (lookahead), or a negative net carry.
    """
    import numpy as np
    import polars as pl

    _ = contract, timestamps, assets
    wide = (
        panel_df.filter(pl.col("asset").is_in(["AAA", "BBB"]))
        .pivot(index="timestamp", on="asset", values="close")
        .sort("timestamp")
    )
    a = wide["AAA"].to_numpy().astype(float)
    b = wide["BBB"].to_numpy().astype(float)
    base = np.log(a) - np.log(b)
    start = int(val_slice.start or 0)
    stop = int(val_slice.stop if val_slice.stop is not None else len(base))
    mode = str(params.get("carry_outputs_mode", "stationary"))
    if mode == "random_walk":
        spread = np.cumsum(base - float(np.mean(base[: max(1, start)])))
    elif mode == "lookahead":
        spread = base + 1.0e-3 * (base[-1] - base[0])
    else:
        spread = base
    level = -5.0e-4 if mode == "negative_carry" else 3.0e-4
    net_carry = np.full(len(spread), level, dtype=float)
    return {"spread": spread[start:stop], "net_carry": net_carry[start:stop]}
'''


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _carry_panel(
    *, stationary: bool, positive_carry: bool, n_bars: int = 240, seed: int = 42
) -> pl.DataFrame:
    """Two-asset panel whose log-price spread is either mean-reverting or a random walk."""
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
    f_a = 0.0015 if positive_carry else -0.0015
    f_b = -0.0015 if positive_carry else 0.0015
    rows: list[dict] = []
    for t in range(n_bars):
        stamp = t0 + timedelta(hours=t)
        for asset, px, rate in (("AAA", pa, f_a), ("BBB", pb, f_b)):
            o = float(px[t])
            c = float(px[min(t + 1, n_bars - 1)]) if t + 1 < n_bars else o
            rows.append(
                {
                    "timestamp": stamp,
                    "asset": asset,
                    "open": o,
                    "high": max(o, c) * 1.002,
                    "low": min(o, c) * 0.998,
                    "close": c,
                    "volume": 5000.0,
                    "funding_rate": float(rate),
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
    for rel in ("src/warden", "contracts/datasets", "strategies/candidates", "strategies/baselines",
                "experiments/active", "cache/features"):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / "AGENTS.md").write_text("# test\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='t13'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version=1\n", encoding="utf-8")

    ts = sorted(panel["timestamp"].unique().to_list())
    n_bars = len(ts)
    data_rel = "data/exploratory/panel.parquet"
    data_path = repo / data_rel
    data_path.parent.mkdir(parents=True, exist_ok=True)
    panel.write_parquet(data_path)
    sha = _sha(data_path)

    holdout_rel = "data/holdout/panel_holdout.parquet"
    holdout_path = repo / holdout_rel
    holdout_path.parent.mkdir(parents=True, exist_ok=True)
    panel.with_columns(
        (pl.col("timestamp") + timedelta(hours=n_bars + 2)).alias("timestamp")
    ).write_parquet(holdout_path)

    contract = EvaluationContract(
        contract_id="T13_CRYPTO_1H",
        asset_class="crypto_perps",
        frequency="1h",
        bars_per_year=8760,
        exploratory_data_path=data_rel,
        holdout_data_path=holdout_rel,
        train_start=ts[0],
        val_start=ts[max(24, n_bars // 3)],
        val_end=ts[-1] + timedelta(hours=1),
        holdout_start=ts[-1] + timedelta(hours=1),
        holdout_end=ts[-1] + timedelta(hours=1 + max(40, n_bars)),
        n_folds=n_folds,
        label_horizon_bars=label_horizon_bars,
        embargo_bars=embargo_bars,
        execution_delay_bars=execution_delay_bars,
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
                "path": data_rel,
                "sha256": sha,
                "holdout_path": holdout_rel,
                "holdout_sha256": _sha(holdout_path),
                "provenance": "verified_market",
                "source": "t13_test",
            }
        ),
        encoding="utf-8",
    )
    ResearchRegistry(repo / "registry" / "registry.db").register_contract(contract)
    generate_manifest(repo)
    return repo, contract


def _write_carry_strategy(repo: Path, *, publish: bool, name: str = "carry_pair.py") -> str:
    rel = f"strategies/candidates/{name}"
    body = CARRY_STRATEGY_BODY + (CARRY_HOOK if publish else "")
    (repo / rel).write_text(body, encoding="utf-8")
    return rel


def _write_ridge_strategy(repo: Path, name: str = "causal_ridge_copy.py") -> str:
    rel = f"strategies/candidates/{name}"
    source = Path(__file__).resolve().parents[1] / "strategies" / "baselines" / "causal_ridge_1h.py"
    (repo / rel).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return rel


def _register(
    repo: Path,
    contract: EvaluationContract,
    *,
    hyp_id: str,
    family: str,
    strat_module: str,
    model_type: str = "rules",
    model_class: str | None = None,
    parameters: dict | None = None,
    run_id: str | None = None,
    expected_holding_bars: int = 24,
) -> Path:
    hyp_md = repo / ".workspace" / "hypotheses" / f"{hyp_id}.md"
    hyp_md.parent.mkdir(parents=True, exist_ok=True)
    hyp_md.write_text(
        "---\n"
        f"hypothesis_id: {hyp_id}\n"
        f"contract_id: {contract.contract_id}\n"
        f"title: T13 test {hyp_id}\n"
        f"strategy_family: {family}\n"
        "economic_mechanism: Positive funding carry on a mean-reverting causal spread\n"
        "counterparty_or_friction: Taker fee and slippage paid when rebalancing the pair hedge\n"
        f"expected_holding_bars: {expected_holding_bars}\n"
        "falsification_criteria: Reject at Stage 1 if net carry <= 0 or the strategy spread is non-stationary\n"
        "---\n",
        encoding="utf-8",
    )
    payload: dict = {
        "contract_id": contract.contract_id,
        "hypothesis_id": hyp_id,
        "strategy_name": f"Strat_{hyp_id}",
        "strategy_family": family,
        "model_type": model_type,
        "strategy_module": strat_module,
        "parameters": parameters or {},
    }
    if model_class:
        payload["model_class"] = model_class
    if run_id:
        payload["run_id"] = run_id
    spec = repo / "experiments" / "active" / f"{hyp_id.lower()}.yaml"
    spec.write_text(yaml.safe_dump(payload), encoding="utf-8")
    assert cli_main(["register-hypothesis", str(hyp_md), "--json"]) == 0
    return spec


def _db(repo: Path) -> Path:
    return repo / "registry" / "registry.db"


def _count(repo: Path, table: str, hyp_id: str) -> int:
    with sqlite3.connect(_db(repo)) as conn:
        return int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE hypothesis_id = ?", (hyp_id,)
            ).fetchone()[0]
        )


def _diag(repo: Path, run_id: str) -> dict:
    with sqlite3.connect(_db(repo)) as conn:
        row = conn.execute(
            "SELECT diagnostics_json FROM trials WHERE run_id = ?", (run_id,)
        ).fetchone()
    assert row is not None, f"no trial row for {run_id}"
    return json.loads(row[0])


def _forbid_engine(monkeypatch: pytest.MonkeyPatch, message: str) -> dict[str, int]:
    """Fail if any in-process execution seam runs (both are asserted live, not assumed)."""
    import ml4t.backtest as bt

    from warden.evaluation import EvaluationKernel

    calls = {"n": 0}
    def _engine(self, *args, **kwargs):
        calls["n"] += 1
        raise AssertionError(message)

    def _kernel(self, *args, **kwargs):
        calls["n"] += 1
        raise AssertionError(message)

    monkeypatch.setattr(bt.Engine, "run", _engine)
    monkeypatch.setattr(EvaluationKernel, "run", _kernel)
    assert bt.Engine.run is _engine and EvaluationKernel.run is _kernel
    return calls


# ---------------------------------------------------------------------------
# 1. cadence / fold-isolation gate primitives
# ---------------------------------------------------------------------------


def test_measure_trading_cadence_reads_the_executed_tape() -> None:
    tape = np.array(
        [
            [0.0, 0.0],
            [0.5, -0.5],
            [0.5, -0.5],
            [0.5, -0.5],
            [0.0, 0.0],
        ]
    )
    measured = measure_trading_cadence(tape)
    assert measured["n_bars"] == 5.0
    assert measured["rebalance_events"] == 2.0  # entry and exit only
    assert measured["rebalance_cadence_bars"] == pytest.approx(2.5)
    assert measured["holding_cadence_bars"] == pytest.approx(3.0)
    assert measured["holding_completed_cadence_bars"] == pytest.approx(3.0)
    assert measured["holding_completed_epochs"] == 2
    assert measured["holding_censored_epochs"] == 0

    # a sign flip starts a new holding epoch even when the asset never went flat
    flipped = measure_trading_cadence(np.array([[1.0], [1.0], [-1.0], [-1.0]]))
    assert flipped["holding_cadence_bars"] == pytest.approx(2.0)
    assert flipped["rebalance_events"] == 2.0
    assert flipped["holding_completed_cadence_bars"] == pytest.approx(2.0)
    assert flipped["holding_completed_epochs"] == 1
    assert flipped["holding_censored_epochs"] == 1


def test_completed_holds_exclude_flat_gaps_before_reentry() -> None:
    tape = np.array([[1.0]] * 4 + [[0.0]] * 4 + [[1.0]] * 4 + [[0.0]] * 4)
    measured = measure_trading_cadence(tape)
    assert measured["holding_completed_cadence_bars"] == pytest.approx(4.0)
    assert measured["holding_completed_epochs"] == 2
    assert measured["holding_censored_epochs"] == 0


def test_cadence_alignment_requires_a_declaration_for_material_divergence() -> None:
    absent = evaluate_cadence_alignment(None, label_horizon_bars=4)
    assert absent["passed"] is True and absent["evidence"] == "absent"

    aligned = evaluate_cadence_alignment(
        {"rebalance_cadence_bars": 4.0, "holding_cadence_bars": 5.0}, label_horizon_bars=4
    )
    assert aligned["passed"] is True and aligned["material_divergence"] is False

    # Holding past the declared horizon is a material divergence on its own: the tape held a
    # position with no declared lifetime behind it.  The bare narrative is not a declaration.
    long_hold = evaluate_cadence_alignment(
        {
            "rebalance_cadence_bars": 1.0,
            "holding_cadence_bars": 160.0,
            "holding_completed_cadence_bars": 160.0,
            "holding_completed_epochs": 4,
            "expected_holding_bars": 2,
        },
        label_horizon_bars=2,
    )
    assert long_hold["passed"] is False
    assert long_hold["decision_divergence"] is False
    assert long_hold["holding_divergence"] is True
    assert long_hold["blocking_dimensions"] == ["holding"]
    assert "holding cadence 160.00 bars" in long_hold["reason"]
    assert "without declared alignment" in long_hold["reason"]

    # A hold declared up front, and consistent with the tape, is a valid alignment.
    declared_hold = evaluate_cadence_alignment(
        {
            "rebalance_cadence_bars": 1.0,
            "holding_cadence_bars": 160.0,
            "holding_completed_cadence_bars": 160.0,
            "holding_completed_epochs": 4,
            "expected_holding_bars": 2,
            "declared_alignment": {
                "reason": "carry hedge is held until the funding leg decays",
                "declared_holding_bars": 160,
            },
        },
        label_horizon_bars=2,
    )
    assert declared_hold["passed"] is True
    assert declared_hold["holding_divergence"] is True
    assert declared_hold["declared_alignment_valid"]["holding"] is True

    # ... but a declaration whose number contradicts the tape is not valid.
    contradicted = evaluate_cadence_alignment(
        {
            "rebalance_cadence_bars": 1.0,
            "holding_cadence_bars": 160.0,
            "holding_completed_cadence_bars": 160.0,
            "holding_completed_epochs": 4,
            "expected_holding_bars": 2,
            "declared_alignment": {
                "reason": "claims a two-bar hold",
                "declared_holding_bars": 2,
            },
        },
        label_horizon_bars=2,
    )
    assert contradicted["passed"] is False
    assert contradicted["declared_alignment_valid"]["holding"] is False

    # A position still open at the last bar is right-censored: the tape cannot measure its
    # lifetime, so it is reported and not judged (a buy-and-hold is not a cadence mismatch).
    censored = evaluate_cadence_alignment(
        {
            "rebalance_cadence_bars": 1.0,
            "holding_cadence_bars": 260.0,
            "holding_completed_cadence_bars": 0.0,
            "holding_completed_epochs": 0,
            "holding_censored_epochs": 2,
            "expected_holding_bars": 8,
        },
        label_horizon_bars=2,
    )
    assert censored["passed"] is True
    assert censored["holding_cadence_basis"] == "right_censored"
    assert censored["holding_divergence"] is None
    assert censored["holding_beyond_label_horizon"] is True

    divergent = evaluate_cadence_alignment(
        {"rebalance_cadence_bars": 64.0, "holding_cadence_bars": 64.0}, label_horizon_bars=2
    )
    assert divergent["passed"] is False
    assert divergent["material_divergence"] is True
    assert "without declared alignment" in divergent["reason"]

    declared = evaluate_cadence_alignment(
        {
            "rebalance_cadence_bars": 64.0,
            "holding_cadence_bars": 64.0,
            "declared_alignment": {
                "reason": "quarterly rebalance against a 2-bar entry label",
                "declared_cadence_bars": 64,
                "declared_holding_bars": 64,
            },
        },
        label_horizon_bars=2,
    )
    assert declared["passed"] is True
    assert declared["material_divergence"] is True
    assert declared["declared_alignment"]["reason"].startswith("quarterly rebalance")

    # a blank declaration is not a declaration
    blank = evaluate_cadence_alignment(
        {
            "rebalance_cadence_bars": 64.0,
            "holding_cadence_bars": 64.0,
            "declared_alignment": {"reason": "   ", "declared_cadence_bars": 64},
        },
        label_horizon_bars=2,
    )
    assert blank["passed"] is False

    # a trial that records an execution protocol but no cadence evidence is refused, not excused
    missing_after_execution = evaluate_cadence_alignment(
        None, label_horizon_bars=2, execution_protocol="causal_rules"
    )
    assert missing_after_execution["passed"] is False
    assert "cadence evidence" in missing_after_execution["reason"]


def _contract(**overrides) -> EvaluationContract:
    fields: dict[str, Any] = {
        "contract_id": "T13_UNIT",
        "asset_class": "crypto_perps",
        "frequency": "1h",
        "bars_per_year": 8760,
        "exploratory_data_path": "data/exploratory/x.parquet",
        "holdout_data_path": "data/holdout/x.parquet",
        "train_start": datetime(2025, 1, 1, tzinfo=UTC),
        "val_start": datetime(2025, 1, 2, tzinfo=UTC),
        "val_end": datetime(2025, 1, 3, tzinfo=UTC),
        "holdout_start": datetime(2025, 1, 3, tzinfo=UTC),
        "holdout_end": datetime(2025, 1, 4, tzinfo=UTC),
        "n_folds": 3,
        "label_horizon_bars": 2,
        "embargo_bars": 1,
        "execution_delay_bars": 1,
        "taker_fee_bps": 1.0,
        "slippage_bps": 1.0,
        "data_snapshot_sha256": "0" * 64,
        "data_provenance": "synthetic",
    }
    fields.update(overrides)
    return EvaluationContract(**fields)


def test_fold_isolation_check_requires_the_runtime_record_for_ml() -> None:
    contract = _contract()
    good = {
        "protocol": "causal_model",
        "folds": 3,
        "min_gap_bars": 4.0,
        "labels_stripped": True,
        "verified": True,
    }
    assert evaluate_fold_isolation_evidence(
        family="cross_sectional_ml", protocol="causal_model", evidence=good, contract=contract
    )["passed"] is True

    missing = evaluate_fold_isolation_evidence(
        family="cross_sectional_ml", protocol="causal_model", evidence=None, contract=contract
    )
    assert missing["passed"] is False
    assert missing["basis"] == "fold_isolation_evidence"

    short_gap = evaluate_fold_isolation_evidence(
        family="cross_sectional_ml",
        protocol="causal_model",
        evidence={**good, "min_gap_bars": 1.0},
        contract=contract,
    )
    assert short_gap["passed"] is False
    assert short_gap["checks"]["purge_embargo_applied"] is False

    unverified = evaluate_fold_isolation_evidence(
        family="cross_sectional_ml",
        protocol="causal_model",
        evidence={**good, "verified": False},
        contract=contract,
    )
    assert unverified["passed"] is False
    assert unverified["checks"]["evaluator_verified"] is False

    # non-ML trials keep the historical static basis
    assert evaluate_fold_isolation_evidence(
        family="rules_ts", protocol="causal_rules", evidence=None, contract=contract
    )["passed"] is True


# ---------------------------------------------------------------------------
# 2. carry Stage 1 on the strategy's own verified outputs
# ---------------------------------------------------------------------------


def test_strategy_published_nonstationary_spread_fails_stage1_while_panel_proxy_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=23)
    repo, contract = _setup_repo(tmp_path, name="carry_own_spread", panel=panel)
    proxy_rel = _write_carry_strategy(repo, publish=False, name="carry_pair_proxy.py")
    own_rel = _write_carry_strategy(repo, publish=True, name="carry_pair_own.py")
    monkeypatch.chdir(repo)

    # Control: without a published series the same panel passes on the panel proxy -- but only
    # through the explicitly authorized legacy-replay path (an operator flag, never a spec
    # field).  That is what makes this A/B discriminating rather than a waiver.
    proxy_spec = _register(
        repo, contract, hyp_id="HYP-T13-PROXY", family="carry_rv", strat_module=proxy_rel
    )
    capsys.readouterr()
    replay = ExperimentRunner(repo_root=repo, allow_legacy_replay=True)
    proxy_report = replay.run_stage1_screen(proxy_spec)
    assert proxy_report.passed is True
    assert proxy_report.metrics["carry_series_source"] == "panel_proxy"
    assert proxy_report.metrics["carry_mechanism_policy"] == CARRY_MECHANISM_LEGACY

    own_spec = _register(
        repo,
        contract,
        hyp_id="HYP-T13-OWN-RW",
        family="carry_rv",
        strat_module=own_rel,
        parameters={"carry_outputs_mode": "random_walk"},
    )
    capsys.readouterr()
    engine_calls = _forbid_engine(
        monkeypatch, "carry Stage 1 must not execute the Engine"
    )
    rc = cli_main(["screen", str(own_spec), "--json"])
    own_out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert own_out["strategy_family"] == "carry_rv"
    assert own_out["passed"] is False
    assert own_out["metrics"]["adf_p_value"] >= 0.05
    assert own_out["metrics"]["carry_series_source"] == "strategy_causal_outputs"
    assert own_out["metrics"]["carry_prefix_cuts"] == 2.0
    assert engine_calls["n"] == 0


def test_active_carry_without_own_outputs_is_refused_before_any_ledger_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#99: an active carry_rv run must publish its own series or fail before the ledger moves.

    The refusal is checked on both entry points and it is checked *by measurement*: no screen
    receipt, no selection attempt, no trial row, and an unchanged ``trials_used`` counter.
    """
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=29)
    repo, contract = _setup_repo(tmp_path, name="carry_no_mechanism", panel=panel)
    proxy_rel = _write_carry_strategy(repo, publish=False, name="carry_pair_proxy.py")
    monkeypatch.chdir(repo)

    spec = _register(
        repo, contract, hyp_id="HYP-T13-NO-MECH", family="carry_rv", strat_module=proxy_rel
    )
    capsys.readouterr()

    # screen refuses, with a typed error and no consumed slot
    runner = ExperimentRunner(repo_root=repo)
    with pytest.raises(CarryMechanismRequiredError, match="causal_carry_outputs"):
        runner.run_stage1_screen(spec)
    assert _count(repo, "screen_receipts", "HYP-T13-NO-MECH") == 0
    assert _count(repo, "selection_attempts", "HYP-T13-NO-MECH") == 0

    # the CLI fails closed with the same message, still without touching the ledger
    capsys.readouterr()
    assert cli_main(["screen", str(spec), "--json"]) == 1
    assert "causal_carry_outputs" in capsys.readouterr().err
    assert _count(repo, "screen_receipts", "HYP-T13-NO-MECH") == 0
    assert _count(repo, "selection_attempts", "HYP-T13-NO-MECH") == 0

    # backtest refuses too, and before the trial slot is registered
    capsys.readouterr()
    assert cli_main(["backtest", str(spec), "--json"]) == 1
    assert "causal_carry_outputs" in capsys.readouterr().err
    assert _count(repo, "trials", "HYP-T13-NO-MECH") == 0
    assert _count(repo, "selection_attempts", "HYP-T13-NO-MECH") == 0
    with sqlite3.connect(_db(repo)) as conn:
        assert conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T13-NO-MECH'"
        ).fetchone()[0] == 0

    # ... and the authorized replay of the same strategy still screens on the recorded proxy
    replay = ExperimentRunner(repo_root=repo, allow_legacy_replay=True)
    report = replay.run_stage1_screen(spec)
    assert report.passed is True
    assert report.metrics["carry_series_source"] == "panel_proxy"


def test_strategy_published_negative_net_carry_fails_stage1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=19)
    repo, contract = _setup_repo(tmp_path, name="carry_own_neg", panel=panel)
    own_rel = _write_carry_strategy(repo, publish=True, name="carry_pair_own.py")
    monkeypatch.chdir(repo)

    spec = _register(
        repo,
        contract,
        hyp_id="HYP-T13-OWN-NEG",
        family="carry_rv",
        strat_module=own_rel,
        parameters={"carry_outputs_mode": "negative_carry"},
    )
    capsys.readouterr()
    engine_calls = _forbid_engine(monkeypatch, "no Engine on negative published carry")
    rc = cli_main(["screen", str(spec), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["passed"] is False
    assert out["metrics"]["mean_net_carry"] <= 0.0
    assert out["metrics"]["adf_p_value"] < 0.05  # the published spread is stationary
    assert out["metrics"]["carry_series_source"] == "strategy_causal_outputs"
    assert engine_calls["n"] == 0


def test_strategy_published_lookahead_series_is_refused_before_the_screen_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=31)
    repo, contract = _setup_repo(tmp_path, name="carry_own_leak", panel=panel)
    own_rel = _write_carry_strategy(repo, publish=True, name="carry_pair_own.py")
    monkeypatch.chdir(repo)

    spec = _register(
        repo,
        contract,
        hyp_id="HYP-T13-OWN-LEAK",
        family="carry_rv",
        strat_module=own_rel,
        parameters={"carry_outputs_mode": "lookahead"},
    )
    capsys.readouterr()
    assert cli_main(["screen", str(spec), "--json"]) == 1
    captured = capsys.readouterr()
    assert "not causal" in captured.err
    # A refusal is evidence, not a consumed screen: no receipt, no selection attempt.
    assert _count(repo, "screen_receipts", "HYP-T13-OWN-LEAK") == 0
    assert _count(repo, "selection_attempts", "HYP-T13-OWN-LEAK") == 0


def test_strategy_published_stationary_spread_passes_stage1_and_reaches_stage3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=37)
    repo, contract = _setup_repo(tmp_path, name="carry_own_pass", panel=panel)
    own_rel = _write_carry_strategy(repo, publish=True, name="carry_pair_own.py")
    monkeypatch.chdir(repo)

    spec = _register(
        repo,
        contract,
        hyp_id="HYP-T13-OWN-OK",
        family="carry_rv",
        strat_module=own_rel,
        parameters={"carry_outputs_mode": "stationary"},
        run_id="RUN-T13-OWN-OK",
    )
    capsys.readouterr()

    assert cli_main(["verify", str(spec), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"

    assert cli_main(["screen", str(spec), "--json"]) == 0
    screen_out = json.loads(capsys.readouterr().out)
    assert screen_out["passed"] is True
    assert screen_out["metrics"]["adf_p_value"] < 0.05
    assert screen_out["metrics"]["mean_net_carry"] > 0.0
    assert screen_out["metrics"]["carry_series_source"] == "strategy_causal_outputs"

    assert cli_main(["backtest", str(spec), "--json"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines[-1] == "WARDEN_RUN_COMPLETE"
    payload = json.loads(lines[0])
    assert payload["stage_reached"] == "STAGE_3_STRESS"
    diag = json.loads(payload["diagnostics_json"])
    assert diag["stage1_carry_rv"]["metrics"]["carry_series_source"] == "strategy_causal_outputs"
    assert diag["stage1_carry_rv"]["metrics"]["adf_p_value"] < 0.05


# ---------------------------------------------------------------------------
# 3. linear-first enforcement in `screen`
# ---------------------------------------------------------------------------


def test_screen_refuses_deep_model_without_logged_linear_baseline_and_writes_no_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=41)
    repo, contract = _setup_repo(tmp_path, name="screen_deep", panel=panel)
    ridge_rel = _write_ridge_strategy(repo)
    monkeypatch.chdir(repo)

    spec = _register(
        repo,
        contract,
        hyp_id="HYP-T13-DEEP",
        family="cross_sectional_ml",
        strat_module=ridge_rel,
        model_type="CAEModel",
        model_class="CAEModel",
    )
    capsys.readouterr()
    runner = ExperimentRunner(repo_root=repo)
    with pytest.raises(LinearBaselineRequiredError, match="Linear baseline required"):
        runner.run_stage1_screen(spec)

    assert _count(repo, "selection_attempts", "HYP-T13-DEEP") == 0
    assert _count(repo, "screen_receipts", "HYP-T13-DEEP") == 0
    with sqlite3.connect(_db(repo)) as conn:
        assert conn.execute(
            "SELECT trials_used FROM hypotheses WHERE hypothesis_id='HYP-T13-DEEP'"
        ).fetchone()[0] == 0

    # the CLI entry point fails closed too, with the same zero-attempt guarantee
    assert cli_main(["screen", str(spec), "--json"]) == 1
    assert "Linear baseline required" in capsys.readouterr().err
    assert _count(repo, "selection_attempts", "HYP-T13-DEEP") == 0


def test_screen_refuses_nonlinear_model_without_logged_linear_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=43)
    repo, contract = _setup_repo(tmp_path, name="screen_nonlinear", panel=panel)
    ridge_rel = _write_ridge_strategy(repo)
    monkeypatch.chdir(repo)

    spec = _register(
        repo,
        contract,
        hyp_id="HYP-T13-NONLINEAR",
        family="cross_sectional_ml",
        strat_module=ridge_rel,
        model_type="LightGBM",
    )
    capsys.readouterr()
    with pytest.raises(LinearBaselineRequiredError, match="Linear baseline required"):
        ExperimentRunner(repo_root=repo).run_stage1_screen(spec)
    assert _count(repo, "selection_attempts", "HYP-T13-NONLINEAR") == 0


def test_screen_refuses_deep_model_when_logged_linear_baseline_misses_cost_breakeven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=47)
    repo, contract = _setup_repo(tmp_path, name="screen_breakeven", panel=panel)
    ridge_rel = _write_ridge_strategy(repo)
    monkeypatch.chdir(repo)

    spec = _register(
        repo,
        contract,
        hyp_id="HYP-T13-HURDLE",
        family="cross_sectional_ml",
        strat_module=ridge_rel,
        model_type="CAEModel",
        model_class="CAEModel",
    )
    capsys.readouterr()

    # A logged linear baseline whose breakeven sits below the contract's one-way cost.
    # ``record_trial`` reserves the trial number itself; pre-reserving would be a duplicate.
    registry = ResearchRegistry(_db(repo))
    cost_bps = contract.total_one_way_cost_decimal() * 1e4
    registry.record_trial(
        ExperimentResult(
            run_id="RUN-T13-LINEAR-BELOW",
            hypothesis_id="HYP-T13-HURDLE",
            contract_id=contract.contract_id,
            strategy_name="RidgeBaseline",
            strategy_family="cross_sectional_ml",
            config_hash="linear0",
            stage_reached="STAGE_3_STRESS",
            verdict="CANDIDATE_FOR_QUALIFICATION",
            gross_sharpe=1.5,
            net_sharpe=1.4,
            deflated_sharpe_prob=0.95,
            expected_max_sharpe_noise=0.2,
            total_trials_n=1,
            effective_trials_keff=1.0,
            mean_ic=0.03,
            ic_hac_tstat=2.5,
            hit_rate=0.52,
            profit_factor=1.2,
            max_drawdown_pct=3.0,
            annualized_turnover=10.0,
            breakeven_cost_bps=max(0.0, cost_bps - 0.5),
            stress_2x_cost_sharpe=0.9,
            param_neighbor_stability=None,
            notes="linear baseline below the contract cost floor",
            delay_plus1_sharpe=0.5,
            synthetic=0,
            data_snapshot_sha256=contract.data_snapshot_sha256,
            strategy_module=ridge_rel,
            model_type="Ridge",
            data_provenance="verified_market",
        ),
        {},
        np.zeros(0),
    )
    with pytest.raises(LinearBaselineRequiredError, match="cost-breakeven"):
        ExperimentRunner(repo_root=repo).run_stage1_screen(spec)

    assert _count(repo, "selection_attempts", "HYP-T13-HURDLE") == 0
    assert _count(repo, "screen_receipts", "HYP-T13-HURDLE") == 0


# ---------------------------------------------------------------------------
# 4. audit evidence produced by real runs, and consumed by `audit-check`
# ---------------------------------------------------------------------------


def test_real_runs_record_tape_cadence_and_verified_fold_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=53)
    repo, contract = _setup_repo(tmp_path, name="audit_producers", panel=panel)
    carry_rel = _write_carry_strategy(repo, publish=True, name="carry_pair_own.py")
    ridge_rel = _write_ridge_strategy(repo)
    monkeypatch.chdir(repo)

    carry_spec = _register(
        repo,
        contract,
        hyp_id="HYP-T13-CADENCE",
        family="carry_rv",
        strat_module=carry_rel,
        parameters={"carry_outputs_mode": "stationary"},
        run_id="RUN-T13-CADENCE",
    )
    capsys.readouterr()
    assert cli_main(["backtest", str(carry_spec), "--json"]) == 0
    capsys.readouterr()
    carry_diag = _diag(repo, "RUN-T13-CADENCE")
    cadence = carry_diag["cadence_evidence"]
    assert cadence["measured_on"] == "evaluation_tape_positions"
    assert cadence["n_bars"] > 100
    # A held pair hedge with a 2-bar label horizon is the divergent case by measurement:
    # the position did not turn over at the label cadence.
    assert cadence["holding_cadence_bars"] > contract.label_horizon_bars * 2
    # The censoring counts behind the Check 1 holding gate are recorded, not inferred later.
    assert "holding_completed_epochs" in cadence
    assert cadence["holding_censored_epochs"] >= 1  # the hedge is still open at the last bar
    audited = evaluate_cadence_alignment(
        cadence, label_horizon_bars=contract.label_horizon_bars
    )
    assert audited["evidence"] == "recorded"
    assert audited["holding_cadence_basis"] in {"right_censored", "completed_epochs"}
    assert carry_diag["execution_protocol"] == "causal_rules"
    assert carry_diag["fold_isolation"] is None

    ridge_spec = _register(
        repo,
        contract,
        hyp_id="HYP-T13-FOLDS",
        family="cross_sectional_ml",
        strat_module=ridge_rel,
        model_type="Ridge",
        model_class="Ridge",
        run_id="RUN-T13-FOLDS",
    )
    capsys.readouterr()
    assert cli_main(["backtest", str(ridge_spec), "--json"]) == 0
    capsys.readouterr()
    ridge_diag = _diag(repo, "RUN-T13-FOLDS")
    folds = ridge_diag["fold_isolation"]
    assert ridge_diag["execution_protocol"] == "causal_model"
    assert folds["verified"] is True
    assert folds["folds"] == contract.n_folds
    assert folds["min_gap_bars"] >= (
        contract.label_horizon_bars + contract.execution_delay_bars + contract.embargo_bars
    )
    assert folds["labels_stripped"] is True
    assert all(record["n_train"] > 0 and record["n_val"] > 0 for record in folds["records"])


def test_audit_check_fails_check1_on_recorded_divergence_and_passes_with_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=59)
    repo, contract = _setup_repo(tmp_path, name="audit_check1", panel=panel)
    ridge_rel = _write_ridge_strategy(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("warden.cli.check_worktree", lambda *a, **k: True)

    # An ML spec whose decision cadence is far slower than its 2-bar label horizon: the
    # label that trained the model describes a horizon the strategy does not trade.
    undeclared = _register(
        repo,
        contract,
        hyp_id="HYP-T13-CHECK1-NO",
        family="cross_sectional_ml",
        strat_module=ridge_rel,
        model_type="Ridge",
        model_class="Ridge",
        parameters={"rebalance_every": 64},
        run_id="RUN-T13-CHECK1-NO",
    )
    capsys.readouterr()
    assert cli_main(["backtest", str(undeclared), "--json"]) == 0
    capsys.readouterr()
    assert cli_main(["audit-check", "--run-id", "RUN-T13-CHECK1-NO", "--json"]) == 1
    audit = json.loads(capsys.readouterr().out)
    check1 = audit["checks"]["1_target_rebalance_alignment"]
    assert check1["passed"] is False
    assert check1["evidence"] == "recorded"
    assert check1["material_divergence"] is True
    assert check1["observed_rebalance_cadence_bars"] > contract.label_horizon_bars * 4
    assert "without declared alignment" in check1["reason"]

    declared = _register(
        repo,
        contract,
        hyp_id="HYP-T13-CHECK1-YES",
        family="cross_sectional_ml",
        strat_module=ridge_rel,
        model_type="Ridge",
        model_class="Ridge",
        parameters={
            "rebalance_every": 64,
            "label_horizon_alignment": {
                "reason": "slow quarterly rebalance; the 2-bar label only ranks entries",
                "declared_cadence_bars": 64,
            },
        },
        run_id="RUN-T13-CHECK1-YES",
    )
    capsys.readouterr()
    assert cli_main(["backtest", str(declared), "--json"]) == 0
    capsys.readouterr()
    assert cli_main(["audit-check", "--run-id", "RUN-T13-CHECK1-YES", "--json"]) == 1
    audit_declared = json.loads(capsys.readouterr().out)
    check1_declared = audit_declared["checks"]["1_target_rebalance_alignment"]
    assert check1_declared["passed"] is True
    assert check1_declared["material_divergence"] is True
    assert check1_declared["declared_alignment"]["reason"].startswith("slow quarterly rebalance")


def test_audit_check_fails_check1_on_recorded_holding_divergence_and_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#99: the holding half of Check 1 gates, and the reason reaches JSON and Markdown.

    The tape is measured by ``measure_trading_cadence`` -- the same primitive a real run uses
    -- so the audit is reading the producer's own numbers.  The decision cadence here is fast
    (a weight change every bar); only the holding cadence is divergent, which proves the
    failure is the dimension the previous Check 1 reported and never judged.
    """
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=67)
    repo, contract = _setup_repo(tmp_path, name="audit_holding", panel=panel)
    ridge_rel = _write_ridge_strategy(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("warden.cli.check_worktree", lambda *a, **k: True)

    # Ramp into a position over 39 bars, step flat on the 40th, repeat: the weight changes
    # every bar (fast decisions) while each completed hold lasts 39 bars.
    n_bars = 180
    tape = np.zeros((n_bars, 1))
    for t in range(n_bars):
        if t % 40 != 39:
            tape[t, 0] = 0.5 + 0.002 * float(t % 40)
    measured = measure_trading_cadence(tape)
    assert measured["rebalance_cadence_bars"] == pytest.approx(1.0)
    assert measured["holding_completed_epochs"] > 0
    assert measured["holding_completed_cadence_bars"] > contract.label_horizon_bars * 4

    _register(
        repo,
        contract,
        hyp_id="HYP-T13-CHECK1-HOLD",
        family="cross_sectional_ml",
        strat_module=ridge_rel,
        model_type="Ridge",
        model_class="Ridge",
        run_id="RUN-T13-CHECK1-HOLD",
        expected_holding_bars=8,
    )
    capsys.readouterr()

    def _record(run_id: str, declared: dict | None) -> None:
        registry = ResearchRegistry(_db(repo))
        registry.record_trial(
            ExperimentResult(
                run_id=run_id,
                hypothesis_id="HYP-T13-CHECK1-HOLD",
                contract_id=contract.contract_id,
                strategy_name="RidgeBaseline",
                strategy_family="cross_sectional_ml",
                config_hash="hold0",
                stage_reached="STAGE_3_STRESS",
                verdict="CANDIDATE_FOR_QUALIFICATION",
                gross_sharpe=1.9,
                net_sharpe=1.7,
                deflated_sharpe_prob=0.95,
                expected_max_sharpe_noise=0.2,
                total_trials_n=1,
                effective_trials_keff=1.0,
                mean_ic=0.03,
                ic_hac_tstat=2.5,
                hit_rate=0.52,
                profit_factor=1.3,
                max_drawdown_pct=3.0,
                annualized_turnover=10.0,
                breakeven_cost_bps=12.0,
                stress_2x_cost_sharpe=1.1,
                param_neighbor_stability=None,
                notes="recorded holding-divergence witness",
                delay_plus1_sharpe=0.9,
                synthetic=0,
                data_snapshot_sha256=contract.data_snapshot_sha256,
                strategy_module=ridge_rel,
                model_type="Ridge",
                data_provenance="verified_market",
            ),
            {},
            np.zeros(0),
            diagnostics={
                "execution_protocol": "causal_model",
                "cadence_evidence": {
                    **measured,
                    "label_horizon_bars": float(contract.label_horizon_bars),
                    "expected_holding_bars": 8.0,
                    "declared_alignment": declared,
                    "measured_on": "evaluation_tape_positions",
                },
                "fold_isolation": {
                    "protocol": "causal_model",
                    "folds": contract.n_folds,
                    "min_gap_bars": float(
                        contract.label_horizon_bars
                        + contract.execution_delay_bars
                        + contract.embargo_bars
                    ),
                    "labels_stripped": True,
                    "verified": True,
                },
            },
        )

    _record("RUN-T13-CHECK1-HOLD", None)
    assert cli_main(["audit-check", "--run-id", "RUN-T13-CHECK1-HOLD", "--json"]) == 1
    audit = json.loads(capsys.readouterr().out)
    check1 = audit["checks"]["1_target_rebalance_alignment"]
    assert check1["passed"] is False
    assert check1["decision_divergence"] is False
    assert check1["holding_divergence"] is True
    assert check1["blocking_dimensions"] == ["holding"]
    assert check1["holding_cadence_basis"] == "completed_epochs"
    assert "holding cadence" in check1["reason"]
    assert "without declared alignment" in check1["reason"]
    report = (repo / ".workspace" / "hypotheses" / "AUDIT_RUN-T13-CHECK1-HOLD.md").read_text(
        encoding="utf-8"
    )
    assert "Cadence check:" in report
    assert "holding cadence" in report
    assert "Cadence observations:" in report
    assert f"holding={check1['observed_holding_completed_cadence_bars']} bars" in report

    # The same tape with a hold declared up front, consistent with the measurement, passes the
    # holding half -- the declaration is checkable, not a narrative waiver.
    _record(
        "RUN-T13-CHECK1-HOLD-YES",
        {
            "reason": "carry hedge is held until the funding leg decays",
            "declared_holding_bars": float(measured["holding_completed_cadence_bars"]),
        },
    )
    cli_main(["audit-check", "--run-id", "RUN-T13-CHECK1-HOLD-YES", "--json"])
    declared_audit = json.loads(capsys.readouterr().out)
    check1_declared = declared_audit["checks"]["1_target_rebalance_alignment"]
    assert check1_declared["passed"] is True
    assert check1_declared["holding_divergence"] is True
    assert check1_declared["declared_alignment_valid"]["holding"] is True


def test_audit_check_fails_check4_when_ml_trial_lacks_fold_isolation_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _carry_panel(stationary=True, positive_carry=True, n_bars=240, seed=61)
    repo, contract = _setup_repo(tmp_path, name="audit_check4", panel=panel)
    ridge_rel = _write_ridge_strategy(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("warden.cli.check_worktree", lambda *a, **k: True)

    # Real ML run: the fold record comes from the folds that ran.
    spec = _register(
        repo,
        contract,
        hyp_id="HYP-T13-CHECK4-OK",
        family="cross_sectional_ml",
        strat_module=ridge_rel,
        model_type="Ridge",
        model_class="Ridge",
        run_id="RUN-T13-CHECK4-OK",
    )
    capsys.readouterr()
    assert cli_main(["backtest", str(spec), "--json"]) == 0
    capsys.readouterr()
    cli_main(["audit-check", "--run-id", "RUN-T13-CHECK4-OK", "--json"])
    audit = json.loads(capsys.readouterr().out)
    check4 = audit["checks"]["4_fold_local_fitting"]
    assert check4["passed"] is True
    assert check4["basis"] == "fold_isolation_evidence"
    assert check4["folds"] == float(contract.n_folds)
    assert check4["checks"]["purge_embargo_applied"] is True

    # Legacy-style ML trial: the same contract has the required integer fields, but the
    # trial carries no execution record, so the check must refuse it.
    registry = ResearchRegistry(_db(repo))
    registry.record_trial(
        ExperimentResult(
            run_id="RUN-T13-CHECK4-LEGACY",
            hypothesis_id="HYP-T13-CHECK4-OK",
            contract_id=contract.contract_id,
            strategy_name="LegacyML",
            strategy_family="cross_sectional_ml",
            config_hash="legacy0",
            stage_reached="STAGE_3_STRESS",
            verdict="CANDIDATE_FOR_QUALIFICATION",
            gross_sharpe=1.6,
            net_sharpe=1.5,
            deflated_sharpe_prob=0.95,
            expected_max_sharpe_noise=0.2,
            total_trials_n=1,
            effective_trials_keff=1.0,
            mean_ic=0.03,
            ic_hac_tstat=2.5,
            hit_rate=0.52,
            profit_factor=1.2,
            max_drawdown_pct=3.0,
            annualized_turnover=10.0,
            breakeven_cost_bps=12.0,
            stress_2x_cost_sharpe=0.9,
            param_neighbor_stability=None,
            notes="ml trial without fold record",
            delay_plus1_sharpe=0.5,
            synthetic=0,
            data_snapshot_sha256=contract.data_snapshot_sha256,
            strategy_module=ridge_rel,
            model_type="Ridge",
            data_provenance="verified_market",
        ),
        {},
        np.zeros(0),
        diagnostics={"execution_protocol": "causal_model"},
    )
    cli_main(["audit-check", "--run-id", "RUN-T13-CHECK4-LEGACY", "--json"])
    legacy_audit = json.loads(capsys.readouterr().out)
    legacy_check4 = legacy_audit["checks"]["4_fold_local_fitting"]
    assert legacy_check4["passed"] is False
    assert legacy_check4["basis"] == "fold_isolation_evidence"
    assert "fold-isolation" in legacy_check4["reason"]
